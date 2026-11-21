"""Role-scoped and standalone NPC Markdown, projected for old JSON readers."""
from datetime import datetime,timezone
import hashlib
import json
import re

from server import Conflict
from npc_store import encoded,digest
from npc_document import decode
from ownership_plan import plan

ROLES=('overview','portrait.art','portrait.representative','portrait.idle')
FORMAT='markdown.v1'
PLANNING=re.compile(r'([A-Za-z0-9][A-Za-z0-9._-]{0,127})/planning/([A-Za-z0-9][A-Za-z0-9._-]{0,127})\.json')
NPC=re.compile(r'(?:npc|character)\.[A-Za-z0-9][A-Za-z0-9._-]{0,120}')
OFFICIAL=re.compile(r'request\.official\.[a-f0-9]{64}')
SHA=re.compile(r'[a-f0-9]{64}')
CUSTOM_CATEGORY=re.compile(r'category\.add\.[a-f0-9]{32}')

def sha(data):return hashlib.sha256(data).hexdigest()
def markdown_path(path):return path.removesuffix('.json')+'/content.md'
def check_content(v):
    if (type(v)is not str or not 0<len(v)<=16000 or not v.strip() or '\0' in v or
        len(v.encode('utf-8'))>64000):
        raise ValueError('npc planning markdown content')
    return v.encode('utf-8')

def planning_write_bytes(path,row):
    raw=check_content(row['content'])
    pointer={**{k:v for k,v in row.items()if k!='content'},
             'contentFormat':FORMAT,'contentSha256':sha(raw)}
    return {markdown_path(path):raw,path:encoded(pointer)}

def planning_projection(path,body,read):
    """A projected compatibility row never makes JSON content authoritative."""
    match=PLANNING.fullmatch(path)
    if not match:return body
    marked='contentFormat'in body or 'contentSha256'in body
    if not marked:return body
    if ('content'in body or body.get('contentFormat')!=FORMAT or type(body.get('contentSha256'))is not str or
        not SHA.fullmatch(body['contentSha256']) or (body.get('planningRole') not in ROLES and
            not (body.get('planningRole')=='custom' and CUSTOM_CATEGORY.fullmatch(match[2]) and body.get('id')==match[2]))):
        raise Conflict('npc planning markdown index')
    raw=read(markdown_path(path))
    if type(raw)is not bytes or not 0<len(raw)<=64000 or sha(raw)!=body['contentSha256']:
        raise Conflict('npc planning markdown proof')
    content=raw.decode('utf-8')
    check_content(content)
    return {**{k:v for k,v in body.items() if k not in ('contentFormat','contentSha256')},'content':content}

class NPCPlanningMarkdown:
    def _current_planning_role(self,b):
        keys={'officialRequestId','taskId','projectId','npcId','payloadSha256','role'}
        if (type(b)is not dict or set(b)!=keys or b['projectId']!='binari' or b['role'] not in ROLES or
            not all(type(b.get(k))is str and rx.fullmatch(b[k]) for k,rx in
                    (('officialRequestId',OFFICIAL),('npcId',NPC),('payloadSha256',SHA))) or
            b['taskId']!='workflow.task.'+digest([b['officialRequestId'],'planning_update'])):
            raise ValueError('npc planning update binding')
        self.require_no_migration()
        owner=b['npcId'];state=self.state(owner);self.require_no_cleanup(state)
        if state['state']!='active' or state['npcId']!=owner:raise Conflict('npc planning update owner inactive')
        docs=self.documents_unlocked();root=docs['metadata.json'];meta=docs.get(owner+'/metadata.json',{})
        if owner not in root['characterOrder'] or meta.get('id')!=owner:
            raise Conflict('npc planning update current owner')
        prefix=owner+'/planning/';order=meta.get('planningCategoryOrder',[])
        rows=[(cid,docs[prefix+cid+'.json']) for cid in order]
        matched=[(cid,row) for cid,row in rows if row.get('planningRole')==b['role'] and row.get('lifecycleState')!='deleted']
        if len(matched)!=1:raise Conflict('npc planning update ambiguous role')
        cid,row=matched[0]
        if (row['id']!=cid or type(row.get('name'))is not str or not row['name'].strip() or
            not PLANNING.fullmatch(prefix+cid+'.json')):
            raise Conflict('npc planning update category changed')
        raw=check_content(row.get('content'))
        return docs,prefix+cid+'.json',row,{'categoryId':cid,'role':b['role'],'title':row['name'],
            'source':row['content'],'sourceSha256':sha(raw),'generationId':state['generationId']}

    def workflow_npc_planning_update_read(self,b):
        with self.lock():return self._current_planning_role(b)[3]

    def workflow_npc_planning_update(self,b,a):
        if (type(a)is not dict or set(a)!={'schemaVersion','categoryId','role','generationId','expectedSourceSha256','content'}
            or a['schemaVersion']!='workflow.npc-planning-update-markdown.v1'
            or type(a['expectedSourceSha256'])is not str or not SHA.fullmatch(a['expectedSourceSha256'])):
            raise ValueError('npc planning update artifact')
        raw=check_content(a['content'])
        with self.lock():
            docs,path,row,current=self._current_planning_role(b)
            if any(a[k]!=current[k] for k in ('categoryId','role','generationId')):
                raise Conflict('npc planning update scope changed')
            rid=b['taskId'];request_hash=digest([b,a]);owner=b['npcId'];prefix=owner+'/'
            physical=self.safe(path)
            md=markdown_path(path)
            index=json.loads(physical.read_bytes())
            prior=self.receipt(rid) if self.receipt_path(rid).is_file() else None
            if prior:
                if (prior.get('status')!='committed' or prior.get('inputHash')!=request_hash or
                    current['sourceSha256']!=sha(raw) or index.get('contentSha256')!=sha(raw)):
                    raise Conflict('npc planning update replay changed')
                return {'ok':True}
            if current['sourceSha256']!=a['expectedSourceSha256']:
                raise Conflict('npc planning update stale source')
            if 'contentFormat' not in index and (self.safe(md).exists() or self.safe(md).is_symlink()):
                raise Conflict('npc planning update unbound markdown')
            root=docs['metadata.json'];rev=root.get('_pdsCurrent',{}).get('revision')
            if type(rev)is not int or rev<0:raise Conflict('npc planning update revision')
            updated={**row,'content':a['content']}
            after={**docs,path:updated}
            derived=lambda source:plan(owner,{k.removeprefix(prefix):v for k,v in source.items() if k.startswith(prefix)})
            old=self.manifests().get(owner);new=derived(after)
            if old!=derived(docs) or old['slots']!=new['slots'] or old['nonSlotReferences']!=new['nonSlotReferences']:
                raise Conflict('npc planning update asset ownership change')
            document=decode(after);document.pop('_pdsCurrent',None)
            meta={**root,'_pdsCurrent':{'revision':rev+1,'savedAt':datetime.now(timezone.utc).isoformat(),
                'note':'Workflow NPC planning Markdown','operationHash':request_hash,'documentHash':digest(document)}}
            self.check_document_hash({**after,'metadata.json':meta})
            receipt={'requestId':rid,'inputHash':request_hash,'status':'committed',
                     'categoryId':current['categoryId'],'generationId':current['generationId'],'sha256':sha(raw)}
            self.commit_owned(rid,receipt,{**planning_write_bytes(path,updated),
                'metadata.json':encoded(meta),prefix+'.owned-assets.json':encoded(new)})
            return {'ok':True}
    def _current_planning_add(self,b):
        keys={'officialRequestId','taskId','projectId','npcId','payloadSha256','categoryId','title'}
        if (type(b)is not dict or set(b)!=keys or b['projectId']!='binari' or
            not all(type(b.get(k))is str and rx.fullmatch(b[k]) for k,rx in
                    (('officialRequestId',OFFICIAL),('npcId',NPC),('payloadSha256',SHA),('categoryId',CUSTOM_CATEGORY))) or
            b['taskId']!='workflow.task.'+digest([b['officialRequestId'],'planning_add']) or
            type(b['title'])is not str or not 1<=len(b['title'])<=120 or
            b['title']!=b['title'].strip().strip('\ufeff').strip() or
            re.search(r'[\x00-\x1f\x7f\u2028\u2029\ud800-\udfff]',b['title'])):
            raise ValueError('npc planning add binding')
        self.require_no_migration()
        owner=b['npcId'];state=self.state(owner);self.require_no_cleanup(state)
        if state['state']!='active' or state['npcId']!=owner:raise Conflict('npc planning add owner inactive')
        docs=self.documents_unlocked();meta=docs.get(owner+'/metadata.json',{})
        if owner not in docs['metadata.json']['characterOrder'] or meta.get('id')!=owner:
            raise Conflict('npc planning add current owner')
        return docs,meta,{'categoryId':b['categoryId'],'title':b['title'],'generationId':state['generationId']}

    def _require_new_planning(self,docs,meta,b,path):
        cid=b['categoryId'];owner=b['npcId'];prefix=owner+'/planning/'
        order=meta.get('planningCategoryOrder',[])
        if (type(order)is not list or cid in order or any(k.endswith('/planning/'+cid+'.json') for k in docs) or
            any(q.exists() or q.is_symlink() for q in (self.safe(path),self.safe(markdown_path(path)).parent))):
            raise Conflict('npc planning add category exists')
        title=b['title'].casefold()
        if any(docs[prefix+cid+'.json']['name'].strip().casefold()==title for cid in order):
            raise Conflict('npc planning add title exists')

    def workflow_npc_planning_add_read(self,b):
        with self.lock():
            docs,meta,current=self._current_planning_add(b)
            self._require_new_planning(docs,meta,b,b['npcId']+'/planning/'+b['categoryId']+'.json')
            return current

    def workflow_npc_planning_add(self,b,a):
        if (type(a)is not dict or set(a)!={'schemaVersion','categoryId','title','generationId','content'}
            or a['schemaVersion']!='workflow.npc-planning-add-markdown.v1'):
            raise ValueError('npc planning add artifact')
        raw=check_content(a['content'])
        with self.lock():
            docs,meta,current=self._current_planning_add(b)
            if any(a[k]!=current[k] for k in ('categoryId','title','generationId')):
                raise Conflict('npc planning add scope changed')
            rid=b['taskId'];request_hash=digest([b,a]);owner=b['npcId'];prefix=owner+'/';cid=b['categoryId']
            path=prefix+'planning/'+cid+'.json';md=markdown_path(path)
            row={'id':cid,'uuid':cid.removeprefix('category.'),'name':b['title'],'planningRole':'custom','content':a['content']}
            receipt={'requestId':rid,'inputHash':request_hash,'status':'committed',
                     'categoryId':cid,'generationId':current['generationId'],'sha256':sha(raw)}
            if self.receipt_path(rid).is_file():
                if (self.receipt(rid)!=receipt or docs.get(path)!=row or
                    meta.get('planningCategoryOrder',[]).count(cid)!=1 or self.safe(md).read_bytes()!=raw):
                    raise Conflict('npc planning add replay changed')
                return {'ok':True}
            self._require_new_planning(docs,meta,b,path)
            root=docs['metadata.json'];rev=root.get('_pdsCurrent',{}).get('revision')
            if type(rev)is not int or rev<0:raise Conflict('npc planning add revision')
            own_path=prefix+'metadata.json';owner_after={**meta,'planningCategoryOrder':[*meta.get('planningCategoryOrder',[]),cid]}
            after={**docs,path:row,own_path:owner_after}
            derived=lambda source:plan(owner,{k.removeprefix(prefix):v for k,v in source.items() if k.startswith(prefix)})
            old=self.manifests().get(owner);new=derived(after)
            if old!=derived(docs) or old['slots']!=new['slots'] or old['nonSlotReferences']!=new['nonSlotReferences']:
                raise Conflict('npc planning add asset ownership change')
            document=decode(after);document.pop('_pdsCurrent',None)
            root_after={**root,'_pdsCurrent':{'revision':rev+1,'savedAt':datetime.now(timezone.utc).isoformat(),
                'note':'Workflow NPC standalone planning Markdown','operationHash':request_hash,'documentHash':digest(document)}}
            self.check_document_hash({**after,'metadata.json':root_after})
            self.commit_owned(rid,receipt,{**planning_write_bytes(path,row),
                own_path:encoded(owner_after),'metadata.json':encoded(root_after),prefix+'.owned-assets.json':encoded(new)})
            return {'ok':True}
