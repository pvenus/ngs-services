import sqlite3,unittest,copy
from project_data_service.legacy_knowledge_retirement import IDS,STAGE,WORKER,inspect,retire
from project_data_service.knowledge_requests import digest
class Tests(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row;self.addCleanup(self.db.close)
  self.db.execute('CREATE TABLE entries(id TEXT PRIMARY KEY,status TEXT,version INT,updated_at TEXT,content TEXT,payload_json TEXT)')
  self.db.executemany('INSERT INTO entries VALUES(?,?,?,?,?,?)',[(i,'active',1,'prior','rich body '+i,'{"facts":[1,2]}')for i in IDS|{'unrelated'}]);self.db.commit()
  self.r={'requestId':'test-retire-1','operation':STAGE,'entries':inspect(self.db)}
 def select(self):return {'requestHash':digest(self.r),'selectionQuality':{'passed':True,'score':1,'threshold':.8},'stages':[{'id':STAGE,'workerId':WORKER}]}
 def test_exact_scope_preservation_and_replay(self):
  result=retire(self.db,self.r,self.select());self.assertEqual(result,retire(self.db,self.r,self.select()));self.assertEqual(self.db.execute('SELECT status FROM entries WHERE id="unrelated"').fetchone()[0],'active')
  self.assertTrue(all(x['status']=='deleted'and x['version']==2 for x in inspect(self.db).values()))
  self.assertTrue(all(result['before'][i]['bodyHash']==result['after'][i]['bodyHash']for i in IDS))
 def test_unregistered_selection_rejected(self):
  with self.assertRaisesRegex(ValueError,'LAYA_REQUIRED'):retire(self.db,self.r,{})
  self.assertEqual(inspect(self.db),self.r['entries'])
 def test_wrong_scope_rejected(self):
  self.r['entries']['unrelated']={}
  with self.assertRaisesRegex(ValueError,'SCOPE'):retire(self.db,self.r,self.select())
 def test_stale_rollback(self):
  self.db.execute('UPDATE entries SET version=2 WHERE id=?',(sorted(IDS)[0],));self.db.commit()
  with self.assertRaisesRegex(ValueError,'VERSION_CONFLICT'):retire(self.db,self.r,self.select())
  self.assertTrue(all(x['status']=='active'for x in inspect(self.db).values()))
