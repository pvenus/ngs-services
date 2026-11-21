import unittest,tempfile,sqlite3,json
from pathlib import Path
from project_data_service.npc_external_provider import ConfiguredInventory,ledger_snapshot
from project_data_service.filex_read import digest
class Tests(unittest.TestCase):
 def test_global_not_execution_and_snapshot_binding(self):
  with tempfile.TemporaryDirectory()as d:
   path=Path(d).resolve()/'proof.json';db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
   db.executescript('CREATE TABLE owned(npc,generation,request,root,path,sha,kind);CREATE TABLE leases(npc,generation,request);')
   registry={'hosts':['h'],'producers':['p']};provider=ConfiguredInventory(None,{'registry':registry,'globalCoverageFile':str(path)})
   proof={'schemaVersion':'npc-global-delete-coverage.v1','scope':'all-owned-data','complete':False,'executionComplete':True,'npcId':'n','generationId':'g','unresolvedPaths':['legacy'],'producerRegistryHash':digest(registry),'coveredHosts':['h'],'coveredProducers':['p'],'ledgerSnapshotHash':ledger_snapshot(db,'n','g'),'legacyDisposition':'fully-owned-accounted'}
   def save():path.write_text(json.dumps(proof));path.chmod(0o600)
   save()
   with self.assertRaisesRegex(ValueError,'INCOMPLETE'):provider.verify(db,'n','g')
   proof.update(complete=True,unresolvedPaths=[]);save();provider.verify(db,'n','g')
   db.execute("INSERT INTO owned VALUES('n','g','r','root','path','sha','scratch')")
   with self.assertRaisesRegex(ValueError,'LEDGER_CHANGED'):provider.verify(db,'n','g')
if __name__=='__main__':unittest.main()
