"""Immutable prop revisions. The sole visibility point is one atomic head write."""
import base64,json,os,shutil,stat,tempfile
from pathlib import Path
from server import Conflict,Store
from workflow_audio_store import bytes_of,fsync_dir
from prop_package_contract import (
    MAX_MANIFEST,MAX_MEDIA,MAX_REVISION,all_media,digest,hex64,keys,manifest,match,need,
    number,official,prop_id,sha,title,verify_media,wire,
)

WRITER='/v1/workflow/props'
WRITER_ROUTES=(WRITER,WRITER+'/read',WRITER+'/asset',WRITER+'/head')
RECEIPT_SCHEMA='ngs.prop-package-publication-receipt.v1'
MAX_BODY=4*((MAX_MEDIA+2)//3)*4+MAX_MANIFEST+64*1024
PIN_KEYS=('propId','revision','sha256')
CANDIDATE_KEYS=('scope','members','manifestSha256','resultRef')
HEAD_SCHEMA='ngs.prop-pointer.v1'
MANAGED=b'filex.prop-package.v1\n'

def pin(q):
    need(keys(q,PIN_KEYS) and prop_id(q['propId']) and number(q['revision'],1,MAX_REVISION) and hex64(q['sha256']))
    return q

def read_json(path,max_bytes):
    raw=bytes_of(path,max_bytes)
    def distinct(pairs):
        result=dict(pairs);need(len(result)==len(pairs));return result
    result=json.loads(raw,object_pairs_hook=distinct)
    need(raw==wire(result))
    return result

def refs(m,scope,members):
    def check(key,prefix,media=None):
        x=members[key];need(x['ref'].startswith('prop.'+prefix+'.'))
        if media:need(x['ref']=='prop.'+prefix+'.'+media['sha256'])
    if scope.get('operation') is None or scope.get('operation')=='regenerate':
        check('prop_detail','detail-rgba',m['media']['detail']);check('prop_pixel','pixel-rgba',m['media']['pixel'])
    if scope.get('motion')=='subtle' or scope.get('operation')=='append-animation':
        clip=m['animations'][-1]
        check('prop_animation_detail','animation-detail-rgba',clip['media']['detail'])
        check('prop_animation_pixel','animation-pixel-rgba',clip['media']['pixel'])
        check('prop_motion','i2v-rgb')
    if 'prop_design' in members:check('prop_design','design-spec')
    if 'prop_base' in members:check('prop_base','base-png')

def validate_candidate(c,m):
    need(keys(c,CANDIDATE_KEYS) and c['manifestSha256']==sha(manifest(m)) and
         c['resultRef']=='prop.package.'+c['manifestSha256'])
    scope=c['scope'];op=scope.get('operation') if type(scope) is dict else False
    base=('officialRequestId','projectId','propId','requestSha256')
    need(op in (None,'regenerate','append-animation','remove-animation') and keys(scope,base+(
        ('designContextSha256','motion') if op is None else
        ('operation','source','motion','designContextSha256') if op=='regenerate' else
        ('operation','source','motionIds') if op=='remove-animation' else ('operation','source','animationId'))) and
        all(scope[k]==m[k] for k in base))
    if op is None:
        need(scope['motion'] in ('still','subtle') and m['revision']==1 and m['parentSha256'] is None)
        expected=['prop_design','prop_base','prop_detail','prop_pixel']
        initial=scope['motion']=='subtle';need(len(m['animations'])==int(initial))
        if initial:need(m['animations'][0]['id']=='initial')
    else:
        s=scope['source'];need(keys(s,('revision','sha256')) and number(s['revision'],1,MAX_REVISION-1) and
          hex64(s['sha256']) and m['revision']==s['revision']+1 and m['parentSha256']==s['sha256'])
        if op=='regenerate':
            need(scope['motion'] in ('still','subtle'))
            expected=['prop_design','prop_base','prop_detail','prop_pixel']
            initial=scope['motion']=='subtle';need(len(m['animations'])==int(initial))
            if initial:need(m['animations'][0]['id']=='initial')
        elif op=='append-animation':
            need(prop_id(scope['animationId']));expected=[];initial=True
            need(m['animations'] and m['animations'][-1]['id']==scope['animationId'])
        else:
            ids=scope['motionIds']
            need(type(ids)is list and 1<=len(ids)<=30 and all(prop_id(i) for i in ids) and len(set(ids))==len(ids))
            expected=[];initial=False
    if op not in ('append-animation','remove-animation'):need(scope['designContextSha256']==m['designContextSha256'])
    if initial:
        expected+=['prop_motion','prop_animation_detail','prop_animation_pixel']
        clip=m['animations'][-1]
        need(clip['officialRequestId']==m['officialRequestId'] and clip['requestSha256']==m['requestSha256'])
    members=c['members']
    need(type(members) is list and len(members)==len(expected))
    for key,row in zip(expected,members):
        need(keys(row,('key','taskId','ref')) and row['key']==key and
            row['taskId']=='workflow.task.'+digest([scope['officialRequestId'],key]) and
            match(row['ref'],r'prop\.[a-z0-9-]+\.[a-f0-9]{64}'))
    refs(m,scope,{x['key']:x for x in members})

def successor(a,b,scope):
    need(a['propId']==b['propId'] and a['officialRequestId']!=b['officialRequestId'] and
         digest(a)==scope['source']['sha256'] and a['revision']==scope['source']['revision'])
    if scope['operation']=='append-animation':
        n=len(a['animations']);need(b['animations'][:n]==a['animations'] and len(b['animations'])==n+1 and
             all(a[k]==b[k] for k in ('media','title','interaction','designContextSha256')))
    if scope['operation']=='remove-animation':
        ids=set(scope['motionIds'])
        need(ids<={i['id'] for i in a['animations']} and
             b['animations']==[i for i in a['animations'] if i['id'] not in ids] and
             all(a[k]==b[k] for k in ('media','title','interaction','designContextSha256')))

class PropPackageStore(Store):
    def __init__(self,root):
        p=Path(root)
        need(p.is_absolute() and p.name=='props' and p.resolve(strict=True)==p and p.is_dir())
        super().__init__(p)
        st=p.lstat();self.identity=(st.st_dev,st.st_ino)
    def path(self,*_):raise ValueError('generic prop storage disabled')
    def snapshot(self):raise ValueError('generic prop storage disabled')
    def cas(self,*a,**k):raise ValueError('generic prop storage disabled')
    def check_root(self):
        st=self.root.lstat()
        if not stat.S_ISDIR(st.st_mode) or (st.st_dev,st.st_ino)!=self.identity:raise Conflict('prop root changed')
    def beneath(self,*parts):
        self.check_root();p=self.root
        for part in parts:
            p=p/part
            try:st=p.lstat()
            except FileNotFoundError:continue
            if stat.S_ISLNK(st.st_mode) or p.resolve()!=p:raise Conflict('prop symlink')
        return p
    def verify_managed(self,p):
        try:return bytes_of(self.beneath(p.name,'.prop-managed-v1'),128)==MANAGED
        except (FileNotFoundError,ValueError,Conflict):return False
    def head_locked(self,prop):
        try:row=read_json(self.beneath(prop,'metadata.json'),8192)
        except FileNotFoundError:return None
        need(keys(row,('schemaVersion','projectId','title',*PIN_KEYS)) and row['schemaVersion']==HEAD_SCHEMA and
             row['projectId']=='binari' and title(row['title']) and row['propId']==prop)
        pin({k:row[k] for k in PIN_KEYS});return row
    def head(self,prop):
        need(prop_id(prop))
        with self.lock():return self.head_locked(prop)
    def release_locked(self,q,head):
        pin(q);prop=q['propId'];n=q['revision']
        if head is None or n>head['revision']:raise FileNotFoundError('prop unpublished')
        m=read_json(self.beneath(prop,'releases',str(n),'manifest.json'),MAX_MANIFEST)
        d=sha(manifest(m));need(m['propId']==prop and m['revision']==n)
        if d!=q['sha256']:raise FileNotFoundError('prop pin not present')
        if n==head['revision']:need(head['sha256']==q['sha256'] and head['title']==m['title'])
        return m
    def asset_locked(self,prop,m,asset):
        member=all_media(m).get(asset)
        if member is None:raise FileNotFoundError('prop asset not pinned')
        raw=bytes_of(self.beneath(prop,'assets',asset),MAX_MEDIA)
        need(len(raw)==member['bytes'] and sha(raw)==member['sha256'])
        return raw
    def read(self,q,asset=None):
        pin(q)
        with self.lock():
            self.check_root();head=self.head_locked(q['propId'])
            m=self.release_locked(q,head)
            if asset is not None:
                need(match(asset,r'[a-f0-9]{64}\.png'))
                raw=self.asset_locked(q['propId'],m,asset)
                return {'assetId':asset,'sha256':sha(raw),'bytes':len(raw),'base64':base64.b64encode(raw).decode('ascii')}
            receipt=read_json(self.beneath(q['propId'],'releases',str(q['revision']),'.receipt.json'),64*1024)
            need(keys(receipt,(*CANDIDATE_KEYS,'schemaVersion','authority','status')) and receipt['schemaVersion']==RECEIPT_SCHEMA and
                 receipt['authority']=='filex' and receipt['status']=='committed')
            validate_candidate({k:receipt[k] for k in CANDIDATE_KEYS},m)
            response={'manifest':m,'receipt':receipt}
            if 'source' in receipt['scope']:
                response['prior']=self.release_locked({'propId':q['propId'],**receipt['scope']['source']},head)
                successor(response['prior'],m,receipt['scope'])
            return response

    def publish(self,command):
        need(keys(command,('candidate','manifest','media')));c,m,body=[command[k] for k in ('candidate','manifest','media')]
        validate_candidate(c,m)
        prop=m['propId'];n=m['revision'];scope=c['scope']; expected=scope.get('source')
        need(type(body) is dict and len(body)<=4)
        all_next=all_media(m)
        decoded={}
        for key,text in body.items():
            member=all_next.get(key);need(member is not None and type(text) is str and len(text)==4*((member['bytes']+2)//3))
            raw=base64.b64decode(text,validate=True);verify_media(raw,member);decoded[key]=raw
        receipt={'schemaVersion':RECEIPT_SCHEMA,'authority':'filex','status':'committed',**c}
        with self.lock():
            self.check_root()
            target=self.beneath(prop);head=self.head_locked(prop)
            rel=self.beneath(prop,'releases',str(n))
            if head is not None and head['revision']>=n:
                # Recognize only byte-identical replay even after later revisions.
                if not rel.is_dir() or read_json(self.beneath(prop,'releases',str(n),'.receipt.json'),64*1024)!=receipt:
                    raise Conflict('prop immutable revision exists')
                q={'propId':prop,'revision':n,'sha256':c['manifestSha256']}
                need(self.release_locked(q,head)==m)
                return receipt
            if (head is None)!=(expected is None) or head is not None and any(head[k]!=expected[k] for k in ('revision','sha256')):
                raise Conflict('prop head changed')
            if expected:
                a=self.release_locked({'propId':prop,**expected},head);successor(a,m,scope)
                prior=all_media(a) if scope['operation'] in ('append-animation','remove-animation') else {}
            else:prior={}
            need(set(decoded)==set(all_next)-set(prior) and all(prior[k]==all_next[k] for k in set(prior)&set(all_next)))
            if rel.exists():
                # A crash between revision write and head CAS can only resume the same input.
                need(read_json(self.beneath(prop,'releases',str(n),'manifest.json'),MAX_MANIFEST)==m and
                     read_json(self.beneath(prop,'releases',str(n),'.receipt.json'),64*1024)==receipt)
            # Never claim an unmanaged folder or touch another prop.
            if target.exists() and not self.verify_managed(target):raise Conflict('prop unmanaged target')
            if not target.exists():
                temp=Path(tempfile.mkdtemp(prefix='.prop-new-',dir=self.root))
                try:
                    self.write(temp/'.prop-managed-v1',MANAGED);os.rename(temp,target);fsync_dir(self.root)
                finally:
                    if temp.exists():shutil.rmtree(temp)
            for key,member in all_next.items():
                ap=self.beneath(prop,'assets',key)
                if key in decoded:
                    if ap.exists():need(bytes_of(ap,MAX_MEDIA)==decoded[key])
                    else:self.write(ap,decoded[key])
                    need(bytes_of(ap,MAX_MEDIA)==decoded[key])
                else:
                    raw=bytes_of(ap,MAX_MEDIA)
                    need(len(raw)==member['bytes'] and sha(raw)==member['sha256'])
            if not rel.exists():
                rel.parent.mkdir(exist_ok=True)
                staging=Path(tempfile.mkdtemp(prefix='.prop-release-',dir=rel.parent))
                try:
                    self.write(staging/'manifest.json',wire(m));self.write(staging/'.receipt.json',wire(receipt))
                    fsync_dir(staging);os.rename(staging,rel);fsync_dir(rel.parent)
                finally:
                    if staging.exists():shutil.rmtree(staging)
            pointer={'schemaVersion':HEAD_SCHEMA,'projectId':'binari','propId':prop,'title':m['title'],
                     'revision':n,'sha256':c['manifestSha256']}
            self.write(self.beneath(prop,'metadata.json'),wire(pointer))
            need(self.head_locked(prop)==pointer)
            return receipt
