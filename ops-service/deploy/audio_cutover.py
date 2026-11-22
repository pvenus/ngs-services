"""Private, fixed ACE HQ binding for a verified audio source release."""
from pathlib import Path
import hashlib
import os
import shutil
import stat
import tempfile

from prepare_ngs_release import (DIGEST, SHA, atomic_write, digest, json_bytes,
                                 json_load, matches, need, private_directory,
                                 private_file, shape, tree_sha)

NATIVE = 'candidate-native-audio-resident'
PKG = 'modules/audioforge/audioforge'
RUNNER = PKG + '/ace_hq_runner.py'
CODE = (
    'workers/__init__.py', 'workers/audio/__init__.py', 'workers/audio/boundary.py',
    'workers/audio/legacy_backend.py', 'workers/audio/hq_bridge.py',
    *(f'{PKG}/{n}' for n in ('__init__.py','backends.py','hq_contract.py','hq_backend.py','hq_pins.json')),
    RUNNER,
)
OUTER = 'workflow.bgm-adapter-binding.v1'
INNER = 'private.music-hq-boundary.v1'


def _read(path, cap):
    private_file(path, 0o600)
    need(0 < path.stat().st_size <= cap, 'TARGET_AUDIO_BINDING_INVALID')
    return path.read_bytes()


def _path(value):
    need(isinstance(value, str) and value.startswith('/'), 'TARGET_AUDIO_BINDING_INVALID')
    path = Path(value)
    need(str(path) == value and path.resolve(strict=True) == path, 'TARGET_AUDIO_BINDING_INVALID')
    return path


def _configured_outer(args):
    need(args.count('--adapter-config') == 1 and args.index('--adapter-config')+1 < len(args),
         'TARGET_AUDIO_BINDING_INVALID')
    return _path(args[args.index('--adapter-config')+1])


def _config(outer):
    raw = _read(outer, 4096)
    o = json_load(raw)
    need(shape(o, 'schemaVersion hqBinding') and o['schemaVersion'] == OUTER, 'TARGET_AUDIO_BINDING_INVALID')
    inner = _path(o['hqBinding'])
    data = _read(inner, 65536)
    c = json_load(data)
    need(shape(c, 'schema release_root runtime_root artifact_root runner_path python_path shared_lock legacy_lock directory_pins file_pins')
         and c['schema'] == INNER, 'TARGET_AUDIO_BINDING_INVALID')
    return inner, c, digest(raw + b'\x00' + data)


def _hash(path):
    info = path.lstat()
    need(path.resolve(strict=True) == path and stat.S_ISREG(info.st_mode)
         and info.st_uid in (0,os.getuid()) and not info.st_mode & 0o022
         and info.st_nlink == 1,'TARGET_AUDIO_BINDING_PINS')
    with path.open('rb') as stream:
        h = hashlib.sha256()
        for data in iter(lambda: stream.read(1024*1024), b''):
            h.update(data)
        return h.hexdigest()


def _verify(c, native, *, bind_paths=True):
    if bind_paths:
        need(c['release_root'] == str(native) and c['runner_path'] == str(native / RUNNER),
             'TARGET_AUDIO_BINDING_RELEASE')
    runtime = _path(c['runtime_root'])
    launcher = Path(c['python_path'])
    need(launcher == runtime / 'source/.venv/bin/python'
         and launcher.parent.resolve(strict=True) == launcher.parent, 'TARGET_AUDIO_BINDING_RUNTIME')
    runtime_files = (launcher.resolve(strict=True), runtime / 'source/.venv/pyvenv.cfg')
    dirs = c['directory_pins']
    need(isinstance(dirs,dict), 'TARGET_AUDIO_BINDING_RUNTIME')
    for key in ('runtime_root','artifact_root'):
        pin = dirs.get(key)
        observed = _path(c[key]).stat()
        need(isinstance(pin,dict) and (pin.get('device'),pin.get('inode')) == (observed.st_dev,observed.st_ino)
             and stat.S_ISDIR(observed.st_mode),'TARGET_AUDIO_BINDING_RUNTIME')
    locks = []
    for key in ('shared_lock','legacy_lock'):
        pin = c[key]
        need(isinstance(pin,dict),'TARGET_AUDIO_BINDING_RUNTIME')
        lock = _path(pin.get('path'))
        observed = lock.stat()
        identity = observed.st_dev,observed.st_ino
        need(identity == (pin.get('device'),pin.get('inode')) and stat.S_ISREG(observed.st_mode)
             and observed.st_uid == os.getuid() and observed.st_nlink == 1 and not observed.st_mode&0o022,
             'TARGET_AUDIO_BINDING_RUNTIME')
        locks.append(identity)
    need(locks[0] != locks[1],'TARGET_AUDIO_BINDING_RUNTIME')
    expected = [*(native / rel for rel in CODE), *runtime_files]
    pins = c['file_pins']
    need(isinstance(pins, dict) and set(pins) == {str(file) for file in expected},
         'TARGET_AUDIO_BINDING_PINS')
    for file in expected:
        need(matches(DIGEST, pins[str(file)]) and _hash(file) == pins[str(file)],
             'TARGET_AUDIO_BINDING_PINS')
    return runtime_files


def _paths(release, sha):
    roots = release.parent
    need(matches(SHA, sha) and release.name == 'workflows-github-' + sha, 'TARGET_AUDIO_BINDING_RELEASE')
    native = roots / ('workflow-native-audio-resident-github-' + sha)
    output = roots.parent.parent / 'runtime' / ('github-workflows-' + sha)
    return native, output / 'audio-workflow-adapter.private.json', output / 'audio-hq-boundary.private.json'


def _copy_verified(src, dst, expected):
    if not os.path.lexists(dst):
        with tempfile.TemporaryDirectory(prefix='.ops-native-audio-',dir=dst.parent) as stage:
            tree = Path(stage)/'native'
            shutil.copytree(src,tree)
            private_directory(tree)
            need(tree_sha(tree) == expected,'TARGET_AUDIO_BINDING_TREE')
            # Same source bytes; the enclosing verified release and file pins are
            # rechecked before readiness and again before the selector can change.
            need(not os.path.lexists(dst),'TARGET_AUDIO_BINDING_ALREADY_STAGED')
            os.rename(tree,dst)
    private_directory(dst)
    need(tree_sha(dst) == expected,'TARGET_AUDIO_BINDING_TREE')


def stage_audio_binding(args, release, sha):
    """Called only after canonical selected source proof and prior selector CAS."""
    src = release / NATIVE
    private_directory(src)
    expected_tree = tree_sha(src)
    live_outer = _configured_outer(args)
    _, old, _ = _config(live_outer)
    prior_native = _path(old['release_root'])
    need(prior_native.parent == release.parent, 'TARGET_AUDIO_BINDING_RELEASE')
    runtime_files = _verify(old, prior_native)
    native, outer, inner = _paths(release, sha)
    _copy_verified(src, native, expected_tree)
    outer.parent.mkdir(mode=0o700, exist_ok=True)
    private_directory(outer.parent)
    pins = {str(native / rel): _hash(native / rel) for rel in CODE}
    pins.update((str(file), old['file_pins'][str(file)]) for file in runtime_files)
    c = {**old,'release_root':str(native),'runner_path':str(native / RUNNER),'file_pins':pins}
    _verify(c, native)
    for file, contents in ((inner,json_bytes(c)),(outer,json_bytes(dict(schemaVersion=OUTER,hqBinding=str(inner))))):
        if os.path.lexists(file):
            need(_read(file,65536) == contents,'TARGET_AUDIO_BINDING_ALREADY_STAGED')
        else:
            atomic_write(file,contents)
    return verify_audio_binding(release,sha)[1]


def verify_audio_binding(release, sha):
    native, outer, inner = _paths(release,sha)
    private_directory(outer.parent)
    private_directory(native)
    private_directory(release / NATIVE)
    need(tree_sha(native) == tree_sha(release / NATIVE),'TARGET_AUDIO_BINDING_TREE')
    configured_inner,c,proof = _config(outer)
    need(configured_inner == inner,'TARGET_AUDIO_BINDING_INVALID')
    _verify(c,native)
    return outer,proof
