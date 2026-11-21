import copy,unittest,hashlib
from project_data_service.filex_knowledge_writer import FilexKnowledgeWriter
from project_data_service.filex_knowledge import PREFIX
from project_data_service.filex_read import canonical,digest
class Memory:
 def __init__(self):
  self.docs={'overview.json':{'revision':1},'categories/workflow/metadata.json':{'id':'workflow'},'categories/workflow/entries/existing.json':{'id':'existing','categoryId':'workflow','version':1,'updatedAt':'before','content':'retain','status':'active'}}
  self.records={};self.writes=0;self.before_cas=None;self.lose_reply=False;self.capable=True
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def capabilities(self):return {'atomicCAS':self.capable}
 def snapshot(self):
  hs={k:digest(v)for k,v in self.docs.items()};return {'sourceVersion':digest(hs),'manifestHash':digest(hs),'documents':copy.deepcopy(self.docs),'hashes':hs}
 def receipt(self,rid):return self.records.get(rid)
 def compare_and_swap(self,key,old,new,*,request_id,input_hash,expected_source_version):
  if self.before_cas:self.before_cas(self)
  path=key[len(PREFIX):];before=self.docs.get(path)
  if self.snapshot()['sourceVersion']!=expected_source_version or (canonical(before)if before else None)!=old:raise ValueError('CAS_CONFLICT')
  import json
  self.docs[path]=json.loads(new);self.writes+=1;self.records[request_id]={'path':path,'inputHash':input_hash,'afterHash':hashlib.sha256(new).hexdigest()}
  if self.lose_reply:raise TimeoutError('lost reply')
class Tests(unittest.TestCase):
 def setUp(self):self.t=Memory();self.w=FilexKnowledgeWriter(lambda:self.t);self.initial=self.w.snapshot()
 def request(self,op='create',eid='new',version=0):return {'projectId':'binari','requestId':'request-'+op,'operation':op,'entryId':eid,'expectedVersion':version,'note':'test change',**({'entry':{'id':eid,'categoryId':'workflow'}}if op=='create'else{})}
 def files(self,r,body=None):
  path='categories/workflow/entries/'+r['entryId']+'.json';s=self.w.snapshot()
  return [{'path':path,'body':body or {'id':r['entryId'],'categoryId':'workflow','version':r['expectedVersion']+1,'updatedAt':'now','content':'new content','status':'active'},'expectedHash':s['hashes'].get(path)}]
 def test_single_file_no_backup_or_metadata_writes(self):
  r=self.request('update','existing',1);result=self.w.apply(r,self.initial['sourceVersion'],{},files=self.files(r));self.assertEqual(len(self.t.docs),3);self.assertEqual(self.t.writes,1)
  self.assertEqual(self.t.docs['overview.json'],self.initial['documents']['overview.json']);self.assertFalse(result['ngsReplicaPulled'])
  self.assertEqual(self.w.apply(r,self.initial['sourceVersion'],{},files=[]),result);self.assertEqual(self.t.writes,1)
 def test_stale_snapshot_no_activation(self):
  r=self.request()
  with self.assertRaisesRegex(ValueError,'SOURCE_CHANGED'):self.w.apply(r,'b'*64,{},files=self.files(r))
  self.assertEqual(self.w.snapshot(),self.initial)
 def test_changed_file_hash_no_write(self):
  r=self.request('update','existing',1);f=self.files(r);f[0]['expectedHash']='f'*64
  with self.assertRaisesRegex(ValueError,'FILE_VERSION_CONFLICT'):self.w.apply(r,self.initial['sourceVersion'],{},files=f)
 def test_no_patch_or_wrong_target(self):
  r=self.request()
  with self.assertRaisesRegex(ValueError,'FULL_FILE_REQUIRED'):self.w.apply(r,self.initial['sourceVersion'],{},files=[])
  f=self.files(r);f[0]['path']='../bad'
  with self.assertRaisesRegex(ValueError,'FILE_VERSION_CONFLICT'):self.w.apply(r,self.initial['sourceVersion'],{},files=f)
 def test_delete_retains_content_no_physical_purge(self):
  r=self.request('delete','existing',1);body={**self.initial['documents']['categories/workflow/entries/existing.json'],'version':2,'status':'deleted','updatedAt':'now'}
  result=self.w.apply(r,self.initial['sourceVersion'],{},files=self.files(r,body));self.assertEqual(result['entry']['content'],'retain');self.assertEqual(len(self.t.docs),3)
 def test_atomic_conflict_preserves_competing_write(self):
  r=self.request();self.t.before_cas=lambda t:t.docs['overview.json'].update(revision=2)
  with self.assertRaisesRegex(ValueError,'CAS_CONFLICT'):self.w.apply(r,self.initial['sourceVersion'],{},files=self.files(r))
  self.assertNotIn('categories/workflow/entries/new.json',self.t.docs)
 def test_lost_response_recovers_without_duplicate(self):
  r=self.request();f=self.files(r);self.t.lose_reply=True
  with self.assertRaises(TimeoutError):self.w.apply(r,self.initial['sourceVersion'],{},files=f)
  self.t.lose_reply=False;result=self.w.apply(r,self.initial['sourceVersion'],{},files=f);self.assertTrue(result['verified']);self.assertEqual(self.t.writes,1)
 def test_superseded_outcome_does_not_replay(self):
  r=self.request();first=self.w.apply(r,self.initial['sourceVersion'],{},files=self.files(r));r2=self.request('update','new',1)
  self.w.apply(r2,first['sourceVersion'],{},files=self.files(r2))
  with self.assertRaisesRegex(ValueError,'SUPERSEDED'):self.w.recover(r['requestId'],first['inputHash'])
 def test_input_collision(self):
  r=self.request();self.w.apply(r,self.initial['sourceVersion'],{},files=self.files(r))
  with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):self.w.recover(r['requestId'],'bad')
 def test_other_category_unchanged(self):
  self.t.docs['categories/other/metadata.json']={'id':'other'}
  self.t.docs['categories/other/entries/keep.json']={'id':'keep','categoryId':'other','version':1,'content':'untouched'}
  s=self.w.snapshot();r=self.request('update','existing',1);before=canonical(self.t.docs['categories/other/entries/keep.json'])
  self.w.apply(r,s['sourceVersion'],{},files=self.files(r))
  self.assertEqual(canonical(self.t.docs['categories/other/entries/keep.json']),before);self.assertEqual(self.t.writes,1)
 def test_category_move_rejected(self):
  self.t.docs['categories/other/metadata.json']={'id':'other'};s=self.w.snapshot();r=self.request('update','existing',1);f=self.files(r);f[0]['body']['categoryId']='other'
  with self.assertRaisesRegex(ValueError,'CATEGORY_CHANGE_DENIED'):self.w.apply(r,s['sourceVersion'],{},files=f)
  self.assertEqual(self.t.writes,0)
 def test_capability_gate(self):
  self.t.capable=False
  with self.assertRaisesRegex(ValueError,'ATOMIC_CAS'):self.w.require_write_capability()
if __name__=='__main__':unittest.main()
