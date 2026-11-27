"""Fixed project map JSON slot, atomic immutable revisions, no arbitrary paths."""
import json
import re
from hashlib import sha256
from assembly_contract import pin as prop_pin

MAP_ROUTE='/v1/workflow/maps'
MAP_DELETE_ROOT='canonical-files/binari/cinematics/maps'
TASK=re.compile(r'workflow\.task\.[a-f0-9]{64}\Z')
REVISION=re.compile(r'[1-9][0-9]{0,15}\.json\Z')
OID=re.compile(r'request\.official\.[a-f0-9]{64}\Z')
HASH=re.compile(r'[a-f0-9]{64}\Z')
MAP_SCHEMA='ngs.map-metadata.v1'
GROUP_SCHEMA='ngs.map-group.v1'
METADATA={'schemaVersion','projectId','mapId','revision','updatedAt','officialRequestId'}
PARTS={'start':'startRegionId','portals':'connections','collisions':'colliders'}
KEY=re.compile(r'[a-z][a-z0-9-]{0,31}\Z')

def match(v,p):return type(v) is str and p.fullmatch(v) is not None
def need(ok):
    if not ok:raise ValueError('map storage packet invalid')
def map_base(store,mid):
    need(match(mid,OID))
    root=store.root/'maps'; target=root/mid
    need(not root.is_symlink() and not target.is_symlink())
    return target
def deletion_marker(store,mid):
    root=store.root/'.map-deleted'
    marker=root/(mid+'.json')
    need(not root.is_symlink() and not marker.is_symlink())
    return marker

def remove_map_folder(store,command):
    from types import SimpleNamespace
    from single_target_delete import PinnedDeleteRejected,SingleTargetDelete,validate_target
    try:
        need(type(command)is dict and set(command)=={'operationId','target','expected'})
        target=validate_target(command['target']);expected=command['expected'];operation=command['operationId']
        need(target['root']==MAP_DELETE_ROOT and target['kind']=='directory' and match(target['path'],OID) and
             match(operation,TASK) and type(expected)is dict and set(expected)=={'revision','officialRequestId'} and
             type(expected['revision'])is int and 1<=expected['revision']<2**53 and match(expected['officialRequestId'],OID))
    except (ValueError,KeyError) as exc:raise PinnedDeleteRejected('map delete binding') from exc
    mid=target['path']; pin=(json.dumps(command,sort_keys=True,separators=(',',':'))+'\n').encode()
    with store.lock():
        try:
            base=map_base(store,mid); marker=deletion_marker(store,mid)
            try:frozen=store._read(marker,4096)
            except FileNotFoundError:frozen=None
            if frozen is not None:need(frozen==pin)
            if base.exists():
                need(base.is_dir())
                revisions=[int(p.stem) for p in base.iterdir() if match(p.name,REVISION)]
                need(revisions and max(revisions)==expected['revision'])
                state=read_revision(store,base,expected['revision'])
                need(state.get('officialRequestId')==expected['officialRequestId'] and
                     state.get('schemaVersion')=='ngs.map-metadata.v1' and state.get('projectId')=='binari')
            else:need(frozen is not None)
        except (FileNotFoundError,NotADirectoryError,ValueError) as exc:
            raise PinnedDeleteRejected('stale map before moving') from exc
        # Same cinematic lock as map publication. A durable fenced marker prevents
        # delayed create/update from recreating a retired (or mid-move) map.
        if frozen is None:store.write(marker,pin)
        return SingleTargetDelete(SimpleNamespace(root=base.parent),MAP_DELETE_ROOT).remove_locked(
            {'operationId':operation,'target':target})

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
def encode(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()

def part_path(base,n,part):
    path=base/f'{n}.{part}.json'
    need(not path.is_symlink())
    return path

def validate_start(graph):
    if 'startPoint' not in graph:return
    pt=graph['startPoint']
    need(type(pt)is dict and set(pt)=={'x','y'} and all(type(v)is int and 0<=v<=1000 for v in pt.values()))

def validate_decorations(graph):
    if 'decorations' not in graph:return
    d=graph['decorations'];regions=graph['regions']
    need(type(d)is list and len(d)<=48 and type(regions)is list)
    ids={r.get('id') for r in regions if type(r)is dict}
    found=set()
    for row in d:
        need(type(row)is dict and set(row)=={'id','regionId','prop','anchor','scale','visibility'} and
             match(row['id'],KEY) and row['id'] not in found and match(row['regionId'],KEY) and row['regionId'] in ids and
             prop_pin(row['prop']) and type(row['scale'])is int and 10<=row['scale']<=300 and
             row['visibility'] in ('both','pixel','cinematic'))
        pt=row['anchor'];need(type(pt)is dict and set(pt)=={'x','y'} and all(type(v)is int and 0<=v<=1000 for v in pt.values()))
        found.add(row['id'])

def read_group(store,base,n,state):
    need(set(state)==METADATA|{'title','regions','parts'} and state['projectId']=='binari' and
         match(state['officialRequestId'],OID))
    digests=state['parts']
    need(type(digests)is dict and set(digests) in (set(PARTS),set(PARTS)|{'decorations'}) and all(match(h,HASH) for h in digests.values()))
    graph={'title':state['title'],'regions':state['regions']}
    members={**PARTS,**({'decorations':'decorations'} if 'decorations' in digests else {})}
    for part,field in members.items():
        try:raw=store._read(part_path(base,n,part),65536)
        except FileNotFoundError as exc:raise ValueError('map revision part missing') from exc
        need(sha256(raw).hexdigest()==digests[part])
        value=json.loads(raw)
        need(type(value)is dict and (set(value)=={field} or part=='collisions' and not value or
             part=='start' and set(value)=={field,'startPoint'}))
        graph.update(value)
    state={k:state[k] for k in METADATA}
    return {**state,'schemaVersion':MAP_SCHEMA,'graph':graph}

def read_revision(store,base,n):
    p=base/(str(n)+'.json')
    need(not p.is_symlink())
    state=json.loads(store._read(p,65536))
    need(type(state)is dict and state.get('revision')==n and state.get('mapId')==base.name)
    if state.get('schemaVersion')==GROUP_SCHEMA:state=read_group(store,base,n,state)
    validate_colliders(state.get('graph')); validate_start(state['graph']);validate_decorations(state['graph'])
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
def map_revision(store,p,write=False,expected=None):
    from server import Conflict
    need(type(p)is dict and match(p.get('mapId'),OID) and type(p.get('revision'))is int and 1<=p['revision']<2**53)
    base=map_base(store,p['mapId']); n=p['revision']
    if not write:return read_revision(store,base,n)
    need(set(p)=={'schemaVersion','projectId','mapId','revision','updatedAt','officialRequestId','graph'} and
         p['schemaVersion']=='ngs.map-metadata.v1' and p['projectId']=='binari' and match(p['officialRequestId'],OID))
    graph=p['graph']; validate_colliders(graph); validate_start(graph);validate_decorations(graph)
    required={'title','regions','startRegionId','connections'}
    need(required<=set(graph)<=required|{'colliders','startPoint','decorations'} and len(encode(p))<65536)
    part_bytes={part:encode({field:graph[field]} if field in graph else {}) for part,field in PARTS.items()}
    if 'decorations' in graph:part_bytes['decorations']=encode({'decorations':graph['decorations']})
    if 'startPoint' in graph:part_bytes['start']=encode({'startRegionId':graph['startRegionId'],'startPoint':graph['startPoint']})
    manifest={**{k:p[k] for k in METADATA},'schemaVersion':GROUP_SCHEMA,
              'title':graph['title'],'regions':graph['regions'],
              'parts':{part:sha256(raw).hexdigest() for part,raw in part_bytes.items()}}
    with store.lock():
        base=map_base(store,p['mapId'])
        if deletion_marker(store,p['mapId']).exists():raise Conflict('map retired')
        target=base/(str(n)+'.json')
        need(not target.is_symlink())
        if target.exists():
            old=read_revision(store,base,n)
            if old['officialRequestId']==p['officialRequestId'] and old['graph']==p['graph']:return old
            raise Conflict('map revision changed')
        if n>1:
            if not (base/(str(n-1)+'.json')).exists():raise Conflict('map revision changed')
            prior=read_revision(store,base,n-1)
            if 'decorations' in prior['graph'] and 'decorations' not in graph:
                raise Conflict('map decorations omitted')
            if expected is not None and (expected['revision']!=prior['revision'] or expected['officialRequestId']!=prior['officialRequestId'] or
               graph['title']!=prior['graph']['title']):raise Conflict('map revision changed')
        # Parts from an interrupted attempt are not published; replacing them is
        # safe while the numeric commit marker is absent and this lock is held.
        for part,raw in part_bytes.items():store.write(part_path(base,n,part),raw)
        need(read_group(store,base,n,manifest)==p)
        store.write(target,encode(manifest))
        return read_revision(store,base,n)


def update_map_metadata(store,p):
    need(type(p) is dict and set(p)=={'expected','state'} and type(p['expected']) is dict and set(p['expected'])=={'revision','officialRequestId'})
    e=p['expected'];state=p['state']
    need(type(e['revision']) is int and e['revision']>=1 and match(e['officialRequestId'],OID) and type(state) is dict and state.get('revision')==e['revision']+1)
    return map_revision(store,state,True,e)
