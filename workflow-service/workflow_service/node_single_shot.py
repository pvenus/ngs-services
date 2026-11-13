"""Explicit single-shot compiler v1. Pure preview; never mutates a saved receipt.

The renderer receives visual prose only. Sources, raw supplemental messages,
review evidence, selected Catalog context and hashes stay in descriptor metadata.
"""
import copy, hashlib, json, re
from .node_contract import validate_decision

VERSION = 'npc-node-single-shot-prompt.v1'
PLAN = 'npc-node-single-shot-plan.v1'

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def require(condition, code):
    if not condition:
        raise ValueError(code)

def single_clause(value, limit=500):
    require(isinstance(value, str) and 5 <= len(value) <= limit, 'NODE_SHOT_TEXT_INVALID')
    require(not re.search(r'[\n\r{}\[\]]|(?:[123１２３]\s*안|첫\s*장면|다음\s*장면|그\s*다음|이후\s*장면|구도\s*비교|클로즈업|close[- ]?up|storyboard|split[- ]?screen|triptych|panel\s*[123])', value, re.I), 'NODE_SHOT_MULTISCENE_TEXT_DENIED')
    return value

def validate_plan(packet, message, plan, review):
    require(packet.get('schemaVersion') == 'npc-node-image-scene-context.v3', 'NODE_SHOT_PACKET_VERSION_REQUIRED')
    require(isinstance(message, str) and len(message) <= 12000, 'NODE_SHOT_REQUEST_MESSAGE_INVALID')
    validate_decision(packet, review)
    keys = {'schemaVersion', 'sourcePacketHash', 'requestMessageHash', 'sceneSentence', 'momentQuote', 'participants', 'camera', 'motherPresentation', 'picturePresentation', 'background', 'styleQuotes', 'supplementalStyleQuotes', 'reviewedOmissions'}
    require(isinstance(plan, dict) and set(plan) == keys and plan['schemaVersion'] == PLAN, 'NODE_SHOT_PLAN_SCHEMA_INVALID')
    require(plan['sourcePacketHash'] == digest(packet) and plan['requestMessageHash'] == digest(message), 'NODE_SHOT_SOURCE_OR_MESSAGE_CHANGED')
    require(plan['camera'] == 'one-medium-wide-continuous-view' and plan['motherPresentation'] == 'rear-cropped-unidentified-silhouette' and plan['picturePresentation'] == 'illustrated-face-occluded-against-body' and plan['background'] == 'nonfigurative-paper-wash', 'NODE_SHOT_COMPOSITION_REQUIRES_REVIEW')
    visible = packet['scene']['visibleText']
    single_clause(plan['sceneSentence'])
    require(isinstance(plan['momentQuote'], str) and len(plan['momentQuote']) >= 10 and plan['momentQuote'] in visible, 'NODE_SHOT_MOMENT_QUOTE_INVALID')
    people = {c['data']['id']: c for c in packet['characters']}
    require(isinstance(plan['participants'], list) and len(plan['participants']) == len(people), 'NODE_SHOT_PARTICIPANT_COVERAGE_REQUIRED')
    require(set(people) == {'character.seojin', 'npc.missing_child_mother', 'npc.chief_choi_manseok'}, 'NODE_SHOT_CURRENT_SCENE_REVIEW_REQUIRED')
    ids = []
    for participant in plan['participants']:
        require(isinstance(participant, dict) and set(participant) == {'id', 'actionQuote', 'appearanceQuotes'}, 'NODE_SHOT_PARTICIPANT_SCHEMA_INVALID')
        identifier = participant['id']; ids.append(identifier)
        require(identifier in people, 'NODE_SHOT_UNKNOWN_PARTICIPANT')
        single_clause(participant['actionQuote'], 250)
        require(participant['actionQuote'] in visible, 'NODE_SHOT_ACTION_NOT_IN_VISIBLE_NODE')
        # Every visible action is retained as grounded metadata; one scene sentence
        # composes them spatially instead of replaying a dialogue sequence.
        require(isinstance(participant['appearanceQuotes'], list) and len(participant['appearanceQuotes']) <= 4, 'NODE_SHOT_APPEARANCE_INVALID')
        public = people[identifier]['data'].get('publicFace', '')
        for quote in participant['appearanceQuotes']:
            single_clause(quote, 180)
            require(quote in public, 'NODE_SHOT_APPEARANCE_NOT_IN_PUBLIC_SOURCE')
        name = people[identifier]['data']['name']
        # The visible body uses the relation name for the node-associated mother.
        name_in_scene = '어머니' if identifier == 'npc.missing_child_mother' else name
        require(name_in_scene in participant['actionQuote'], 'NODE_SHOT_ACTION_PARTICIPANT_MISMATCH')
        require(name_in_scene in plan['sceneSentence'], 'NODE_SHOT_SCENE_OMITS_PARTICIPANT')
    require(len(set(ids)) == len(ids) and set(ids) == set(people), 'NODE_SHOT_PARTICIPANT_COVERAGE_REQUIRED')
    require('그림' in plan['sceneSentence'] and '손을 뻗' in plan['sceneSentence'], 'NODE_SHOT_SCENE_OMITS_CORE_ACTION_OR_PROP')
    require(not next(c for c in plan['participants'] if c['id'] == 'npc.missing_child_mother')['appearanceQuotes'], 'NODE_SHOT_UNDEFINED_MOTHER_APPEARANCE_DENIED')
    style = next((s for s in packet['styleRules'] if s['id'] == 'visualStyle'), None)
    require(style is not None and isinstance(plan['styleQuotes'], list) and 1 <= len(plan['styleQuotes']) <= 4, 'NODE_SHOT_STYLE_REQUIRED')
    for quote in plan['styleQuotes']:
        single_clause(quote, 180)
        require(quote in style['value'], 'NODE_SHOT_STYLE_NOT_IN_MASTER')
    require(isinstance(plan['supplementalStyleQuotes'], list) and len(plan['supplementalStyleQuotes']) <= 2, 'NODE_SHOT_SUPPLEMENT_INVALID')
    # Only an explicit compatible style phrase can enter the visual prose.
    # All other requestMessage content, including comparison alternatives, is
    # retained verbatim in metadata rather than silently treated as source canon.
    for quote in plan['supplementalStyleQuotes']:
        require(quote == '한국풍 2D 애니메이션 느낌' and quote in message, 'NODE_SHOT_SUPPLEMENT_REQUIRES_REVIEW')
    require(isinstance(plan['reviewedOmissions'], list) and set(plan['reviewedOmissions']) == set(review['omittedFindingIds']), 'NODE_SHOT_OMISSION_REVIEW_MISMATCH')
    for identifier in ('mother-details', 'setting-details', 'picture-content'):
        require(identifier in plan['reviewedOmissions'], 'NODE_SHOT_UNDEFINED_DETAIL_REVIEW_REQUIRED')
    return copy.deepcopy(plan)

def compile_descriptor(packet, message, plan, review, selection, context):
    """Compile one compact provider payload plus separately hash-bound evidence.

    This is a new explicit contract. Existing v2/v3 compilers, sessions, tool
    arguments and failure records must never be rebuilt with this function.
    """
    validate_plan(packet, message, plan, review)
    require(isinstance(selection, dict) and selection.get('serviceId') == 'npc-planning' and selection.get('actionId') == 'npc-planning.prepare-context', 'NODE_SHOT_SELECTED_ROUTE_REQUIRED')
    require(isinstance(context, dict) and context.get('action', {}).get('id') == selection['actionId'], 'NODE_SHOT_SELECTED_CONTEXT_MISMATCH')
    require([k.get('id') for k in context.get('knowledge', [])] == selection.get('contextEntryIds') and len(set(selection['contextEntryIds'])) == len(selection['contextEntryIds']), 'NODE_SHOT_CATALOG_BINDING_INVALID')
    require(context.get('instruction') or any(k.get('content') for k in context.get('knowledge', [])), 'NODE_SHOT_CATALOG_CONTEXT_REQUIRED')
    people = {c['data']['id']: c for c in packet['characters']}
    details = []
    for participant in plan['participants']:
        if participant['appearanceQuotes']:
            details.append(people[participant['id']]['data']['name'] + '의 외형은 ' + ', '.join(participant['appearanceQuotes']) + '이다')
    prompt = (
        '한 장의 완성 삽화. 한 순간, 한 카메라의 중간 거리 넓은 구도로 세 인물이 함께 보이는 하나의 연속된 화면을 그린다. 각 인물은 이 화면에 한 번씩만 나타난다. '
        + plan['sceneSentence'] + ' '
        + '어머니를 가까운 쪽에서 뒤로 바라보아 어깨와 그림을 품은 두 팔의 단순한 실루엣을 보인다. 얼굴과 머리·옷의 세부는 판독되지 않을 정도로 단순화한다. 그림의 그려진 면은 어머니의 몸 쪽에 가려져 관객에게 그 내용이 드러나지 않는다. '
        + '서진과 최만석의 공개된 외형 단서만 간결하게 반영한다. ' + '. '.join(details) + '. '
        + '세 인물의 대화와 최만석의 손짓, 그림을 품은 팔이 같은 화면에서 읽힌다. 장소·건물·수목·시간·날씨를 특정하지 않는 비구상 닥종이와 옅은 색 번짐을 바탕으로 삼는다. '
        + '화풍은 ' + ', '.join(plan['styleQuotes']) + '. '
        + ('보조적인 표현 느낌은 ' + ', '.join(plan['supplementalStyleQuotes']) + '. ' if plan['supplementalStyleQuotes'] else '')
        + '테두리로 나뉘지 않은 하나의 공간을 유지한다. 글자와 말풍선이 없는 삽화다.'
    )
    require(len(prompt) <= 2200, 'NODE_SHOT_PROMPT_TOO_LARGE')
    require(all(token not in prompt for token in ['1안', '2안', '3안', 'WORKFLOW SELECTED CONTEXT', 'sourceBindings', 'fieldHash', 'Do not execute workflow_start']), 'NODE_SHOT_METADATA_LEAK')
    arguments = {'prompt': prompt, 'transparent_background': False}
    metadata = {
        'schemaVersion': VERSION, 'sourcePacketHash': digest(packet),
        'sourceBindings': copy.deepcopy(packet['sourceBindings']),
        'plan': copy.deepcopy(plan), 'planHash': digest(plan),
        'requestMessage': message, 'requestMessageHash': digest(message),
        'requestMessageHandling': 'Only reviewed compatible style quotes enter the image prompt. Raw comparison alternatives and all other supplemental text remain here for review, not as image layout instructions.',
        'sourceReview': copy.deepcopy(review), 'sourceReviewHash': digest(review),
        'selection': copy.deepcopy(selection), 'selectionHash': digest(selection),
        'catalogContext': copy.deepcopy(context), 'catalogContextHash': digest(context),
        'sourceStatuses': {'style': {s['id']: s['status'] for s in packet['styleRules']}, 'characters': {c['data']['id']: c['data'].get('status') for c in packet['characters']}},
        'providerRequestHash': digest(arguments),
        'canonicalStatus': 'noncanonical-preview; source proposal/review statuses preserved',
        'semanticReviewRequired': 'Evidence checks prove exact source projection and reviewed omissions, not generated-image fidelity or every paraphrase. Visual output still requires one separately authorized real test.'
    }
    return {'providerId': 'codex.image_gen', 'toolName': 'image_gen.imagegen', 'compilerVersion': VERSION, 'arguments': arguments, 'outputContract': {'mime': 'image/png', 'maxBytes': 5242880, 'compositions': 1}, 'sourcePacketHash': digest(packet), 'renderMetadata': metadata, 'renderMetadataHash': digest(metadata)}
