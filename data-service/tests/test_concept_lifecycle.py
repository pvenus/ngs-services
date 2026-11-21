import copy,json,unittest
from unittest.mock import patch
from pathlib import Path
from test_concept_publications import Test as Base
from test_concept_filex import Transport
from project_data_service.concept_lifecycle import execute
from project_data_service.concept_publications import publish
from project_data_service.request_primitives import digest
class LifecycleTransport(Transport):
 def capabilities(self):return {**super().capabilities(),'conceptRecoverableLifecycleV1':True}
 def call(self,method,path,body=None):
  if path.startswith('/v1/concept/removed-scope?'):return self.store.removed_scope(path.split('=',1)[1])
  if path in ('/v1/concept/remove-scope','/v1/concept/restore-scope'):
   return self.store.lifecycle_commit(body['requestId'],body['inputHash'],body['scope'],body['expectedSourceVersion'],body['files'],body['readSet'],restore_info=body['restoreInfo'],backup_id=body['backupId'])
  return super().call(method,path,body)
class Lifecycle(unittest.TestCase):
 setUp=Base.setUp
 summary=Base.summary
 payload=Base.payload
 def setup_item(self):
  self.source.factory=lambda:LifecycleTransport(self.store);d=self.source.current()['document'];f=d['sections'][0]['fields'];f.append({'id':'extra','label':'Extra','value':'EXACT CONTENT','priority':len(f)+1,'sources':[],'status':'confirmed','summary':self.summary()});publish(self.source,self.payload('item.add',d));return self.cid,'extra'
 def setup_category(self):
  self.source.factory=lambda:LifecycleTransport(self.store);d=self.source.current()['document'];section={'id':'extra.category','title':'Extra','description':'Extra','priority':len(d['sections'])+1,'summary':self.summary(),'fields':[{'id':'extra.field','label':'Extra','value':'EXACT CONTENT','priority':1,'sources':[],'status':'confirmed','summary':self.summary()}]};d['sections'].append(section);d['hierarchy']['groups'][0]['categoryIds'].append(section['id']);publish(self.source,self.payload('category.add',d));return section['id'],None
 def command(self,scope,restore=None):
  e=self.source.current();cid,iid=scope;return {'schemaVersion':'ngs.concept-lifecycle.v1','projectId':'binari','requestId':'lifecycle.request.'+str(e['revision']),'action':('category.'if iid is None else'item.')+('restore-removed'if restore else'remove'),'scope':{'categoryId':cid,'itemId':iid},'expectedRevision':e['revision'],'documentHash':digest(e['document']),'backupId':restore}
 def test_item_file_remove_restore_and_replay(self):
  scope=self.setup_item();before=self.source.current()['document'];p=self.command(scope);r=execute(self.source,p);path='sections/'+scope[0]+'/fields/extra.json'
  self.assertNotIn(path,self.source.snapshot()['documents']);self.assertFalse((self.store.root/path).exists());self.assertTrue(r['recoverable']);self.assertEqual(r,execute(self.source,p))
  q=self.command(scope,r['backupId'])
  with self.assertRaisesRegex(ValueError,'RECOVERY_FEATURE_REMOVED'):execute(self.source,q)
  self.assertTrue((self.store.root/'.removed'/r['backupId']/'manifest.json').exists());self.assertFalse((self.store.root/path).exists())

 def test_category_subtree_remove_restore(self):
  scope=self.setup_category();p=self.command(scope);r=execute(self.source,p);self.assertFalse((self.store.root/'sections'/scope[0]).exists());self.assertNotIn(scope[0],self.source.current()['document']['hierarchy']['groups'][0]['categoryIds'])
  with self.assertRaisesRegex(ValueError,'RECOVERY_FEATURE_REMOVED'):execute(self.source,self.command(scope,r['backupId']))
  self.assertFalse((self.store.root/'sections'/scope[0]).exists())
 def test_required_targets_and_last_item_denied(self):
  self.source.factory=lambda:LifecycleTransport(self.store)
  for scope,error in [((self.cid,None),'REQUIRED_CATEGORY'),((self.cid,self.iid),'REQUIRED_ITEM')]:
   with self.assertRaisesRegex(ValueError,error):execute(self.source,self.command(scope))
  scope=self.setup_category()
  with self.assertRaisesRegex(ValueError,'LAST_ITEM'):execute(self.source,self.command((scope[0],'extra.field')))
 def test_stale_source_and_binding(self):
  scope=self.setup_item();p=self.command(scope);q=copy.deepcopy(p);q['requestId']='lifecycle.concurrent';execute(self.source,p)
  with self.assertRaisesRegex(ValueError,'SOURCE_CONFLICT'):execute(self.source,q)
  changed=copy.deepcopy(p);changed['scope']['itemId']='other'
  with self.assertRaisesRegex(ValueError,'RECEIPT_BINDING'):execute(self.source,changed)
 def test_lost_response_and_partial_write_recovery(self):
  scope=self.setup_item();p=self.command(scope);original=self.store.write;hit=False
  def fail(path,data):
   nonlocal hit
   original(path,data)
   if path==self.store.root/'metadata.json'and not hit:hit=True;raise TimeoutError('crash after root index')
  with patch.object(self.store,'write',side_effect=fail):
   with self.assertRaises(TimeoutError):execute(self.source,p)
  r=execute(self.source,p,recover_only=True);self.assertTrue(r['verified']);self.assertFalse((self.store.root/'sections'/scope[0]/'fields/extra.json').exists())
 def test_corrupt_backup_stops_restore(self):
  scope=self.setup_item();r=execute(self.source,self.command(scope));base=self.store.root/'.removed'/r['backupId'];f=base/'files'/'sections'/scope[0]/'fields/extra.json';f.write_text('{}')
  with self.assertRaisesRegex(ValueError,'RECOVERY_FEATURE_REMOVED'):execute(self.source,self.command(scope,r['backupId']))
 def test_undeclared_file_and_symlink_preserved(self):
  scope=self.setup_category();folder=self.store.root/'sections'/scope[0];(folder/'unexpected.txt').write_text('preserve')
  with self.assertRaisesRegex(ValueError,'undeclared files'):execute(self.source,self.command(scope))
  self.assertEqual((folder/'unexpected.txt').read_text(),'preserve')
  (folder/'unexpected.txt').unlink();(folder/'link').symlink_to(self.store.root/'metadata.json')
  with self.assertRaisesRegex(ValueError,'symlink'):execute(self.source,self.command(scope))
 def test_remove_journal_then_unlink_crash_resumes(self):
  scope=self.setup_item();p=self.command(scope);real=Path.unlink;hit=False
  def unlink(path,*a,**kw):
   nonlocal hit
   real(path,*a,**kw)
   if path.name=='extra.json'and not hit:hit=True;raise TimeoutError('crash after unlink')
  with patch.object(Path,'unlink',unlink):
   with self.assertRaises(TimeoutError):execute(self.source,p)
  r=execute(self.source,p,recover_only=True);self.assertTrue(r['verified']);self.assertEqual(self.source.current()['revision'],3)
 def test_restore_collision_does_not_overwrite(self):
  scope=self.setup_item();r=execute(self.source,self.command(scope));self.setup_item()
  with self.assertRaisesRegex(ValueError,'RECOVERY_FEATURE_REMOVED'):execute(self.source,self.command(scope,r['backupId']))

del Base
