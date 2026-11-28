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
MAX_BODY=28*((MAX_MEDIA+2)//3)*4+MAX_MANIFEST+64*1024
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
        if 'spirit' in m['media']:check('prop_spirit','spirit-rgba',m['media']['spirit'])
    if scope.get('motion')=='subtle' or scope.get('operation')=='append-animation':
        clip=m['animations'][-1]
        check('prop_animation_detail','animation-detail-rgba',clip['media']['detail'])
        check('prop_animation_pixel','animation-pixel-rgba',clip['media']['pixel'])
        check('prop_motion','i2v-rgb')
    if 'prop_design' in members:check('prop_design','design-spec')
    if 'prop_base' in members:check('prop_base','base-png')
    if scope.get('presentationProfile')=='spirit-v2':
        check('prop_pixel_source','pixel-source-png');check('prop_pixel_detail','pixel-detail-rgba')

def validate_candidate(c,m):
    need(keys(c,CANDIDATE_KEYS) and c['manifestSha256']==sha(manifest(m)) and
         c['resultRef']=='prop.package.'+c['manifestSha256'])
    scope=c['scope'];op=scope.get('operation') if type(scope) is dict else False
    base=('officialRequestId','projectId','propId','requestSha256')
    profile=type(scope)is dict and 'presentationProfile' in scope
    if profile:need(op in (None,'regenerate') and scope['presentationProfile'] in ('spirit-v1','spirit-v2'))
    scales='scalePercents' in scope if type(scope)is dict else False
    need(scales==(op in ('append-scales','replace-scales') or op is None and 'scaleVariants' in m) and (op is not None or scales or 'scaleSourcePixel' not in m))
    need(op in (None,'regenerate','append-animation','remove-animation','append-scales','replace-scales') and keys(scope,base+(('presentationProfile',) if profile else ())+(('scalePercents',) if scales else ())+(
        ('designContextSha256','motion') if op is None else
        ('operation','source','motion','designContextSha256') if op=='regenerate' else
        ('operation','source','motionIds') if op=='remove-animation' else
        ('operation','source') if op in ('append-scales','replace-scales') else ('operation','source','animationId'))) and
        all(scope[k]==m[k] for k in base))
    if op=='regenerate':need('scaleVariants' not in m and 'scaleSourcePixel' not in m)
    if scales:
        if op is None:need(scope.get('presentationProfile')=='spirit-v2' and 'scaleSourcePixel' in m)
        values=scope['scalePercents']
        need(type(values)is list and 1<=len(values)<=11 and all(type(n)is int and n in (5,10,20,30,40,50,60,70,80,90,100) for n in values) and len(set(values))==len(values))
        actual={r['scalePercent'] for r in m.get('scaleVariants',[])}
        need(set(values)==actual if op is None else set(values)<=actual)
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
        elif op in ('append-scales','replace-scales'):expected=[];initial=False
        else:
            ids=scope['motionIds']
            need(type(ids)is list and 1<=len(ids)<=30 and all(prop_id(i) for i in ids) and len(set(ids))==len(ids))
            expected=[];initial=False
    if op not in ('append-animation','remove-animation','append-scales','replace-scales'):
        need(scope['designContextSha256']==m['designContextSha256'] and profile==('spirit' in m['media']))
        if scope.get('presentationProfile')=='spirit-v2':
            expected[3:3]=['prop_pixel_source','prop_pixel_detail']
        if profile:expected.append('prop_spirit')
    if initial:
        expected+=['prop_motion','prop_animation_detail','prop_animation_pixel']
        clip=m['animations'][-1]
        need(clip['officialRequestId']==m['officialRequestId'] and clip['requestSha256']==m['requestSha256'])
    members=c['members']
    # Read old receipts unchanged; split topology contributes every selected
    # scale pair in numeric order, content-bound to independently verified PNGs.
    need(type(members) is list)
    if scales and (op=='replace-scales' or len(members)>len(expected)):
        pairs={v['scalePercent']:v for v in m['scaleVariants']}
        tail=[f'prop_scale_{n:03d}' for n in sorted(values)]
        need(len(members)==len(expected)+len(tail))
        for n,row in zip(sorted(values),members[len(expected):]):
            need(type(row) is dict and row.get('ref')=='prop.scale-pair.'+digest(pairs[n]))
        expected+=tail
    need(len(members)==len(expected))
    for key,row in zip(expected,members):
        need(keys(row,('key','taskId','ref')) and row['key']==key and
            row['taskId']=='workflow.task.'+digest([scope['officialRequestId'],key]) and
            match(row['ref'],r'prop\.[a-z0-9-]+\.[a-f0-9]{64}'))
    refs(m,scope,{x['key']:x for x in members})
    if 'scaleSourcePixel' in m and op is None:
        need(any(row['key']=='prop_pixel_detail' and row['ref']=='prop.pixel-detail-rgba.'+m['scaleSourcePixel']['sha256'] for row in members))

def successor(a,b,scope):
    need(a['propId']==b['propId'] and a['officialRequestId']!=b['officialRequestId'] and
         digest(a)==scope['source']['sha256'] and a['revision']==scope['source']['revision'])
    if scope['operation']=='append-animation':
        n=len(a['animations']);need(b['animations'][:n]==a['animations'] and len(b['animations'])==n+1 and
             all(a[k]==b[k] for k in ('media','title','interaction','designContextSha256')))
    if scope['operation'] in ('append-animation','remove-animation'):
        need(all(a.get(k)==b.get(k) for k in ('scaleVariants','scaleSourcePixel')))
    if scope['operation'] in ('append-scales','replace-scales'):
        need(all(a.get(k)==b.get(k) for k in ('media','title','interaction','designContextSha256','animations','scaleSourcePixel')))
        old={r['scalePercent']:r for r in a.get('scaleVariants',[])}
        new={r['scalePercent']:r for r in b['scaleVariants']}
        selected=set(scope['scalePercents'])
        need((scope['operation']=='replace-scales' or not (set(old)&selected)) and
             set(new)==set(old)|selected and all(new[k]==v for k,v in old.items() if k not in selected))
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
    def remove_package(self,command):
        from single_target_delete import PinnedDeleteRejected,SingleTargetDelete,validate_target
        root='canonical-files/binari/props'
        need(keys(command,('operationId','target','expected')))
        target=validate_target(command['target']);p=target['path']
        need(target['root']==root and target['kind']=='directory' and prop_id(p))
        expected=command['expected']
        need(keys(expected,('revision','sha256')))
        q=pin({'propId':p,**expected})
        storage=SingleTargetDelete(self,root)
        with self.lock():
            path=self.beneath(p)
            try:path.lstat()
            except FileNotFoundError:pass # existing recovery record only; no new move
            else:
                head=self.head_locked(p)
                if not self.verify_managed(path) or head is None or any(head[k]!=v for k,v in q.items()):
                    raise PinnedDeleteRejected('prop delete pin stale')
                self.release_locked(q,head)
            return storage.remove_locked({'operationId':command['operationId'],'target':target})
    def head(self,prop):
        need(prop_id(prop))
        with self.lock():return self.head_locked(prop)
    def assembly_bindings(self,query):
        from assembly_contract import ROLES,pin as assembly_pin,binding_pin,image_scale
        generate=keys(query,())
        decorate=keys(query,('decorationPins',))
        if decorate:
            rows=query['decorationPins']
            need(type(rows)is list and len(rows)<=48 and all(
                type(row)is dict and set(row) in ({'prop','visibility'},{'prop','visibility','imageScalePercent'}) and
                assembly_pin(row['prop']) and ('imageScalePercent' not in row or image_scale(row['imageScalePercent'])) and
                row['visibility'] in ('both','pixel','cinematic') for row in rows))
        need(generate or decorate or keys(query,('pins',)) and type(query['pins']) is list and len(query['pins'])<=128 and
             all(binding_pin(q) for q in query['pins']))
        with self.lock():
            self.check_root()
            def verified(q,head,lanes=('pixel',),scale=None):
                m=self.release_locked(q,head)
                media=m['media']
                if scale is not None:
                    media=next((row for row in m.get('scaleVariants',()) if row['scalePercent']==scale),None)
                    if media is None:raise FileNotFoundError('pinned prop image scale missing')
                for lane in lanes:
                    if lane not in media:raise FileNotFoundError('bound prop lane missing')
                    self.asset_locked(q['propId'],m,media[lane]['assetId'])
                return q
            if not generate:
                rows=query['decorationPins'] if decorate else [
                    {'prop':{k:q[k] for k in PIN_KEYS},'visibility':'both','imageScalePercent':q['imageScalePercent']}
                    if 'imageScalePercent' in q else {'prop':q,'visibility':'pixel'} for q in query['pins']]
                for row in rows:
                    q=row['prop'];v=row['visibility']
                    if not self.verify_managed(self.beneath(q['propId'])):raise FileNotFoundError('bound prop missing')
                    lanes=('pixel','spirit') if v=='both' else ('spirit',) if v=='cinematic' else ('pixel',)
                    verified(q,self.head_locked(q['propId']),lanes,row.get('imageScalePercent'))
                return {'ok':True}
            candidates={k:[] for k in ROLES}
            for dir in self.root.iterdir():
                name=dir.name
                if not prop_id(name):continue
                role='table' if name.startswith('ritual-seal-altar-') else 'well' if name.startswith('ritual-seal-well-v') else 'lid' if name.startswith('ritual-seal-well-cover-') else 'bowl' if 'water-bowl' in name or name.startswith('ritual-seal-ash-catcher-') else None
                if role is None:continue
                try:
                    path=self.beneath(name)
                    if not self.verify_managed(path):continue
                    pointer=self.beneath(name,'metadata.json')
                    h=self.head_locked(name)
                    if h:candidates[role].append((int(role=='bowl' and 'water-bowl' in name),pointer.stat().st_mtime_ns,name,h))
                except (FileNotFoundError,ValueError,Conflict):continue
            out={}
            for role in ROLES:
                for _,_,name,h in sorted(candidates[role],reverse=True):
                    try:
                        out[role]=verified({k:h[k] for k in PIN_KEYS},h)
                        break
                    except (FileNotFoundError,ValueError,Conflict):continue
                else:raise FileNotFoundError('assembly prop role missing: '+role)
            return {'bindings':out}

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
        if scope.get('operation')=='regenerate':raise Conflict('prop full regeneration retired')
        need(type(body) is dict and len(body)<=(28 if 'scalePercents' in scope else 5 if scope.get('presentationProfile') in ('spirit-v1','spirit-v2') else 4))
        scale_assets={row[k]['assetId'] for row in m.get('scaleVariants',[]) for k in ('pixel','spirit')}
        original_groups=[m['media'],*(clip['media'] for clip in m['animations'])]
        scale_assets-={a['assetId'] for group in original_groups for a in group.values()}
        if m.get('scaleSourcePixel'):scale_assets.discard(m['scaleSourcePixel']['assetId'])
        all_next=all_media(m)
        repair_ids={row[k]['assetId'] for row in m.get('scaleVariants',[]) if row['scalePercent'] in scope['scalePercents']
                   for k in ('pixel','spirit')} if scope.get('operation')=='replace-scales' else set()
        decoded={}
        for key,text in body.items():
            member=all_next.get(key);need(member is not None and type(text) is str and len(text)==4*((member['bytes']+2)//3))
            raw=base64.b64decode(text,validate=True);verify_media(raw,member,variant=key in scale_assets);decoded[key]=raw
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
                prior=all_media(a) if scope['operation'] in ('append-animation','remove-animation','append-scales','replace-scales') else {}
            else:prior={}
            need(set(decoded)==(set(all_next)-set(prior))|repair_ids and
                 all(prior[k]==all_next[k] for k in set(prior)&set(all_next)))
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
            # Preflight every retained original/unselected asset before touching
            # an asset. Only selected, stage-proven, exact-hash bytes can repair.
            # Such a repair also safely restores any older pin to the same ID.
            writes=[]
            for key,member in all_next.items():
                ap=self.beneath(prop,'assets',key)
                try:
                    info=ap.lstat()
                except FileNotFoundError:
                    if key not in decoded:raise
                    writes.append((ap,decoded[key]))
                    continue
                if key in repair_ids:
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise Conflict('unsafe prop asset shape')
                    current=bytes_of(ap,MAX_MEDIA) if 0<info.st_size<=MAX_MEDIA else None
                    if current!=decoded[key]:writes.append((ap,decoded[key]))
                else:
                    raw=bytes_of(ap,MAX_MEDIA)
                    if key in decoded:need(raw==decoded[key])
                    else:need(len(raw)==member['bytes'] and sha(raw)==member['sha256'])
            for ap,raw in writes:
                self.write(ap,raw)
                need(bytes_of(ap,MAX_MEDIA)==raw)
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
