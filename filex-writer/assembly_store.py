"""Atomic assembly publication on the existing cinematic store and lock."""
import json
from assembly_contract import LIMIT,OID,int_range,keys,match,playable,state,wire,MAX_REV
from map_store import map_base,read_revision,REVISION,deletion_marker
from server import Conflict

ASSEMBLY='/v1/workflow/assemblies'
ASSEMBLY_DELETE_ROOT='canonical-files/binari/cinematics/assemblies'

def need(ok):
    if not ok:raise ValueError('ASSEMBLY_STORAGE_INVALID')
def base(store,aid):
    need(match(aid,OID))
    root=store.root/'assemblies';p=root/aid
    need(not root.is_symlink() and not p.is_symlink())
    return p
def retirement(store,aid):
    need(match(aid,OID))
    root=store.root/'.assembly-deleted';p=root/(aid+'.json')
    need(not root.is_symlink() and not p.is_symlink())
    return p

def remove_assembly_folder(store,command):
    from types import SimpleNamespace
    from single_target_delete import PinnedDeleteRejected,SingleTargetDelete,validate_target
    try:
        need(keys(command,('operationId','target','expected')))
        t=validate_target(command['target']);pin=command['expected']
        need(t['root']==ASSEMBLY_DELETE_ROOT and t['kind']=='directory' and match(t['path'],OID) and
             match(command['operationId'],r'workflow\.task\.[a-f0-9]{64}') and keys(pin,('revision','officialRequestId')) and
             int_range(pin['revision'],1,MAX_REV) and match(pin['officialRequestId'],OID))
    except (TypeError,ValueError,KeyError) as exc:raise PinnedDeleteRejected('assembly delete binding') from exc
    aid=t['path'];frozen_command=wire(command)+b'\n'
    with store.lock():
        try:
            p=base(store,aid);marker=retirement(store,aid)
            try:frozen=store._read(marker,4096)
            except FileNotFoundError:frozen=None
            if frozen is not None:need(frozen==frozen_command)
            if p.exists():
                need(head(store,p)==pin and revision(store,p,pin['revision'])['officialRequestId']==pin['officialRequestId'])
            else:need(frozen is not None)
        except (FileNotFoundError,NotADirectoryError,ValueError) as exc:
            raise PinnedDeleteRejected('stale assembly before moving') from exc
        if frozen is None:store.write(marker,frozen_command)
        return SingleTargetDelete(SimpleNamespace(root=p.parent),ASSEMBLY_DELETE_ROOT).remove_locked(
            {'operationId':command['operationId'],'target':t})

def read(store,path,limit=LIMIT):
    need(not path.is_symlink())
    raw=store._read(path,limit)
    result=json.loads(raw)
    need(wire(result)+b'\n'==raw)
    return result
def head(store,p):
    try:v=read(store,p/'revision.json',1024)
    except FileNotFoundError:return None
    need(keys(v,('revision','officialRequestId')) and int_range(v['revision'],1,MAX_REV) and match(v['officialRequestId'],OID))
    return v
def revision(store,p,n):
    need(int_range(n,1,MAX_REV))
    row=read(store,p/f'{n}.json')
    need(state(row) and row['assemblyId']==p.name and row['revision']==n)
    return row
def map_state(store,v):
    need(keys(v,('mapId',)) or keys(v,('mapId','revision','officialRequestId')))
    mid=v['mapId'];p=map_base(store,mid)
    if deletion_marker(store,mid).exists():raise FileNotFoundError('map retired')
    if 'revision' in v:
        need(int_range(v['revision'],1,MAX_REV) and match(v['officialRequestId'],OID))
        m=read_revision(store,p,v['revision']);need(m['officialRequestId']==v['officialRequestId'])
    else:
        ns=[int(x.stem) for x in p.iterdir() if match(x.name,REVISION)]
        if not ns:raise FileNotFoundError('map absent')
        m=read_revision(store,p,max(ns))
    return m
def call(store,operation,body):
    need(type(body)is dict)
    with store.lock():
        if operation=='map':return map_state(store,body)
        if operation=='list':
            need(not body)
            root=store.root/'assemblies';need(not root.is_symlink())
            rows=[]
            if root.is_dir():
                for p in root.iterdir():
                    if not match(p.name,OID):continue
                    if retirement(store,p.name).exists():continue
                    p=base(store,p.name);h=head(store,p)
                    if h:rows.append(revision(store,p,h['revision']))
            rows.sort(key=lambda x:(-x['updatedAt'],x['assemblyId']))
            return {'assemblies':rows}
        if operation in ('head','revision'):
            need(keys(body,('assemblyId',) if operation=='head' else ('assemblyId','revision')))
            if retirement(store,body['assemblyId']).exists():raise FileNotFoundError('assembly retired')
            p=base(store,body['assemblyId']);h=head(store,p)
            if h is None or operation=='revision' and not int_range(body['revision'],1,h['revision']):raise FileNotFoundError('assembly absent')
            row=revision(store,p,h['revision'] if operation=='head' else body['revision'])
            if row['revision']==h['revision']:need(row['officialRequestId']==h['officialRequestId'])
            return row
        need(operation=='publish' and state(body))
        if retirement(store,body['assemblyId']).exists():raise Conflict('assembly retired')
        p=base(store,body['assemblyId']);h=head(store,p);n=body['revision']
        if h and n<=h['revision']:
            old=revision(store,p,n)
            if old==body:return old
            raise Conflict('assembly revision changed')
        if n!=(h['revision']+1 if h else 1):raise Conflict('assembly revision changed')
        if h:
            previous=revision(store,p,h['revision'])
            need(previous['officialRequestId']==h['officialRequestId'] and previous['title']==body['title'] and previous['map']==body['map'])
            # Check the current map while holding the same storage lock as publication.
            m=map_state(store,{'mapId':body['map']['mapId']})
        else:
            need(body['assemblyId']==body['officialRequestId'])
            m=map_state(store,{'mapId':body['map']['mapId']})
            if any(m[k]!=body['map'][k] for k in ('mapId','revision','officialRequestId')):raise Conflict('assembly map pin stale')
        need(playable(m['graph'],body['puzzles']))
        raw=wire(body)+b'\n';need(len(raw)<=LIMIT)
        target=p/f'{n}.json';need(not target.is_symlink() and not (p/'revision.json').is_symlink())
        # The head is the commit point. A non-visible orphan may be rewritten.
        store.write(target,raw)
        store.write(p/'revision.json',wire({k:body[k] for k in ('revision','officialRequestId')})+b'\n')
        return body
