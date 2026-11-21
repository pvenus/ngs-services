import unittest, copy
from project_data_service.npc_retirement_closure import verify
class Tests(unittest.TestCase):
 def fixture(self):
  g={'npcId':'n','generationId':'g','complete':True,'unsupportedReferences':[],'sourceVersion':'v','roots':['npc'],'nodes':{'npc':{'sha256':'1','verifiedBytes':True,'referenceScanComplete':True,'references':['shared','image']},'image':{'sha256':'2','verifiedBytes':True,'referenceScanComplete':True,'references':[]},'shared':{'sha256':'3','verifiedBytes':True,'referenceScanComplete':True,'references':[]}}}
  a={'npcId':'n','generationId':'g','verified':True,'backupId':'b','sourceVersion':'v','entries':{'npc':'1','image':'2','shared':'3'}}
  f={'npcId':'n','generationId':'g','admissionBlocked':True,'activeLeases':0,'legacyResumeBlocked':True}
  return g,a,f
 def test_closure_without_global_ownership(self):
  result=verify(*self.fixture(),'n','g');self.assertTrue(result['restoreClosureVerified']);self.assertFalse(result['globalCoverageComplete']);self.assertFalse(result['deletionAuthorized'])
 def test_missing_shared_or_stale_or_resume_blocks(self):
  for mutation in ('shared','version','resume','schema'):
   g,a,f=self.fixture()
   if mutation=='shared':a['entries'].pop('shared')
   if mutation=='version':a['sourceVersion']='old'
   if mutation=='resume':f['legacyResumeBlocked']=False
   if mutation=='schema':g['nodes']['image']['referenceScanComplete']=False
   with self.assertRaises(ValueError):verify(g,a,f,'n','g')
if __name__=='__main__':unittest.main()
