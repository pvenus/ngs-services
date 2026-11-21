"""Stable portrait-planning role; display names are not runtime identifiers."""
import hashlib,json
TITLES={'representative':'대표 포즈 기획','combat':'전투 포즈 기획','idle':'아이들 포즈 기획'}
def select_portrait_planning(character,pose):
 if pose not in TITLES:raise ValueError('NPC_PORTRAIT_POSE_INVALID')
 rows=[c for c in character.get('planningCategories',[])if c.get('lifecycleState')!='deleted']
 incarnation=character.get('creationBasis',{}).get('requestId')
 key='category.preset.'+hashlib.sha256(json.dumps(['npc.standard',character.get('planningPreset',{}).get('version','2'),incarnation,'portrait-art'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:32]
 art=[c for c in rows if c.get('id')==key]
 if not art:art=[c for c in rows if c.get('name')=='포트레이트용 아트']
 selected=[c for c in rows if c.get('planningRole')=='portrait.'+pose]
 if not selected:selected=[c for c in rows if not c.get('planningRole') and c.get('name')==TITLES[pose]]
 if len(art)!=1 or len(selected)!=1 or any(not isinstance(c.get('content'),str) or not c['content'].strip()for c in art+selected):raise ValueError('NPC_PORTRAIT_PLANNING_NOT_READY')
 return art+selected
