"""Exact Planner-owned, Filex-published Master Concept category workflows."""
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import hashlib
import json
import re
import sqlite3

from .workflow_save_reservation import optionally_save
from .concept import ROOT,validate
from .concept_filex import FilexConcept,join,split
from .database import utc_now
from .filex_read import canonical,digest
from .rendering_rules import validate_document
from .workflow_delete_claim import ClaimDenied,PrivatePlannerClaim

PATH='/v1/concept/category-workflow'
RPC='/v1/concept/category-markdown'
SCHEMA='ngs.concept-category-workflow.v1'
TYPE='concept.category.workflow'
CONCEPT_ROOT='canonical-files/binari/concept'
CID=r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}'
def task(oid,key):return 'workflow.task.'+digest([oid,key])
def text(v,n,*,empty=False):return type(v)is str and len(v.encode('utf-16-le'))//2<=n and (bool(v.strip()) or empty)
def match(v,p):return type(v)is str and re.fullmatch(p,v) is not None
class Rejected(ValueError):pass
def require(b,why):
    if not b:raise Rejected(why)

def required_categories():
    return {s['id'] for s in json.loads((ROOT/'seed.json').read_text())['sections']}

def required_headings(value):
    return (type(value)is list and 1<=len(value)<=16 and
            all(type(title)is str and 1<=len(title)<=80 and title==title.strip() and
                not re.search(r'[\x00-\x1f\x7f\u2028\u2029\ud800-\udfff]',title) for title in value) and
            len(set(value))==len(value))

def payload(value):
    keys={'schemaVersion','projectId','operation','categoryId','groupId','title','outline','requirements'}
    require(type(value)is dict and set(value) in (keys,keys|{'requiredSections'})
            and value['schemaVersion']==SCHEMA and value['projectId']=='binari','CONCEPT_WORKFLOW_PAYLOAD')
    require('requiredSections' not in value or value['operation']=='add' and required_headings(value['requiredSections']),
            'CONCEPT_WORKFLOW_REQUIRED_HEADINGS')
    op=value['operation'];cid=value['categoryId']
    def workflow_text(v,minimum,maximum):
        return type(v)is str and minimum<=len(v)<=maximum and v==v.strip().strip('﻿').strip() and not re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]',v)
    require(op in ('add','replace','migrate','remove','update') and match(cid,CID)
            and (match(cid,r'category\.add\.[0-9a-f]{32}') and match(value['groupId'],r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}')
                 if op=='add' else value['groupId'] is None)
            and workflow_text(value['title'],1,120) and not re.search(r'[\t\r\n\u2028\u2029]',value['title'])
            and (value['outline']==value['requirements']=='' if op in ('migrate','remove') else
                 value['outline']=='' and workflow_text(value['requirements'],1,4000) if op=='update' else
                 workflow_text(value['outline'],1,4000) and workflow_text(value['requirements'],0,4000)),
            'CONCEPT_WORKFLOW_PAYLOAD')
    return value

def as_markdown(section):
    """Readable unambiguous lossless representation, including machine-readable registered field IDs."""
    def meta(v):
        return (canonical(v).decode().replace('<','\\u003c').replace('>','\\u003e')
                .replace('\u2028','\\u2028').replace('\u2029','\\u2029'))
    def heading(v):return ' '.join(v.splitlines())
    m={k:v for k,v in section.items() if k not in ('fields','priority')}
    m['fieldIds']=[f['id']for f in section['fields']]
    out=['<!-- ngs/category:'+meta(m)+' -->\n','# '+heading(section['title'])+'\n\n']
    if section['description']:out.extend(('\n'.join('> '+x for x in section['description'].splitlines()),'\n\n'))
    for f in section['fields']:
        line=f['value'];fence='`'*max(3,1+max((len(m[0]) for m in re.finditer(r'`+',line)),default=0))
        out.extend(('<!-- ngs/field:'+meta({k:v for k,v in f.items() if k!='value'})+' -->\n',
                    '## '+heading(f['label'])+'\n',fence+'text\n',line,'\n',fence,'\n\n'))
    return ''.join(out)

class CategoryWorkflow:
    def __init__(self,source,verifier):
        require(isinstance(source,FilexConcept) and isinstance(verifier,PrivatePlannerClaim),'CONCEPT_WORKFLOW_AUTHORITY')
        self.source,self.verifier=source,verifier
    def capability(self):
        with self.verifier.snapshot():pass
        with self.source.factory() as t:
            c=t.capabilities()
            require(c.get('conceptCategoryMarkdownV1') is True,'CONCEPT_WORKFLOW_WRITER_REQUIRED')
        return {'enabled':True,'schemaVersion':'workflow.concept-category-write.v1'}
    def auth(self,db,c):
        require(type(c)is dict and set(c)=={'officialRequestId','taskId','projectId','categoryId','payloadSha256','claimStamp'}
                and match(c['officialRequestId'],r'request\.official\.[a-f0-9]{64}') and match(c['taskId'],r'workflow\.task\.[a-f0-9]{64}')
                and match(c['payloadSha256'],r'[a-f0-9]{64}') and match(c['categoryId'],CID) and c['projectId']=='binari'
                and type(c['claimStamp'])is int and 0<c['claimStamp']<2**53,'CONCEPT_WORKFLOW_CLAIM_DENIED')
        oid=c['officialRequestId'];tid=c['taskId'];cid=c['categoryId']
        stages={'category_delete':('concept.category-delete','file'),'category_generate':('concept.category-generate','document')}
        key=next((k for k in stages if task(oid,k)==tid),None)
        require(key is not None,'CONCEPT_WORKFLOW_CLAIM_DENIED')
        lane=stages[key]
        r=db.execute("""SELECT r.*,t.task_input,t.depends_on_task_id,t.updated_at AS stamp
            FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
            WHERE t.task_id=? AND t.official_request_id=? AND t.task_key=? AND t.task_type=? AND t.worker_type=?
            AND t.status='running' AND t.result_ref IS NULL AND r.state='planned'""",(tid,oid,key,*lane)).fetchone()
        require(r and r['project_id']=='binari' and r['schema_version']==SCHEMA and r['request_type']==TYPE
                and r['reason'] is None and r['stamp']==c['claimStamp'] and r['registered_at']<=r['stamp']
                and r['task_input']==canonical({'categoryId':cid}).decode(),'CONCEPT_WORKFLOW_CLAIM_DENIED')
        p=payload(json.loads(r['payload']))
        require(p['categoryId']==cid and r['payload']==canonical(p).decode() and r['payload_sha256']==c['payloadSha256']==digest(p)
                and ((key=='category_delete' and p['operation'] in ('remove','replace')) or
                     (key=='category_generate' and p['operation'] in ('add','migrate','replace','update'))),'CONCEPT_WORKFLOW_CLAIM_DENIED')
        prev=task(oid,'category_delete') if key=='category_generate' and p['operation']=='replace' else None
        require(r['depends_on_task_id']==prev,'CONCEPT_WORKFLOW_CLAIM_DENIED')
        if prev:
            require(db.execute("""SELECT 1 FROM workflow_tasks WHERE task_id=? AND official_request_id=? AND task_key='category_delete'
                AND task_type='concept.category-delete' AND worker_type='file' AND status='completed'
                AND depends_on_task_id IS NULL AND task_input=?""",(prev,oid,r['task_input'])).fetchone(),'CONCEPT_WORKFLOW_CLAIM_DENIED')
        return key,p,prev
    def selected(self,snap,p):
        c=join(snap['documents']);d=c['document']
        section=next((x for x in d['sections'] if x['id']==p['categoryId']),None)
        if section:require(section['title']==p['title'],'CONCEPT_WORKFLOW_TITLE_CHANGED')
        return c,section
    def previous(self,c,p,prev):
        with self.source.factory() as t:
            b=t.call('POST',RPC,{'previousRequestId':prev})
        require((b['categoryId'],b['title'],b['operation'],b['payloadSha256'])==
                (p['categoryId'],p['title'],'replace',c['payloadSha256']),'CONCEPT_WORKFLOW_PREDECESSOR')
        return {k:b[k] for k in ('categoryId','title','source','placement')}
    def source_text(self,snap,section):
        cid=section['id']
        if snap.get('markdownSections',{}).get(cid,{}).get('mode')=='authored':
            return section['fields'][0]['value']
        return as_markdown(section)
    def current_markdown(self,snap,p):
        cid=p['categoryId']
        proof=snap.get('markdownSections',{}).get(cid,{})
        require(bool(proof),'CONCEPT_WORKFLOW_MARKDOWN_REQUIRED')
        with self.source.factory() as t:r=t.call('POST',RPC,{'readCategoryId':cid})
        require(type(r)is dict and set(r)=={'categoryId','title','source','sourceSha256','mode'}
                and (r['categoryId'],r['title'],r['sourceSha256'],r['mode'])==
                    (cid,p['title'],proof['sha256'],proof['mode'])
                and type(r['source'])is str and hashlib.sha256(r['source'].encode()).hexdigest()==r['sourceSha256'],
                'CONCEPT_WORKFLOW_MARKDOWN_SOURCE_CHANGED')
        return r

    @staticmethod
    def updated_section(old,content,mode):
        if mode=='authored':
            return {**old,'fields':[{**old['fields'][0],'value':content}]}
        m=re.match(r'^<!-- ngs/category:({[^\n]*}) -->\n',content)
        require(mode=='structured' and bool(m),'CONCEPT_WORKFLOW_OVERWRITE_STRUCTURE')
        meta=json.loads(m[1]);fields=[]
        for item in re.finditer(r'^<!-- ngs/field:({[^\n]*}) -->\n(#+ [^\n]*\n)?(`{3,})text\n([\s\S]*?)\n\3\n',content[m.end():],re.M):
            fields.append({**json.loads(item[1]),'value':item[4]})
        old_ids=[x['id'] for x in old['fields']]
        immutable=lambda f:{k:v for k,v in f.items() if k not in ('value','summary')}
        require((meta.get('id'),meta.get('title'),meta.pop('fieldIds',None))==(old['id'],old['title'],old_ids)
                and [f.get('id') for f in fields]==old_ids
                and [immutable(f) for f in fields]==[immutable(f) for f in old['fields']],
                'CONCEPT_WORKFLOW_OVERWRITE_FIELD_IDENTITY')
        return {**meta,'priority':old['priority'],'fields':fields}

    def storage_provider(self):
        """Explicit opt-in domain wiring, not automatically advertised by PDS."""
        from .single_target_delete import AuthorizedStorage,ClaimDenied as StorageDenied
        active=ContextVar('master_single_target_claim')
        @contextmanager
        def authorize(claim):
            try:
                with self.verifier.snapshot() as db:
                    key,p,_=self.auth(db,claim)
                    if key!='category_delete' or p['categoryId'] in required_categories():
                        raise StorageDenied()
                    binding={'operationId':claim['taskId'],'target':{'root':CONCEPT_ROOT,
                        'path':'sections/'+p['categoryId'],'kind':'directory'}}
                token=active.set(claim)
                try:yield binding
                finally:active.reset(token)
            except (Rejected,ClaimDenied) as e:
                raise StorageDenied() from e
        def execute(binding):
            # Scope is still pinned by the calling workflow. The storage
            # primitive receives one folder, never a list of its files.
            return self.run({'claim':active.get()},False,[],storage_target=binding)
        def ready():
            with self.verifier.snapshot():pass
            with self.source.factory() as t:
                return t.capabilities().get('conceptSingleTargetDeleteV1') is True
        return AuthorizedStorage(authorize,execute,ready)

    def run(self,envelope,reading,mutation_started,*,storage_target=None):
        require(type(envelope)is dict and set(envelope) in ({'claim'},{'claim','artifact'})
                and (not reading or set(envelope)=={'claim'}),'CONCEPT_WORKFLOW_ENVELOPE')
        c=envelope['claim'];a=envelope.get('artifact')
        # Exact read-only Planner claim and role binding for the entire short PDS publish.
        with self.verifier.snapshot() as db:
            key,p,prev=self.auth(db,c);op=p['operation'];cid=p['categoryId']
        with optionally_save(self.verifier,c,not reading and storage_target is None):
            if reading:
                require(key=='category_generate' and op in ('migrate','replace','update'),'CONCEPT_WORKFLOW_READ_DENIED')
                if op=='update':
                    snap=self.source.snapshot();_,s=self.selected(snap,p)
                    require(s is not None,'CONCEPT_WORKFLOW_TARGET_MISSING')
                    r=self.current_markdown(snap,p)
                    return {k:r[k] for k in ('categoryId','title','source','sourceSha256')}
                if prev:source=self.previous(c,p,prev)['source']
                else:
                    snap=self.source.snapshot();_,s=self.selected(snap,p)
                    require(s is not None,'CONCEPT_WORKFLOW_TARGET_MISSING')
                    source=self.source_text(snap,s)
                return {'categoryId':cid,'title':p['title'],'source':source}
            delete=key=='category_delete'
            builtins=required_categories()
            require(not(delete and cid in builtins),'CONCEPT_REQUIRED_CATEGORY_REMOVAL_DENIED')
            artifact_keys={'schemaVersion','categoryId','title','content'}|({'expectedSourceSha256'} if op=='update' else set())
            require((delete and set(envelope)=={'claim'}) or
                    (not delete and type(a)is dict and set(a)==artifact_keys
                    and (a['schemaVersion'],a['categoryId'],a['title'])==('workflow.concept-category-markdown.v1',cid,p['title'])
                    and text(a['content'],1024*1024 if op in ('migrate','update') else 12000)
                    and (op!='update' or match(a['expectedSourceSha256'],r'[a-f0-9]{64}'))
                    and '\0' not in a['content']),'CONCEPT_WORKFLOW_ARTIFACT')
            action='remove' if delete else op
            h=digest({'officialRequestId':c['officialRequestId'],'taskId':c['taskId'],'payload':p,'artifact':a})
            with self.source.factory() as t:prior=t.receipt(c['taskId'])
            snap=self.source.snapshot();current,s=self.selected(snap,p)
            if prior:
                require(prior['inputHash']==h and prior['operation']==action and prior['categoryId']==cid and prior['status']=='committed'
                        and (s is None if delete else snap.get('markdownSections',{}).get(cid,{}).get('sha256')==
                            hashlib.sha256(a['content'].encode()).hexdigest()),'CONCEPT_WORKFLOW_REPLAY_CHANGED')
                if storage_target is not None:
                    # Writer re-verifies recovery proof/final absence on replay.
                    if 'storageDelete' not in prior:raise ValueError('CONCEPT_WORKFLOW_LEGACY_DELETE_RECEIPT')
                    with self.source.factory() as t:
                        checked=t.call('POST',RPC,dict(requestId=c['taskId'],inputHash=h,operation=action,
                            categoryId=cid,sourceVersion=snap['sourceVersion'],metadata={},content=None,
                            backup=None,storageTarget=storage_target))
                    if checked!=prior:raise ValueError('CONCEPT_WORKFLOW_STORAGE_REPLAY')
                    return checked['storageDelete']
                return {'ok':True}
            require((s is not None)==(action in ('remove','migrate','update')),'CONCEPT_WORKFLOW_TARGET_CHANGED')
            d=copy.deepcopy(current['document']);sections=d['sections'];hier=d.get('hierarchy')
            require(type(hier)is dict,'CONCEPT_WORKFLOW_HIERARCHY_REQUIRED')
            archive=hier.get('archivedCategoryIds',[]);group=lambda gid:next((g for g in hier['groups'] if g['id']==gid),None)
            backup=None
            if delete:
                active=next((g for g in hier['groups'] if cid in g['categoryIds']),None)
                pos=sections.index(s)
                placement=dict(index=pos,groupId=active['id']if active else None,
                    groupIndex=active['categoryIds'].index(cid)if active else None,archived=cid in archive,
                    archiveIndex=archive.index(cid)if cid in archive else None)
                backup={'categoryId':cid,'payloadSha256':c['payloadSha256'],'operation':op,'title':p['title'],
                        'placement':placement,'source':self.source_text(snap,s)}
                sections.pop(pos)
                if active:active['categoryIds'].remove(cid)
                if cid in archive:archive.remove(cid)
                if 'archivedItems'in hier:hier['archivedItems']=[x for x in hier['archivedItems'] if x['categoryId']!=cid]
            elif op=='update':
                source=self.current_markdown(snap,p)
                require(source['sourceSha256']==a['expectedSourceSha256'],'CONCEPT_WORKFLOW_OVERWRITE_SOURCE_CHANGED')
                require(len(a['content'].encode())<=1024*1024 and
                        (source['mode']!='authored' or text(a['content'],12000)), 'CONCEPT_WORKFLOW_OVERWRITE_BODY')
                sections[sections.index(s)]=self.updated_section(s,a['content'],source['mode'])
            elif op=='migrate':
                require(cid not in snap.get('markdownSections',{}) and a['content']==as_markdown(s),'CONCEPT_WORKFLOW_MIGRATION_NOT_LOSSLESS')
                backup={'categoryId':cid,'payloadSha256':c['payloadSha256'],'operation':op,'title':p['title'],
                        'placement':None,'source':a['content']}
            else:
                placement=self.previous(c,p,prev)['placement'] if prev else dict(index=len(sections),groupId=p['groupId'],archived=False,groupIndex=None)
                g=group(placement['groupId'])
                require((placement['archived'] and g is None) or g is not None,'CONCEPT_WORKFLOW_GROUP_CHANGED')
                if not placement['archived']:
                    require(not any(x['title'].casefold()==p['title'].casefold() and x['id'] not in archive for x in sections),'CONCEPT_WORKFLOW_DUPLICATE_TITLE')
                summary={'schemaVersion':'npc-concept-summary.v1','keywords':['마스터 컨셉'],'sentence':p['title']}
                sections.insert(placement['index'],dict(id=cid,title=p['title'],description='',summary=summary,
                    fields=[dict(id='content',label='내용',value=a['content'],status='proposal',sources=[],priority=1,summary=summary)]))
                if placement['archived']:hier.setdefault('archivedCategoryIds',[]).insert(placement['archiveIndex'],cid)
                elif placement['groupIndex'] is None:g['categoryIds'].append(cid)
                else:g['categoryIds'].insert(placement['groupIndex'],cid)
                d['schemaVersion']=2
            if action not in ('migrate','update'):
                for i,sec in enumerate(sections,1):sec['priority']=i
            validate(d);validate_document(d)
            meta=split({'revision':current['revision']+1,'savedAt':utc_now(),'note':'Master category workflow '+action,
                        'document':d})['metadata.json']
            if action in ('add','replace'):meta['targetTitle']=p['title']
            req=dict(requestId=c['taskId'],inputHash=h,operation=action,categoryId=cid,sourceVersion=snap['sourceVersion'],
                     metadata=meta,content=None if delete else a['content'],backup=backup)
            if storage_target is not None:req['storageTarget']=storage_target
            if op=='update':req['expectedSourceSha256']=a['expectedSourceSha256']
            mutation_started.append(True)
            with self.source.factory() as t:result=t.call('POST',RPC,req)
            if (result.get('status'),result.get('inputHash'),result.get('categoryId'))!=('committed',h,cid):
                raise ValueError('CONCEPT_WORKFLOW_WRITE_NOT_CONFIRMED')
            saved=self.source.snapshot();_,after=self.selected(saved,p)
            confirmed=(after is None and bool(result['backupHash']) if delete else
                       saved.get('markdownSections',{}).get(cid,{}).get('sha256')==hashlib.sha256(a['content'].encode()).hexdigest())
            if not confirmed:raise ValueError('CONCEPT_WORKFLOW_WRITE_NOT_CONFIRMED')
            return result['storageDelete'] if storage_target is not None else {'ok':True}

def serve(handler):
    svc=getattr(handler.server,'workflow_concept_category',None)
    if not isinstance(svc,CategoryWorkflow):return handler.respond(404,{'error':'CONCEPT_WORKFLOW_DISABLED'})
    method=handler.command;path=handler.path
    if (path not in ((PATH,) if method=='GET' else (PATH,PATH+'/read')) or method not in ('GET','POST') or
        handler.server.server_address[0]!='127.0.0.1' or handler.client_address[0]!='127.0.0.1'
        or handler.headers.get_all('Host')!=['127.0.0.1:'+str(handler.server.server_port)]
        or handler.headers.get('Origin') is not None or handler.headers.get('Transfer-Encoding') is not None):
        return handler.respond(403,{'error':'CONCEPT_WORKFLOW_INTERNAL_ONLY'})
    mutation_started=[]
    try:return handler.respond(200,svc.capability() if method=='GET' else svc.run(handler.read_json(max_body=2*1024*1024),path.endswith('/read'),mutation_started))
    except (ClaimDenied,Rejected) as e:
        if mutation_started:return handler.respond(409,{'error':'CONCEPT_WORKFLOW_NOT_CONFIRMED'})
        return handler.respond(403,{'error':'CONCEPT_WORKFLOW_CLAIM_DENIED' if isinstance(e,ClaimDenied) else str(e)})
    except (ValueError,KeyError,TypeError):return handler.respond(409,{'error':'CONCEPT_WORKFLOW_NOT_CONFIRMED'})
    except (OSError,sqlite3.Error):return handler.respond(503,{'error':'CONCEPT_WORKFLOW_OUTCOME_UNCONFIRMED'})
