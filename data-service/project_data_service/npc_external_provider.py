"""Explicit server-private provider wiring; never consume execution-only coverage.
This does not create global evidence or reinterpret preserved legacy as covered.
"""
import json
from pathlib import Path
from .filex_read import private_read,require,digest
from .npc_external_inventory import TrustedExternalInventory,AuthenticatedInventoryPDS
from .npc_external_coordinator import Coordinator

def ledger_snapshot(db,npc,generation):
 rows=[dict(r)for r in db.execute('SELECT * FROM owned WHERE npc=? AND generation=? ORDER BY root,path',(npc,generation))]
 leases=[dict(r)for r in db.execute('SELECT * FROM leases WHERE npc=? AND generation=? ORDER BY request',(npc,generation))]
 return digest({'npcId':npc,'generationId':generation,'owned':rows,'leases':leases})

class ConfiguredInventory(TrustedExternalInventory):
 def __init__(self,coordinator,config):self.coordinator=coordinator;self.config=config
 def verify(self,db,npc,generation):
  p=json.loads(private_read(Path(self.config['globalCoverageFile'])))
  require(p.get('schemaVersion')=='npc-global-delete-coverage.v1' and p.get('complete')is True and p.get('scope')=='all-owned-data' and p.get('npcId')==npc and p.get('generationId')==generation and p.get('unresolvedPaths')==[],'NPC_EXTERNAL_INVENTORY_INCOMPLETE')
  registry=self.config['registry']
  require(p.get('producerRegistryHash')==digest(registry)and set(p.get('coveredHosts',[]))==set(registry['hosts'])and set(p.get('coveredProducers',[]))==set(registry['producers']),'NPC_GLOBAL_COVERAGE_REGISTRY')
  require(p.get('ledgerSnapshotHash')==ledger_snapshot(db,npc,generation),'NPC_GLOBAL_COVERAGE_LEDGER_CHANGED')
  require(p.get('legacyDisposition')in ('fully-owned-accounted','verified-no-owner-data'),'NPC_LEGACY_GLOBAL_UNRESOLVED')
 def prepare(self,npc,generation,request,staged=None):
  return self.coordinator.prepare_delete(npc,generation,request,coverage_guard=lambda db:self.verify(db,npc,generation))

def configured(path,transport_factory,host_reader=None):
 c=json.loads(private_read(Path(path)))
 require(c.get('schemaVersion')=='npc-external-delete-provider.v1'and c.get('enabled')is True,'NPC_DELETE_PROVIDER_DISABLED')
 require(c.get('roots')and c.get('registry',{}).get('hosts')and c['registry'].get('producers'),'NPC_DELETE_PROVIDER_SCOPE_REQUIRED')
 # Existing ledger only. Merely installing the provider must not create a new
 # empty ledger and mistake missing ownership for an empty inventory.
 require(Path(c['ledger']).is_file(),'NPC_DELETE_LEDGER_REQUIRED')
 if c.get('scope')=='current-generation':
  if host_reader is None and c.get('hostEvidenceSocket'):
   from .npc_retirement_host_client import client
   host_reader=client(c['hostEvidenceSocket'])
  require(callable(host_reader),'NPC_CURRENT_GENERATION_LIVE_HOST_READER_REQUIRED')
  require(c.get('freshRoots')and c.get('preservedLegacyRoots'),'NPC_CURRENT_GENERATION_PARTITIONS_REQUIRED')
  require(set(c['freshRoots'])<=set(c['roots']) and all(c['freshRoots'][k]==c['roots'][k]for k in c['freshRoots']),'NPC_CURRENT_GENERATION_ROOT_BINDING')
  coordinator=Coordinator(c['ledger'],c['roots'],c['quarantine'],AuthenticatedInventoryPDS(transport_factory))
  from .npc_current_generation_evidence import EvidenceReader
  from .npc_current_generation_inventory import CurrentGenerationInventory
  return CurrentGenerationInventory(coordinator,EvidenceReader(c,host_reader))
 require(c.get('scope','all-owned-data')=='all-owned-data','NPC_DELETE_PROVIDER_SCOPE_UNKNOWN')
 coordinator=Coordinator(c['ledger'],c['roots'],c['quarantine'],AuthenticatedInventoryPDS(transport_factory))
 return ConfiguredInventory(coordinator,c)
