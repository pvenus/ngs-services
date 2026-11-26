"""Fixed-path OPS housekeeping. Schedule this program externally with launchd."""

from contextlib import contextmanager
from dataclasses import dataclass
import argparse
import errno
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import stat
import subprocess
import time


RETENTION_DAYS = 10
NANOSECONDS_PER_DAY = 86_400 * 1_000_000_000
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@dataclass(frozen=True)
class Rule:
    name: str
    directory: str  # Relative to ~/Workspaces, never a glob.
    suffix: str
    kind: str = 'file'  # 'empty-directory' is supported for narrowly named leaf folders.
    lock: str = 'none'  # 'huggingface' guards the producer's existing download lock.

    def __post_init__(self):
        parts = self.directory.split('/')
        if (any(p in ('', '.', '..') for p in parts)
                or not self.suffix.startswith('.') or len(self.suffix) < 2
                or '/' in self.suffix or any(c in self.suffix for c in '*?[]')
                or self.kind not in ('file', 'empty-directory')
                or self.lock not in ('none', 'huggingface')):
            raise ValueError('invalid cleanup rule')


RULES = (
    Rule('wan-part', 'Workers/code/experiments/comfyui-wan-animate2-20261111/models/diffusion_models', '.part'),
    Rule('ace-incomplete', 'Workers/runtime/acestep/source/checkpoints/acestep-v15-xl-sft/.cache/huggingface/download',
         '.incomplete', lock='huggingface'),
)


@contextmanager
def open_directory(path):
    # Resolve each path component under a no-follow descriptor, not Path.resolve().
    if not path.is_absolute():
        raise ValueError('workspace root must be absolute')
    fd = os.open('/', DIRECTORY_FLAGS)
    try:
        for name in path.parts[1:]:
            if name in ('.', '..'):
                raise ValueError('unsafe cleanup path')
            child = os.open(name, DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def identity(info):
    return info.st_dev, info.st_ino


def unchanged(info):
    return (*identity(info), info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def no_open_descriptors(path):
    # A true no-match from macOS lsof is status 1 with no output. Errors, warnings
    # and timeouts are not proof the file is unused. Keep argument separate.
    try:
        result = subprocess.run(['/usr/sbin/lsof', '-nP', '-Fn', str(path)],
                                stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=8, check=False, env={'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'})
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 1 and not result.stdout and not result.stderr


@contextmanager
def download_guard(directory_fd, name, rule, owner):
    if rule.lock == 'none':
        yield True
        return
    # Hugging Face local-dir child partials are filename.ETAG.incomplete.
    # Skip unknown names instead of guessing at their producer lock.
    parts = name.rsplit('.', 2)
    if len(parts) != 3 or not all(parts):
        yield False
        return
    lockname = parts[0] + '.lock'
    try:
        fd = os.open(lockname, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    except FileNotFoundError:
        # No pre-existing lock. A newly appeared lock is checked separately.
        yield None
        return
    except OSError:
        yield False
        return
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_nlink != 1:
            yield False
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield identity(info)
    finally:
        os.close(fd)


def same_producer_lock(directory_fd, name, rule, guard):
    if rule.lock == 'none':
        return True
    lockname = name.rsplit('.', 2)[0] + '.lock'
    try:
        info = os.stat(lockname, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return guard is None
    return bool(guard and guard == identity(info) and stat.S_ISREG(info.st_mode))


def cleanup(workspace, *, rules=RULES, now_ns=None, dry_run=False, open_check=no_open_descriptors):
    cutoff = (time.time_ns() if now_ns is None else now_ns) - RETENTION_DAYS * NANOSECONDS_PER_DAY
    result = {}
    owner = os.geteuid()
    for rule in rules:
        counts = dict(removed=0, eligible=0, skipped=0, allocatedBytes=0, errors=0)
        result[rule.name] = counts
        path = workspace.joinpath(*PurePosixPath(rule.directory).parts)
        try:
            with open_directory(path) as fd:
                directory = os.fstat(fd)
                if directory.st_uid != owner:
                    counts['errors'] += 1
                    continue
                for name in os.listdir(fd):
                    if not name.endswith(rule.suffix):
                        continue
                    try:
                        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                        is_file = rule.kind == 'file'
                        good_type = stat.S_ISREG(info.st_mode) and info.st_nlink == 1 if is_file else stat.S_ISDIR(info.st_mode)
                        if (not good_type or info.st_uid != owner or info.st_dev != directory.st_dev
                                or info.st_mtime_ns > cutoff):
                            counts['skipped'] += 1
                            continue
                        with download_guard(fd, name, rule, owner) as protected:
                            if protected is False or not open_check(path / name):
                                counts['skipped'] += 1
                                continue
                            # Do not remove an entry changed or swapped during the open-file probe.
                            with open_directory(path) as current:
                                if (identity(os.fstat(current)) != identity(directory)
                                        or not same_producer_lock(fd, name, rule, protected)
                                        or unchanged(os.stat(name, dir_fd=fd, follow_symlinks=False)) != unchanged(info)):
                                    counts['skipped'] += 1
                                    continue
                                if not is_file:
                                    nested = os.open(name, DIRECTORY_FLAGS, dir_fd=fd)
                                    try:
                                        if unchanged(os.fstat(nested)) != unchanged(info) or os.listdir(nested):
                                            counts['skipped'] += 1
                                            continue
                                    finally:
                                        os.close(nested)
                                counts['eligible'] += 1
                                if not dry_run:
                                    if is_file:
                                        os.unlink(name, dir_fd=fd)
                                    else:
                                        # A concurrent child appearing makes rmdir fail safely.
                                        os.rmdir(name, dir_fd=fd)
                                    counts['removed'] += 1
                                    counts['allocatedBytes'] += info.st_blocks * 512
                    except OSError as exc:
                        key = 'skipped' if exc.errno in (errno.ENOENT, errno.ENOTEMPTY, errno.EBUSY) else 'errors'
                        counts[key] += 1
        except FileNotFoundError:
            # Older machines may not have a given model/cache installed.
            pass
        except OSError:
            counts['errors'] += 1
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    report = cleanup(home / 'Workspaces', dry_run=args.dry_run)
    print(json.dumps({'dryRun': args.dry_run, 'retentionDays': RETENTION_DAYS, 'rules': report}, sort_keys=True))
    return int(any(rule['errors'] for rule in report.values()))


if __name__ == '__main__':
    raise SystemExit(main())
