"""Process-local registry of service names and their heartbeat receipt times."""
import re
import time
from datetime import datetime, timezone
from threading import Lock

INTERVAL = 60
SERVER_NAME_PATTERN = r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}'
MCP_TOOL = 'ops_service_health_report'
NGS_SERVERS = ('ngs-local', 'ngs-space')
COMMIT_PATTERN = r'[0-9a-f]{40}'


class Heartbeats:
    def __init__(self, clock=time.monotonic, wall=None):
        self._clock = clock
        self._wall = wall or (lambda: datetime.now(timezone.utc))
        self._receipts = {}
        self._lock = Lock()

    def record(self, server_name, *, commit_sha=None):
        if not isinstance(server_name,str) or not re.fullmatch(SERVER_NAME_PATTERN,server_name):
            raise ValueError('INVALID_SERVER_NAME')
        if commit_sha is not None and (server_name not in NGS_SERVERS or not isinstance(commit_sha, str)
                                      or not re.fullmatch(COMMIT_PATTERN, commit_sha)):
            raise ValueError('INVALID_COMMIT_SHA')
        with self._lock:
            self._receipts[server_name] = (self._clock(), self._wall().isoformat(), commit_sha)

    def snapshot(self):
        with self._lock:
            now = self._clock()
            services = []
            for name, (received, timestamp, sha) in sorted(self._receipts.items()):
                missed = int(max(0, now-received) // INTERVAL)
                state = 'stop' if missed >= 3 else 'warning' if missed >= 2 else 'connected'
                row = {'serverName': name, 'state': state, 'missed': missed, 'lastReceivedAt': timestamp}
                if name in NGS_SERVERS:
                    row['commitSha'] = sha
                services.append(row)
            return {'services': services, 'checkedAt': self._wall().isoformat()}
