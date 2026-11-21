import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from npc_store import NPCStore,NPC_PREFIX,digest
from server import Conflict

class NPC(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=NPCStore(self.tmp.name)
        self.files=[{'path':NPC_PREFIX+p,'body':v,'expectedHash':None} for p,v in [('metadata.json',{'characterOrder':['seojin']}),('seojin/metadata.json',{'id':'seojin'}),('seojin/planning/art.json',{'id':'art'})]]
        self.rs={x['path']:None for x in self.files}
    def tearDown(self):self.tmp.cleanup()
    def test_register_replay(self):
        r=self.store.batch('a','i',self.files,self.rs)
        self.assertEqual(r['status'],'committed');self.assertEqual(len(self.store.snapshot()['documents']),3)
        self.assertEqual(self.store.batch('a','i',self.files,self.rs),r)
        self.assertFalse((Path(self.tmp.name)/'.stage').exists())
    def test_stale_unchanged_dependency(self):
        self.store.batch('a','i',self.files,self.rs)
        with self.assertRaises(Conflict):self.store.batch('b','j',self.files,self.rs)
    def test_crash_rollforward_before_read(self):
        original=self.store.write
        count=0
        def fail(path,body):
            nonlocal count
            if path.name=='metadata.json':
                count+=1
                if count==2:raise OSError('simulated crash')
            return original(path,body)
        with patch.object(self.store,'write',side_effect=fail):
            with self.assertRaises(OSError):self.store.batch('a','i',self.files,self.rs)
        # new process object observes durable pending journal and completes before read
        recovered=NPCStore(self.tmp.name)
        self.assertEqual(len(recovered.snapshot()['documents']),3)
        self.assertEqual(recovered.receipt('a')['status'],'committed')
    def test_outside_scope(self):
        with self.assertRaises(ValueError): self.store.path(NPC_PREFIX+'../foo.json')
if __name__=='__main__':unittest.main()
