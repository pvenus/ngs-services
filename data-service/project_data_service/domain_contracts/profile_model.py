# -*- coding: utf-8 -*-
"""Shared numeric persona contract. Scores are design proposals, not runtime decisions."""
import json
import re
from pathlib import Path

CATALOG = json.loads((Path(__file__).parent / 'npc_profile_catalog.json').read_text(encoding='utf-8'))
TRAIT_GROUPS = {g['id']: tuple(a['key'] for a in g['axes']) for g in CATALOG['groups'] if g['kind'] == 'traits'}
PROFILE_GROUPS = {g['id']: tuple(a['key'] for a in g['axes']) for g in CATALOG['groups'] if g['kind'] == 'profile'}
RELATION_AXES = tuple(a['key'] for a in CATALOG['relationshipAxes'])


def blank_profile():
    return {'version': 1, 'reviewStatus': 'proposal',
            'groups': {g: dict.fromkeys(keys) for g, keys in PROFILE_GROUPS.items()},
            'context': {'branchId': '', 'sceneId': '', 'eventRef': ''},
            'relationships': [], 'rules': []}


def upgrade_character(character, propose=False):
    """Idempotently add new fields; never overwrite existing values, facts or portraits."""
    traits = character.setdefault('traits', {})
    is_new = 'numericProfile' not in character
    profile = character.setdefault('numericProfile', blank_profile())
    for group in CATALOG['groups']:
        dest = traits.setdefault(group['id'], {}) if group['kind'] == 'traits' else profile['groups'][group['id']]
        for axis in group['axes']:
            value = None  # Project-specific character proposals are not shipped.
            if group['kind'] == 'traits':
                dest.setdefault(axis['key'], value)
            elif is_new and propose and character['id'] == 'character.seojin':
                dest[axis['key']] = value
    if is_new and propose and character['id'] == 'character.seojin':
        profile['rules'] = [
            {'id': 'protect_life', 'priority': 1, 'mode': 'priority', 'condition': '눈앞에 임박한 생명·안전 위협이 있을 때', 'action': '대피와 보호를 먼저 하고 필요한 최소한의 힘으로 위협을 중단한다.'},
            {'id': 'protect_witness', 'priority': 2, 'mode': 'priority', 'condition': '피해자와 증인의 안전이 위협받을 때', 'action': '증거 확보나 명령 집행보다 사람의 보호를 우선한다.'},
            {'id': 'verify_truth', 'priority': 3, 'mode': 'priority', 'condition': '즉각적인 위험을 통제한 뒤', 'action': '관찰·증언·기록을 대조하고 의지가 있는 상대에게 의도를 묻는다.'},
            {'id': 'public_duty', 'priority': 4, 'mode': 'priority', 'condition': '상위 보호·검증 원칙과 충돌하지 않을 때', 'action': '공적 명령과 절차를 집행한다.'},
            {'id': 'no_child_sacrifice', 'priority': 5, 'mode': 'boundary', 'condition': '대의를 이유로 아이의 희생을 요구받을 때', 'action': '기본 인격의 금지선. 플레이어가 이탈하는 경로에는 일탈·비용·책임을 명시한다.'},
            {'id': 'no_fabrication', 'priority': 6, 'mode': 'boundary', 'condition': '기록을 조작해 무고한 사람에게 책임을 돌리는 선택', 'action': '기본 인격의 금지선. 수치 가중치로 정당화하지 않는다.'},
            {'id': 'no_species_verdict', 'priority': 7, 'mode': 'boundary', 'condition': '괴이라는 분류만을 이유로 처단하는 선택', 'action': '실제 위협과 의도·피해를 확인한다. 분류만으로 처단을 정당화하지 않는다.'},
        ]
    if is_new and propose and character['id'] == 'character.seojin' and any(v is not None for v in profile['groups']['state'].values()):
        design=json.loads((Path(__file__).parent/'seojin_design.json').read_text(encoding='utf-8'))
        profile['context']=dict(design['context'])
    return character


def validate_scores(values, keys, label):
    if not isinstance(values, dict) or set(values) != set(keys):
        raise ValueError(f'{label}의 수치 항목 구성을 확인하세요.')
    if any(v is not None and (type(v) is not int or not 0 <= v <= 100) for v in values.values()):
        raise ValueError(f'{label}은 0~100 정수 또는 미설정이어야 합니다.')


def text_value(value, maximum, label, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f'{label}을 확인하세요.')


def validate_profile(character):
    p = character.get('numericProfile')
    if not isinstance(p, dict) or set(p) != {'version', 'reviewStatus', 'groups', 'context', 'relationships', 'rules'} or type(p['version']) is not int or p['version'] != 1:
        raise ValueError('수치 프로필 형식을 확인하세요. 새로고침 후 편집하세요.')
    if p['reviewStatus'] not in ('proposal', 'confirmed'):
        raise ValueError('수치 프로필 검토 상태를 확인하세요.')
    if not isinstance(p['groups'], dict) or set(p['groups']) != set(PROFILE_GROUPS):
        raise ValueError('욕구·말투·능력·장면 상태가 필요합니다.')
    for group, keys in PROFILE_GROUPS.items():
        validate_scores(p['groups'][group], keys, group)
    context = p['context']
    if not isinstance(context, dict) or set(context) != {'branchId', 'sceneId', 'eventRef'}:
        raise ValueError('상태 스냅샷의 문맥을 확인하세요.')
    for k, v in context.items():
        text_value(v, 200, k)
    relations = p['relationships']
    if not isinstance(relations, list) or len(relations) > 100:
        raise ValueError('대상별 관계는 100개 이하로 기록하세요.')
    seen = set()
    for row in relations:
        if not isinstance(row, dict) or set(row) != {'targetId', 'values'}:
            raise ValueError('관계 대상 형식을 확인하세요.')
        target = row['targetId']
        if not isinstance(target, str) or not re.fullmatch(r'[a-z][a-z0-9._-]{1,89}', target) or target in seen or target == character['id']:
            raise ValueError('관계 대상 ID는 본인·중복을 제외하고 영문 ID로 기록하세요.')
        seen.add(target)
        validate_scores(row['values'], RELATION_AXES, '대상별 관계')
    if relations or any(v is not None for v in p['groups']['state'].values()):
        for k, v in context.items():
            text_value(v, 200, f'상태·관계의 {k}', required=True)
    rules = p['rules']
    if not isinstance(rules, list) or len(rules) > 30:
        raise ValueError('판단 규칙은 최대 30개입니다.')
    rule_ids, priorities = set(), set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {'id', 'priority', 'mode', 'condition', 'action'}:
            raise ValueError('판단 규칙 형식을 확인하세요.')
        text_value(rule['id'], 90, '규칙 ID', required=True)
        if rule['id'] in rule_ids or type(rule['priority']) is not int or not 1 <= rule['priority'] <= 100 or rule['priority'] in priorities or rule['mode'] not in ('priority', 'boundary'):
            raise ValueError('규칙 ID·순서는 중복 없이, 유형은 우선 원칙 또는 금지선으로 지정하세요.')
        rule_ids.add(rule['id']); priorities.add(rule['priority'])
        text_value(rule['condition'], 1000, '규칙 조건', required=True)
        text_value(rule['action'], 1000, '규칙 행동', required=True)
    return p
