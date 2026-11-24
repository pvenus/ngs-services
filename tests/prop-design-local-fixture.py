"""Only this test executable mounts a concept writer to a disposable directory."""
import hashlib
import json
from pathlib import Path
import sys

from project_data_service.concept import ROOT
from project_data_service.concept_filex import FilexConcept, PREFIX, canonical, digest
from project_data_service.concept_migration import migrate
from project_data_service.prop_design_context import PropDesignContext, PROP, TITLE
from project_data_service.workflow_concept_category import CategoryWorkflow, RPC
from project_data_service.workflow_delete_claim import PrivatePlannerClaim

root=Path(sys.argv[1]).resolve()
db=root/'planner.sqlite'
claim=json.loads(sys.stdin.read())
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'filex-live-roles/npc-owned'))
from concept_store import ConceptStore

storage=root/'filex-concept'
storage.mkdir(mode=0o700)
store=ConceptStore(storage)
class Transport:
    def __enter__(self):return self
    def __exit__(self,*_):pass
    def capabilities(self):
        return {'namespace':PREFIX,'lockedBatch':True,'conceptCategoryMarkdownV1':True}
    def snapshot(self):return store.snapshot()
    def receipt(self,key):
        try:return store.receipt(key)
        except FileNotFoundError:return None
    def call(self,method,path,body):
        assert method=='POST'
        if path==RPC and set(body)=={'readCategoryId'}:return store.markdown_current(body['readCategoryId'])
        if path==RPC:return store.markdown_commit(body)
        assert path=='/v1/batch'
        return store.batch(body['requestId'],body['inputHash'],body['files'],body['readSet'])

seed=json.loads((ROOT/'seed.json').read_text())
seed.update(projectId='ProjectBS',schemaVersion=2,hierarchy={'schemaVersion':'npc-concept-groups.v1','archivedCategoryIds':[],
  'groups':[{'id':'group.art-design','title':'아트·디자인','categoryIds':['expression']},
            {'id':'group.fixture','title':'fixture','categoryIds':[s['id'] for s in seed['sections'] if s['id']!='expression']}]})
for i,section in enumerate(seed["sections"],1):section["priority"]=i

expression=next(s for s in seed['sections'] if s['id']=='expression')
content={'visualStyle':{'schemaVersion':'ngs.character-style-contract.v1','styleBlock':'master_fixture_1','faceAndHands':'do_not_forward_npc_anatomy'},
 'visualProhibitions':{'schemaVersion':'ngs.character-style-constraints.v1','renderingExclusions':'master_fixture_2','identityAndStateGuards':'do_not_forward_npc_identity'}}
for field in expression['fields']:
    if field['id'] in content:field['value']=canonical(content[field['id']])
    elif field['id']=='narrativeTone':field['value']='do_not_forward_narrative'
assert migrate({'document':seed,'revision':10,'savedAt':'fixture','note':'isolated'},Transport)['verified']
authority=FilexConcept(transport_factory=Transport)
context=PropDesignContext(authority)
try: context.read('binari')
except ValueError as error:assert str(error)=='PROP_DESIGN_CATEGORY_MISSING'
else:raise AssertionError('missing prop read should fail')
expected=json.loads(canonical(authority.current()['document']))
raw=(Path(__file__).resolve().parents[1]/'docs/prop-design/content.md').read_bytes()
workflow=CategoryWorkflow(authority,PrivatePlannerClaim(str(db),db.stat().st_dev,db.stat().st_ino))
result=workflow.run({'claim':claim,'artifact':{'schemaVersion':'workflow.concept-category-markdown.v1','categoryId':PROP,'title':TITLE,'content':raw.decode()}},False,[])
assert result=={'ok':True}
after=authority.current()['document']
assert after['sections'][:-1]==expected['sections'] and after['sections'][-1]['id']==PROP
assert context.read('binari')['sources'][1]['sha256']==hashlib.sha256(raw).hexdigest()

# Isolated NGS file-replica fixture, copied only from this disposable Filex.
replica=root/'replica/binari/concept'
import shutil
shutil.copytree(storage,replica,ignore=lambda _,names:[s for s in names if s.startswith('.')])
for p in [replica,*replica.rglob('*')]:p.chmod(0o700 if p.is_dir() else 0o600)
(root/'replica').chmod(0o700);(root/'replica/binari').chmod(0o700)
print(canonical(context.read('binari')))
