import base64
import concurrent.futures
import hashlib
import http.client
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from server import Store, Conflict, PREFIX, handler

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
if __name__=='__main__': unittest.main()

class ReceiptTests(Tests):
    def test_receipt_recovery(self):
        version=self.store.snapshot()['sourceVersion']
        record=self.store.cas(self.key,None,b'{}','req','ih',version)
        self.assertEqual(record['status'],'committed')
        self.assertEqual(self.store.cas(self.key,None,b'{}','req','ih',version),record)
        with self.assertRaises(Conflict): self.store.cas(self.key,None,b'{"a":1}','req','ih',version)
        self.assertEqual(self.store.snapshot()['documents'],{'categories/test/entries/test.json':{}})
