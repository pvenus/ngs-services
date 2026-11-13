"""Backwards-compatible story library: legacy root is main story, storylines are peers."""
import copy
import re
from .episode_flow import validate_flow

def upgrade(document):
    d=copy.deepcopy(document)
    d.setdefault('storyId','story.main.act01')
    d.setdefault('tags',['메인'])
    d.setdefault('npcIds',[])
    d.setdefault('storylines',[])
    return d

def validate_library(d,npc_ids):
    lines=d.get('storylines',[])
    if not isinstance(lines,list) or len(lines)>100:raise ValueError('스토리라인은 최대 100개입니다.')
    seen=set()
    for line in [d]+lines:
        if not isinstance(line,dict):raise ValueError('스토리라인 형식을 확인하세요.')
        sid=line.get('storyId')
        if not isinstance(sid,str) or not re.fullmatch(r'story\.[a-z0-9._-]{1,80}',sid) or sid in seen:raise ValueError('스토리 ID가 올바르지 않거나 중복되었습니다.')
        seen.add(sid)
        tags=line.get('tags')
        if not isinstance(tags,list) or len(tags)>12 or any(not isinstance(t,str) or not t.strip() or t!=t.strip() or len(t)>30 for t in tags) or len(set(tags))!=len(tags):raise ValueError('태그는 중복 없이 30자 이내, 최대 12개로 입력하세요.')
        ids=line.get('npcIds')
        if not isinstance(ids,list) or len(ids)>100 or any(not isinstance(i,str) or i not in npc_ids for i in ids) or len(set(ids))!=len(ids):raise ValueError('공유 NPC 연결을 확인하세요.')
        if type(line.get('masterRevision')) is not int or line['masterRevision']<1:raise ValueError('마스터 기준 개정을 확인하세요.')
        if line.get('status') not in ('proposal','confirmed'):raise ValueError('스토리 상태를 확인하세요.')
        for key in ['title','actPremise','mandatoryEvents','endingDesign']:
            v=line.get(key)
            if not isinstance(v,str) or len(v)>(120 if key=='title' else 12000) or (key=='title' and not v.strip()):raise ValueError('스토리 제목과 기획 내용을 확인하세요.')
        chapters=line.get('chapters')
        if not isinstance(chapters,list) or len(chapters)>100:raise ValueError('Chapter는 최대 100개입니다.')
        children=set()
        for chapter in chapters:
            if not isinstance(chapter,dict):raise ValueError('Chapter 형식을 확인하세요.')
            cid=chapter.get('id')
            if not isinstance(cid,str) or not re.fullmatch(r'[a-z0-9._-]{1,100}',cid) or cid in children:raise ValueError('Chapter ID가 중복되거나 올바르지 않습니다.')
            children.add(cid)
            if chapter.get('status') not in ('proposal','confirmed'):raise ValueError('Chapter 상태를 확인하세요.')
            for key in ['title','goal','continuity']:
                if not isinstance(chapter.get(key),str) or len(chapter[key])>12000:raise ValueError('Chapter 내용을 확인하세요.')
            episodes=chapter.get('episodes')
            if not isinstance(episodes,list) or len(episodes)>200:raise ValueError('Episode는 최대 200개입니다.')
            for episode in episodes:
                if not isinstance(episode,dict):raise ValueError('Episode 형식을 확인하세요.')
                eid=episode.get('id')
                if not isinstance(eid,str) or not re.fullmatch(r'[a-z0-9._-]{1,100}',eid) or eid in children:raise ValueError('Episode ID가 중복되거나 올바르지 않습니다.')
                children.add(eid)
                if episode.get('status') not in ('proposal','confirmed','review'):raise ValueError('Episode 상태를 확인하세요.')
                if 'playFlow' in episode:validate_flow(episode['playFlow'],npc_ids)
                for key in ['title','summary','sceneOutline','choices','stateEffects','reviewNotes','sourcePath']:
                    if not isinstance(episode.get(key),str) or len(episode[key])>12000:raise ValueError('Episode 내용을 확인하세요.')
    return d
