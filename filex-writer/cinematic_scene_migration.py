"""Offline, one scene at a time. Dry-run is the default; old entries are archived."""
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path

from media_store import (CinematicProjectStore, CinematicTargetDelete, CINEMATIC_MAX, DIGEST,
                         source_entry, source_image, wire, seq_sync_dir)


def regular_tree(path, *, skip_hidden=False):
    result = {}
    queue = [path]
    while queue:
        item = queue.pop()
        mode = item.lstat()
        if stat.S_ISDIR(mode.st_mode):
            queue.extend(x for x in item.iterdir() if not (skip_hidden and x.name.startswith('.')))
        elif stat.S_ISREG(mode.st_mode) and mode.st_nlink == 1:
            with item.open('rb') as stream:
                result[item.relative_to(path).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
        else:
            raise ValueError('scene migration encountered a link or special file')
    return result


def migrate(store, source, *, apply=False):
    guard = CinematicTargetDelete(store)
    with store.lock():
        guard.ensure_source_active(source)
        old = store.root
        final = store._scene_dir(source)
        backup = old / '.scene-migration' / source
        if final.exists() or backup.exists() or backup.parent.is_symlink() or backup.is_symlink():
            raise ValueError('scene migration destination exists; reconcile before retry')
        meta = store._path('sources', source + '.json')
        image = store._path('sources', source + '.png')
        raw = store._read(meta, 8192)
        m = source_entry(json.loads(raw))
        if raw != wire(m) or m['sha256'] != source:
            raise ValueError('scene migration source identity')
        source_image(store._read(image, CINEMATIC_MAX), m)
        guard._uncorrelated_sources(source, m['officialRequestId'])
        entries = {'source.json': meta, 'source.png': image}
        locators = set()
        for row in guard._remaining_children(source):
            rel = row['path']
            folder, name = rel.split('/')
            oid = name if folder == 'sequences' else name.rsplit('.', 1)[0]
            if folder == 'sequences':
                manifest = store._seq_result(oid)
                if manifest.get('group'):
                    continue
                if manifest['sourceSha256'] != source:
                    raise ValueError('scene migration sequence identity')
            else:
                if store._result(oid)['sourceSha256'] != source:
                    raise ValueError('scene migration result identity')
            if store._located_scene(folder, oid) is not None:
                raise ValueError('scene migration result is already indexed')
            entries[rel] = old / rel
            locators.add((folder, oid))
        expected = {name: regular_tree(path) for name, path in entries.items()}
        report = {'sourceSha256': source, 'targets': sorted(entries),
                  'files': sum(map(len, expected.values())), 'applied': apply}
        if not apply:
            return report
        final.parent.mkdir(mode=0o700, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.migrate-', dir=final.parent))
        for name, path in entries.items():
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if path.is_dir():
                shutil.copytree(path, dest)
            else:
                shutil.copy2(path, dest)
        if any(regular_tree(stage / name) != value for name, value in expected.items()):
            raise ValueError('scene migration copy verification failed')
        for parent, _, files in os.walk(stage, topdown=False):
            for name in files:
                with (Path(parent) / name).open('rb') as fp:
                    os.fsync(fp.fileno())
            seq_sync_dir(Path(parent))
        os.rename(stage, final)
        seq_sync_dir(final.parent)
        for folder, oid in sorted(locators):
            store._bind_scene(folder, oid, source)
        # Preserve the original entries outside the synchronized tree. A crash
        # after publish but before this finishes leaves verified copies in the
        # new location; absence alone is never interpreted as success.
        for _, path in entries.items():
            dest = backup / path.relative_to(old)
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.rename(path, dest)
            seq_sync_dir(dest.parent)
            seq_sync_dir(path.parent)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('source_sha256')
    parser.add_argument('--apply-offline', action='store_true',
                        help='Only after all readers are upgraded and sync/workers are paused')
    a = parser.parse_args()
    root = a.root.resolve(strict=True)
    if a.root != root or not root.is_dir() or not re.fullmatch(DIGEST, a.source_sha256):
        parser.error('supply the canonical cinematics directory and exact source SHA-256')
    print(json.dumps(migrate(CinematicProjectStore(root), a.source_sha256, apply=a.apply_offline)))


if __name__ == '__main__':
    main()
