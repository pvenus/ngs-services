"""Private immutable workflow stage receipts. No canonical path or browser caller."""
import base64,os,re,shutil,stat,struct,tempfile
from pathlib import Path

from .filex_read import canonical,digest
from .media_validation import png
from .prop_package_contract import sha,verify_media
from .prop_stage_claim import (
    BASE,DESIGN,DETAIL,MOTION,MOTION_DETAIL,MOTION_PIXEL,PIXEL,OFFICIAL,TASK,HASH,ref_name,text,valid_ref,
)
from .workflow_motion_2d import strict_json
from .workflow_prop_package import keys,match,need

MAX_MEDIA=16*1024*1024
MAX_SEQUENCE=40*1024*1024
MAX_METADATA=256*1024
STAGES=(DESIGN,BASE,MOTION,DETAIL,PIXEL,MOTION_DETAIL,MOTION_PIXEL)

def _fsync(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)

def _write(path,data):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())

def _read(path,max_bytes):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        s=os.fstat(f.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_uid==os.getuid() and stat.S_IMODE(s.st_mode)==0o600 and 0<s.st_size<=max_bytes)
        raw=f.read(max_bytes+1);a=os.fstat(f.fileno())
        need(len(raw)==s.st_size and s.st_size==a.st_size and s.st_mtime_ns==a.st_mtime_ns and s.st_ctime_ns==a.st_ctime_ns)
        return raw

def decode(raw,maximum):
    need(type(raw)is str and 0<len(raw)<=4*((maximum+2)//3))
    try:b=base64.b64decode(raw,validate=True)
    except (TypeError,ValueError):need(False)
    need(0<len(b)<=maximum and base64.b64encode(b).decode()==raw);return b

def png_fields(raw,*,rgb=False):
    png(raw)
    w,h,depth,color,_,_,interlace=struct.unpack_from('!IIBBBBB',raw,16)
    need(12<=w<=2048 and 12<=h<=2048 and depth==8 and not interlace and (color==2 if rgb else color in (2,6)))
    return w,h

def design_artifact(v,source):
    need(keys(v,('schemaVersion','propId','designContextSha256','title','appearancePrompt','motion','motionPrompt','interaction')) and
      v['schemaVersion']=='ngs.prop-design-spec.v1' and all(v[k]==source[k] for k in ('propId','designContextSha256','motion')) and
      type(v['title'])is str and 1<=len(v['title'])<=160 and v['title']==v['title'].strip().strip('\ufeff').strip() and
      not re.search(r'[\x00-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff]',v['title']) and text(v['appearancePrompt'],4000) and
      not re.search(r'\bembedding:',v['appearancePrompt'],re.I) and
      (v['motionPrompt'] is None if v['motion']=='still' else text(v['motionPrompt'],2000) and not re.search(r'\bembedding:',v['motionPrompt'],re.I)) and
      keys(v['interaction'],('pixelOpen','detailTrigger')))
    for r in v['interaction'].values():
        if r is None:continue
        need(keys(r,('xPermille','yPermille','widthPermille','heightPermille')) and
          all(type(n)is int and 0<=n<=1000 for n in r.values()) and
          1<=r['widthPermille']<=1000-r['xPermille'] and 1<=r['heightPermille']<=1000-r['yPermille'])
    return v

def base_artifact(v):
    need(keys(v,('media','base64')) and keys(v['media'],('sha256','bytes','width','height','mediaType')))
    m=v['media'];b=decode(v['base64'],MAX_MEDIA)
    need(m['sha256']==sha(b) and m['bytes']==len(b) and m['mediaType']=='image/png' and (m['width'],m['height'])==png_fields(b))
    return m,b

def derivative_artifact(v,animated):
    need(keys(v,('media','base64')) and keys(v['media'],('assetId','sha256','bytes','width','height','mediaType','frameDurationsMs')))
    m=v['media'];b=decode(v['base64'],MAX_MEDIA);times=m['frameDurationsMs']
    need(type(m['width'])is int and 12<=m['width']<=2048 and type(m['height'])is int and 12<=m['height']<=2048 and
      type(times)is list and m['assetId']==sha(b)+'.png' and (m['mediaType']=='image/apng' if animated else m['mediaType']=='image/png') and
      (2<=len(times)<=96 and all(type(n)is int and 10<=n<=1000 for n in times) and 100<=sum(times)<=10000 if animated else not times) and
      m['width']*m['height']*max(1,len(times))<=30_000_000)
    verify_media(b,m)
    return m,b

def raw_artifact(v,scope):
    need(keys(v,('manifest','frames')) and type(v['frames'])is list)
    m=v['manifest']
    need(keys(m,('schemaVersion','officialRequestId','taskId','propId','width','height','frameDurationsMs','frames')) and
      m['schemaVersion']=='ngs.prop-native-rgb-sequence.v1' and all(m[k]==scope[k] for k in ('officialRequestId','taskId','propId')) and
      type(m['frameDurationsMs'])is list and type(m['frames'])is list and
      2<=len(m['frames'])==len(m['frameDurationsMs'])==len(v['frames'])<=96 and
      all(type(n)is int and 10<=n<=1000 for n in m['frameDurationsMs']) and 100<=sum(m['frameDurationsMs'])<=10000 and
      all(type(m[k])is int and 12<=m[k]<=2048 for k in ('width','height')) and m['width']*m['height']*len(m['frames'])<=30_000_000)
    out={};total=0
    for i,(row,raw) in enumerate(zip(m['frames'],v['frames'])):
        need(keys(row,('index','sha256','bytes','mediaType')) and type(row['index'])is int and row['index']==i and row['mediaType']=='image/png')
        b=decode(raw,MAX_MEDIA);total+=len(b)
        need(total<=MAX_SEQUENCE and (m['width'],m['height'])==png_fields(b,rgb=True) and row['bytes']==len(b) and row['sha256']==sha(b))
        out[f'{i:03d}.png']=b
    return m,out

class PropStageStore:
    def __init__(self,root):
        p=Path(root)
        need(p.is_absolute() and p.resolve(strict=True)==p and p.is_dir())
        self.root=p;st=p.lstat();self.identity=(st.st_dev,st.st_ino)
        self._root()
    def _root(self):
        st=self.root.lstat()
        need((st.st_dev,st.st_ino)==self.identity and stat.S_ISDIR(st.st_mode) and st.st_uid==os.getuid() and stat.S_IMODE(st.st_mode)==0o700)
    def _closed_path(self,oid,task):
        self._root();need(match(oid,OFFICIAL) and match(task,TASK))
        target=self.root/oid/task
        for parent in (target.parent,target):
            if not parent.exists():
                need(not parent.is_symlink());continue
            st=parent.lstat()
            need(stat.S_ISDIR(st.st_mode) and st.st_uid==os.getuid() and stat.S_IMODE(st.st_mode)==0o700 and parent.resolve()==parent)
        return target
    def put(self,oid,task,key,artifact,source,*,lineage=None):
        need(key in STAGES)
        if key==DESIGN:
            content=design_artifact(artifact,source);files={};ref=ref_name(key,digest(content))
        elif key==MOTION:
            content,files=raw_artifact(artifact,{'officialRequestId':oid,'taskId':task,'propId':source['propId']})
            ref=ref_name(key,digest(content))
        else:
            content,raw=base_artifact(artifact) if key==BASE else derivative_artifact(artifact,key in (MOTION_DETAIL,MOTION_PIXEL))
            files={'asset.png':raw};ref=ref_name(key,sha(raw))
        record={'officialRequestId':oid,'taskId':task,'key':key,'resultRef':ref,'artifact':content,'lineage':lineage}
        target=self._closed_path(oid,task);target.parent.mkdir(mode=0o700,exist_ok=True)
        stage=Path(tempfile.mkdtemp(prefix='.pending-',dir=target.parent))
        try:
            for name,raw in files.items():_write(stage/name,raw)
            _write(stage/'receipt.json',canonical(record))
            _fsync(stage)
            try:os.rename(stage,target)
            except OSError:
                if not target.exists():raise
                need(self.get(oid,task,key,ref)==record)
            _fsync(target.parent);_fsync(self.root)
        finally:
            if stage.exists():shutil.rmtree(stage)
        return {'ok':True,'resultRef':ref}
    def get(self,oid,task,key,ref):
        need(key in STAGES and valid_ref(key,ref))
        raw=_read(self._closed_path(oid,task)/'receipt.json',MAX_METADATA);v=strict_json(raw)
        need(raw==canonical(v) and keys(v,('officialRequestId','taskId','key','resultRef','artifact','lineage')) and
          (v['officialRequestId'],v['taskId'],v['key'],v['resultRef'])==(oid,task,key,ref))
        artifact=v['artifact']
        sha_declared=digest(artifact) if key in (DESIGN,MOTION) else artifact['sha256']
        need(ref_name(key,sha_declared)==ref)
        return v
    def bytes(self,oid,task,key,ref,index=None):
        r=self.get(oid,task,key,ref);d=r['artifact']
        if key==MOTION:
            need(type(index)is int and 0<=index<len(d['frames']));d=d['frames'][index];name=f'{index:03d}.png'
        else:
            need(key!=DESIGN and index is None);name='asset.png'
        raw=_read(self._closed_path(oid,task)/name,MAX_MEDIA)
        need(len(raw)==d['bytes'] and sha(raw)==d['sha256']);return raw
