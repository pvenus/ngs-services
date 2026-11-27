"""Atomic assembly publication on the existing cinematic store and lock."""
import json
from assembly_contract import LIMIT,OID,int_range,keys,match,playable,state,wire,MAX_REV
from map_store import map_base,read_revision,REVISION,deletion_marker
from server import Conflict

ASSEMBLY='/v1/workflow/assemblies'

def need(ok):
    if not ok:raise ValueError('ASSEMBLY_STORAGE_INVALID')
def base(store,aid):
    need(match(aid,OID))
    root=store.root/'assemblies';p=root/aid
    need(not root.is_symlink() and not p.is_symlink())
    return p
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
                    p=base(store,p.name);h=head(store,p)
                    if h:rows.append(revision(store,p,h['revision']))
            rows.sort(key=lambda x:(-x['updatedAt'],x['assemblyId']))
            return {'assemblies':rows}
        if operation in ('head','revision'):
            need(keys(body,('assemblyId',) if operation=='head' else ('assemblyId','revision')))
            p=base(store,body['assemblyId']);h=head(store,p)
            if h is None or operation=='revision' and not int_range(body['revision'],1,h['revision']):raise FileNotFoundError('assembly absent')
            row=revision(store,p,h['revision'] if operation=='head' else body['revision'])
            if row['revision']==h['revision']:need(row['officialRequestId']==h['officialRequestId'])
            return row
        need(operation=='publish' and state(body))
        p=base(store,body['assemblyId']);h=head(store,p);n=body['revision']
        if h and n<=h['revision']:
            old=revision(store,p,n)
            if old==body:return old
            raise Conflict('assembly revision changed')
        if n!=(h['revision']+1 if h else 1):raise Conflict('assembly revision changed')
        if h:
            previous=revision(store,p,h['revision'])
            need(previous['officialRequestId']==h['officialRequestId'] and previous['title']==body['title'] and previous['map']==body['map'])
            m=map_state(store,body['map'])
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
