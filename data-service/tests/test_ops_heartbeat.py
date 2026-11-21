import json
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from project_data_service import ops_heartbeat as hb


@contextmanager
def receiver(reply):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path, self.headers['Host'], body))
            status, result=reply
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01})
    thread.start()
    try:
        with patch.object(hb,'_PORT',server.server_port):yield calls,server.server_port
    finally:server.shutdown();thread.join();server.server_close()


class HeartbeatTests(unittest.TestCase):
    def test_real_http_uses_only_consolidated_identity(self):
        with receiver((200,{'ok':True})) as (calls,port):
            self.assertTrue(hb._send('pds'));self.assertTrue(hb._send('pds'))
        self.assertEqual(calls,[('/api/service-heartbeat',f'127.0.0.1:{port}',{'serverName':k})
                               for k in ('pds','pds')])

    def test_response_and_connect_failure_nonfatal(self):
        for response in ((503,{'ok':True}),(200,{'ok':False}),(200,{'ok':1})):
            with receiver(response):self.assertFalse(hb._send('pds'))
        with patch.object(hb.http.client,'HTTPConnection') as c:
            c.return_value.request.side_effect=TimeoutError
            self.assertFalse(hb._send('pds'))
            c.return_value.close.assert_called_once()

    def test_immediate_then_interval_and_retry_failure(self):
        class Stop:
            count=0
            def is_set(self):return False
            def wait(self,seconds):
                self.count += 1
                self_seconds.append(seconds)
                return self.count==2
        self_seconds=[]
        with patch.object(hb,'_send',side_effect=[False,True]) as send,patch.object(hb.time,'monotonic',side_effect=[0.,2.,60.,61.]):
            hb._run('pds',Stop())
        self.assertEqual(self_seconds,[58.,59.]);self.assertEqual(send.call_count,2)

    def test_start_isolation_and_stop(self):
        attempted=threading.Event()
        with patch.object(hb,'_send',side_effect=lambda _:attempted.set()):
            stop=hb.start('pds')
            self.assertTrue(attempted.wait(1))
            stop()
        with patch.object(hb.threading.Thread,'start',side_effect=RuntimeError):
            hb.start('pds')()
        with self.assertRaises(ValueError):hb.start('unrelated')
