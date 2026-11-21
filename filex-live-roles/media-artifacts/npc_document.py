"""Lossless NPC file contract for a Filex authority cutover.

Pure conversion/planning only. No DB writes, deployment, local original access,
backup-on-edit, or fallback to an old DB. A writer must atomically validate the
read set and commit the complete changed files. NPC deletion is a separate
verified whole-folder backup operation, never an implicit diff side effect.
"""
import copy
import re
from npc_store import digest
def require(condition,message):
    if not condition:raise ValueError(message)

ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
PREFIX = 'canonical-files/binari/npcs/'
POSE_VIEW_KEY = re.compile(r'(representative|combat|idle)/(pixel|portrait)/(n|ne|e|se|s|sw|w|nw)\Z')
ANIMATION_KEY = re.compile(r'(representative|combat|idle)/[A-Za-z0-9][A-Za-z0-9._-]{0,127}/(n|ne|e|se|s|sw|w|nw)\Z')

FRAME_SET_KEY = re.compile(r'(?:representative|combat|idle)/[A-Za-z0-9][A-Za-z0-9._-]{0,127}(?:/half)?\Z')

def encode(document):
    """Split each category into a file without discarding legacy unknown fields."""
    require(isinstance(document, dict) and isinstance(document.get('characters'), list), 'NPC_DOCUMENT_REQUIRED')
    root = copy.deepcopy(document)
    characters = root.pop('characters')
    root['characterOrder'] = []
    files = {}
    for original in characters:
        npc = copy.deepcopy(original)
        nid = npc.get('id')
        require(isinstance(nid, str) and ID.fullmatch(nid) and nid not in root['characterOrder'], 'NPC_ID_INVALID')
        root['characterOrder'].append(nid)
        categories = npc.pop('planningCategories', None)
        # Preserve absence separately from an explicitly empty list.
        if categories is not None:
            require(isinstance(categories, list), 'NPC_CATEGORIES_INVALID')
            order = []
            for category in categories:
                cid = category.get('id') if isinstance(category, dict) else None
                require(isinstance(cid, str) and ID.fullmatch(cid) and cid not in order, 'NPC_CATEGORY_ID_INVALID')
                order.append(cid)
                files[f'{nid}/planning/{cid}.json'] = category
            npc['planningCategoryOrder'] = order
        views=npc.pop('poseViews',None)
        if views is not None:
            require(isinstance(views,dict),'NPC_POSE_VIEWS_INVALID')
            keys=[]
            for pose,types in views.items():
                require(isinstance(types,dict),'NPC_POSE_VIEWS_INVALID')
                for kind,directions in types.items():
                    require(isinstance(directions,dict),'NPC_POSE_VIEWS_INVALID')
                    for direction,value in directions.items():
                        key='/'.join((pose,kind,direction))
                        require(POSE_VIEW_KEY.fullmatch(key)and isinstance(value,dict),'NPC_POSE_VIEW_INVALID')
                        keys.append(key);files[f'{nid}/pose-views/{key}.json']=copy.deepcopy(value)
            npc['poseViewKeys']=sorted(keys)
        require('poseViewKeys' not in original,'NPC_RESERVED_FIELD_CONFLICT')
        frame_sets=npc.pop('animationFrameSets',None)
        if frame_sets is not None:
            require(isinstance(frame_sets,dict),'NPC_FRAME_SETS_INVALID')
            npc['animationFrameSetKeys']=sorted(frame_sets)
            for key,value in frame_sets.items():
                require(isinstance(key,str) and FRAME_SET_KEY.fullmatch(key) and isinstance(value,dict),'NPC_FRAME_SET_INVALID')
                files[f'{nid}/animation-frames/{key}.json']=copy.deepcopy(value)
        require('animationFrameSetKeys' not in original,'NPC_RESERVED_FIELD_CONFLICT')
        slots=npc.pop('animationSlots',None)
        if slots is not None:
            require(isinstance(slots,dict),'NPC_ANIMATION_SLOTS_INVALID')
            npc['animationSlotKeys']=sorted(slots)
            for key,value in slots.items():
                require(isinstance(key,str)and ANIMATION_KEY.fullmatch(key),'NPC_ANIMATION_SLOT_INVALID')
                files[f'{nid}/animations/{key}.json']=copy.deepcopy(value)
        require('animationSlotKeys' not in original,'NPC_RESERVED_FIELD_CONFLICT')
        experiments=npc.pop('animationExperiments',None)
        if experiments is not None:
            require(isinstance(experiments,dict) and set(experiments)<={'eight-up-run-4x2','eight-up-run-4x2-matched-reference'},'NPC_EXPERIMENT_KEYS_INVALID')
            npc['animationExperimentKeys']=sorted(experiments)
            for key,value in experiments.items():files[f'{nid}/animation-experiments/{key}.json']=copy.deepcopy(value)
        require('animationExperimentKeys' not in original,'NPC_RESERVED_FIELD_CONFLICT')
        require('planningCategoryOrder' not in original, 'NPC_RESERVED_FIELD_CONFLICT')
        files[f'{nid}/metadata.json'] = npc
    require('characterOrder' not in document, 'NPC_RESERVED_FIELD_CONFLICT')
    files['metadata.json'] = root
    return files


def decode(files):
    root = copy.deepcopy(files['metadata.json'])
    order = root.pop('characterOrder')
    require(isinstance(order, list) and len(order) == len(set(order)), 'NPC_ORDER_INVALID')
    characters = []
    for nid in order:
        require(isinstance(nid, str) and ID.fullmatch(nid), 'NPC_ID_INVALID')
        npc = copy.deepcopy(files[f'{nid}/metadata.json'])
        require(npc.get('id') == nid, 'NPC_FILE_ID_MISMATCH')
        if 'planningCategoryOrder' in npc:
            ids = npc.pop('planningCategoryOrder')
            require(isinstance(ids, list) and len(ids) == len(set(ids)), 'NPC_CATEGORY_ORDER_INVALID')
            npc['planningCategories'] = []
            for cid in ids:
                require(isinstance(cid, str) and ID.fullmatch(cid), 'NPC_CATEGORY_ID_INVALID')
                item = copy.deepcopy(files[f'{nid}/planning/{cid}.json'])
                require(item.get('id') == cid, 'NPC_FILE_ID_MISMATCH')
                npc['planningCategories'].append(item)
        if 'poseViewKeys' in npc:
            keys=npc.pop('poseViewKeys');require(isinstance(keys,list)and len(keys)==len(set(keys)),'NPC_POSE_VIEW_KEYS_INVALID')
            npc['poseViews']={}
            for key in keys:
                require(isinstance(key,str)and POSE_VIEW_KEY.fullmatch(key),'NPC_POSE_VIEW_INVALID')
                pose,kind,direction=key.split('/')
                npc['poseViews'].setdefault(pose,{}).setdefault(kind,{})[direction]=copy.deepcopy(files[f'{nid}/pose-views/{key}.json'])
        if 'animationFrameSetKeys' in npc:
            keys=npc.pop('animationFrameSetKeys')
            require(isinstance(keys,list) and len(keys)==len(set(keys)) and all(isinstance(key,str) and FRAME_SET_KEY.fullmatch(key) for key in keys),'NPC_FRAME_SET_KEYS_INVALID')
            npc['animationFrameSets']={key:copy.deepcopy(files[f'{nid}/animation-frames/{key}.json']) for key in keys}
        if 'animationSlotKeys' in npc:
            keys=npc.pop('animationSlotKeys');require(isinstance(keys,list)and len(keys)==len(set(keys)),'NPC_ANIMATION_KEYS_INVALID')
            npc['animationSlots']={}
            for key in keys:
                require(isinstance(key,str)and ANIMATION_KEY.fullmatch(key),'NPC_ANIMATION_SLOT_INVALID')
                npc['animationSlots'][key]=copy.deepcopy(files[f'{nid}/animations/{key}.json'])
        if 'animationExperimentKeys' in npc:
            keys=npc.pop('animationExperimentKeys');require(isinstance(keys,list) and len(keys)==len(set(keys)) and set(keys)<={'eight-up-run-4x2','eight-up-run-4x2-matched-reference'},'NPC_EXPERIMENT_KEYS_INVALID')
            npc['animationExperiments']={key:copy.deepcopy(files[f'{nid}/animation-experiments/{key}.json'])for key in keys}
        characters.append(npc)
    root['characters'] = characters
    return root


def changed_files(before, after, expected_hashes):
    """Produce complete-file CAS writes; never interpret omissions as deletion.

    expected_hashes is the worker's actual source read set. It may contain
    unchanged common files whose stale content must also reject the result.
    A server must validate this read set in the same transaction as the writes.
    """
    require(isinstance(expected_hashes, dict), 'NPC_READ_SET_REQUIRED')
    for path in set(before) | set(after) | set(expected_hashes):
        require(isinstance(path, str) and (path == 'metadata.json' or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}/(?:metadata\.json|planning/[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json|animation-experiments/eight-up-run-4x2(?:-matched-reference)?\.json|pose-views/(?:representative|combat|idle)/(?:pixel|portrait)/(?:n|ne|e|se|s|sw|w|nw)\.json|animation-frames/(?:representative|combat|idle)/[A-Za-z0-9][A-Za-z0-9._-]{0,127}(?:/half)?\.json|animations/(?:representative|combat|idle)/[A-Za-z0-9][A-Za-z0-9._-]{0,127}/(?:n|ne|e|se|s|sw|w|nw)\.json)', path)), 'NPC_FILE_PATH_INVALID')
    for path, expected in expected_hashes.items():
        actual = digest(before[path]) if path in before else None
        require(actual == expected, 'NPC_SOURCE_CHANGED')
    require(set(before) <= set(after), 'NPC_EXPLICIT_DELETE_REQUEST_REQUIRED')
    writes = []
    for path, body in sorted(after.items()):
        if path in before and digest(before[path]) == digest(body):
            continue
        require(path in expected_hashes, 'NPC_WRITE_BASIS_MISSING')
        writes.append({'path': PREFIX + path, 'body': copy.deepcopy(body), 'expectedHash': expected_hashes[path]})
    return {'files': writes, 'readSet': {PREFIX + p: h for p, h in expected_hashes.items()}, 'backupPolicy': 'none', 'requiresAtomicReadSetCommit': True}
