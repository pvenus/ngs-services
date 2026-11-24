"""Read-only, scoped style inputs for the Mac prop image worker."""
import hashlib
import json
import re

from .concept_filex import FilexConcept
from .filex_read import digest
from .workflow_concept_category import RPC

PROJECT='binari'
GROUP='group.art-design'
MASTER='expression'
PROP='category.add.26710502f5713d99018b77a29384d5da'
TITLE='프랍 디자인 설계'
SCHEMA='ngs.prop-image-design.v1'
# Existing canonical visual fields; neither portrait anatomy nor scene plans
# are silently drawn from other documents.
FIELDS=(('visualStyle','ngs.character-style-contract.v1'),
        ('visualProhibitions','ngs.character-style-constraints.v1'))
HEADINGS=('형태와 식별','재료와 색','상태 사이의 일관성','납품과 검수')
HEX=r'[a-f0-9]{64}'

def require(value,code='PROP_DESIGN_CONTEXT_INVALID'):
    if not value:raise ValueError(code)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def bounded(text,limit):
    return type(text)is str and bool(text.strip()) and len(text.encode())<=limit and not re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff]',text)

def group_members(snapshot):
    meta=snapshot['documents']['metadata.json']
    ids=meta['sectionIds'];hier=meta['document']['hierarchy']
    groups=hier['groups'];art=[g for g in groups if g['id']==GROUP]
    require(len(art)==1,'PROP_DESIGN_GROUP_MISSING')
    # Exact active art membership, not arbitrary similarly named categories.
    for cid in (MASTER,PROP):
        require(ids.count(cid)==1 and cid not in hier.get('archivedCategoryIds',[]) and
                sum(g['categoryIds'].count(cid) for g in groups)==1 and cid in art[0]['categoryIds'],
                'PROP_DESIGN_CATEGORY_MISSING')

def master_from(snapshot):
    docs=snapshot['documents'];base='sections/'+MASTER+'/'
    description=docs[base+'metadata.json']
    require(description['id']==MASTER,'PROP_MASTER_ID')
    archive=docs['metadata.json']['document']['hierarchy'].get('archivedItems',[])
    fields=[]
    for key,schema in FIELDS:
        path=base+'fields/'+key+'.json'
        require(description['fieldIds'].count(key)==1 and path in docs and
                {'categoryId':MASTER,'itemId':key} not in archive,'PROP_MASTER_FIELD_MISSING')
        row=docs[path];text=row['value']
        require(row['id']==key and bounded(text,12000),'PROP_MASTER_FIELD_INVALID')
        try:data=json.loads(text)
        except ValueError:raise ValueError('PROP_MASTER_FIELD_INVALID') from None
        require(type(data)is dict and data.get('schemaVersion')==schema,'PROP_MASTER_FIELD_INVALID')
        fields.append({'id':key,'sha256':sha(text.encode()),'text':text})
    return {'role':'master-design','categoryId':MASTER,'sha256':digest(fields),'fields':fields}

def prop_from(snapshot,row):
    proof=snapshot.get('markdownSections',{}).get(PROP)
    require(type(proof)is dict and set(proof)=={'mode','sha256'} and proof['mode']=='authored' and
            re.fullmatch(HEX,proof['sha256']) is not None,'PROP_DESIGN_MARKDOWN_REQUIRED')
    require(type(row)is dict and set(row)=={'categoryId','title','source','sourceSha256','mode'} and
            (row['categoryId'],row['title'],row['sourceSha256'],row['mode'])==(PROP,TITLE,proof['sha256'],'authored'),
            'PROP_DESIGN_SOURCE_CHANGED')
    text=row['source']
    require(bounded(text,24000) and sha(text.encode())==proof['sha256'],'PROP_DESIGN_SOURCE_CHANGED')
    require(text.splitlines()[0]=='# '+TITLE and tuple(re.findall(r'^## (.+)$',text,re.M))==HEADINGS,
            'PROP_DESIGN_SECTION_INVALID')
    return {'role':'prop-design','categoryId':PROP,'sha256':proof['sha256'],'markdown':text}

class PropDesignContext:
    def __init__(self,source):
        require(isinstance(source,FilexConcept))
        self.source=source

    def read(self,project_id):
        require(project_id==PROJECT,'PROP_DESIGN_PROJECT_DENIED')
        before=self.source.snapshot();group_members(before)
        master=master_from(before)
        with self.source.factory() as transport:
            prop=prop_from(before,transport.call('POST',RPC,{'readCategoryId':PROP}))
        # Tolerate unrelated transactions; the two exact source versions cannot
        # be mixed across concurrent publications or moved out of the group.
        after=self.source.snapshot();group_members(after)
        require(master_from(after)==master and after['markdownSections'].get(PROP)==before['markdownSections'][PROP],
                'PROP_DESIGN_SOURCE_CHANGED')
        sources=[master,prop]
        return {'schemaVersion':SCHEMA,'projectId':PROJECT,'sources':sources,
                'contextSha256':digest([[part['role'],part['categoryId'],part['sha256']] for part in sources])}
