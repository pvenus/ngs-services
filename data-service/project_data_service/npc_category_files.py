"""Owned planning-file transport. Only edited bytes cross the worker/PDS boundary."""
import base64, copy, hashlib, json, re, uuid
from datetime import datetime, timezone
from .filex_read import digest, require
from .filex_npc_authority import FilexCurrentNPC, MARKER
from .filex_npc_files import ID, decode
from .npc_ownership import GUARD
from .domain_contracts import legacy_http as native

NATIVE_FIELDS = {
    'kind':'인물 분류','role':'역할',
    'summary':'핵심 요약','publicFace':'처음 보이는 모습','actualTruth':'실제 사실 · 숨겨진 정보',
    'desire':'핵심 욕구','fear':'두려움 · 회피','values':'가치와 충돌 우선순위',
    'belief':'세계를 해석하는 방식','behavior':'행동 판단 규칙','voice':'말투와 대화 규칙',
    'arc':'변화 가능성과 유지할 원칙','relationships':'인물 관계와 갈등',
    'stateKeys':'선택에 따라 이어질 상태','openQuestions':'미결정 · 정합성 검토','sourceRefs':'근거 문서',
    'numericProfile':'수치 · 관계 · 판단 기준','traits':'기본 성향',
}

def native_categories(character):
    return [{'id':'field:'+key,'uuid':str(uuid.uuid5(uuid.NAMESPACE_URL, character['id']+':'+key)),
             'name':name,'content':'\n'.join(character[key]) if key=='sourceRefs' else json.dumps(character[key],ensure_ascii=False,sort_keys=True,separators=(',',':')) if key in ('numericProfile','traits') else character[key],
             'sourceField':key,**({'sourceFormat':'json'} if key in ('numericProfile','traits') else {})} for key,name in NATIVE_FIELDS.items() if key in character]

MAX_FILE = 2 * 1024 * 1024
NATIVE = {'kind','role','summary','publicFace','actualTruth','desire','fear','values','belief','behavior','voice','arc','relationships','stateKeys','openQuestions','sourceRefs','numericProfile','traits'}

def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()

def header(value):return base64.b64encode(encoded(value)).decode()

def parse_header(value):
    require(isinstance(value,str) and len(value)<=32768,'CATEGORY_FILE_HEADER_INVALID')
    data=json.loads(base64.b64decode(value,validate=True));require(isinstance(data,dict),'CATEGORY_FILE_HEADER_INVALID');return data

class CategoryFiles:
    def __init__(self,domains):self.domains=domains
    def _guard(self,npc):
        guard=GUARD.get()
        require(isinstance(guard,dict) and guard.get('npcId')==npc and 'leaseToken' in guard,'CATEGORY_FILE_OWNER_REQUIRED')
        require(isinstance(self.domains.npcs,FilexCurrentNPC),'CATEGORY_FILEX_REQUIRED')
        return guard
    def _path(self,npc,category):
        require(isinstance(npc,str) and ID.fullmatch(npc),'CATEGORY_FILE_NPC_INVALID')
        if isinstance(category,str) and category.startswith('field:'):
            require(category[6:] in NATIVE,'CATEGORY_FILE_NATIVE_INVALID');return npc+'/metadata.json'
        require(isinstance(category,str) and ID.fullmatch(category),'CATEGORY_FILE_ID_INVALID')
        return npc+'/planning/'+category+'.json'
    def download(self,npc,category,revision,mode='modify'):
        guard=self._guard(npc);path=self._path(npc,category)
        require(mode in ('modify','add') and type(revision)is int,'CATEGORY_FILE_REQUEST_INVALID')
        with self.domains.lock:
            s,marker,_=self.domains.npcs._read()
            require(marker['revision']==revision,'CATEGORY_REVISION_CONFLICT')
            require(npc+'/metadata.json' in s['documents'],'CATEGORY_NPC_NOT_FOUND')
            if mode=='add':
                require(not category.startswith('field:') and path not in s['documents'],'CATEGORY_FILE_ADD_EXISTS')
                body={'id':category,'uuid':category.removeprefix('category.'),'name':'','content':''}
            else:
                require(path in s['documents'],'CATEGORY_TARGET_NOT_FOUND');body=s['documents'][path]
                if category.startswith('field:'):require(category[6:] in body,'CATEGORY_TARGET_NOT_FOUND')
            raw=encoded(body)
            require(len(raw)<=MAX_FILE,"CATEGORY_FILE_SIZE_INVALID")
            basis={'schemaVersion':'npc-category-file.v1','npcId':npc,'categoryId':category,'mode':mode,'path':path,'revision':revision,'sourceVersion':s['sourceVersion'],'fileHash':s['hashes'].get(path),'sha256':hashlib.sha256(raw).hexdigest(),'requestId':guard['requestId'],'stageRequestId':guard.get('stageRequestId',guard['requestId'])}
            return raw,basis
    def replace(self,p,raw):
        require(isinstance(raw,bytes) and 0<len(raw)<=MAX_FILE,'CATEGORY_FILE_SIZE_INVALID')
        required={'projectId','expectedRevision','npcId','categoryId','mode','operationHash','metadata','basis','note'}
        require(set(p)==required and p['projectId']=='ProjectBS','CATEGORY_FILE_REQUEST_INVALID')
        npc,cat=p['npcId'],p['categoryId'];guard=self._guard(npc);path=self._path(npc,cat);basis=p['basis'];mode=p['mode'];meta=p['metadata'];op=p['operationHash']
        require(isinstance(op,str) and re.fullmatch('[a-f0-9]{64}',op),'CATEGORY_FILE_OPERATION_INVALID')
        require(isinstance(meta,dict) and set(meta)<={'inputHash','operationHash','categoryId','categoryUUID'} and meta.get('categoryId')==cat and re.fullmatch('[a-f0-9]{64}',str(meta.get('inputHash',''))),'CATEGORY_FILE_METADATA_INVALID')
        require(isinstance(basis,dict) and basis.get('schemaVersion')=='npc-category-file.v1' and basis.get('requestId')==guard['requestId'] and basis.get('stageRequestId')==guard.get('stageRequestId',guard['requestId']) and basis.get('npcId')==npc and basis.get('categoryId')==cat and basis.get('path')==path and basis.get('mode')==mode and basis.get('revision')==p['expectedRevision'],'CATEGORY_FILE_BASIS_INVALID')
        require(isinstance(p['note'],str) and len(p['note'])<=1000,'CATEGORY_FILE_NOTE_INVALID')
        body=json.loads(raw);require(isinstance(body,dict),'CATEGORY_FILE_OBJECT_REQUIRED')
        binding=digest({'requestId':guard['requestId'],'payload':p,'fileSha256':hashlib.sha256(raw).hexdigest()})
        rid='npc.category.file.'+op
        with self.domains.lock,self.domains.npc_receipts.connection:
            with self.domains.npcs.store.factory() as t:prior=t.receipt(rid)
            if prior:
                require(prior.get('inputHash')==binding and prior.get('status')=='committed','CATEGORY_OPERATION_PAYLOAD_CONFLICT')
                receipt=prior['resultMetadata']['categoryFileReceipt']
                self._persist(op,receipt);return receipt
            existing=self.domains.npc_receipts.connection.execute('SELECT receipt FROM npc_category_apply_receipts WHERE project_id=? AND operation_hash=?',('ProjectBS',op)).fetchone()
            require(existing is None,'CATEGORY_OPERATION_PAYLOAD_CONFLICT')
            s,marker,document=self.domains.npcs._read()
            require(marker['revision']==p['expectedRevision'] and s['sourceVersion']==basis['sourceVersion'] and s['hashes'].get(path)==basis['fileHash'],'CATEGORY_REVISION_CONFLICT')
            files=copy.deepcopy(s['documents']);before=files.get(path)
            if mode=='add':
                require(before is None and not cat.startswith('field:'),'CATEGORY_FILE_ADD_EXISTS')
                require(set(body)<={'id','uuid','name','content','planningRole'} and body.get('id')==cat and body.get('uuid')==cat.removeprefix('category.'),'CATEGORY_FILE_ID_CHANGED')
                require(basis['sha256']==hashlib.sha256(encoded({'id':cat,'uuid':cat.removeprefix('category.'),'name':'','content':''})).hexdigest(),'CATEGORY_FILE_DOWNLOAD_MISMATCH')
                owner=files[npc+'/metadata.json'];owner.setdefault('planningCategoryOrder',[]).append(cat)
            else:
                require(mode=='modify' and before is not None,'CATEGORY_TARGET_NOT_FOUND')
                require(basis['sha256']==hashlib.sha256(encoded(before)).hexdigest(),'CATEGORY_FILE_DOWNLOAD_MISMATCH')
                allowed={cat[6:]} if cat.startswith('field:') else {'name','content','planningRole'}
                require({k for k in set(before)|set(body) if before.get(k)!=body.get(k)}<=allowed,'CATEGORY_FILE_SCOPE_CHANGED')
                require(set(before)-set(body)==set(),'CATEGORY_FILE_FIELD_REMOVED')
                require(set(body)-set(before)<=allowed,'CATEGORY_FILE_SCOPE_CHANGED')
                if not cat.startswith('field:'):
                    require(before.get('lifecycleState')!='deleted','CATEGORY_TARGET_NOT_FOUND')
                    require(before.get('planningRole') in (None,body.get('planningRole')),'CATEGORY_PLANNING_ROLE_IMMUTABLE')
            if not cat.startswith('field:'):
                require(isinstance(body.get('name'),str) and body['name'].strip() and isinstance(body.get('content'),str),'CATEGORY_FILE_CONTENT_INVALID')
                require(body.get('planningRole') in (None,'portrait.representative','portrait.idle','portrait.combat'),'CATEGORY_PLANNING_ROLE_INVALID')
            files[path]=body
            clean=copy.deepcopy(files);clean['metadata.json'].pop(MARKER,None);after=decode(clean)
            native.validate_npcs(after,getattr(self.domains.npcs,'asset_get',None))
            character=next(c for c in after['characters'] if c['id']==npc)
            all_categories=[c for n in after['characters'] for c in n.get('planningCategories',[])]
            require(len({c['id'] for c in all_categories})==len(all_categories) and len({c.get('uuid',c['id']) for c in all_categories})==len(all_categories),'CATEGORY_IDENTITY_COLLISION')
            categories=character.get('planningCategories',[])
            require(len({c['name'].strip().casefold() for c in categories})==len(categories),'DUPLICATE_CATEGORY_NAME')
            roles=[c['planningRole'] for c in categories if c.get('planningRole') not in (None,'','custom') and c.get('lifecycleState')!='deleted']
            require(len(set(roles))==len(roles),'CATEGORY_PLANNING_ROLE_DUPLICATE')
            before_category=next((c for c in native_categories(before) if c['id']==cat),None) if cat.startswith('field:') else before
            after_category=next(c for c in native_categories(body) if c['id']==cat) if cat.startswith('field:') else body
            now=datetime.now(timezone.utc).isoformat()
            receipt={**meta,'categoryId':cat,'categoryUUID':after_category.get('uuid',cat),'diff':{'before':before_category,'after':after_category},'operationHash':op,'revision':marker['revision']+1,'savedAt':now,'documentHash':digest(after),'commitId':rid,'authority':'filex','filePath':path,'fileHash':digest(body),'transport':'pds-file-replace.v1'}
            files['metadata.json'][MARKER]={'revision':receipt['revision'],'savedAt':now,'note':p['note'],'operationHash':binding,'categoryFileReceipt':receipt}
            result=self.domains.npcs.store.apply(rid,binding,before=s['documents'],after=files,expected_hashes={k:s['hashes'].get(k) for k in set(files)|set(s['documents'])})
            require(result['receipt']['resultMetadata'].get('categoryFileReceipt')==receipt,'CATEGORY_FILE_RECEIPT_MISMATCH')
            self._persist(op,receipt);return receipt
    def _persist(self,op,receipt):
        c=self.domains.npc_receipts.connection
        row=c.execute('SELECT receipt FROM npc_category_apply_receipts WHERE project_id=? AND operation_hash=?',('ProjectBS',op)).fetchone()
        require(row is None or json.loads(row[0])==receipt,'CATEGORY_OPERATION_PAYLOAD_CONFLICT')
        c.execute('INSERT OR IGNORE INTO npc_category_apply_receipts VALUES(?,?,?)',('ProjectBS',op,json.dumps(receipt,ensure_ascii=False)))
