"""Best-effort local OPS heartbeat. No service state or authorization changes."""
import http.client
import json
import threading
import time

_HOST = '127.0.0.1'
_PORT = 8090
_ROUTE = '/api/service-heartbeat'
_INTERVAL = 60
_IDS = ('pds',)


def _send(server_name):
    conn = http.client.HTTPConnection(_HOST, _PORT, timeout=2)
    try:
        body = json.dumps({'serverName': server_name}, separators=(',', ':')).encode()
        conn.request('POST', _ROUTE, body, {'Content-Type': 'application/json'})
        with conn.getresponse() as response:
            if response.status != 200:
                return False
            value = json.loads(response.read(257))
            return isinstance(value, dict) and value.get('ok') is True
    except (OSError, http.client.HTTPException, ValueError):
        return False
    finally:
        conn.close()


def _run(server_name, stopped):
    while not stopped.is_set():
        started = time.monotonic()
        _send(server_name)
        if stopped.wait(max(0, _INTERVAL - (time.monotonic() - started))):
            break


def start(server_name):
    """Start independent background delivery; return a nonblocking stop callback."""
    if server_name not in _IDS:
        raise ValueError('PDS_HEARTBEAT_SERVER_NAME')
    stopped = threading.Event()
    try:
        threading.Thread(target=_run, args=(server_name, stopped),
                         name='ops-heartbeat-' + server_name, daemon=True).start()
    except (RuntimeError, OSError):
        pass  # Diagnostics must not become a service readiness dependency.
    return stopped.set
