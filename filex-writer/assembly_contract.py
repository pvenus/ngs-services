"""Exact Binari assembly metadata; mirrored by the PDS and Request Service."""
import json,re
V2='ngs.assembly-generate.v2'
KINDS={'assembly.generate':('ngs.assembly-generate.v1',V2),'assembly.update':('ngs.assembly-update.v1',)}
TYPE='binari.ritual-well-water-demo'
STATE='ngs.assembly.v1'
ROLES=('table','bowl','well','lid')
LIMIT=65536
MAX_REV=2**53-2
OID=r'request\.official\.[a-f0-9]{64}'
SLUG=r'[a-z0-9]+(?:[-._][a-z0-9]+)*'
REGION=r'[a-z][a-z0-9-]{0,31}'
HASH=r'[a-f0-9]{64}'
FIELDS=('schemaVersion','projectId','assemblyId','revision','updatedAt','officialRequestId','title','map','puzzles')

def keys(v,fields):return type(v)is dict and set(v)==set(fields)
def match(v,pat):return type(v)is str and re.fullmatch(pat,v) is not None
def int_range(v,a,b):return type(v)is int and a<=v<=b
def clean(v,n):
    return type(v)is str and 1<=len(v.encode('utf-16-le'))//2<=n and v==v.strip().strip('\ufeff').strip() and not re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]',v)
def slug(v):return match(v,SLUG) and 2<=len(v)<=80
def pin(v):
    return keys(v,('propId','revision','sha256')) and slug(v['propId']) and int_range(v['revision'],1,MAX_REV) and match(v['sha256'],HASH)
def xy(v,a,b):return int_range(v['x'],a,b) and int_range(v['y'],a,b)
def station(v):return keys(v,('regionId','x','y')) and match(v['regionId'],REGION) and xy(v,10,90)
def offset(v):return keys(v,('x','y')) and xy(v,-12,12)
def puzzles(v):
    if type(v)is not list or not 0<=len(v)<=32:return False
    ids=set()
    for p in v:
        if not (keys(p,('id','type','layout','attachments','bindings')) and slug(p['id']) and p['id'] not in ids and p['type']==TYPE and
                keys(p['layout'],('altar','well')) and all(station(x) for x in p['layout'].values()) and
                keys(p['attachments'],('bowl','lid')) and all(offset(x) for x in p['attachments'].values()) and
                keys(p['bindings'],ROLES) and all(pin(x) for x in p['bindings'].values()) and
                len({x['propId'] for x in p['bindings'].values()})==4):return False
        ids.add(p['id'])
    return True
def state(v):
    return (keys(v,FIELDS) and v['schemaVersion']==STATE and v['projectId']=='binari' and
            all(match(v[k],OID) for k in ('assemblyId','officialRequestId')) and int_range(v['revision'],1,MAX_REV) and
            int_range(v['updatedAt'],1,2**53-1) and clean(v['title'],100) and
            keys(v['map'],('mapId','revision','officialRequestId')) and
            all(match(v['map'][k],OID) for k in ('mapId','officialRequestId')) and int_range(v['map']['revision'],1,MAX_REV) and puzzles(v['puzzles']))
def puzzle_plan(v):
    if type(v)is not list or not 1<=len(v)<=8:return False
    ids=set()
    for p in v:
        if not (keys(p,('id','type','intent')) and slug(p['id']) and p['id'] not in ids and p['type']==TYPE and
                (p['intent']=='' or clean(p['intent'],1000))):return False
        ids.add(p['id'])
    return True
def payload(v,kind):
    if type(v)is not dict or kind not in KINDS:return False
    gen=kind=='assembly.generate'
    base=('schemaVersion','projectId')
    v2=v.get('schemaVersion')==V2
    return (keys(v,base+(('title','mapId','intent','puzzles' if v2 else 'puzzleType') if gen else
                         ('assemblyId','expectedRevision','puzzles'))) and
            v['schemaVersion'] in KINDS[kind] and v['projectId']=='binari' and
            ((clean(v['title'],100) and match(v['mapId'],OID) and clean(v['intent'],2000) and
              (puzzle_plan(v['puzzles']) if v2 else v['puzzleType']==TYPE)) if gen else
            (match(v['assemblyId'],OID) and int_range(v['expectedRevision'],1,MAX_REV-1) and puzzles(v['puzzles']))))
def graph_reaches(graph,start):
    seen={start};pending=[start]
    edges={}
    for e in graph['connections']:edges.setdefault(e['from'],[]).append(e['to'])
    while pending:
        for target in edges.get(pending.pop(),[]):
            if target not in seen:seen.add(target);pending.append(target)
    return seen
def playable(graph,rows):
    ids={r['id'] for r in graph['regions']}
    visit=graph_reaches(graph,graph['startRegionId'])
    for p in rows:
        a,w=(p['layout'][k]['regionId'] for k in ('altar','well'))
        if a not in ids or w not in ids or a not in visit or w not in visit or w not in graph_reaches(graph,a) or a not in graph_reaches(graph,w):return False
    return True
def wire(v):return json.dumps(v,ensure_ascii=False,separators=(',',':'),sort_keys=True,allow_nan=False).encode()
