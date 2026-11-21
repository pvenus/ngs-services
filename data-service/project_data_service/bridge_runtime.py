"""Optional PDS-owned Filex delivery. No WebSocket or NGS polling.
The formal publisher owns intents; this thread never executes requests or models.
No independently registered daemon, local replica read, or inbound public listener.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import threading
import types
from pathlib import Path

DEFAULT_CONFIG = Path.home() / 'Library/Application Support/NPCGen/pds-filex-bridge.json'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_file(path, private=False):
    path = Path(path)
    if path.is_symlink() or not path.is_absolute() or path.resolve() != path:
        raise ValueError('BRIDGE_CANONICAL_PATH_REQUIRED')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
        st = os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode) or private and (st.st_uid != os.getuid() or st.st_mode & 0o077):
            raise ValueError('BRIDGE_PRIVATE_CONFIG_REQUIRED')
        return stream.read()


def load_engine(config):
    root = Path(config['runtimeRoot'])
    if root.resolve() != root or root.stat().st_uid != os.getuid():
        raise ValueError('BRIDGE_RUNTIME_IDENTITY')
    folder = root / 'local/services/published_files'
    hashes = config['codeHashes']
    if set(hashes) != {'exporter.py', 'producer.py'}:
        raise ValueError('BRIDGE_CODE_PIN_REQUIRED')
    raws = {}
    for name, expected in hashes.items():
        raw = read_file(folder / name)
        if not re.fullmatch('[a-f0-9]{64}', str(expected)) or digest(raw) != expected:
            raise ValueError('BRIDGE_CODE_CHANGED')
        raws[name] = raw
    # Private namespace: PDS does not add a mutable NGS checkout to sys.path.
    namespace = '_pds_filex_' + digest(canonical(hashes))[:24]
    package = types.ModuleType(namespace)
    package.__path__ = [str(folder)]
    sys.modules[namespace] = package
    for name in ('exporter', 'producer'):
        module = types.ModuleType(namespace + '.' + name)
        module.__file__ = str(folder / (name + '.py'))
        module.__package__ = namespace
        sys.modules[module.__name__] = module
        exec(compile(raws[name + '.py'], module.__file__, 'exec'), module.__dict__)
    return sys.modules[namespace + '.producer']



class FilexBridge:
    def __init__(self, config_path=DEFAULT_CONFIG, engine_loader=load_engine):
        self.config_path = Path(config_path)
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self._status = {'enabled':False, 'state':'disabled', 'tickActive':False, 'ownerPid':os.getpid()}
        self.engine = None
        if not self.config_path.exists():
            return
        raw = read_file(self.config_path, private=True)
        self.config_hash = digest(raw)
        self.config = json.loads(raw)
        if self.config.get('enabled') is not True:
            return
        if self.config.get('schemaVersion') != 'pds-filex-bridge.v1' or self.config.get('projectId') != 'binari' or not 1 <= self.config.get('intervalSeconds', 30) <= 60:
            raise ValueError('BRIDGE_CONFIG_INVALID')
        self.engine = engine_loader(self.config)
        self.producer_path = Path(self.config['producerConfig'])
        self.producer_hash = digest(read_file(self.producer_path, private=True))
        producer = self.engine.configuration(self.producer_path)
        if producer is None:
            raise ValueError('BRIDGE_PRODUCER_DISABLED')
        self.output_root = producer['outputRoot']
        self._status.update(enabled=True, state='idle')

    @property
    def enabled(self):
        return self._status['enabled']

    def status(self):
        with self.lock:
            return dict(self._status)

    def cycle(self):
        if not self.enabled:
            return self.status()
        with self.lock:
            self._status.update(state='delivering', tickActive=True)
        try:
            if digest(read_file(self.config_path, private=True)) != self.config_hash or digest(read_file(self.producer_path, private=True)) != self.producer_hash:
                raise ValueError('BRIDGE_CONFIGURATION_CHANGED')
            result = self.engine.produce_once(config_path=self.producer_path)
            with self.lock:
                self._status = {'enabled':True, 'state':result.get('state', 'idle'), 'tickActive':False, 'ownerPid':os.getpid()}
                if result.get('version'):
                    self._status['version'] = result['version']
                if result.get('error'):
                    self._status['lastError'] = result['error'] if re.fullmatch('[A-Z0-9_]+', str(result['error'])) else 'DELIVERY_PENDING'
        except Exception as exc:
            code = str(exc) if re.fullmatch('[A-Z0-9_]+', str(exc)) else type(exc).__name__
            with self.lock:
                self._status = {'enabled':True, 'state':'blocked', 'tickActive':False, 'ownerPid':os.getpid(), 'lastError':code}
        return self.status()

    def start(self):
        if not self.enabled or self.thread:
            return
        def loop():
            while not self.stop_event.is_set():
                self.cycle()
                self.stop_event.wait(self.config.get('intervalSeconds', 30))
        self.thread = threading.Thread(target=loop, name='pds-filex-delivery', daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=8)
