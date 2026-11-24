"""Claim-bound 3D-to-2D motion staging and one Filex catalog publication."""
import base64
from contextlib import contextmanager
import fcntl
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import struct
import subprocess
import tempfile
import zlib

from .filex_read import canonical, digest, private_read
from .motion_presets import ID, MotionPresets
from .filex_cas_transport import FilexCASTransport
from .filex_writer_binding import scope_config
from .workflow_delete_claim import ClaimDenied, PrivatePlannerClaim

PATH = '/v1/motion-presets/workflow-convert-2d'
REQUEST = 'motion-preset.convert-2d'
SCHEMA = 'ngs.motion-preset-convert-2d.v1'
CAPABILITY = 'workflow.motion-preset-convert-2d.v1'
FRAME_CAPABILITY = 'workflow.motion-preset-convert-2d.frames.v1'
FRAMES = PATH+'/frames'
FRAME_READ = FRAMES+'/read'
ROUTES = (PATH, PATH+'/read', FRAMES, FRAME_READ)
IMAGE = 'motion_2d_image'
FINAL = 'motion_2d_finalize'
DIRECTIONS = ('n','ne','e','se','s','sw','w','nw')
CLAIM = {'officialRequestId','taskId','projectId','payloadSha256','claimStamp'}
OFFICIAL = re.compile(r'request\.official\.[a-f0-9]{64}')
HASH = re.compile(r'[a-f0-9]{64}')
VARIANT = re.compile(r'[a-z][a-z0-9-]{1,63}')
SAFE_INT = 2**53-1
FFPROBE = Path('/opt/homebrew/bin/ffprobe')
JS_TRIM = '\t\n\r\v\f \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
MAX_IMAGE = 8*1024*1024
MAX_REFERENCE = 64*1024*1024
MAX_OUTPUT = 8*1024*1024
MAX_FRAME = 8*1024*1024
MAX_PACK = 64*1024*1024
MAX_FRAMES = 1000
MAX_HEADER = 128*1024
MAX_PACKET = 4+MAX_HEADER+MAX_PACK
MAX_BODY = ((max(MAX_IMAGE,MAX_OUTPUT)+2)//3)*4+8192
PNG = b'\x89PNG\r\n\x1a\n'


class Rejected(ValueError):
    pass


def require(ok):
    if not ok:
        raise Rejected('MOTION_2D_REJECTED')


def match(value,pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def sha(data):
    return hashlib.sha256(data).hexdigest()


def task_id(oid,key):
    return 'workflow.task.'+digest([oid,key])


def frames_key(direction):
    return 'motion_2d_frames_'+direction


def encode_key(direction):
    return 'motion_2d_encode_'+direction


FRAME_DIRECTIONS = {frames_key(d):d for d in DIRECTIONS}
ENCODE_DIRECTIONS = {encode_key(d):d for d in DIRECTIONS}
ALL_DIRECTIONS = {**FRAME_DIRECTIONS, **ENCODE_DIRECTIONS}
TASKS = (IMAGE, FINAL, *ALL_DIRECTIONS)


def type_for(key):
    if key==IMAGE:return REQUEST+'.image','image'
    if key==FINAL:return REQUEST+'.finalize','postprocess'
    if key in FRAME_DIRECTIONS:return REQUEST+'.frames','animation'
    require(key in ENCODE_DIRECTIONS)
    return REQUEST+'.encode','postprocess'


def payload(value):
    require(type(value) is dict and set(value)=={'schemaVersion','projectId','sourcePresetId','variantPresetId','label'}
            and value['schemaVersion']==SCHEMA and value['projectId']=='binari'
            and match(value['sourcePresetId'],ID) and match(value['variantPresetId'],VARIANT)
            and value['sourcePresetId'].lower()!=value['variantPresetId'].lower())
    label=value['label']
    require(type(label) is str and 1<=len(label)<=120 and label==label.strip(JS_TRIM)
            and not re.search(r'[\x00-\x1f\x7f-\x9f\ud800-\udfff\u2028\u2029]',label))
    return value


def expected_input(value,oid,key):
    source={'sourcePresetId':value['sourcePresetId'],'variantPresetId':value['variantPresetId']}
    if key==FINAL:
        return {**source,'dependsOnTaskIds':[task_id(oid,encode_key(d)) for d in DIRECTIONS]}
    if key==IMAGE:return source
    return {**source,'direction':ALL_DIRECTIONS[key]}


def expected_parent(oid,key):
    if key in (IMAGE,FINAL):return None
    parent=IMAGE if key in FRAME_DIRECTIONS else frames_key(ENCODE_DIRECTIONS[key])
    return task_id(oid,parent)


def get_row(db,oid,key):
    kind,worker=type_for(key)
    return db.execute('''SELECT task_input,depends_on_task_id,status,result_ref,updated_at
        FROM workflow_tasks WHERE official_request_id=? AND task_id=?
        AND task_key=? AND task_type=? AND worker_type=?''',
        (oid,task_id(oid,key),key,kind,worker)).fetchone()


def valid_input(row,value,oid,key):
    return (row is not None and row['task_input']==canonical(expected_input(value,oid,key)).decode()
            and row['depends_on_task_id']==expected_parent(oid,key))


def predecessor(db,oid,p,key):
    row=get_row(db,oid,key)
    require(valid_input(row,p,oid,key) and row['status']=='completed' and type(row['result_ref']) is str)
    return row['result_ref'],row['updated_at']


def run_info(db,claim):
    row=db.execute('''SELECT project_id,request_type,schema_version,payload,payload_sha256,state,reason,registered_at
        FROM workflow_runs WHERE official_request_id=?''',(claim['officialRequestId'],)).fetchone()
    require(row is not None and row['project_id']=='binari' and row['request_type']==REQUEST
            and row['schema_version']==SCHEMA and row['state']=='planned' and row['reason'] is None
            and type(row['registered_at']) is int and 0<=row['registered_at']<=claim['claimStamp']
            and row['payload_sha256']==claim['payloadSha256'])
    p=payload(json.loads(row['payload']))
    require(row['payload']==canonical(p).decode() and digest(p)==claim['payloadSha256'])
    return p


@contextmanager
def claimed(verifier,claim):
    require(type(claim) is dict and set(claim)==CLAIM and match(claim['officialRequestId'],OFFICIAL)
            and claim['projectId']=='binari' and match(claim['payloadSha256'],HASH)
            and type(claim['claimStamp']) is int and 0<=claim['claimStamp']<=SAFE_INT)
    oid=claim['officialRequestId']
    keys={task_id(oid,k):k for k in TASKS}
    require(type(claim['taskId']) is str and claim['taskId'] in keys)
    key=keys[claim['taskId']]
    with verifier.snapshot() as db:
        p=run_info(db,claim)
        row=get_row(db,oid,key)
        require(valid_input(row,p,oid,key) and row['status']=='running' and row['result_ref'] is None
                and row['updated_at']==claim['claimStamp'])
        refs={}
        if key in FRAME_DIRECTIONS:
            refs[IMAGE]=predecessor(db,oid,p,IMAGE)
        elif key in ENCODE_DIRECTIONS:
            frame_key=frames_key(ENCODE_DIRECTIONS[key])
            refs[frame_key]=predecessor(db,oid,p,frame_key)
        elif key==FINAL:
            refs.update((encode_key(d),predecessor(db,oid,p,encode_key(d))) for d in DIRECTIONS)
        yield oid,key,p,refs


def private_dir(path):
    require(path.resolve()==path)
    attrs=path.lstat()
    require(stat.S_ISDIR(attrs.st_mode) and attrs.st_uid==os.getuid() and stat.S_IMODE(attrs.st_mode)==0o700)


def private_file(path,limit):
    require(path.parent.resolve()==path.parent)
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
        a=os.fstat(stream.fileno())
        require(stat.S_ISREG(a.st_mode) and a.st_uid==os.getuid() and stat.S_IMODE(a.st_mode)==0o600
                and a.st_nlink==1 and 0<a.st_size<=limit)
        data=stream.read(limit+1)
        b=path.lstat()
        require(len(data)==a.st_size and (a.st_dev,a.st_ino,a.st_mtime_ns)==(b.st_dev,b.st_ino,b.st_mtime_ns))
        return data


def sync(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def save(path,data,limit,guard):
    if os.path.lexists(path):
        require(private_file(path,limit)==data)
        with guard():
            pass
        return
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.new-')
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        with guard():
            os.replace(tmp,path)
        sync(path.parent)
    finally:
        if os.path.lexists(tmp):os.unlink(tmp)


@contextmanager
def staging(root,oid,*,create,direction=None):
    if create:
        require(root.parent.resolve()==root.parent)
        root.mkdir(mode=0o700,exist_ok=True)
    private_dir(root)
    path=root/oid
    if create:
        path.mkdir(mode=0o700,exist_ok=True)
        sync(root)
    private_dir(path)
    lockpath=path/('.lock' if direction is None else '.'+direction+'.lock')
    lock=os.open(lockpath,os.O_CREAT|os.O_RDWR|os.O_NONBLOCK|os.O_NOFOLLOW,0o600)
    try:
        a=os.fstat(lock)
        require(stat.S_ISREG(a.st_mode) and a.st_uid==os.getuid() and a.st_nlink==1 and stat.S_IMODE(a.st_mode)==0o600)
        fcntl.flock(lock,fcntl.LOCK_EX)
        private_dir(path)
        require(lockpath.lstat().st_ino==a.st_ino)
        yield path
    finally:
        os.close(lock)


def artifact_bytes(artifact,limit):
    require(type(artifact) is dict and set(artifact)=={'base64','sha256'}
            and match(artifact['sha256'],HASH) and type(artifact['base64']) is str
            and 0<len(artifact['base64'])<=((limit+2)//3)*4)
    raw=base64.b64decode(artifact['base64'],validate=True)
    require(0<len(raw)<=limit and sha(raw)==artifact['sha256'])
    return raw


def validate_png(data):
    require(len(data)>=45 and data.startswith(PNG))
    at=8; image_data=False
    while at+12<=len(data):
        size=struct.unpack_from('!I',data,at)[0]
        end=at+12+size
        require(end<=len(data))
        chunk=data[at+4:at+8]
        require(re.fullmatch(b'[A-Za-z]{4}',chunk) is not None
                and zlib.crc32(data[at+4:end-4])==struct.unpack_from('!I',data,end-4)[0])
        if at==8:
            require(chunk==b'IHDR' and size==13)
            w,h=struct.unpack_from('!II',data,at+8)
            require(1<=w<=4096 and 1<=h<=4096 and w*h<=8*1024*1024)
        elif chunk==b'IEND':
            require(image_data and size==0 and end==len(data))
            return w,h
        elif chunk==b'IDAT':
            image_data |= size>0
        elif chunk in (b'IHDR',b'acTL',b'fcTL',b'fdAT'):
            require(False)
        at=end
    require(False)


def asset_ref(key,raw):
    stem='image' if key==IMAGE else 'clip.'+ENCODE_DIRECTIONS[key]
    return 'motion.2d.'+stem+'.'+sha(raw)


def strict_json(raw):
    def unique(pairs):
        result=dict(pairs)
        require(len(result)==len(pairs))
        return result
    return json.loads(raw.decode('utf-8'),object_pairs_hook=unique)


def frame_sizes(records,count):
    require(type(records) is list and len(records)==count and 2<=count<=MAX_FRAMES)
    total=0
    for row in records:
        require(type(row) is dict and set(row)=={'bytes','sha256'} and type(row['bytes']) is int
                and 0<row['bytes']<=MAX_FRAME and match(row['sha256'],HASH))
        total+=row['bytes']
    require(total<=MAX_PACK)
    return total


def framing(raw):
    require(0<len(raw)<=MAX_HEADER)
    return struct.pack('!I',len(raw))+raw


def read_exact(reader,size):
    data=reader.read(size)
    require(len(data)==size)
    return data


def incoming_header(reader,size):
    require(4<size<=MAX_PACKET)
    length=struct.unpack('!I',read_exact(reader,4))[0]
    require(0<length<=MAX_HEADER and length<size-4)
    meta=strict_json(read_exact(reader,length))
    require(type(meta) is dict and set(meta)=={'claim','frames'})
    return meta,size-4-length


def frames_ref(meta):
    proof={k:meta[k] for k in ('direction','sourcePresetHash','width','height','frames')}
    return 'motion.2d.frames.'+proof['direction']+'.'+digest(proof)


def packet_metadata(direction, source_hash, row, width, height, frames):
    return dict(direction=direction,sourcePresetHash=source_hash,fps=row['fps'],
                width=width,height=height,frames=frames)


def verify_packet(blob,direction,source_hash,row,*,verify_frames):
    require(len(blob)>4)
    length=struct.unpack_from('!I',blob)[0]
    require(0<length<=MAX_HEADER and len(blob)>4+length)
    meta=strict_json(blob[4:4+length])
    require(type(meta) is dict and set(meta)=={'direction','sourcePresetHash','fps','width','height','frames'})
    require(meta==packet_metadata(direction,source_hash,row,meta['width'],meta['height'],meta['frames'])
            and all(type(meta[k]) is int and 1<=meta[k]<=4096 for k in ('width','height'))
            and meta['width']*meta['height']<=8*1024*1024
            and frame_sizes(meta['frames'],row['frameCount'])==len(blob)-4-length)
    if verify_frames:
        offset=4+length
        raw=memoryview(blob)
        for item in meta['frames']:
            require(sha(raw[offset:offset+item['bytes']])==item['sha256'])
            offset+=item['bytes']
    return meta


class FFProbe:
    """Pinned local media metadata reader; no remote or external MOV references."""
    def __init__(self,executable):
        path=Path(executable)
        require(path.is_absolute() and path.is_file() and os.access(path,os.X_OK))
        self.path=path.resolve(strict=True)

    def __call__(self,path):
        result=subprocess.run([str(self.path),'-v','error','-protocol_whitelist','file','-enable_drefs','0',
             '-f','mov','-show_entries','stream=codec_type,nb_frames,avg_frame_rate,duration,width,height',
             '-of','json','-i',str(path)],check=False,timeout=15,stdin=subprocess.DEVNULL,
             stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env={'PATH':'/usr/bin:/bin'})
        require(result.returncode==0 and 0<len(result.stdout)<=16384)
        streams=json.loads(result.stdout)['streams']
        require(type(streams) is list and len(streams)==1 and streams[0].get('codec_type')=='video')
        s=streams[0]
        return {'frames':int(s['nb_frames']),'fps':float(Fraction(s['avg_frame_rate'])),
                'duration':float(s['duration']),'width':s['width'],'height':s['height']}


def check_clip_timing(meta,row):
    target_duration=row.get('durationSeconds',row['frameCount']/row['fps'])
    require(type(meta) is dict and meta['frames']==row['frameCount']
            and math.isclose(meta['fps'],row['fps'],rel_tol=1e-6,abs_tol=1e-6)
            and math.isclose(meta['duration'],target_duration,rel_tol=1e-6,abs_tol=.01)
            and all(type(meta[k]) is int and 1<=meta[k]<=8192 for k in ('width','height')))
    return {k:meta[k] for k in ('width','height')}


class WorkflowMotion2D:
    def __init__(self,motion,verifier,root,probe):
        require(isinstance(motion,MotionPresets) and isinstance(verifier,PrivatePlannerClaim) and callable(probe))
        self.motion,self.verifier,self.root,self.probe=motion,verifier,Path(root),probe

    def capability(self,*,frames=False):
        with self.verifier.snapshot():pass
        self.motion.snapshot()
        return {'enabled':True,'schemaVersion':FRAME_CAPABILITY if frames else CAPABILITY}

    def basis(self,folder,p,*,create,guard):
        snapshot=self.motion.snapshot()
        source=snapshot['documents'].get('presets/'+p['sourcePresetId']+'.json')
        require(source is not None and 'variant' not in source)
        source_hash=digest(source)
        pin=folder/'basis.json'
        if create and not os.path.lexists(pin):
            require('presets/'+p['variantPresetId']+'.json' not in snapshot['documents'])
            save(pin,canonical({'sourcePresetHash':source_hash,'payloadSha256':digest(p)}),4096,guard)
        require(json.loads(private_file(pin,4096))=={'sourcePresetHash':source_hash,'payloadSha256':digest(p)})
        return source_hash,source

    def check_clip(self,folder,key,raw,source):
        direction=ALL_DIRECTIONS[key]
        row=next(x for x in source['directions'] if x['direction']==direction)
        require(len(raw)>12 and raw[4:8]==b'ftyp' and sha(raw)!=row['sha256'])
        fd,tmp=tempfile.mkstemp(dir=folder,prefix='.probe-',suffix='.mp4')
        try:
            with os.fdopen(fd,'wb') as stream:stream.write(raw)
            return check_clip_timing(self.probe(Path(tmp)),row)
        finally:os.unlink(tmp)

    @contextmanager
    def session(self,claim,*,keys,read=False):
        # Validate and detach before taking any file lock or moving media.
        with claimed(self.verifier,claim) as expected:
            oid,key,p,refs=expected
            require(key in keys)

        @contextmanager
        def guard():
            with claimed(self.verifier,claim) as fresh:
                require(fresh==expected)
                yield

        creates=key==IMAGE and read
        with staging(self.root,oid,create=creates,direction=ALL_DIRECTIONS.get(key)) as folder:
            source_hash,source=self.basis(folder,p,create=creates,guard=guard)
            yield oid,key,p,refs,guard,folder,source_hash,source

    @staticmethod
    def source_direction(source,key):
        direction=ALL_DIRECTIONS[key]
        return direction,next(row for row in source['directions'] if row['direction']==direction)

    @staticmethod
    def frame_image(folder,refs):
        image=private_file(folder/'image.png',MAX_IMAGE)
        require(refs[IMAGE][0]==asset_ref(IMAGE,image))
        return image

    def write_frames(self,reader,length):
        meta,body_size=incoming_header(reader,length)
        with self.session(meta['claim'],keys=FRAME_DIRECTIONS) as (_,key,_,refs,guard,folder,source_hash,source):
            direction,row=self.source_direction(source,key)
            self.frame_image(folder,refs)
            records=meta['frames']
            require(frame_sizes(records,row['frameCount'])==body_size)
            path=folder/(direction+'.frames')
            fd,tmp=tempfile.mkstemp(dir=folder,prefix='.frames-')
            try:
                with os.fdopen(fd,'wb') as out:
                    geometry=None
                    for frame in records:
                        raw=read_exact(reader,frame['bytes'])
                        require(sha(raw)==frame['sha256'])
                        got=validate_png(raw)
                        if geometry is None:
                            geometry=got
                            packet=packet_metadata(direction,source_hash,row,*geometry,records)
                            out.write(framing(canonical(packet)))
                        require(got==geometry)
                        out.write(raw)
                    out.flush()
                    os.fsync(out.fileno())
                if os.path.lexists(path):
                    old=verify_packet(private_file(path,MAX_PACKET),direction,source_hash,row,verify_frames=True)
                    require(old==packet)
                    with guard():pass
                else:
                    with guard():os.replace(tmp,path)
                    sync(folder)
                return {'ok':True,'resultRef':frames_ref(packet)}
            finally:
                if os.path.lexists(tmp):os.unlink(tmp)

    def frame_packet(self,folder,source_hash,source,key,refs,*,verify_frames):
        direction,row=self.source_direction(source,key)
        blob=private_file(folder/(direction+'.frames'),MAX_PACKET)
        packet=verify_packet(blob,direction,source_hash,row,verify_frames=verify_frames)
        require(refs[frames_key(direction)][0]==frames_ref(packet))
        return blob,packet

    def read_frames(self,body):
        require(type(body) is dict and set(body)=={'claim'})
        with self.session(body['claim'],keys=ENCODE_DIRECTIONS) as (_,key,_,refs,guard,folder,source_hash,source):
            packet,_=self.frame_packet(folder,source_hash,source,key,refs,verify_frames=True)
            with guard():return packet

    def run(self,body,*,read,started):
        require(type(body) is dict and set(body)==({'claim'} if read else {'claim','artifact'}))
        keys=(IMAGE,FINAL,*FRAME_DIRECTIONS) if read else (IMAGE,FINAL,*ENCODE_DIRECTIONS)
        with self.session(body['claim'],keys=keys,read=read) as (oid,key,p,refs,guard,folder,source_hash,source):
            proof={'sourcePresetHash':source_hash}
            if key==IMAGE:
                if read:
                    with guard():return proof
                raw=artifact_bytes(body['artifact'],MAX_IMAGE)
                validate_png(raw)
                save(folder/'image.png',raw,MAX_IMAGE,guard)
                return {'ok':True,'resultRef':asset_ref(key,raw)}
            if key in FRAME_DIRECTIONS:
                direction,row=self.source_direction(source,key)
                require(row['frameCount']<=MAX_FRAMES)
                image=self.frame_image(folder,refs)
                ref=self.motion.get(p['sourcePresetId'],direction,
                                    expected_preset_hash=source_hash,max_asset_bytes=MAX_REFERENCE)
                require(ref['presetHash']==source_hash)
                answer={'direction':direction,**proof,'reference':ref,
                        'image':{'sha256':sha(image),'bytes':len(image),'base64':base64.b64encode(image).decode()}}
                with guard():return answer
            if key in ENCODE_DIRECTIONS:
                direction=ENCODE_DIRECTIONS[key]
                _,packet=self.frame_packet(folder,source_hash,source,key,refs,verify_frames=False)
                raw=artifact_bytes(body['artifact'],MAX_OUTPUT)
                dimensions=self.check_clip(folder,key,raw,source)
                require(dimensions=={k:packet[k] for k in ('width','height')})
                save(folder/(direction+'.mp4'),raw,MAX_OUTPUT,guard)
                return {'ok':True,'resultRef':asset_ref(key,raw)}
            if read:
                with guard():return proof
            require(type(body['artifact']) is dict and not body['artifact'])
            rows=[]
            for row in source['directions']:
                direction=row['direction'];clip_key=encode_key(direction)
                raw=private_file(folder/(direction+'.mp4'),MAX_OUTPUT)
                require(refs[clip_key][0]==asset_ref(clip_key,raw))
                dimensions=self.check_clip(folder,clip_key,raw,source)
                rows.append({**row,**dimensions,'assetId':sha(raw)+'.mp4','sha256':sha(raw),'bytes':len(raw)})
            variant={**source,'presetId':p['variantPresetId'],'label':p['label'],
                     'variant':{'kind':'2d','sourcePresetId':p['sourcePresetId'],'sourcePresetHash':source_hash},
                     'directions':rows}
            started.append(True)
            result=self.motion.publish_2d(oid,source,variant,lambda d:private_file(folder/(d+'.mp4'),MAX_OUTPUT),guard)
            require(result.endswith('/presets/'+p['variantPresetId']+'.json'))
            with guard():return {'ok':True,'resultRef':'motion.2d.published.'+digest(variant)}


def configured(verifier,storage_root,host,mode,*,config_path=None,executable=None):
    """Discover the already-approved local motion+Planner ports, never add a credential.

    An explicit 0 disables the feature. Automatic discovery failing leaves the
    old startup unchanged, and the capability route deliberately stays 404.
    """
    if mode not in ('auto','0','1'):
        raise ValueError('MOTION_2D_OPT_IN')
    if mode=='0':
        return None
    executable=FFPROBE if executable is None else Path(executable)
    try:
        require(host=='127.0.0.1' and isinstance(verifier,PrivatePlannerClaim))
        config=scope_config('motion',source=config_path)
        with FilexCASTransport(config):pass  # Verify private credential without contacting Filex on startup.
        motion=MotionPresets(lambda:FilexCASTransport(scope_config('motion',source=config_path)))
        st=executable.resolve(strict=True).lstat()
        require(stat.S_ISREG(st.st_mode) and st.st_uid in (0,os.getuid()) and st.st_nlink==1
                and not st.st_mode&0o022)
        return WorkflowMotion2D(motion,verifier,storage_root.resolve()/'workflow-motion-convert-2d',
                                FFProbe(executable))
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        if mode=='1':raise ValueError('MOTION_2D_EXISTING_PORTS_UNAVAILABLE') from None
        return None


def serve(handler):
    service=getattr(handler.server,'workflow_motion_2d',None)
    if not isinstance(service,WorkflowMotion2D):
        return handler.respond(404,{'error':'MOTION_2D_DISABLED'})
    route=(handler.command,handler.path)
    if (handler.server.server_address[0]!='127.0.0.1' or handler.client_address[0]!='127.0.0.1'
            or handler.headers.get_all('Host')!=['127.0.0.1:'+str(handler.server.server_port)]
            or handler.headers.get_all('Origin') is not None or handler.headers.get_all('Transfer-Encoding') is not None
            or handler.headers.get_all('Content-Encoding') is not None
            or route not in (('GET',PATH),('GET',FRAMES),*(('POST',path) for path in ROUTES))):
        return handler.respond(403,{'error':'MOTION_2D_INTERNAL_ONLY'})
    started=[]
    try:
        if handler.command=='GET':
            return handler.respond(200,service.capability(frames=handler.path==FRAMES))
        handler.connection.settimeout(20)
        size=handler.headers.get_all('Content-Length',[])
        binary=handler.path==FRAMES
        expect='application/octet-stream' if binary else 'application/json'
        limit=MAX_PACKET if binary else MAX_BODY if handler.path==PATH else 8192
        require(handler.headers.get_all('Content-Type')==[expect] and len(size)==1
                and re.fullmatch(r'[1-9][0-9]{0,9}',size[0]) is not None and int(size[0])<=limit)
        n=int(size[0])
        if binary:
            return handler.respond(200,service.write_frames(handler.rfile,n))
        body=strict_json(read_exact(handler.rfile,n))
        if handler.path==FRAME_READ:
            packet=service.read_frames(body)
        else:
            return handler.respond(200,service.run(body,read=handler.path==PATH+'/read',started=started))
    except (Rejected,ClaimDenied,ValueError,KeyError,TypeError,RecursionError,ArithmeticError):
        return handler.respond(409,{'error':'MOTION_2D_OUTCOME_UNCONFIRMED'}) if started else handler.respond(422,{'error':'MOTION_2D_REJECTED'})
    except (OSError,sqlite3.Error,subprocess.SubprocessError):
        return handler.respond(503,{'error':'MOTION_2D_OUTCOME_UNCONFIRMED'})

    try:
        handler.send_response(200)
        handler.send_header('Content-Type','application/octet-stream')
        handler.send_header('Content-Length',str(len(packet)))
        handler.send_header('X-Content-Type-Options','nosniff')
        handler.send_header('Cache-Control','no-store')
        handler.end_headers()
        handler.wfile.write(packet)
    except OSError:
        # The read changes no stage or receipt. Never append JSON to a
        # partially transmitted binary response.
        handler.close_connection=True
