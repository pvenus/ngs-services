import copy,json,unittest
from test_fenced_lifecycle import LifecycleTests
from fenced_store import FencedOwnedNPCStore
from npc_store import encoded
from server import Conflict
class AbandonTests(unittest.TestCase):
 setUp=LifecycleTests.setUp
 tearDown=LifecycleTests.tearDown
 create_packet=LifecycleTests.create_packet
 deleted=LifecycleTests.deleted
 publish=LifecycleTests.publish
 def prepare(self):
  snap,files,read=self.create_packet()
  return self.s.prepare_create('new','create',snap['sourceVersion']),snap,files,read
 def test_lost_response_replay_and_late_create(self):
  g,snap,files,read=self.prepare();before=self.s.snapshot();r=self.s.abandon_creation(g)
  other=FencedOwnedNPCStore(self.root)
  self.assertEqual(r,other.abandon_creation(g));self.assertEqual(before,other.snapshot())
  with self.assertRaises(Conflict):other.create_character('create','h',files,read,snap['sourceVersion'],g)
  with self.assertRaises(Conflict):other.prepare_create('new','create',snap['sourceVersion'])
  fresh=other.prepare_create('new','create2',snap['sourceVersion'])
  self.assertNotEqual(fresh['generationId'],g['generationId'])
  state=other.state('new');self.assertEqual(r,other.abandon_creation(g));self.assertEqual(state,other.state('new'))
  with self.assertRaises(Conflict):other.create_character('create','h',files,read,snap['sourceVersion'],g)
 def test_deleted_lineage_backup_and_receipt_preserved(self):
  deleted=self.deleted();snap=self.s.snapshot();g=self.s.prepare_create('npc','new1',snap['sourceVersion'])
  self.s.abandon_creation(g);g2=self.s.prepare_create('npc','new2',snap['sourceVersion'])
  state=self.s.state('npc');self.assertEqual(state['previousGenerationId'],self.gen)
  self.assertIn(g['generationId'],state['abandonedCreations']);self.assertEqual(self.s.receipt('delete'),deleted)
  self.assertTrue(self.s.backup_preview(deleted['backupId'])['backupVerified'])
  self.s.abandon_creation(g2);self.s.prepare_create('npc','new3',snap['sourceVersion'])
  self.assertEqual(self.s.state('npc')['previousGenerationId'],self.gen)
  self.assertEqual(len(self.s.state('npc')['abandonedCreations']),2)
 def test_abandoned_state_without_history_cannot_advance(self):
  g,snap,_,_=self.prepare();self.s.abandon_creation(g);s=self.s.state('new');s['abandonedCreations']={};self.s.write(self.s.safe(self.s.state_path('new')),encoded(s))
  with self.assertRaises(Conflict):self.s.prepare_create('new','create2',snap['sourceVersion'])
 def test_active_create_rejected(self):
  g,snap,files,read=self.prepare();self.s.create_character('create','h',files,read,snap['sourceVersion'],g)
  with self.assertRaises(Conflict):self.s.abandon_creation(g)
 def test_exact_guard_fields(self):
  g,*_=self.prepare()
  for key in ('npcId','generationId','requestId','lifecycleToken','expectedSourceVersion'):
   bad=dict(g);bad[key]='wrong'
   with self.assertRaises((Conflict,FileNotFoundError)):self.s.abandon_creation(bad)
  with self.assertRaises(ValueError):self.s.abandon_creation(dict(g,extra=True))
  self.s.abandon_creation(g)
  with self.assertRaises(Conflict):self.s.abandon_creation(dict(g,lifecycleToken='wrong'))
 def test_unsettled_authority_rejected(self):
  g,*_=self.prepare();original=self.s.state('new')
  for key,value in [('leases',{'x':{}}),('finished',{'x':{}}),('cleanupReservations',{'x':{}}),('frameMaintenance',{'x':True})]:
   s=copy.deepcopy(original);s[key]=value;self.s.write(self.s.safe(self.s.state_path('new')),encoded(s))
   with self.assertRaises(Conflict):self.s.abandon_creation(g)
  self.s.write(self.s.safe(self.s.state_path('new')),encoded(original))
 def test_orphan_owner_file_rejected(self):
  g,*_=self.prepare();(self.root/'new').mkdir();(self.root/'new/metadata.json').write_text('{}')
  with self.assertRaises(Conflict):self.s.abandon_creation(g)
 def test_canonical_order_or_commit_rejected(self):
  g,*_=self.prepare();path=self.root/'metadata.json';original=path.read_bytes();doc=json.loads(original);doc['characterOrder'].append('new');path.write_bytes(encoded(doc))
  with self.assertRaises(Conflict):self.s.abandon_creation(g)
  path.write_bytes(original);self.s.write(self.s.receipt_path('create'),encoded({'generationId':g['generationId']}))
  with self.assertRaises(Conflict):self.s.abandon_creation(g)
 def test_pending_atomic_create_recovers_active_and_denies_abandon(self):
  g,snap,files,read=self.prepare();recover=self.s.recover_owned;self.s.recover_owned=lambda:None
  self.s.create_character('create','h',files,read,snap['sourceVersion'],g);self.s.recover_owned=recover
  other=FencedOwnedNPCStore(self.root)
  with self.assertRaises(Conflict):other.abandon_creation(g)
  self.assertEqual(other.generation('new')['state'],'active')
  self.assertEqual(other.receipt('create')['generationId'],g['generationId'])
 def test_unrelated_malformed_receipt_not_read_or_changed(self):
  g,*_=self.prepare();path=self.s.receipt_path('unrelated');path.parent.mkdir(exist_ok=True);path.write_bytes(b'preserve unrelated malformed history')
  self.assertTrue(self.s.abandon_creation(g)['abandoned']);self.assertEqual(path.read_bytes(),b'preserve unrelated malformed history')
 def test_new_generation_can_commit_old_guard_stays_revoked(self):
  g,snap,_,_=self.prepare();r=self.s.abandon_creation(g);g2=self.s.prepare_create('new','create2',snap['sourceVersion'])
  snap,files,read=self.create_packet('new','create2');self.s.create_character('create2','h2',files,read,snap['sourceVersion'],g2)
  before=self.s.snapshot();self.assertEqual(r,self.s.abandon_creation(g));self.assertEqual(before,self.s.snapshot())
  self.assertEqual(self.s.generation('new')['state'],'active')
del LifecycleTests
if __name__=='__main__':unittest.main()
