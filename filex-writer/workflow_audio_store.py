"""Exact scoped audio result; stored with one same-volume atomic folder publication."""
import base64,hashlib,io,json,os,re,shutil,stat,struct,tempfile,time,wave
from pathlib import Path
from concept_store import ConceptStore, encoded, digest
from concept_markdown import parse
from server import Conflict

WRITER_PATH='/v1/workflow/audio'
PREFIX='canonical-files/binari/audio/'
MAX_WAV=24*1024*1024
MAX_SOURCE=256*1024
DIRECT='direct'
MAX_BODY=((MAX_WAV+2)//3)*4+16384
CID=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
SHA=re.compile(r'[a-f0-9]{64}')
OFFICIAL=re.compile(r'request\.official\.[a-f0-9]{64}')
TASK=re.compile(r'workflow\.task\.[a-f0-9]{64}')

class Rejected(ValueError):
    """Definitively did not start a result write."""

def require(condition):
    if not condition:raise Rejected('audio workflow scope or input')

def match(value,pattern):return type(value) is str and pattern.fullmatch(value) is not None

def sha(raw):return hashlib.sha256(raw).hexdigest()

def fsync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)

def bytes_of(path,maximum):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as file:
        before=os.fstat(file.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or not 0<before.st_size<=maximum:
            raise Conflict('audio file shape')
        raw=file.read(maximum+1)
        after=os.fstat(file.fileno())
        if (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns) or len(raw)!=after.st_size:
            raise Conflict('audio file changed')
        return raw

def wav_artifact(artifact,duration):
    require(type(artifact) is dict and set(artifact) in ({'mimeType','sha256','bytes','base64'},{'mimeType','sha256','bytes','base64','bpm'})
            and (artifact.get('bpm') is None or type(artifact['bpm']) is int and 30<=artifact['bpm']<=300)
            and artifact['mimeType']=='audio/wav' and match(artifact['sha256'],SHA)
            and type(artifact['bytes']) is int and 44<=artifact['bytes']<=MAX_WAV
            and type(artifact['base64']) is str and len(artifact['base64'])==((artifact['bytes']+2)//3)*4)
    try:
        raw=base64.b64decode(artifact['base64'],validate=True)
        require(len(raw)==artifact['bytes'] and sha(raw)==artifact['sha256']
                and raw[:4]==b'RIFF' and raw[8:12]==b'WAVE' and int.from_bytes(raw[4:8],'little')==len(raw)-8)
        offset=12;seen=[];data_size=None
        while offset<len(raw):
            require(offset+8<=len(raw))
            tag=raw[offset:offset+4];size=int.from_bytes(raw[offset+4:offset+8],'little')
            start=offset+8;end=start+size
            require(end+(size%2)<=len(raw) and tag in (b'fmt ',b'data') and tag not in seen)
            if tag==b'fmt ':
                require(not seen and size==16)
                codec,channels,rate,bytes_per_second,align,bits=struct.unpack_from('<HHIIHH',raw,start)
                require(codec==1 and channels in (1,2) and 8000<=rate<=96000 and bits==16
                        and align==channels*2 and bytes_per_second==rate*align)
            if tag==b'data':
                require(b'fmt ' in seen and size>0 and size%align==0)
                data_size=size
            seen.append(tag);offset=end+(size%2)
        require(offset==len(raw) and b'fmt ' in seen and b'data' in seen)
        with wave.open(io.BytesIO(raw),'rb') as pcm:
            channels,width,hz,frames,compression,_=pcm.getparams()
            require(compression=='NONE' and channels in (1,2) and width==2 and 8000<=hz<=96000 and frames>0
                    and len(pcm.readframes(frames))==data_size==frames*channels*width)
        seconds=frames/hz
        require(10<=seconds<=120 if duration is None else abs(seconds-duration)<=0.101)
    except (ValueError,TypeError,wave.Error,struct.error,EOFError) as exc:
        raise Rejected('audio workflow WAV invalid') from exc
    return raw,{'sha256':artifact['sha256'],'bytes':len(raw),'mimeType':'audio/wav',
                'sampleRate':hz,'channels':channels,'bitsPerSample':16,'frames':frames,'durationSeconds':seconds}

class AudioResult:
    def __init__(self, store, root):
        if not isinstance(store,ConceptStore):raise ValueError('audio concept authority')
        path=Path(root)
        if not path.is_absolute() or path.name!='audio' or path.resolve(strict=True)!=path or not path.is_dir() or path==store.root:
            raise ValueError('audio result root')
        if store.root in path.parents or path in store.root.parents:
            raise ValueError('audio result tree scope')
        self.store,self.root=store,path
        self.identity=path.stat().st_dev,path.stat().st_ino

    def check_root(self):
        st=self.root.lstat()
        if (not stat.S_ISDIR(st.st_mode) or (st.st_dev,st.st_ino)!=self.identity or
            os.statvfs(self.root).f_flag & os.ST_RDONLY or not os.access(self.root,os.W_OK|os.X_OK)):
            raise Conflict('audio result root changed')

    def concept_locked(self,cid):
        require(match(cid,CID) and cid!=DIRECT)
        root_path=self.store.path(self.store.namespace+'metadata.json')
        root=json.loads(bytes_of(root_path,4*1024*1024))
        hierarchy=root['document']['hierarchy']
        grouped=[g['id'] for g in hierarchy['groups'] if cid in g['categoryIds']]
        require(root['sectionIds'].count(cid)==1 and grouped==['group.sound'] and cid not in hierarchy.get('archivedCategoryIds',[]))
        archived={x['itemId'] for x in hierarchy.get('archivedItems',[]) if x['categoryId']==cid}
        marker=root.get('_markdownSections',{}).get(cid)
        base='sections/'+cid+'/'
        md=self.store.md_path(cid)
        if marker is not None:
            if self.store.path(self.store.namespace+base+'metadata.json').exists():
                raise Conflict('audio concept has conflicting authorities')
            raw=bytes_of(md,1024*1024)
            require(sha(raw)==marker['sha256'] and marker['mode'] in ('authored','structured'))
            text=raw.decode('utf-8'); title=marker['title']; physical=marker['sha256']
            if marker['mode']=='authored':
                require('content' not in archived and bool(text.strip()))
                return self.source(cid,title,text,physical)
            section,fields=parse(text) or ({},[])
            require(section.get('id')==cid and section.get('title')==title)
        else:
            if md.exists():raise Conflict('audio concept has conflicting authorities')
            section=json.loads(bytes_of(self.store.path(self.store.namespace+base+'metadata.json'),128*1024))
            require(section['id']==cid)
            title=section['title'];physical=None;fields=[]
            for item in section['fieldIds']:
                require(match(item,CID))
                if item in archived:continue
                f=json.loads(bytes_of(self.store.path(self.store.namespace+base+'fields/'+item+'.json'),1024*1024))
                require(f['id']==item);fields.append(f)
        # Render only active content from the selected category, in canonical order.
        lines=['# '+title]
        if section.get('description'):lines.append(section['description'])
        for field in fields:
            if field['id'] not in archived:lines.extend(('## '+field['label'],field['value']))
        return self.source(cid,title,'\n\n'.join(lines)+'\n',physical)

    @staticmethod
    def source(cid,title,content,physical):
        require(type(title) is str and bool(title.strip()) and len(title.encode('utf-8'))<=600
                and type(content) is str and bool(content.strip()))
        require(len(content.encode('utf-8'))<=MAX_SOURCE)
        return {'conceptId':cid,'sectionSha256':digest([cid,'group.sound',title,content,physical]),
                'title':title,'content':content}

    def read(self,value):
        require(type(value) is dict and set(value)=={'conceptId'})
        with self.store.lock():
            self.check_root()
            return self.concept_locked(value['conceptId'])

    def verify_removable_result(self,path,oid):
        if path.is_symlink() or not path.is_dir() or {p.name for p in path.iterdir()}!={'metadata.json','audio.wav'}:
            raise Conflict('audio delete target shape')
        meta=json.loads(bytes_of(path/'metadata.json',32768))
        raw=bytes_of(path/'audio.wav',MAX_WAV)
        require(meta.get('schemaVersion')=='workflow.audio-result.v1' and meta.get('officialRequestId')==oid
                and meta.get('projectId')=='binari' and meta.get('taskId')=='workflow.task.'+digest([oid,'audio_generate'])
                and (meta['audio']['path'],meta['audio']['bytes'],meta['audio']['sha256'])==('audio.wav',len(raw),sha(raw)))
        return meta

    def remove_result(self,command):
        from single_target_delete import SingleTargetDelete,validate_target
        require(type(command) is dict and set(command)=={'operationId','target'} and match(command['operationId'],TASK))
        target=validate_target(command['target']);oid=target['path']
        require(target=={'root':PREFIX.rstrip('/'),'path':oid,'kind':'directory'} and match(oid,OFFICIAL))
        with self.store.lock():
            self.check_root()
            path=self.root/oid
            if path.is_symlink() or path.exists():self.verify_removable_result(path,oid)
            # Use the same one-folder mover and lock as the shared workflow
            # primitives; archive ownership is local configuration, not wire.
            mover=SingleTargetDelete(self,PREFIX.rstrip('/'),archive='.deleted')
            ack=mover.remove_locked(command)
            archive=self.root/'.deleted'/ack['recoveryId']/'target'
            if mover.verify_recovery_locked(command,ack['recoveryId']) is not True:raise Conflict('audio recovery unverified')
            self.verify_removable_result(archive,oid)
            return ack

    def existing(self,path,meta,raw):
        if path.is_symlink() or not path.is_dir() or {p.name for p in path.iterdir()}!={'metadata.json','audio.wav'}:
            raise Conflict('audio existing result shape')
        persisted=bytes_of(path/'metadata.json',32768)
        previous=json.loads(persisted)
        if type(previous) is not dict or persisted!=encoded(previous):
            raise Conflict('audio result metadata invalid')
        if 'generatedAt' in previous and (type(previous['generatedAt']) is not int or not 0<=previous['generatedAt']<=2**53-1):
            raise Conflict('audio result timestamp invalid')
        if (('generatedAt' in meta and previous.get('generatedAt')!=meta['generatedAt'])
            or {k:v for k,v in previous.items() if k!='generatedAt'}!={k:v for k,v in meta.items() if k!='generatedAt'}
            or bytes_of(path/'audio.wav',MAX_WAV)!=raw):
            raise Conflict('audio result identity reused')
        fsync_dir(path);fsync_dir(self.root)
        return {'ok':True,'resultRef':PREFIX+meta['officialRequestId']+'/metadata.json'}

    def publish(self,value):
        require(type(value) is dict and set(value)=={'binding','source','artifact'})
        b=value['binding'];s=value['source']
        fields={'officialRequestId','taskId','projectId','payloadSha256','kind','title','conceptId','durationSeconds'}
        direct=type(b) is dict and b.get('conceptId')==DIRECT
        require(type(b) is dict and set(b)==(fields|{'directConceptSha256'} if direct else fields)
                and b['projectId']=='binari' and match(b['officialRequestId'],OFFICIAL) and match(b['taskId'],TASK)
                and b['taskId']=='workflow.task.'+digest([b['officialRequestId'],'audio_generate'])
                and match(b['payloadSha256'],SHA) and match(b['conceptId'],CID)
                and b['kind']=='bgm' and type(b['title']) is str and 1<=len(b['title'])<=120
                and (b['durationSeconds'] is None or type(b['durationSeconds']) in (int,float) and 10<=b['durationSeconds']<=30)
                and type(s) is dict and set(s)=={'conceptId','sectionSha256'} and s['conceptId']==b['conceptId'] and match(s['sectionSha256'],SHA))
        if direct:
            require(match(b['directConceptSha256'],SHA) and b['directConceptSha256']==s['sectionSha256'])
        raw,info=wav_artifact(value['artifact'],b['durationSeconds'])
        bpm=value['artifact'].get('bpm')
        meta={'schemaVersion':'workflow.audio-result.v1',**{k:b[k] for k in fields},'source':s,'bpm':bpm,'bpmBasis':'model' if bpm is not None else 'unavailable',
              'audio':{'path':'audio.wav',**info}}
        path=self.root/b['officialRequestId']
        with self.store.lock():
            self.check_root()
            if path.exists() or path.is_symlink():return self.existing(path,meta,raw)
            if not direct:
                current=self.concept_locked(b['conceptId'])
                require(current['sectionSha256']==s['sectionSha256'])
            meta['generatedAt']=time.time_ns()//1_000_000
            stage=Path(tempfile.mkdtemp(prefix='.audio-stage-',dir=self.root))
            try:
                for name,data in (('audio.wav',raw),('metadata.json',encoded(meta))):
                    with (stage/name).open('xb') as f:
                        f.write(data);f.flush();os.fsync(f.fileno())
                fsync_dir(stage)
                # Same lock is the only writer authority for this mounted root.
                if path.exists() or path.is_symlink():raise Conflict('audio result concurrently occupied')
                os.rename(stage,path);fsync_dir(self.root)
            finally:
                if stage.exists():shutil.rmtree(stage)
            return self.existing(path,meta,raw)
