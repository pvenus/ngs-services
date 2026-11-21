"""Attempt-local publication lock, not a global image queue."""
import tempfile,threading,time,unittest
from pathlib import Path
from project_data_service.portrait_pair_processor import PortraitPairProcessor
class Tests(unittest.TestCase):
 def run_pair(self,same):
  with tempfile.TemporaryDirectory()as tmp:
   p=PortraitPairProcessor.__new__(PortraitPairProcessor);p.root=Path(tmp).resolve();active=0;maximum=0;guard=threading.Lock();start=threading.Barrier(3);results=[]
   def process(c,*args):
    nonlocal active,maximum
    with guard:active+=1;maximum=max(maximum,active)
    time.sleep(.06)
    with guard:active-=1
    return c['attempt_id']
   p._process=process;p.readback=lambda c,h:c['attempt_id'];p._retire_inputs=lambda *a:None
   def run(i):
    start.wait();results.append(p({'request_id':'r','attempt_id':'a'if same else str(i)},{},{},{}))
   threads=[threading.Thread(target=run,args=(i,))for i in range(2)]
   for t in threads:t.start()
   start.wait()
   for t in threads:t.join(3);self.assertFalse(t.is_alive())
   self.assertEqual(len(results),2);return maximum
 def test_same_attempt_serialized(self):self.assertEqual(self.run_pair(True),1)
 def test_independent_attempts_parallel(self):self.assertEqual(self.run_pair(False),2)

class RetirementTests(unittest.TestCase):
 def test_only_verified_attempt_inputs_move_receipts_and_other_files_stay(self):
  import json
  with tempfile.TemporaryDirectory()as tmp:
   p=PortraitPairProcessor.__new__(PortraitPairProcessor);p.root=Path(tmp).resolve()
   folder=p.root/'key';(folder/'artifacts').mkdir(parents=True);other=p.root/'other';other.mkdir();(other/'x').write_bytes(b'other')
   claim={'request_id':'r','attempt_id':'a'};packet={'manifest':{'id':'manifest'}};files={'output.png':b'verified-png'}
   (folder/'claim.json').write_text(json.dumps(claim,sort_keys=True));(folder/'artifacts/pair-manifest.json').write_text(json.dumps(packet['manifest'],sort_keys=True));(folder/'artifacts/output.png').write_bytes(files['output.png'])
   (folder/'receipt.json').write_text('receipt');(folder/'artifacts/pair-publication.json').write_text('publication');(folder/'unknown').write_text('preserve')
   p._retire_inputs('key',claim,packet,files)
   self.assertFalse((folder/'artifacts/output.png').exists());self.assertEqual((p.root/'.retired/key/artifacts/output.png').read_bytes(),files['output.png'])
   self.assertEqual((folder/'receipt.json').read_text(),'receipt');self.assertEqual((folder/'artifacts/pair-publication.json').read_text(),'publication');self.assertEqual((folder/'unknown').read_text(),'preserve');self.assertEqual((other/'x').read_bytes(),b'other')
 def test_retired_symlink_is_rejected_without_following(self):
  with tempfile.TemporaryDirectory()as tmp:
   p=PortraitPairProcessor.__new__(PortraitPairProcessor);p.root=Path(tmp).resolve();outside=p.root/'outside';outside.mkdir();(p.root/'.retired').symlink_to(outside,target_is_directory=True)
   with self.assertRaisesRegex(ValueError,'RETIRE_PATH'):p._retire_inputs('key',{}, {},{})
