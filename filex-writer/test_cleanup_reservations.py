import hashlib,unittest
from test_fenced_store import Tests as Fixture
from fenced_store import FencedOwnedNPCStore
from npc_store import NPC_PREFIX
from server import Conflict
class Reservations(unittest.TestCase):
 setUp=Fixture.setUp
 tearDown=Fixture.tearDown
 publish=Fixture.publish
 def prepare(self):
  self.publish();sha=hashlib.sha256(b'pixels').hexdigest();return sha,self.s.reserve_equivalent('npc',self.gen,'r',sha,'a'*64)
 def test_current_copy_pinned_until_exact_release(self):
  other=FencedOwnedNPCStore(self.root);g2=other.admit('npc',self.gen,'second.parent');sha,proof=self.prepare()
  self.assertTrue(proof['currentSlot']);self.assertEqual(proof,self.s.reserve_equivalent('npc',self.gen,'r',sha,'a'*64))
  for call in [lambda:other.admit('npc',self.gen,'new.parent'),lambda:other.finish(self.g),lambda:other.put_asset(hashlib.sha256(b'new').hexdigest()+'.png',b'new',g2),lambda:other.prepare_delete('npc',self.gen,'delete','f'*64)]:
   with self.assertRaises(Conflict):call()
  key=NPC_PREFIX+'npc/metadata.json';snap=other.snapshot();body={'id':'npc'}
  with self.assertRaises(Conflict):other.batch('replacement','h',[{'path':key,'expectedHash':snap['hashes']['npc/metadata.json'],'body':body}],{key:snap['hashes']['npc/metadata.json']},g2)
  with self.assertRaises(Conflict):other.release_equivalent('npc',self.gen,'r','a'*64,'wrong')
  self.assertEqual(self.s.generation('npc')['cleanupReservations'],1)
  args=('npc',self.gen,'r','a'*64,proof['reservationToken']);self.assertTrue(other.release_equivalent(*args)['released']);self.assertTrue(other.release_equivalent(*args)['released'])
  other.batch('replacement','h',[{'path':key,'expectedHash':snap['hashes']['npc/metadata.json'],'body':body}],{key:snap['hashes']['npc/metadata.json']},g2)
  self.assertEqual(self.s.generation('npc')['cleanupReservations'],0)
 def test_crash_recovery_same_token_no_ttl_or_steal(self):
  sha,proof=self.prepare();restarted=FencedOwnedNPCStore(self.root)
  self.assertEqual(restarted.reserve_equivalent('npc',self.gen,'r',sha,'a'*64),proof)
  with self.assertRaises(Conflict):restarted.reserve_equivalent('npc',self.gen,'r','b'*64,'a'*64)
  with self.assertRaises(Conflict):restarted.admit('npc',self.gen,'after.crash')
 def test_missing_current_copy_does_not_create_reservation(self):
  self.publish();p=self.s.reserve_equivalent('npc',self.gen,'r','b'*64,'a'*64)
  self.assertFalse(p['currentSlot']);self.assertNotIn('reservationToken',p);self.assertEqual(self.s.generation('npc')['cleanupReservations'],0)
 def test_released_reservation_id_cannot_be_recycled(self):
  sha,p=self.prepare();self.s.release_equivalent('npc',self.gen,'r','a'*64,p['reservationToken'])
  with self.assertRaises(Conflict):self.s.reserve_equivalent('npc',self.gen,'r',sha,'a'*64)
del Fixture
if __name__=='__main__':unittest.main()
