#!/usr/bin/env python3
"""Start a real isolated stack, check its empty read path, then stop only it."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OFFSETS = (0, 1, 2, 10, 11, 20, 30)


def get(url):
    with urllib.request.urlopen(url, timeout=2) as response:
        assert response.status == 200
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage-root', type=Path, required=True, help='must not exist; parent must exist')
    parser.add_argument('--port-base', type=int, required=True)
    args = parser.parse_args()
    storage = args.storage_root
    if not storage.is_absolute() or not 1024 <= args.port_base <= 65505:
        parser.error('absolute new storage directory and port base 1024..65505 required')
    storage.mkdir(mode=0o700)  # Never reuse, modify, or delete existing data.
    command = [sys.executable, '-B', str(ROOT / 'runtime/stack.py'), '--storage-root', str(storage), '--port-base', str(args.port_base)]
    with (storage / 'smoke.log').open('xb') as log:
        child = subprocess.Popen(command, stdout=log, stderr=log, cwd=ROOT,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        try:
            deadline = time.monotonic() + 40
            while True:
                if child.poll() is not None:
                    raise RuntimeError('stack exited before readiness; inspect smoke.log')
                try:
                    if b'ready workflow ' not in (storage / 'smoke.log').read_bytes():
                        raise OSError('supervisor not ready')
                    health = [get(f'http://127.0.0.1:{args.port_base+i}/health') for i in OFFSETS]
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise RuntimeError('startup timeout; inspect smoke.log')
                    time.sleep(.2)
            data = get(f'http://127.0.0.1:{args.port_base}/v1/workflows/sessions')
            workflows = get(f'http://127.0.0.1:{args.port_base+30}/v1/tracking/workflows')
            assert data.get('items') == [], data
            assert workflows.get('items') == [], workflows
            result = {'healthyServices': len(health), 'isolatedWorkflowRead': True,
                      'nativeProcesses': True, 'modelsStarted': False, 'contentRequests': 0}
        finally:
            child.terminate()
            code = child.wait(timeout=70)
            if sys.exc_info()[0] is None:
                assert code == 0, 'unclean stack shutdown'
    for offset in OFFSETS:
        with socket.socket() as sock:
            assert sock.connect_ex(('127.0.0.1', args.port_base + offset)) != 0, 'test listener remained'
    result['allTestListenersStopped'] = True
    (storage / 'smoke-result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
