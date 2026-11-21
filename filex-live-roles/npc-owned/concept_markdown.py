"""Scoped Markdown authority, with crash-recoverable switching and legacy read projection."""
import hashlib
import json
import os
import re

from server import Conflict
from single_target_delete import SingleTargetDelete

def encoded(v):
    return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def digest(v):return hashlib.sha256(encoded(v)).hexdigest()
def sha(b):return hashlib.sha256(b).hexdigest()
INDEX='_markdownSections'
RANKS='_sectionOrderAuthoritative'
LIMIT=1024*1024
CID=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')

def parse(text):
    """A structured migration file has readable values, never a duplicate JSON body."""
    m=re.match(r'^<!-- ngs/category:({[^\n]*}) -->\n',text)
    if not m:return None
    meta=json.loads(m[1]);lines=text[m.end():]
    fields=[]
    for item in re.finditer(r'^<!-- ngs/field:({[^\n]*}) -->\n(#+ [^\n]*\n)?(`{3,})text\n([\s\S]*?)\n\3\n',lines,re.M):
        f=json.loads(item[1])
        f['value']=item[4]
        fields.append(f)
    if [f['id'] for f in fields]!=meta.pop('fieldIds'):raise ValueError('concept structured markdown fields')
    return meta,fields

class ConceptMarkdown:
    def md_path(self,cid):
        if type(cid)is not str or not CID.fullmatch(cid):raise ValueError('concept markdown category')
        return self.lifecycle_internal('sections/'+cid+'/content.md')

    def _physical_root_metadata(self):
        p=self.path(self.namespace+'metadata.json')
        return json.loads(p.read_bytes()) if p.exists() else {}

    def md_index(self):
        return self._physical_root_metadata().get(INDEX,{})

    def modern_concept_authority(self):
        root=self._physical_root_metadata()
        return bool(root.get(INDEX)) or root.get(RANKS) is True

    def md_snapshot_projection(self,documents):
        metadata=documents.get('metadata.json',{})
        idx=metadata.pop(INDEX,{})
        ranked=metadata.pop(RANKS,False)
        if ranked is not False and ranked is not True:raise Conflict('concept rank projection')
        proofs={}
        for cid,v in idx.items():
            raw=self.md_path(cid).read_bytes()
            if sha(raw)!=v['sha256']:raise Conflict('concept markdown source changed')
            text=raw.decode('utf-8');base='sections/'+cid+'/'
            if any(k.startswith(base) for k in documents):raise Conflict('duplicate active concept authority')
            parsed=parse(text) if v.get('mode')=='structured' else None
            if v.get('mode')=='structured' and parsed is None:raise Conflict('missing structured markdown')
            if parsed:
                meta,fields=parsed
                if (meta['id'],meta['title'])!=(cid,v['title']):raise Conflict('concept markdown identity')
            else:
                summary={'schemaVersion':'npc-concept-summary.v1','keywords':['마스터 컨셉'],'sentence':v['title']}
                meta={'id':cid,'title':v['title'],'description':'','summary':summary}
                fields=[{'id':'content','label':'내용','value':text,'status':'proposal','sources':[],
                         'priority':1,'summary':summary}]
            documents[base+'metadata.json']={**meta,'priority':v['priority'],'fieldIds':[f['id']for f in fields]}
            for f in fields:documents[base+'fields/'+f['id']+'.json']=f
            proofs[cid]={'sha256':v['sha256'],'mode':v['mode']}
        if ranked:
            for priority,cid in enumerate(metadata['sectionIds'],1):
                meta=documents.get('sections/'+cid+'/metadata.json')
                if meta is None:raise Conflict('concept projection missing category')
                meta['priority']=priority
        return proofs

    def recover_markdown(self):
        jp=self.lifecycle_internal('.concept-markdown.json')
        if not jp.exists():return
        tx=json.loads(jp.read_bytes())
        backup=tx.get('backup')
        if backup:
            saved=self.lifecycle_internal('.markdown-removed/'+backup+'/record.json')
            if sha(saved.read_bytes())!=tx['backupHash']:raise Conflict('concept markdown backup changed')
        if tx.get('storageTarget') is not None:
            # Same ConceptStore lock covers generic move and catalog projection.
            # The prepared catalog journal is not observable through cooperating reads.
            delete=SingleTargetDelete(self,self.namespace.rstrip('/'))
            tx['receipt']['storageDelete']=delete.remove_locked(tx['storageTarget'])
        for item in tx['changes']:
            to=self.lifecycle_internal(item['path']);data=to.read_bytes() if to.exists() else None
            before=sha(data) if data is not None else None
            if before==item['afterHash']:continue
            if before!=item['beforeHash']:raise Conflict('concept markdown recovery source changed')
            if item['after'] is None:
                to.unlink()
                fd=os.open(to.parent,os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)
            else:
                payload=item['after'].encode()
                if sha(payload)!=item['afterHash']:raise Conflict('concept markdown staged content changed')
                self.write(to,payload)
        self.write(self.receipt_path(tx['receipt']['requestId']),encoded(tx['receipt']))
        jp.unlink()
        fd=os.open(self.root,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)

    def markdown_previous(self,rid):
        if type(rid)is not str or not re.fullmatch(r'workflow\.task\.[a-f0-9]{64}',rid):raise ValueError('concept backup identifier')
        with self.lock():
            rec=self.receipt_path(rid)
            if not rec.is_file():raise FileNotFoundError('concept backup receipt')
            receipt=json.loads(rec.read_bytes())
            root=self.lifecycle_internal('.markdown-removed/'+sha(rid.encode())+'/record.json')
            raw=root.read_bytes()
            if receipt.get('backupHash')!=sha(raw):raise Conflict('concept markdown backup identity')
            if receipt.get('operation')!='remove':raise Conflict('concept prior not removal')
            if 'storageDelete' in receipt:
                delete=SingleTargetDelete(self,self.namespace.rstrip('/'))
                delete.verify_recovery_locked(self.scope_delete_command(rid,receipt['categoryId']),
                                              receipt['storageDelete']['recoveryId'])
            return {k:v for k,v in json.loads(raw).items() if k!='physical'}

    def markdown_current(self,cid):
        """Scoped true canonical bytes, not a Markdown display reconstruction."""
        with self.lock():
            info=self.md_index().get(cid)
            if not info:raise FileNotFoundError('concept markdown target')
            raw=self.md_path(cid).read_bytes()
            if sha(raw)!=info['sha256']:raise Conflict('concept markdown source mismatch')
            return {'categoryId':cid,'title':info['title'],'source':raw.decode('utf-8'),
                    'sourceSha256':info['sha256'],'mode':info['mode']}

    def scope_delete_command(self,rid,cid):
        self.md_path(cid)
        return {'operationId':rid,'target':{'root':self.namespace.rstrip('/'),'path':'sections/'+cid,'kind':'directory'}}

    def markdown_commit(self,p):
        keys={'requestId','inputHash','operation','categoryId','sourceVersion','metadata','content','backup'}
        if type(p)is not dict or set(p) not in (keys,keys|{'storageTarget'},keys|{'expectedSourceSha256'}):
            raise ValueError('concept markdown contract')
        storage_target=p.get('storageTarget')
        generic=storage_target is not None
        rid,h,op,cid=(p[k]for k in ('requestId','inputHash','operation','categoryId'))
        if not all(type(x)is str and pat.fullmatch(x) for x,pat in ((rid,re.compile(r'workflow\.task\.[a-f0-9]{64}')),(h,re.compile('[a-f0-9]{64}')),(cid,CID))):
            raise ValueError('concept markdown identity')
        if op not in ('add','replace','migrate','remove','update'):raise ValueError('concept markdown operation')
        if op in ('remove','replace') and cid in ('experience','world','story','gameplay','character','timeSpace','expression','governance'):
            raise ValueError('required concept category cannot be removed')
        target=self.md_path(cid);path=target.relative_to(self.root).as_posix()
        if generic and (op!='remove' or storage_target!=self.scope_delete_command(rid,cid)):
            raise ValueError('concept markdown storage target')
        if 'storageTarget' in p and not generic:raise ValueError('concept markdown storage target')
        source_hash=p.get('expectedSourceSha256')
        if (op=='update' and (type(source_hash)is not str or not re.fullmatch(r'[a-f0-9]{64}',source_hash))
                or op!='update' and 'expectedSourceSha256'in p):
            raise ValueError('concept markdown update binding')
        text=p['content'];meta=p['metadata']
        if (op=='remove')!=(text is None) or text is not None and (type(text)is not str or not text.strip() or len(text.encode())>LIMIT):
            raise ValueError('concept markdown document')
        with self.lock():
            rp=self.receipt_path(rid)
            if rp.exists():
                old=json.loads(rp.read_bytes())
                if (old.get('inputHash'),old.get('categoryId'),old.get('operation'))!=(h,cid,op) or generic!=('storageDelete' in old):
                    raise Conflict('concept markdown request reused')
                if generic:
                    SingleTargetDelete(self,self.namespace.rstrip('/')).verify_recovery_locked(storage_target,old['storageDelete']['recoveryId'])
                    if target.parent.exists():raise Conflict('concept markdown target recreated')
                return old
            snap=self.snapshot_unlocked();docs=snap['documents']
            if snap['sourceVersion']!=p['sourceVersion']:raise Conflict('concept markdown stale source')
            root=docs['metadata.json'];before_ids=root['sectionIds'];new_ids=meta['sectionIds']
            if (cid in before_ids)!=(op in ('remove','migrate','update')) or (cid in new_ids)!=(op!='remove'):
                raise Conflict('concept markdown target occupied or absent')
            if [x for x in before_ids if x!=cid]!=[x for x in new_ids if x!=cid] or meta['revision']!=root['revision']+1:
                raise Conflict('concept markdown unrelated order')
            # Independently preserve all non-hierarchy master metadata, and every
            # unrelated category/group/archive membership.
            before_doc=root['document'];after_doc=meta['document']
            if {k:v for k,v in before_doc.items()if k not in ('schemaVersion','hierarchy')}!={
                k:v for k,v in after_doc.items()if k not in ('schemaVersion','hierarchy')}:
                raise Conflict('concept markdown unrelated metadata')
            def unselected(d):
                h=d['hierarchy']
                return ([{**g,'categoryIds':[x for x in g['categoryIds']if x!=cid]}for g in h['groups']],
                        [x for x in h.get('archivedCategoryIds',[])if x!=cid],
                        [x for x in h.get('archivedItems',[])if x['categoryId']!=cid])
            if unselected(before_doc)!=unselected(after_doc):raise Conflict('concept markdown unrelated hierarchy')
            if op in ('migrate','update') and (before_ids!=new_ids or before_doc!=after_doc):
                raise Conflict('concept migration changed metadata')
            index=self.md_index();current=index.get(cid)
            if (current is not None)!=target.is_file():raise Conflict('concept markdown unexpected physical target')
            if op=='update' and (current is None or current['sha256']!=p['expectedSourceSha256'] or
                sha(target.read_bytes())!=p['expectedSourceSha256']):
                raise Conflict('concept markdown overwrite source changed')
            base='sections/'+cid+'/'
            scoped={k:v for k,v in docs.items() if k.startswith(base)}
            backup_hash=None;backup_id=None
            if op in ('remove','migrate'):
                backup=p['backup']
                if type(backup)is not dict or set(backup)!={'categoryId','payloadSha256','operation','title','placement','source'} or backup['categoryId']!=cid:
                    raise ValueError('concept markdown backup binding')
                backup_id=sha(rid.encode());bp=self.lifecycle_internal('.markdown-removed/'+backup_id+'/record.json')
                record=encoded(backup if generic else {**backup,'physical':self._md_scoped_bytes(cid)})
                backup_hash=sha(record)
                if bp.exists() and sha(bp.read_bytes())!=backup_hash:raise Conflict('concept markdown backup conflict')
                if not bp.exists():self.write(bp,record)
                if sha(bp.read_bytes())!=backup_hash:raise Conflict('concept markdown backup unverified')
            elif p['backup'] is not None:raise ValueError('concept markdown backup mode')
            if op=='migrate' and current is not None:raise Conflict('concept markdown already migrated')
            if op in ('add','replace') and self._md_scoped_bytes(cid):raise Conflict('concept markdown canonical target occupied')
            changes={}
            if op in ('migrate','remove') and not generic:
                changes.update({k:None for k in self._md_scoped_bytes(cid)})
            if text is not None:
                mode=current['mode'] if op=='update' else 'structured' if op=='migrate' else 'authored'
                if mode=='structured':
                    sm,fs=parse(text) or ({},[])
                    section_meta=scoped.get(base+'metadata.json')
                    old_fields={k:v for k,v in scoped.items()if '/fields/'in k}
                    new_fields={base+'fields/'+f['id']+'.json':f for f in fs}
                    if op=='migrate':
                        if not section_meta or sm!={k:v for k,v in section_meta.items() if k not in ('fieldIds','priority')} or new_fields!=old_fields:
                            raise Conflict('concept migration changed legacy content')
                    else:
                        immutable=lambda f:{k:v for k,v in f.items() if k not in ('value','summary')}
                        if ((sm.get('id'),sm.get('title'))!=(cid,current['title']) or
                            [f['id'] for f in fs]!=section_meta['fieldIds'] or
                            {k:immutable(v) for k,v in new_fields.items()}!={k:immutable(v) for k,v in old_fields.items()}):
                            raise Conflict('concept overwrite changed field identity')
                changes[path]=text
                title=(current['title'] if op=='update' else parse(text)[0]['title'] if mode=='structured' else meta.pop('targetTitle'))
                index[cid]={'title':title,'priority':(current['priority'] if op=='update' else
                    scoped[base+'metadata.json']['priority'] if op=='migrate' else new_ids.index(cid)+1),
                    'mode':mode,'sha256':sha(text.encode())}
            else:index.pop(cid,None)
            # Existing validation mandates contiguous priorities. Adjustment
            # is index/order metadata only, not another category's content.
            ranked=generic or self._physical_root_metadata().get(RANKS,False)
            for i,k in enumerate(new_ids,1) if op not in ('migrate','update') and not ranked else ():
                if k in index:index[k]['priority']=i
                elif k!=cid:
                    rel='sections/'+k+'/metadata.json';v=docs[rel]
                    if v['priority']!=i:changes[rel]=encoded({**v,'priority':i}).decode()
            changes['metadata.json']=encoded({**meta,INDEX:index,**({RANKS:True} if ranked else {})}).decode()
            record=[]
            for rel,body in sorted(changes.items(),key=lambda pair:pair[0]!='sections/'+cid+'/content.md' or pair[1] is None):
                disk=self.lifecycle_internal(rel)
                data=disk.read_bytes() if disk.exists() else None
                record.append(dict(path=rel,beforeHash=sha(data) if data is not None else None,
                                   after=body,afterHash=sha(body.encode()) if body is not None else None))
            receipt=dict(requestId=rid,inputHash=h,operation=op,categoryId=cid,status='committed',
                         markdownSha256=index.get(cid,{}).get('sha256'),backupHash=backup_hash)
            self.write(self.lifecycle_internal('.concept-markdown.json'),encoded(dict(changes=record,receipt=receipt,
                backup=backup_id,backupHash=backup_hash,storageTarget=storage_target)))
            self.recover_markdown()
            return json.loads(rp.read_bytes())

    def _md_scoped_bytes(self,cid):
        folder=self.md_path(cid).parent
        paths=list(folder.rglob('*'))
        if any(p.is_symlink()for p in paths):raise Conflict('concept markdown scope symlink')
        out={}
        for p in paths:
            if not p.is_file() or p.name=='.DS_Store':continue
            rel=p.relative_to(self.root).as_posix()
            if p!=self.md_path(cid):self.path(self.namespace+rel)
            out[rel]=p.read_bytes().decode('utf-8')
        return out
