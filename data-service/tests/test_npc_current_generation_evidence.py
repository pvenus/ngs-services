import unittest, tempfile, sqlite3, hashlib, json
from pathlib import Path
from project_data_service.filex_read import digest
from project_data_service.npc_current_generation_evidence import EvidenceReader
class Tests(unittest.TestCase):
 def test_live_scans_preserve_unknown_and_block_new_unknown(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d).resolve();fresh=p/'fresh';fresh.mkdir();old=p/'old';old.mkdir();(old/'unknown').write_bytes(b'legacy')
   dbpath=p/'ledger';db=sqlite3.connect(dbpath);db.executescript('CREATE TABLE owned(npc,generation,request,root,path,sha,kind);CREATE TABLE leases(npc,generation,request);');db.commit();db.close()
   registry={'hosts':['h'],'producers':['p']}
   host={'npcId':'n','generationId':'g','requestId':'r','legacyResumeBlocked':True,'verified':True,'producerRegistryHash':digest(registry),'coveredHosts':['h'],'coveredProducers':['p'],'unresolvedCurrentPaths':[],'currentOwnedFiles':[]}
   c={'ledger':str(dbpath),'registry':registry,'freshRoots':{'f':str(fresh)},'preservedLegacyRoots':{'o':str(old)}}
   reader=EvidenceReader(c,lambda *args:host)
   result=reader('n','g','r');self.assertFalse(result['globalCoverageComplete']);self.assertEqual((old/'unknown').read_bytes(),b'legacy')
   (fresh/'new').write_bytes(b'new')
   with self.assertRaisesRegex(ValueError,'NOT_ACCOUNTED'):reader('n','g','r')
   (fresh/'new').write_bytes(b'')
   host['preservedInfrastructureFiles']=[{'rootKey':'f','path':'new','sha256':hashlib.sha256(b'').hexdigest(),'kind':'request-lock','disposition':'retain-infrastructure','requestId':'actual-db-id'}]
   self.assertEqual(reader('n','g','r')['preservedInfrastructureCount'],1)
   (fresh/'new').write_bytes(b'changed')
   with self.assertRaisesRegex(ValueError,'NOT_ACCOUNTED'):reader('n','g','r')
   host['legacyResumeBlocked']=False
   with self.assertRaisesRegex(ValueError,'FENCE_REQUIRED'):reader('n','g','r')
 def test_factory_requires_dynamic_host_before_touching_ledger(self):
  from project_data_service.npc_external_provider import configured
  with tempfile.TemporaryDirectory() as d:
   p=Path(d).resolve();ledger=p/'ledger';ledger.write_bytes(b'untouched')
   config=p/'config';config.write_text(json.dumps({'schemaVersion':'npc-external-delete-provider.v1','enabled':True,'scope':'current-generation','ledger':str(ledger),'roots':{'f':str(p/'fresh')},'registry':{'hosts':['h'],'producers':['p']}}));config.chmod(0o600)
   with self.assertRaisesRegex(ValueError,'LIVE_HOST_READER_REQUIRED'):configured(config,lambda:None)
   self.assertEqual(ledger.read_bytes(),b'untouched')
if __name__=='__main__':unittest.main()
