"""Exact Binari assembly metadata; mirrored by the PDS and Request Service."""
import json,re
V2='ngs.assembly-generate.v2'
V3='ngs.assembly-generate.v3'
V4='ngs.assembly-generate.v4'
KINDS={'assembly.generate':('ngs.assembly-generate.v1',V2,V3,V4),'assembly.update':('ngs.assembly-update.v1',)}
TYPE='binari.ritual-well-water-demo'
SPIRIT_TYPE='binari.ritual-spirit-manifestation'
STATE='ngs.assembly.v1'
ROLES=('table','bowl','well','lid')
LIMIT=65536
MAX_REV=2**53-2
OID=r'request\.official\.[a-f0-9]{64}'
SLUG=r'[a-z0-9]+(?:[-._][a-z0-9]+)*'
REGION=r'[a-z][a-z0-9-]{0,31}'
HASH=r'[a-f0-9]{64}'
PORTAL_STATES=('altar-empty','carried-empty','carried-filled','altar-filled')
FIELDS=('schemaVersion','projectId','assemblyId','revision','updatedAt','officialRequestId','title','map','puzzles')

def keys(v,fields):return type(v)is dict and set(v)==set(fields)
def match(v,pat):return type(v)is str and re.fullmatch(pat,v) is not None
def int_range(v,a,b):return type(v)is int and a<=v<=b
def clean(v,n):
    return type(v)is str and 1<=len(v.encode('utf-16-le'))//2<=n and v==v.strip().strip('\ufeff').strip() and not re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]',v)
def slug(v):return match(v,SLUG) and 2<=len(v)<=80
def pin(v):
    return keys(v,('propId','revision','sha256')) and slug(v['propId']) and int_range(v['revision'],1,MAX_REV) and match(v['sha256'],HASH)
def image_scale(v):return type(v)is int and (v==5 or 10<=v<=100 and v%10==0)
def binding_pin(v):
    if type(v)is not dict:return False
    if 'imageScalePercent' not in v:return pin(v)
    return image_scale(v['imageScalePercent']) and pin({k:x for k,x in v.items() if k!='imageScalePercent'})
def xy(v,a,b):return int_range(v['x'],a,b) and int_range(v['y'],a,b)
def station(v):return keys(v,('regionId','x','y')) and match(v['regionId'],REGION) and xy(v,10,90)
def offset(v):return keys(v,('x','y')) and xy(v,-12,12)
def valid_conditions(v):
    if type(v)is not list or len(v)>16:return False
    ids=set()
    for c in v:
        if not (keys(c,('portalId','propRole','requiredState')) and match(c['portalId'],REGION) and c['portalId'] not in ids and
                c['propRole']=='bowl' and c['requiredState'] in PORTAL_STATES):return False
        ids.add(c['portalId'])
    return True

def condition_index(rows):
    return {(p['id'],c['portalId']):c for p in rows for c in p.get('portalConditions',())}

def affected_conditions_exist(previous,current,graph):
    prior=condition_index(previous)
    portals={e['id'] for e in graph['connections'] if 'id' in e}
    return all(c==prior.get(k) or c['portalId'] in portals for k,c in condition_index(current).items())

WATER_STATES={'bowl':{'altar-empty':(False,'empty'),'carried-empty':(True,'empty'),
                     'carried-filled':(True,'filled'),'altar-filled':(False,'filled')},
              'well':{'ready':(False,'ready')},'altar':{'ready':(False,'ready')},
              'lid':{'closed':(False,'closed'),'open':(False,'open')}}
WATER_RULES=(
 ('inventory.move.v1','pickup','bowl',None,'bowl','altar-empty','carried-empty',()),
 ('switch.v1','activate','lid',None,'lid','closed','open',()),
 ('prop.interact.v1','drop','well','bowl','bowl','carried-empty','carried-filled',(('lid','open'),)),
 ('inventory.move.v1','drop','altar','bowl','bowl','carried-filled','altar-filled',()),
)
def checked_action(a):
    return (a['kind'],a['target'],a['item']) if keys(a,('kind','target','item')) else None
def valid_definition(v):
    if not (keys(v,('schemaVersion','entities','rules','goal','solution')) and v['schemaVersion']=='ngs.puzzle-definition.v1' and
            type(v['entities'])is list and len(v['entities'])==4 and type(v['rules'])is list and len(v['rules'])==4 and
            v['goal']=={'role':'bowl','state':'altar-filled'} and type(v['solution'])is list and len(v['solution'])==4):return False
    active={}
    for e in v['entities']:
        if not (keys(e,('role','initial','states')) and type(e['role'])is str and e['role'] in WATER_STATES and
                e['role'] not in active and type(e['states'])is list):return False
        expected=WATER_STATES[e['role']]
        if e['initial']!=next(iter(expected)) or len(e['states'])!=len(expected):return False
        seen=set()
        for s in e['states']:
            if not (keys(s,('id','held','payload')) and type(s['id'])is str and s['id'] in expected and s['id'] not in seen and
                    type(s['held'])is bool and (s['held'],s['payload'])==expected[s['id']]):return False
            seen.add(s['id'])
        active[e['role']]=e['initial']
    leftover=list(WATER_RULES)
    for r in v['rules']:
        if not (keys(r,('module','action','from','to','requires')) and keys(r['from'],('role','state')) and
                keys(r['to'],('role','state')) and r['to']['role']==r['from']['role'] and type(r['requires'])is list and
                all(keys(x,('role','state')) for x in r['requires'])):return False
        a=checked_action(r['action'])
        if a is None:return False
        t=(r['module'],*a,r['from']['role'],r['from']['state'],r['to']['state'],tuple((x['role'],x['state']) for x in r['requires']))
        if t not in leftover:return False
        leftover.remove(t)
    for a in v['solution']:
        step=checked_action(a)
        rule=next((r for r in WATER_RULES if r[1:4]==step),None)
        if not rule:return False
        _,_,_,_,role,before,after,conditions=rule
        if active[role]!=before or any(active[k]!=value for k,value in conditions):return False
        active[role]=after
    return active['bowl']=='altar-filled'

def definitions_preserved(prior,next_puzzles):
    before={p['id']:p['definition'] for p in prior if 'definition' in p}
    # A user may explicitly remove the entire row. Retained definitions cannot be rewritten or stripped.
    return all(p.get('definition')==before.get(p['id']) for p in next_puzzles)

def valid_z_order(v,roles):
    return type(v)is dict and bool(v) and all(k in roles and int_range(z,-1000,1000) for k,z in v.items())
def puzzles(v,well_only=False):
    if type(v)is not list or not 0<=len(v)<=32:return False
    ids=set()
    fields=('id','type','layout','attachments','bindings')
    for p in v:
        optional=tuple(k for k in ('scale','portalConditions','definition','zOrder') if type(p)is dict and k in p)
        if not (keys(p,fields+optional) and slug(p['id']) and p['id'] not in ids):return False
        if p['type']==SPIRIT_TYPE and not well_only:
            if 'portalConditions' in optional or 'definition' in optional:return False
            if not (keys(p['layout'],('spirit',)) and station(p['layout']['spirit']) and
                    keys(p['attachments'],()) and keys(p['bindings'],('spirit',)) and binding_pin(p['bindings']['spirit']) and
                    ('scale' not in optional or keys(p['scale'],('spirit',)) and int_range(p['scale']['spirit'],10,300))):return False
        else:
            if 'definition' in optional and not valid_definition(p['definition']):return False
            if 'scale' in optional and not (keys(p['scale'],ROLES) and all(int_range(x,10,300) for x in p['scale'].values())):return False
            if 'portalConditions' in optional and not valid_conditions(p['portalConditions']):return False
            if not (p['type']==TYPE and
                    keys(p['layout'],('altar','well')) and all(station(x) for x in p['layout'].values()) and
                    keys(p['attachments'],('bowl','lid')) and all(offset(x) for x in p['attachments'].values()) and
                    keys(p['bindings'],ROLES) and all(binding_pin(x) for x in p['bindings'].values()) and
                    len({x['propId'] for x in p['bindings'].values()})==4):return False
        if 'zOrder' in optional and not valid_z_order(p['zOrder'],('spirit',) if p['type']==SPIRIT_TYPE else ROLES):return False
        ids.add(p['id'])
    return True
def content(v):
    return keys(v,('title','mapId','puzzles')) and clean(v['title'],100) and match(v['mapId'],OID) and puzzles(v['puzzles'])
def state(v):
    return (keys(v,FIELDS) and v['schemaVersion']==STATE and v['projectId']=='binari' and
            all(match(v[k],OID) for k in ('assemblyId','officialRequestId')) and int_range(v['revision'],1,MAX_REV) and
            int_range(v['updatedAt'],1,2**53-1) and clean(v['title'],100) and
            keys(v['map'],('mapId','revision','officialRequestId')) and
            all(match(v['map'][k],OID) for k in ('mapId','officialRequestId')) and int_range(v['map']['revision'],1,MAX_REV) and puzzles(v['puzzles']))
def puzzle_plan(v,allow_spirit=False):
    if type(v)is not list or not 1<=len(v)<=8:return False
    ids=set()
    for p in v:
        base=('id','type','intent')
        if not ((keys(p,base) and p['type']==TYPE or
                 allow_spirit and keys(p,base+('prop',)) and p['type']==SPIRIT_TYPE and pin(p['prop'])) and
                slug(p['id']) and p['id'] not in ids and (p['intent']=='' or clean(p['intent'],1000))):return False
        ids.add(p['id'])
    return True
def payload(v,kind):
    if type(v)is not dict or kind not in KINDS:return False
    gen=kind=='assembly.generate'
    base=('schemaVersion','projectId')
    batch=v.get('schemaVersion') in (V2,V3,V4)
    return (keys(v,base+(('title','mapId','intent','puzzles' if batch else 'puzzleType') if gen else
                         ('assemblyId','expectedRevision','puzzles'))) and
            v['schemaVersion'] in KINDS[kind] and v['projectId']=='binari' and
            ((clean(v['title'],100) and match(v['mapId'],OID) and clean(v['intent'],2000) and
              ((puzzle_plan(v['puzzles'],v['schemaVersion']==V3) and (v['schemaVersion']!=V4 or len(v['puzzles'])==1)) if batch else v['puzzleType']==TYPE)) if gen else
            (match(v['assemblyId'],OID) and int_range(v['expectedRevision'],1,MAX_REV-1) and puzzles(v['puzzles'],well_only=True))))
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
        if p['type']==SPIRIT_TYPE:
            if p['layout']['spirit']['regionId'] not in ids & visit:return False
            continue
        a,w=(p['layout'][k]['regionId'] for k in ('altar','well'))
        if a not in ids or w not in ids or a not in visit or w not in visit or w not in graph_reaches(graph,a) or a not in graph_reaches(graph,w):return False
    return True
def wire(v):return json.dumps(v,ensure_ascii=False,separators=(',',':'),sort_keys=True,allow_nan=False).encode()
