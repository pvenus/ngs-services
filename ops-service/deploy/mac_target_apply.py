#!/opt/homebrew/bin/python3.12
"""Fixed local launchd target adapter. The operator provisions verified releases."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import time
import urllib.request

from prepare_ngs_release import (
    DIGEST, SHA, atomic_write, digest, json_load, matches, need, private_directory,
    private_file, shape, tree_sha,
)


@dataclass(frozen=True)
class Target:
    label: str
    workspace: str
    release_prefix: str
    entry: str
    heartbeat: str | None


def worker(name, entry):
    return Target(f'com.vinus.workflow.{name}-worker', 'Workers', 'workflows-github-', entry, f'{name}-worker')


TARGETS = {
    'worker-document': worker('document', 'document/cli.mjs'),
    'worker-file': worker('file', 'file/cli.mjs'),
    'worker-image': worker('image', 'image/image-cli.mjs'),
    'worker-animation': worker('animation', 'animation/animation-cli.mjs'),
    'worker-postprocess': worker('postprocess', 'postprocess/postprocess-cli.mjs'),
    'worker-audio': worker('audio', 'candidate-audio-resident/audio-cli.mjs'),
    'request-service': Target('com.vinus.ngs.request-service', 'BackendServices', 'ngs-services-github-',
                              'request-service/request-service.mjs', 'rs'),
    'rs-mcp': Target('com.vinus.ngs.rs-mcp', 'NGS', 'project-page-github-',
                     'sites/npcgen/build/rs-mcp.mjs', 'rs-mcp'),
    'backend-stack': Target('com.projectbs.backend-stack', 'BackendServices', 'ngs-services-github-',
                            'runtime/stack.py', None),
}
PORTS = (8030, 8031, 8032, 8040, 8041, 8050, 8060)
LABEL_PID = re.compile(r'^\s*pid = ([1-9][0-9]*)\s*$', re.MULTILINE)


def validate_and_rewrite(prior, release, catalog, base):
    v = plistlib.loads(prior)
    need(isinstance(v, dict) and v.get('Label') == catalog.label, 'TARGET_SELECTOR_LABEL')
    args = v.get('ProgramArguments')
    need(isinstance(args, list) and len(args) >= 2 and all(isinstance(a, str) for a in args),
         'TARGET_SELECTOR_ARGUMENTS')
    root = base / catalog.workspace / 'code/releases'
    source = Path(args[1])
    rel = source.relative_to(root).parts if source.is_relative_to(root) else ()
    need(len(rel) >= 2 and re.fullmatch(re.escape(catalog.release_prefix) + r'[a-f0-9]{7,40}', rel[0])
         and '/'.join(rel[1:]) == catalog.entry, 'TARGET_SELECTOR_ENTRY')
    old = root / rel[0]
    changes = {str(old / catalog.entry): str(release / catalog.entry)}
    if catalog.heartbeat == 'rs-mcp':
        env = v.get('EnvironmentVariables')
        need(args[2:] == ['--watch'] and v.get('WorkingDirectory') == str(old)
             and isinstance(env, dict)
             and isinstance(env.get('NGS_SPACE_MACHINE_CREDENTIALS_FILE'), str)
             and Path(env['NGS_SPACE_MACHINE_CREDENTIALS_FILE']).is_absolute()
             and env.get('NGS_SPACE_REQUEST_RELAY_ENABLED') == '1', 'TARGET_RS_MCP_SELECTOR')
    if catalog.heartbeat == 'audio-worker':
        adapter = 'candidate-audio-resident/adapters/load.mjs'
        need(args.count('--adapter-module') == 1 and args.index('--adapter-module') + 1 < len(args)
             and args[args.index('--adapter-module') + 1] == str(old / adapter), 'TARGET_AUDIO_ADAPTER')
        changes[str(old / adapter)] = str(release / adapter)
    if catalog.heartbeat == 'postprocess-worker':
        native = 'native-postprocess'
        need(args.count('--worker-source') == 1 and args.index('--worker-source') + 1 < len(args)
             and args[args.index('--worker-source') + 1] == str(old / native), 'TARGET_POSTPROCESS_ADAPTER')
        changes[str(old / native)] = str(release / native)
    for old_path, new_path in changes.items():
        new = Path(new_path)
        need(new.exists() and not new.is_symlink() and new.resolve() == new,
             'TARGET_RELEASE_ENTRY_MISSING')
    for arg in args:
        need(not arg.startswith(str(old) + '/') or arg in changes, 'TARGET_UNHANDLED_RELEASE_ARG')
    v['ProgramArguments'] = [changes.get(arg, arg) for arg in args]
    if 'WorkingDirectory' in v:
        wd = Path(v['WorkingDirectory'])
        need(wd == (old if catalog.heartbeat == 'rs-mcp' else old / Path(catalog.entry).parent),
             'TARGET_SELECTOR_WORKDIR')
        v['WorkingDirectory'] = str(release / wd.relative_to(old))
    # Reject hidden old release references rather than rewrite unrelated provider/environment values.
    check = plistlib.dumps(v)
    need(str(old).encode() not in check, 'TARGET_UNHANDLED_RELEASE_REFERENCE')
    return check


class LaunchHost:
    def __init__(self, uid):
        self.gui = f'gui/{uid}'
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def ctl(self, *args):
        return subprocess.run(['/bin/launchctl', *args], timeout=15, capture_output=True,
                              text=True, stdin=subprocess.DEVNULL)

    def pid(self, label):
        state = self.ctl('print', self.gui + '/' + label)
        m = LABEL_PID.search(state.stdout) if state.returncode == 0 else None
        return int(m[1]) if m else None

    def remove(self, label):
        result = self.ctl('bootout', self.gui + '/' + label)
        need(result.returncode == 0, 'TARGET_UNLOAD_FAILED')

    def start(self, label, selector):
        need(self.ctl('bootstrap', self.gui, str(selector)).returncode == 0, 'TARGET_BOOTSTRAP_FAILED')
        if self.pid(label) is None:
            need(self.ctl('kickstart', self.gui + '/' + label).returncode == 0, 'TARGET_START_FAILED')

    def get(self, port, path):
        with self.http.open(f'http://127.0.0.1:{port}{path}', timeout=2) as r:
            need(r.status == 200 and r.geturl() == f'http://127.0.0.1:{port}{path}', 'TARGET_HEALTH_HTTP')
            return r.read(128 * 1024 + 1)

    def wait_healthy(self, catalog, since, previous_pid):
        deadline = time.monotonic() + 95
        stable = None
        while time.monotonic() < deadline:
            pid = self.pid(catalog.label)
            try:
                if pid is None or pid == previous_pid:
                    stable = None
                elif catalog.heartbeat:
                    rows = json_load(self.get(8090, '/api/service-status'))['services']
                    row = next((row for row in rows if row.get('serverName') == catalog.heartbeat), None)
                    if (row and row.get('state') == 'connected' and
                            datetime.fromisoformat(row['lastReceivedAt']) >= since and
                            stable == pid and self.pid(catalog.label) == pid):
                        return
                elif stable == pid:
                    for port in PORTS:
                        self.get(port, '/health')
                    if self.pid(catalog.label) == pid:
                        return
            except (OSError, ValueError, KeyError, TypeError):
                pass
            stable = pid
            time.sleep(1)
        need(False, 'TARGET_HEALTH_TIMEOUT')


def apply_target(target, sha, archive_sha, directory, home, host):
    need(target in TARGETS, 'TARGET_UNSUPPORTED')
    need(matches(SHA, sha) and matches(DIGEST, archive_sha), 'TARGET_INPUT_INVALID')
    private_directory(directory)
    manifest_path = directory / 'candidate.json'
    private_file(manifest_path, 0o600)
    m = json_load(manifest_path.read_bytes())
    need(shape(m, 'schemaVersion target sha archiveSha256 treeSha256 expectedPriorPlistSha256') and
         m['schemaVersion'] == 'ops.mac-target-candidate.v1' and m['target'] == target and
         m['sha'] == sha and m['archiveSha256'] == archive_sha and
         all(matches(DIGEST, m[k]) for k in ('treeSha256', 'expectedPriorPlistSha256')),
         'TARGET_CANDIDATE_MISMATCH')
    catalog = TARGETS[target]
    base = home / 'Workspaces'
    release = base / catalog.workspace / 'code/releases' / (catalog.release_prefix + sha)
    private_directory(release)
    need(tree_sha(release) == m['treeSha256'], 'TARGET_RELEASE_TREE_MISMATCH')
    selector = home / 'Library/LaunchAgents' / (catalog.label + '.plist')
    private_file(selector)
    prior = selector.read_bytes()
    need(digest(prior) == m['expectedPriorPlistSha256'], 'TARGET_SELECTOR_CHANGED')
    desired = validate_and_rewrite(prior, release, catalog, base)
    lock_fd = os.open(directory / '.apply.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        private_file(directory / '.apply.lock', 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        private_file(selector)
        need(selector.read_bytes() == prior, 'TARGET_SELECTOR_CHANGED')
        pid = host.pid(catalog.label)
        need(pid is not None, 'TARGET_PRIOR_NOT_RUNNING')
        since = datetime.now(timezone.utc)
        host.remove(catalog.label)
        try:
            atomic_write(selector, desired)
            host.start(catalog.label, selector)
            host.wait_healthy(catalog, since, pid)
        except Exception:
            failed_pid = host.pid(catalog.label)
            try:
                host.remove(catalog.label)
            except Exception:
                if failed_pid:
                    raise RuntimeError('TARGET_ROLLBACK_UNLOAD_FAILED')
            atomic_write(selector, prior)
            before = datetime.now(timezone.utc)
            host.start(catalog.label, selector)
            host.wait_healthy(catalog, before, failed_pid)
            raise RuntimeError('TARGET_APPLY_FAILED_ROLLED_BACK')
    finally:
        os.close(lock_fd)
    return dict(schemaVersion='ops.deploy-target-receipt.v1', target=target, sha=sha,
                deployedAt=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                state='deployed', health='verified')


def main():
    need(len(sys.argv) == 1, 'TARGET_ARGS_FORBIDDEN')
    directory = Path(sys.argv[0]).absolute().parent
    need(directory.parent.name == 'targets' and Path(sys.argv[0]).name == 'run-once',
         'TARGET_RUNNER_LOCATION')
    receipt = apply_target(directory.name, os.environ.get('OPS_DEPLOY_CANDIDATE_SHA'),
                           os.environ.get('OPS_DEPLOY_ARTIFACT_SHA256'), directory,
                           Path.home().resolve(), LaunchHost(os.getuid()))
    print(json.dumps(receipt, separators=(',', ':')))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        code = str(error)
        print(code if re.fullmatch(r'TARGET_[A-Z_]+', code) else 'TARGET_APPLY_REFUSED', file=sys.stderr)
        raise SystemExit(1)
