"""Authoring contract for in-game popup flow; planning text never rendered as dialogue."""
import re

def validate_flow(flow,npc_ids):
    if not isinstance(flow,dict) or flow.get('version')!=1:raise ValueError('지문 흐름 버전을 확인하세요.')
    nodes=flow.get('nodes')
    if not isinstance(nodes,list) or not 1<=len(nodes)<=150:raise ValueError('지문 단계는 1~150개여야 합니다.')
    ids=set(); targets=[]
    score_pattern=r'(trait|relationship):[a-z][a-z0-9._-]{0,119}:[a-z][a-z0-9._-]{0,119}'
    if 'simulateScores' in flow and type(flow['simulateScores']) is not bool:raise ValueError('점수 미리보기 설정을 확인하세요.')
    if flow.get('maxVisibleChoices',3)!=3:raise ValueError('동시 선택지는 최대 3개입니다.')
    initial=flow.get('initialScores',{})
    if not isinstance(initial,dict) or len(initial)>100 or any(not isinstance(k,str) or not re.fullmatch(score_pattern,k) or type(v) is not int or not 0<=v<=100 for k,v in initial.items()):raise ValueError('초기 점수는 올바른 항목 키와 0~100 정수로 작성하세요.')
    def conditions(owner):
        cs=owner.get('conditions',[])
        if not isinstance(cs,list) or len(cs)>12:raise ValueError('노출 조건은 최대 12개입니다.')
        if type(owner.get('priority',0)) is not int or abs(owner.get('priority',0))>10000:raise ValueError('우선순위는 -10000~10000 정수입니다.')
        for c in cs:
            if not isinstance(c,dict) or c.get('type') not in ('score','flag') or c.get('op') not in ('gte','lt','eq'):raise ValueError('노출 조건 형식을 확인하세요.')
            k=c.get('key');v=c.get('value')
            if c['type']=='score':
                if not isinstance(k,str) or not re.fullmatch(score_pattern,k) or type(v) is not int or not 0<=v<=100:raise ValueError('점수 조건의 항목 키와 기준값을 확인하세요.')
            elif not isinstance(k,str) or not re.fullmatch(r'[a-z][a-z0-9._-]{0,79}',k) or c['op']!='eq' or type(v) not in (str,bool,int):raise ValueError('사건 상태 조건은 키와 값의 같음 비교로 작성하세요.')

    def text(v,limit,label,required=False):
        if not isinstance(v,str) or len(v)>limit or (required and not v.strip()):raise ValueError(label+'을 확인하세요.')
    def presentation(owner):
        if owner.get('tone','neutral') not in ('neutral','warm','tense'):raise ValueError('팝업 분위기를 확인하세요.')
        hints=owner.get('hints',[])
        if not isinstance(hints,list) or len(hints)>2:raise ValueError('본문 힌트는 최대 2개입니다.')
        spans=[]
        for h in hints:
            if not isinstance(h,dict):raise ValueError('힌트 형식을 확인하세요.')
            text(h.get('anchor'),120,'힌트 대상 구절',True);text(h.get('text'),300,'힌트 내용',True)
            body=owner['body'];anchor=h['anchor'];at=body.find(anchor)
            if at<0 or body.find(anchor,at+1)>=0 or any(at<b and at+len(anchor)>a for a,b in spans):raise ValueError('힌트 대상은 본문에서 겹치지 않게 한 번만 나타나야 합니다.')
            spans.append((at,at+len(anchor)))
    clues=flow.get('clues',[])
    if not isinstance(clues,list) or len(clues)>100:raise ValueError('수첩 항목은 최대 100개입니다.')
    clue_ids=set()
    for clue in clues:
        if not isinstance(clue,dict):raise ValueError('수첩 형식을 확인하세요.')
        for k,limit in [('id',79),('title',120),('source',160),('body',1200)]:text(clue.get(k),limit,'수첩 '+k,True)
        if not re.fullmatch(r'clue\.[a-z0-9._-]{1,74}',clue['id']) or clue['id'] in clue_ids or clue.get('kind') not in ('observation','testimony','inference'):raise ValueError('수첩 ID·기록 종류를 확인하세요.')
        clue_ids.add(clue['id'])
    outcomes=flow.get('outcomes')
    if outcomes is not None:
        if not isinstance(outcomes,list) or not 1<=len(outcomes)<=30:raise ValueError('에피소드 결과는 1~30개여야 합니다.')
        outcome_ids=set();defaults=0
        for outcome in outcomes:
            if not isinstance(outcome,dict):raise ValueError('에피소드 결과 형식을 확인하세요.')
            for k,limit in [('id',90),('title',120),('body',2400)]:text(outcome.get(k),limit,'에피소드 결과 '+k,True)
            if not re.fullmatch(r'[a-z0-9._-]+',outcome['id']) or outcome['id'] in outcome_ids:raise ValueError('에피소드 결과 ID를 고유하게 작성하세요.')
            outcome_ids.add(outcome['id']);conditions(outcome)
            if type(outcome.get('fallback',False)) is not bool:raise ValueError('기본 결과 설정을 확인하세요.')
            if outcome.get('fallback'):
                defaults+=1
                if outcome.get('conditions'):raise ValueError('기본 결과에는 조건을 지정하지 않습니다.')
            carry=outcome.get('carry')
            if not isinstance(carry,dict) or len(carry)>30 or any(not isinstance(k,str) or not re.fullmatch(r'[a-z][a-z0-9._-]{0,79}',k) or type(v) not in (str,bool,int) or (type(v) is int and abs(v)>100000) or (isinstance(v,str) and len(v)>200) for k,v in carry.items()):raise ValueError('결과 전달값은 키와 짧은 문자열·불리언·정수로 작성하세요.')
        if defaults!=1:raise ValueError('기본 에피소드 결과를 정확히 하나 지정하세요.')
    for n in nodes:
        if not isinstance(n,dict):raise ValueError('지문 단계 형식을 확인하세요.')
        nid=n.get('id');text(nid,90,'단계 ID',True)
        if not re.fullmatch(r'[a-z0-9._-]+',nid) or nid in ids:raise ValueError('단계 ID는 영문·숫자로 중복 없이 작성하세요.')
        ids.add(nid)
        if n.get('kind') not in ('popup','gameplay','end'):raise ValueError('단계 종류를 확인하세요.')
        for k,limit in [('title',120),('body',2400),('speaker',80),('planning',4000)]:text(n.get(k),limit,k,k in ('title','body'))
        presentation(n)
        view=n.get('relationshipView')
        if 'relationshipView' in n:
            if not isinstance(view,dict):raise ValueError('상대 관계 표시 형식을 확인하세요.')
            text(view.get('name'),80,'상대 이름',True)
            target=view.get('targetId');keys=view.get('visibleKeys')
            if not isinstance(target,str) or target=='character.seojin' or (target not in npc_ids and target!='faction.aekmagi'):raise ValueError('관계 표시 대상은 서진 외의 NPC 또는 등록된 조직이어야 합니다.')
            if not isinstance(keys,list) or not 1<=len(keys)<=5 or any(not isinstance(k,str) or k not in ('goodwill','trust','vigilance','fear','hostility') for k in keys) or len(keys)!=len(set(keys)):raise ValueError('공개할 관계 항목을 중복 없이 지정하세요.')
        portrait=n.get('portraitNpcId')
        if portrait and portrait not in npc_ids:raise ValueError('삽화 NPC를 확인하세요.')
        if not isinstance(portrait,str):raise ValueError('삽화 NPC 형식을 확인하세요.')
        choices=n.get('choices')
        if not isinstance(choices,list) or len(choices)>30:raise ValueError('선택지 후보는 최대 30개입니다.')
        if n['kind']=='end' and choices:raise ValueError('종료 단계에는 선택지를 연결하지 않습니다.')
        if n['kind']!='end' and not choices:raise ValueError('종료 외 단계에는 다음 단계 연결이 필요합니다.')
        variants=n.get('variants',[])
        if not isinstance(variants,list) or len(variants)>30:raise ValueError('지문 후보는 최대 30개입니다.')
        vids=set()
        for v in variants:
            if not isinstance(v,dict):raise ValueError('지문 후보 형식을 확인하세요.')
            text(v.get('id'),90,'지문 후보 ID',True);text(v.get('body'),2400,'후보 지문',True)
            if v['id'] in vids:raise ValueError('지문 후보 ID가 중복되었습니다.')
            vids.add(v['id']);conditions(v);presentation(v)
        if choices and not any(not c.get('conditions') for c in choices if isinstance(c,dict)):raise ValueError('항상 표시 가능한 기본 선택지가 필요합니다.')
        if sum(bool(c.get('fallback')) for c in choices if isinstance(c,dict))>1:raise ValueError('기본 진행 선택지는 하나만 지정하세요.')
        seen=set()
        for c in choices:
            if not isinstance(c,dict):raise ValueError('선택지 형식을 확인하세요.')
            for k,limit,required in [('id',90,True),('label',160,True),('resultText',2400,False),('next',90,True),('planning',2000,False)]:text(c.get(k),limit,k,required)
            if 'hint' in c:text(c['hint'],160,'선택 전 암시 문구')
            if c['id'] in seen:raise ValueError('선택지 ID가 중복되었습니다.')
            seen.add(c['id']);targets.append(c['next'])
            rewards=c.get('rewards',[])
            if not isinstance(rewards,list) or len(rewards)>12:raise ValueError('보상 목록은 최대 12개입니다.')
            reward_ids=set()
            for r in rewards:
                if not isinstance(r,dict):raise ValueError('보상 형식을 확인하세요.')
                for k,limit in [('id',90),('targetId',120),('key',120),('label',120),('condition',1000),('note',2000)]:text(r.get(k),limit,'보상 '+k,k not in ('condition','note'))
                if r['id'] in reward_ids:raise ValueError('보상 ID가 중복되었습니다.')
                reward_ids.add(r['id'])
                if r.get('kind') not in ('trait','relationship','currency','item','experience','custom'):raise ValueError('보상 종류를 확인하세요.')
                if any(not re.fullmatch(r'[a-z][a-z0-9._-]{0,119}',r[k]) for k in ('targetId','key')):raise ValueError('보상 대상 ID와 항목 키는 영문·숫자·점·밑줄·하이픈으로 작성하세요.')
                if type(r.get('amount')) is not int or r['amount']==0 or abs(r['amount'])>1000000:raise ValueError('보상 변화량은 0이 아닌 -1000000~1000000 정수입니다.')
            if type(c.get('nextByOutcome',False)) is not bool:raise ValueError('종료 자동 연결 설정을 확인하세요.')
            conditions(c)
            if type(c.get('fallback',False)) is not bool or (c.get('fallback') and c.get('conditions')):raise ValueError('기본 진행 선택지에는 노출 조건을 지정하지 않습니다.')
            effects=c.get('effects')
            if not isinstance(effects,dict) or len(effects)>30 or any(not isinstance(k,str) or not re.fullmatch(r'[a-z][a-z0-9._-]{0,79}',k) or not isinstance(v,(str,bool,int)) or (type(v) is int and abs(v)>100000) or (isinstance(v,str) and len(v)>200) for k,v in effects.items()):raise ValueError('선택 결과 상태는 키와 짧은 값으로 작성하세요.')
    for n in nodes:
        for c in n['choices']:
            for key,value in c['effects'].items():
                if key.startswith('clue.') and (key not in clue_ids or type(value) is not bool):raise ValueError('수첩 기록은 등록된 ID와 true/false로 지정하세요.')
    if flow.get('startNodeId') not in ids or any(t not in ids for t in targets):raise ValueError('시작 단계 또는 선택지의 연결 대상이 없습니다.')
    lookup={n['id']:n for n in nodes}
    automatic=any(c.get('nextByOutcome') for n in nodes for c in n['choices'])
    if automatic:
        if not outcomes:raise ValueError('종료 자동 연결에는 결과 분기가 필요합니다.')
        endings=[o.get('endNodeId') for o in outcomes]
        if any(not isinstance(k,str) or k not in lookup or lookup[k]['kind']!='end' for k in endings) or len(endings)!=len(set(endings)):raise ValueError('각 결과에 서로 다른 종료 노드를 연결하세요.')
    def choice_targets(c):return [o['endNodeId'] for o in outcomes] if c.get('nextByOutcome') else [c['next']]
    reached=set();todo=[flow['startNodeId']]
    while todo:
        k=todo.pop()
        if k in reached:continue
        reached.add(k);todo.extend(target for c in lookup[k]['choices'] for target in choice_targets(c))
    if reached!=ids:raise ValueError('시작 단계에서 도달할 수 없는 단계가 있습니다.')
    finishing={n['id'] for n in nodes if n['kind']=='end'}
    while True:
        nxt=finishing|{n['id'] for n in nodes if any(target in finishing for c in n['choices'] for target in choice_targets(c))}
        if nxt==finishing:break
        finishing=nxt
    if finishing!=ids:raise ValueError('종료 단계로 이어지지 않는 흐름이 있습니다.')
    return flow
