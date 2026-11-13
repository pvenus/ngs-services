#!/usr/bin/env python3
"""Run the seven local services from this source tree; models are not started."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request

HERE = Path(__file__).resolve().parent
BACKENDS = HERE.parent
OFFSETS = {'data': 0, 'catalog': 1, 'execution': 2, 'laya': 10,
           'files': 11, 'video-alpha': 20, 'workflow': 30}


def commands(storage, port_base):
    ports = {key: port_base + value for key, value in OFFSETS.items()}
    urls = {key: f'http://127.0.0.1:{value}' for key, value in ports.items()}
    specs = [
        ('data', 'data-service', ['--storage-root', str(storage)]),
        ('catalog', 'catalog-service', ['--data-service-url', urls['data']]),
        ('execution', 'execution-service', ['--data-service', urls['data']]),
        ('laya', 'laya-service', ['--catalog-url', urls['catalog'], '--data-service-url', urls['data']]),
        ('files', 'file-service', ['--storage-root', str(storage), '--data-service', urls['data']]),
        ('video-alpha', 'video-alpha-service', ['--data-service-url', urls['data'], '--file-service-url', urls['files']]),
        ('workflow', 'workflow-service', ['--laya', urls['laya'], '--catalog', urls['catalog'],
          '--execution', urls['execution'], '--files', urls['files'], '--video-alpha', urls['video-alpha'], '--data', urls['data']]),
    ]
    return [(name, folder, args + ['--host', '127.0.0.1', '--port', str(ports[name])], urls[name] + '/health')
            for name, folder, args in specs]


def wait_health(url, process, stopping, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stopping():
            raise InterruptedError('service startup canceled')
        if process.poll() is not None:
            raise RuntimeError(f'service exited with {process.returncode}: {url}')
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(.15)
    raise RuntimeError(f'service did not become healthy: {url}')


def check_ports(port_base):
    # Check the complete group before opening any database. Children still own
    # their actual binds; a concurrent bind race fails and stops this group.
    sockets = []
    try:
        for offset in OFFSETS.values():
            sock = socket.socket()
            sockets.append(sock)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('127.0.0.1', port_base + offset))
    finally:
        for sock in sockets:
            sock.close()


def token_for(storage):
    path = storage / '.file-token-secret'
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'w') as handle:
                handle.write(secrets.token_urlsafe(48))
    value = path.read_text(encoding='utf-8').strip()
    if len(value.encode()) < 32:
        raise ValueError('existing file-token secret must contain at least 32 bytes')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage-root', type=Path, required=True,
                        help='explicit absolute service data directory, separate from code')
    parser.add_argument('--port-base', type=int, default=8030,
                        help='loopback ports base+[0,1,2,10,11,20,30] (default: 8030)')
    parser.add_argument('--print-plan', action='store_true', help='print commands without creating data or starting services')
    args = parser.parse_args()
    if not args.storage_root.is_absolute() or not 1024 <= args.port_base <= 65505:
        parser.error('absolute storage root and port base 1024..65505 are required')
    storage = args.storage_root.resolve()
    if storage == BACKENDS or BACKENDS in storage.parents:
        parser.error('storage root must be outside the backend code tree')
    specs = commands(storage, args.port_base)
    for _, folder, _, _ in specs:
        if not (BACKENDS / folder / 'run.py').is_file():
            parser.error('service source is missing: ' + folder)
    if args.print_plan:
        print(json.dumps({'storageRoot': str(storage), 'services': specs}, indent=2))
        return
    check_ports(args.port_base)
    storage.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env['PROJECTBS_FILE_TOKEN_SECRET'] = token_for(storage)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    processes = []
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True
        for _, process in reversed(processes):
            if process.poll() is None:
                process.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for name, folder, arguments, health in specs:
            if stopping:
                break
            root = BACKENDS / folder
            child_env = {**env, 'PYTHONPATH': str(root)}
            process = subprocess.Popen([sys.executable, '-B', str(root / 'run.py'), *arguments], env=child_env, cwd=root)
            processes.append((name, process))
            wait_health(health, process, lambda: stopping)
            print(f'ready {name} {health}', flush=True)
        while not stopping:
            failed = next(((name, process.returncode) for name, process in processes if process.poll() is not None), None)
            if failed:
                raise RuntimeError(f'service stopped: {failed[0]} ({failed[1]})')
            time.sleep(.2)
    except InterruptedError:
        if not stopping:
            raise
    finally:
        stop()
        for _, process in reversed(processes):
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == '__main__':
    main()
