import base64
import concurrent.futures
import hashlib
import fcntl
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from time import monotonic
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from server import Store, Conflict, PREFIX, OWNERSHIP_WAIT_SECONDS, handler

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=Store(self.tmp.name); self.key=PREFIX+'categories/test/entries/test.json'
    def tearDown(self): self.tmp.cleanup()
    def test_replace_and_stale(self):
        self.store.cas(self.key,None,b'{"old":1}'); self.store.cas(self.key,b'{"old":1}',b'{"new":1}')
        with self.assertRaises(Conflict): self.store.cas(self.key,b'{"old":1}',b'{"bad":1}')
        self.assertEqual(self.store.get(self.key), b'{"new":1}')
    def test_failure_preserves(self):
        self.store.cas(self.key,None,b'{"old":1}')
        with patch('server.os.replace',side_effect=OSError('disk')):
            with self.assertRaises(OSError): self.store.cas(self.key,b'{"old":1}',b'{"new":1}')
        self.assertEqual(self.store.get(self.key),b'{"old":1}')
    def test_concurrent(self):
        self.store.cas(self.key,None,b'{"old":1}')
        def update(n):
            try: self.store.cas(self.key,b'{"old":1}',('{"n":'+str(n)+'}').encode()); return True
            except Conflict: return False
        with concurrent.futures.ThreadPoolExecutor(8) as pool: self.assertEqual(sum(pool.map(update,range(8))),1)
    def test_paths(self):
        for key in ['other/current.json',PREFIX+'../outside',PREFIX+'entries/../../outside',PREFIX+'versions/a/x',PREFIX+'arbitrary.bin']:
            with self.assertRaises(ValueError): self.store.cas(key,None,b'{}')
        (Path(self.tmp.name)/'categories').symlink_to('/tmp')
        with self.assertRaises(ValueError): self.store.cas(self.key,None,b'{}')
    def test_http(self):
        server=ThreadingHTTPServer(('127.0.0.1',0), handler(self.store,'x'*32))
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            client=http.client.HTTPConnection('127.0.0.1',server.server_port)
            client.request('GET','/v1/capabilities'); response=client.getresponse(); self.assertEqual(response.status,401); response.read()
            client.request('GET','/v1/capabilities',headers={'Authorization':'Bearer '+'x'*32}); response=client.getresponse(); self.assertEqual(response.status,200); self.assertIn(b'atomicCAS',response.read()); client.close()
        finally: server.shutdown(); server.server_close(); thread.join()

class ReceiptTests(Tests):
    def test_receipt_recovery(self):
        version=self.store.snapshot()['sourceVersion']
        record=self.store.cas(self.key,None,b'{}','req','ih',version)
        self.assertEqual(record['status'],'committed')
        self.assertEqual(self.store.cas(self.key,None,b'{}','req','ih',version),record)
        with self.assertRaises(Conflict): self.store.cas(self.key,None,b'{"a":1}','req','ih',version)
        self.assertEqual(self.store.snapshot()['documents'],{'categories/test/entries/test.json':{}})

class WriterOwnershipTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(tmp.name)
        self.key = PREFIX+'categories/test/entries/test.json'
        self.version = self.store.snapshot()['sourceVersion']
        server = ThreadingHTTPServer(('127.0.0.1',0), handler(self.store,'x'*32))
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval':0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_port

    def request(self, *, request_id='candidate', expected=None):
        client = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        body = {'key':self.key,'expectedBase64':expected,'base64':base64.b64encode(b'{}').decode(),
                'requestId':request_id,'inputHash':'ih','expectedSourceVersion':self.version}
        try:
            client.request('POST','/v1/cas',json.dumps(body),
                           headers={'Authorization':'Bearer '+'x'*32,'Content-Type':'application/json'})
            response = client.getresponse()
            return response.status,json.loads(response.read())
        finally:
            client.close()

    def test_waits_and_commits_same_request_after_owner_releases(self):
        blocked = threading.Event()
        flock = fcntl.flock
        def observe(fd, flags):
            try: return flock(fd, flags)
            except BlockingIOError:
                blocked.set()
                raise
        with (patch('server.fcntl.flock', side_effect=observe), patch('server._ownership_event') as log,
              concurrent.futures.ThreadPoolExecutor(1) as pool):
            with self.store.lock():
                result = pool.submit(self.request)
                self.assertTrue(blocked.wait(1), 'request never encountered the held Writer lock')
                self.assertFalse(result.done())
                self.assertFalse(self.store.path(self.key).exists())
                self.assertFalse(self.store.receipt_path('candidate').exists())
            self.assertEqual(result.result(timeout=4)[0],200)
            waiting, acquired = [call.args for call in log.call_args_list]
            self.assertEqual((waiting[0],acquired[0]),('waiting','acquired'))
            self.assertEqual(waiting[1:],acquired[1:])
        self.assertEqual(self.store.get(self.key),b'{}')
        self.assertEqual(self.store.receipt('candidate')['status'],'committed')
        # The same durable receipt is not an invitation to re-run a mutation.
        self.assertEqual(self.request()[0],200)
        self.assertEqual(self.request(request_id='stale')[0],409)

    def test_ownership_timeout_leaves_content_and_receipt_untouched(self):
        with self.store.lock(), patch('server._ownership_event') as log:
            started = monotonic()
            self.assertEqual(self.request(),(503,{'error':'FILEX_WRITE_BUSY'}))
            waiting, expired = [call.args for call in log.call_args_list]
            self.assertEqual((waiting[0],expired[0]),('waiting','timeout'))
            self.assertEqual(waiting[1:],expired[1:])
            self.assertGreaterEqual(monotonic()-started,OWNERSHIP_WAIT_SECONDS)
            self.assertFalse(self.store.path(self.key).exists())
            self.assertFalse(self.store.receipt_path('candidate').exists())

if __name__=='__main__': unittest.main()
