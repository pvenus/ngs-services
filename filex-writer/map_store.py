"""Fixed project map JSON slot, atomic immutable revisions, no arbitrary paths."""
import json
import re
from hashlib import sha256

MAP_ROUTE='/v1/workflow/maps'
OID=re.compile(r'request\.official\.[a-f0-9]{64}\Z')
HASH=re.compile(r'[a-f0-9]{64}\Z')
KEY=re.compile(r'[a-z][a-z0-9-]{0,31}\Z')

def match(v,p):return type(v) is str and p.fullmatch(v) is not None
def need(ok):
    if not ok:raise ValueError('map storage packet invalid')
def map_base(store,mid):
    need(match(mid,OID))
    root=store.root/'maps'; target=root/mid
    need(not root.is_symlink() and not target.is_symlink())
    return target
def validate_colliders(graph):
    need(type(graph)is dict)
    if 'colliders' not in graph:return
    colliders=graph['colliders']
    regions=graph.get('regions')
    need(type(colliders)is list and len(colliders)<=96 and type(regions)is list and
         all(type(r)is dict and match(r.get('id'),KEY) for r in regions))
    region_ids={r['id'] for r in regions}
    ids=set()
    for c in colliders:
        fields={'id','regionId','rect'}
        need(type(c)is dict and (set(c)==fields or (set(c)==fields|{'shape','rotation'} and
             c['shape'] in ('rect','ellipse') and type(c['rotation']) is int and 0<=c['rotation']<360)) and match(c['id'],KEY) and c['id'] not in ids and
             match(c['regionId'],KEY) and c['regionId'] in region_ids)
        r=c['rect']
        need(type(r)is dict and set(r)=={'x','y','width','height'} and all(type(x)is int for x in r.values()) and
             0<=r['x'] and 0<=r['y'] and 0<r['width']<=1000-r['x'] and 0<r['height']<=1000-r['y'])
        ids.add(c['id'])
def read_revision(store,base,n):
    p=base/(str(n)+'.json')
    need(not p.is_symlink())
    state=json.loads(store._read(p,65536))
    need(type(state)is dict and state.get('revision')==n and state.get('mapId')==base.name)
    validate_colliders(state.get('graph'))
    return state
def map_context(store,p):
    ids=p.get('backgroundIds') if type(p)is dict else None
    need(type(ids)is list and 0<len(ids)<=24 and len(set(ids))==len(ids) and
         all(match(v,re.compile(r'background\.[a-f0-9]{64}\Z')) for v in ids))
    wanted={v[-64:]:v for v in ids}; found={}
    root=store.root/'backgrounds'
    need(not root.is_symlink())
    if not root.is_dir():raise FileNotFoundError('no map backgrounds')
    for path in root.glob('*.json'):
        if not match(path.stem,HASH) or path.is_symlink():continue
        try:m=json.loads(store._read(path,4096))
        except FileNotFoundError:continue
        oid=m.get('officialRequestId','')
        if not match(oid,OID) or oid[-64:] not in wanted or m.get('sha256')!=path.stem:continue
        title=m.get('title','')
        if not isinstance(title,str) or title.endswith((' · 픽셀 생성본',' · 시네마틱 원본')):continue
        data=path.with_suffix('.png')
        if not data.is_file() or data.is_symlink():continue
        row={'id':wanted[oid[-64:]],'title':title.removesuffix(' · 픽셀 보정'),'sha256':path.stem}
        need(row['id'] not in found);found[row['id']]=row
        if len(found)==len(ids):return {'backgrounds':[found[v] for v in ids]}
    raise FileNotFoundError('map background not found')
def map_revision(store,p,write=False):
    from server import Conflict
    need(type(p)is dict and match(p.get('mapId'),OID) and type(p.get('revision'))is int and 1<=p['revision']<2**53)
    base=map_base(store,p['mapId']); n=p['revision']
    if not write:return read_revision(store,base,n)
    need(set(p)=={'schemaVersion','projectId','mapId','revision','updatedAt','officialRequestId','graph'} and
         p['schemaVersion']=='ngs.map-metadata.v1' and p['projectId']=='binari' and match(p['officialRequestId'],OID))
    validate_colliders(p['graph'])
    raw=(json.dumps(p,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()
    need(len(raw)<65536)
    with store.lock():
        target=base/(str(n)+'.json')
        need(not target.is_symlink())
        if target.exists():
            old=read_revision(store,base,n)
            if old['officialRequestId']==p['officialRequestId'] and old['graph']==p['graph']:return old
            raise Conflict('map revision changed')
        if n>1:
            if not (base/(str(n-1)+'.json')).exists():raise Conflict('map revision changed')
            read_revision(store,base,n-1)
        store.write(target,raw)
        return read_revision(store,base,n)
