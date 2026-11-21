import sqlite3, unittest, copy
from project_data_service.npc_current_generation_inventory import CurrentGenerationInventory
from project_data_service.npc_external_provider import ledger_snapshot
class Coordinator:
 def __init__(self):
  self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
  self.db.executescript('CREATE TABLE npc_generation(npc,generation,state,delete_request,owner,receipt);CREATE TABLE owned(npc,generation,request,root,path,sha,kind);CREATE TABLE leases(npc,generation,request);')
 def prepare_delete(self,n,g,r,coverage_guard):return coverage_guard(self.db)
class Tests(unittest.TestCase):
 def test_scope_remains_honest_and_missing_proofs_block(self):
  c=Coordinator();e={'npcId':'n','generationId':'g','requestId':'r','scope':'current-generation','currentGenerationCoverageComplete':True,'unresolvedCurrentPaths':[],'allRegisteredHostsVerified':True,'ledgerSnapshotHash':ledger_snapshot(c.db,'n','g'),'legacyPreserved':True,'legacySnapshotHash':'legacy','globalCoverageComplete':False,'legacyResumeBlocked':True,'restoreClosure':{'restoreClosureVerified':True,'npcId':'n','generationId':'g','backupId':'b','closureHash':'h'}}
  p=CurrentGenerationInventory(c,lambda *args:e)
  with self.assertRaisesRegex(ValueError,'STAGED_RETIREMENT_REQUIRED'):p.prepare('n','g','r')
 def test_staged_archive_builds_closure_and_rejects_missing_bytes(self):
  c=Coordinator();e={'npcId':'n','generationId':'g','requestId':'r','scope':'current-generation','currentGenerationCoverageComplete':True,'unresolvedCurrentPaths':[],'allRegisteredHostsVerified':True,'ledgerSnapshotHash':ledger_snapshot(c.db,'n','g'),'legacyPreserved':True,'legacySnapshotHash':'legacy','globalCoverageComplete':False,'legacyResumeBlocked':True}
  p=CurrentGenerationInventory(c,lambda *args:e)
  staged={'npcId':'n','generationId':'g','requestId':'r','graph':{'npcId':'n','generationId':'g','complete':True,'unsupportedReferences':[],'sourceVersion':'v','roots':['npc'],'nodes':{'npc':{'sha256':'h','verifiedBytes':True,'referenceScanComplete':True,'references':[]}}},'archive':{'npcId':'n','generationId':'g','verified':True,'backupId':'b','sourceVersion':'v','entries':{'npc':'h'}},'fence':{'npcId':'n','generationId':'g','admissionBlocked':True,'activeLeases':0}}
  result=p.prepare('n','g','r',staged=staged)
  self.assertTrue(result['restoreClosure']['restoreClosureVerified']);self.assertNotIn('restoreClosure',e);self.assertEqual(c.db.execute('SELECT generation FROM npc_generation').fetchone()[0],'g')
  staged['archive']['entries'].clear()
  with self.assertRaisesRegex(ValueError,'MISSING_BYTES'):p.prepare('n','g','r',staged=staged)
  staged['requestId']='other'
  with self.assertRaisesRegex(ValueError,'BINDING'):p.prepare('n','g','r',staged=staged)
if __name__=='__main__':unittest.main()
