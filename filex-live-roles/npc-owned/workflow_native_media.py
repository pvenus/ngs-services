"""Direct current-slot publication for formal native-PNG workflow tasks."""
import base64
from datetime import datetime,timezone
import hashlib
import json
import math
import re
import struct

import native_pixel_direction as pixel_contract
from media_validation import png
from npc_document import decode
from npc_store import encoded,digest
from ownership_plan import plan
from server import Conflict

CAPABILITY='workflowNativePNGMediaV1'
REGEN_MODES=('animation-sequences','animation-alpha','animation-processed-directions')
GENERATION=re.compile(r'[a-f0-9]{48}')
REGEN_SOURCE={'generationId','portraitAssetId','resultBasisSha256'}
REGEN_RECEIPT='filex.workflow-animation-regeneration-receipt.v1'
DIRECTIONS=('n','ne','e','se','s','sw','w','nw')
MOTIONS=('walk','run')
MOTION_ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
POSES=('idle','combat')
RAW='ngs.animation-native-png-sequence.v1'
ALPHA='ngs.animation-alpha-output.v1'
RETENTION='retain-native-sequence-independently'
FRAME={'index','assetId','sha256','bytes','mimeType'}
LINEAGE={'npcId','generationId','posePreset','presetId','direction','sourceBindingHash'}
ALPHA_KEYS=LINEAGE|{'schemaVersion','requestId','rawRequestId','rawManifestId','rawManifestHash',
    'dependencyHash','frameCount','fps','width','height','frames','inferenceProvenance','rawRetention'}
MAX_ASSET=8*1024*1024
MAX_ASSETS=64*1024*1024
MAX_BODY=90*1024*1024
PNG=re.compile(r'[a-f0-9]{64}\.png')
SHA=re.compile(r'[a-f0-9]{64}')
IDENT=re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}')


def require(ok,error):
    if not ok:raise ValueError('workflow native media '+error)


def txt(pattern,value):
    return type(value) is str and pattern.fullmatch(value) is not None


def dimensions(width,height):
    return all(type(x) is int and 0<x<=8192 for x in (width,height)) and width*height<=16777216


def frame_bytes(data,width,height):
    require(type(data) is str and 0<len(data)<=4*((MAX_ASSET+2)//3),'PNG transfer bound')
    raw=base64.b64decode(data,validate=True)
    require(0<len(raw)<=MAX_ASSET and len(raw)>=33
            and struct.unpack('>II',raw[16:24])==(width,height),'PNG dimensions')
    png(raw)
    return hashlib.sha256(raw).hexdigest(),raw


def frame_row(idx,aid,raw):
    return {'index':idx,'assetId':aid,'sha256':aid[:-4],'bytes':len(raw),'mimeType':'image/png'}


def scope(kind,b):
    base={'officialRequestId','taskId','projectId','npcId','payloadSha256','motion','direction'}
    selected={'posePreset','assetSetSha256'}
    require(kind in ('animation','frames') and type(b) is dict and set(b) in (base,base|selected)
            and b['projectId']=='binari' and txt(re.compile(r'request\.official\.[a-f0-9]{64}'),b['officialRequestId'])
            and txt(re.compile(r'(?:npc|character)\.[A-Za-z0-9][A-Za-z0-9._-]{0,120}'),b['npcId'])
            and txt(SHA,b['payloadSha256']) and b['direction'] in DIRECTIONS,'scope')
    oid,owner,motion,direction=(b[k] for k in ('officialRequestId','npcId','motion','direction'))
    if selected<=b.keys():
        pose,assets=b['posePreset'],b['assetSetSha256']
        require(pose in POSES and txt(MOTION_ID,motion) and txt(SHA,assets),'selected source')
        slot=hashlib.sha256(motion.encode('utf-8')).hexdigest()[:16]
        stem=f'motion_{slot}_{direction}'
    else:
        require(motion in MOTIONS,'legacy motion')
        pose,assets='idle',None
        stem=f'{motion}_{direction}'
    require(b['taskId']==task(oid,kind+'_'+stem),'task')
    return oid,owner,motion,direction,pose,assets,stem


def source_binding(oid, direction, motion, pose, assets):
    base=[oid,'portrait_'+direction,motion]
    return digest(base if assets is None else [*base,pose,assets])


def task(oid,key):
    return 'workflow.task.'+digest([oid,key])


class WorkflowNativeMedia:
    def _workflow_media_current(self,kind,scope_keys):
        oid,owner,motion,direction,pose,assets,stem=scope_keys
        self.require_no_migration()
        state=self.state(owner);self.require_no_cleanup(state)
        if state['state']!='active':raise Conflict('workflow media owner inactive')
        docs=self.documents_unlocked();root=docs['metadata.json'];meta=docs.get(owner+'/metadata.json',{})
        require(owner in root['characterOrder'] and meta.get('id')==owner
                and meta.get('creationBasis',{}).get('requestId')==task(oid,'preset'),
                'current owner')
        suffix=f'{pose}/{motion}/{direction}'
        if kind=='animation':
            key=f'idle/portrait/{direction}'
            view=docs.get(f'{owner}/pose-views/{key}.json',{})
            require(key in meta.get('poseViewKeys',[]) and view.get('schemaVersion')=='ngs.npc-pose-view.v1'
                    and (view.get('posePreset'),view.get('referenceType'),view.get('direction'))==('idle','portrait',direction)
                    and view.get('requestId')==task(oid,'portrait_'+direction)
                    and txt(PNG,view.get('assetId')),'current native portrait')
            return docs,state,view,suffix
        raw=docs.get(f'{owner}/animation-sequences/{suffix}.json',{})
        require(raw.get('schemaVersion')==RAW and raw.get('requestId')==task(oid,'animation_'+stem)
                and (assets is None or raw.get('sourceBindingHash')==source_binding(oid,direction,motion,pose,assets))
                and all(raw.get(k)==v for k,v in (('npcId',owner),('generationId',state['generationId']),
                ('posePreset',pose),('presetId',motion),('direction',direction))), 'canonical raw sequence')
        return docs,state,raw,suffix

    def _workflow_static_pixel_current(self,b):
        require(type(b) is dict and set(b)=={'officialRequestId','taskId','projectId','npcId','payloadSha256','posePreset','direction'}
                and b['projectId']=='binari' and b['posePreset']=='idle'
                and b['direction'] in (None,*DIRECTIONS)
                and txt(re.compile(r'request\.official\.[a-f0-9]{64}'),b['officialRequestId'])
                and txt(re.compile(r'(?:npc|character)\.[A-Za-z0-9][A-Za-z0-9._-]{0,120}'),b['npcId'])
                and txt(SHA,b['payloadSha256']),'static pixel scope')
        oid,owner,d=(b[k] for k in ('officialRequestId','npcId','direction'))
        require(b['taskId']==task(oid,'portrait_pixel_idle_'+(d or 'reference')),'static pixel identity')
        self.require_no_migration()
        state=self.state(owner);self.require_no_cleanup(state)
        require(state['state']=='active' and state['npcId']==owner,'static pixel owner')
        docs=self.documents_unlocked();prefix=owner+'/';meta=docs.get(prefix+'metadata.json',{})
        require(owner in docs['metadata.json']['characterOrder'] and meta.get('id')==owner
                and meta.get('creationBasis',{}).get('requestId')==task(oid,'preset'),'static pixel current generation')
        if d:
            key='idle/portrait/'+d
            src=docs.get(prefix+'pose-views/'+key+'.json',{})
            require(key in meta.get('poseViewKeys',[]) and src.get('schemaVersion')=='ngs.npc-pose-view.v1'
                    and (src.get('posePreset'),src.get('referenceType'),src.get('direction'))==('idle','portrait',d)
                    and src.get('requestId')==task(oid,'portrait_'+d),'static pixel direction source')
            center=meta.get('portraitSets',{}).get('idle',{})
            center_id=center.get('pixelAssetId')
            require(txt(PNG,center_id) and src.get('referenceArtifactId')==center_id
                    and center.get('pixelProvenance',{}).get('postprocess',{}).get('workflowTaskId')==
                        task(oid,'portrait_pixel_idle_reference'),'static pixel direction center lineage')
            center_bytes=self.safe(prefix+'assets/portraits/idle/pixel.png').read_bytes()
            require(hashlib.sha256(center_bytes).hexdigest()+'.png'==center_id,'static pixel center bytes')
            aid=src.get('assetId');slot='assets/pose-views/'+key+'/image.png'
        else:
            src=meta.get('portraitSets',{}).get('idle',{})
            require(src.get('requestId')==task(oid,'portrait_idle_reference'),'static pixel idle source')
            aid=src.get('portraitAssetId');slot='assets/portraits/idle/portrait.png'
        require(txt(PNG,aid),'static pixel source id')
        raw=self.safe(prefix+slot).read_bytes()
        require(0<len(raw)<=MAX_ASSET and hashlib.sha256(raw).hexdigest()+'.png'==aid,'static pixel source bytes')
        png(raw)
        return docs,meta,raw,aid

    def workflow_static_pixel_publish(self,b,a):
        require(type(a) is dict and set(a)=={'sourceAssetId','base64'} and txt(PNG,a['sourceAssetId'])
                and type(a['base64']) is str and 0<len(a['base64'])<=4*((MAX_ASSET+2)//3),'static pixel artifact')
        raw=base64.b64decode(a['base64'],validate=True)
        require(33<=len(raw)<=MAX_ASSET,'static pixel output size')
        png(raw)
        w,h=struct.unpack('>II',raw[16:24])
        require((w,h)==(128,128),'static pixel dimensions')
        aid=hashlib.sha256(raw).hexdigest()+'.png'
        with self.lock():
            docs,meta,source,src_id=self._workflow_static_pixel_current(b)
            require(a['sourceAssetId']==src_id and aid!=src_id,'static pixel source changed')
            sw,sh=struct.unpack('>II',source[16:24])
            require(max(sw,sh)>128,'static pixel output not downscaled')
            owner=b['npcId'];prefix=owner+'/';d=b['direction'];changes={}
            if d:
                key='idle/pixel/'+d
                path='pose-views/'+key+'.json'
                require(key not in meta.get('poseViewKeys',[]) and prefix+path not in docs,'static pixel target exists')
                changes[path]={'schemaVersion':'ngs.npc-pose-view.v1','posePreset':'idle','referenceType':'pixel',
                    'direction':d,'requestId':b['taskId'],'assetId':aid,'sourceArtifactId':src_id,
                    'sourceHash':src_id[:-4],**({'referenceArtifactId':docs[prefix+'pose-views/idle/portrait/'+d+'.json']['referenceArtifactId']}
                            if 'referenceArtifactId' in docs[prefix+'pose-views/idle/portrait/'+d+'.json'] else {}),'mimeType':'image/png','bytes':len(raw),'width':w,'height':h}
                changes['metadata.json']={**meta,'poseViewKeys':sorted(set(meta.get('poseViewKeys',[]))|{key})}
                expected_slots={'assets/pose-views/'+key+'/image.png':(aid,raw),
                                'assets/pose-views/'+key+'/source.png':(src_id,source)}
            else:
                pairs=meta['portraitSets'];pair=pairs['idle'];representative=pairs.get('representative',{})
                reference=representative.get('portraitAssetId')
                require(set(pair)=={'portraitAssetId','requestId'} and txt(PNG,reference)
                        and representative.get('requestId')==task(b['officialRequestId'],'portrait_representative'),
                        'static pixel reference or target')
                inventory=meta['portraits']
                if not any(item['assetId']==aid for item in inventory):
                    inventory=[*inventory,{'assetId':aid,'label':'아이들 픽셀 포트레이트','sourceRef':b['taskId']}]
                changes['metadata.json']={**meta,'portraits':inventory,'portraitSets':{**pairs,'idle':{**pair,
                    'pixelAssetId':aid,'pixelSourceAssetId':src_id,'referencePortraitAssetId':reference,
                    'pixelProvenance':{'pipeline':{'postprocessCompleted':True,'portraitSha256':src_id[:-4],
                        'referencePortraitSha256':src_id[:-4],'pixelSha256':aid[:-4],'pixelSourceSha256':src_id[:-4],
                        'postprocessInputSha256':src_id[:-4]},'postprocess':{'workflowTaskId':b['taskId']}}}}}
                expected_slots={'assets/portraits/idle/pixel.png':(aid,raw),
                                'assets/portraits/idle/pixel-source.png':(src_id,source)}
            current={k.removeprefix(prefix):v for k,v in docs.items() if k.startswith(prefix)}
            previous=self.manifests().get(owner)
            require(previous==plan(owner,current),'static pixel owner manifest')
            following=plan(owner,{**current,**changes})
            slots=following['slots']
            require(all(slots.get(k)==v for k,v in previous['slots'].items())
                    and previous['nonSlotReferences']==following['nonSlotReferences']
                    and {k:v for k,v in slots.items() if k not in previous['slots']}==
                        {k:{'assetId':v[0],'sha256':v[0][:-4]} for k,v in expected_slots.items()}
                    and all(not self.safe(prefix+k).exists() for k in expected_slots),'static pixel write scope')
            root=docs['metadata.json'];rev=root.get('_pdsCurrent',{}).get('revision')
            require(type(rev) is int and rev>=0,'static pixel revision')
            after={**docs,**{prefix+k:v for k,v in changes.items()}}
            document=decode(after);document.pop('_pdsCurrent',None)
            after_root={**root,'_pdsCurrent':{'revision':rev+1,'savedAt':datetime.now(timezone.utc).isoformat(),
                'note':'Workflow static portrait pixel','operationHash':digest([b,aid,src_id]),'documentHash':digest(document)}}
            self.check_document_hash({**after,'metadata.json':after_root})
            writes={prefix+k:v[1] for k,v in expected_slots.items()}
            writes.update({prefix+k:encoded(v) for k,v in changes.items()})
            writes.update({'metadata.json':encoded(after_root),prefix+'.owned-assets.json':encoded(following)})
            return self.commit_workflow_native_bytes(writes)

    @staticmethod
    def _regen_scope(kind,b):
        require(type(b) is dict and b.get('regeneration') is True and 'assetSetSha256' in b,'regeneration scope')
        return scope(kind,{k:v for k,v in b.items() if k!='regeneration'})

    def _regen_target(self,parsed):
        oid,owner,motion,direction,pose,assets,stem=parsed
        self.require_no_migration()
        state=self.state(owner);self.require_no_cleanup(state)
        require(state.get('state')=='active' and state.get('npcId')==owner
                and txt(GENERATION,state.get('generationId')),'regeneration owner')
        docs=self.documents_unlocked();root=docs['metadata.json'];prefix=owner+'/'
        meta=docs.get(prefix+'metadata.json',{})
        require(type(root['characterOrder']) is list and root['characterOrder'].count(owner)==1
                and meta.get('id')==owner,'regeneration current NPC')
        key=f'idle/portrait/{direction}'
        view=docs.get(prefix+'pose-views/'+key+'.json',{})
        aid=view.get('assetId')
        require(key in meta.get('poseViewKeys',[]) and view.get('schemaVersion')=='ngs.npc-pose-view.v1'
                and (view.get('posePreset'),view.get('referenceType'),view.get('direction'))==('idle','portrait',direction)
                and txt(PNG,aid),'regeneration current appearance')
        portrait_slot='assets/pose-views/'+key+'/image.png'
        manifest=self.manifests().get(owner)
        require(manifest is not None and manifest['slots'].get(portrait_slot)=={'assetId':aid,'sha256':aid[:-4]},
                'regeneration portrait slot')
        portrait=self.safe(prefix+portrait_slot)
        require(0<portrait.stat().st_size<=MAX_ASSET and hashlib.sha256(portrait.read_bytes()).hexdigest()==aid[:-4],
                'regeneration portrait bytes')
        suffix=f'{pose}/{motion}/{direction}'
        paths=tuple(k+'/'+suffix+'.json' for k in REGEN_MODES)
        old=tuple(docs.get(prefix+path) for path in paths)
        # Existing layers are either absent or from the same canonical
        # generation/slot. Never replace a legacy/non-native artifact.
        for row,schema in zip(old,(RAW,ALPHA,pixel_contract.WORKFLOW_SCHEMA)):
            if row is None:continue
            require(old[0] is not None and type(row) is dict and row.get('schemaVersion')==schema
                    and all(row.get(k)==value for k,value in
                         (('npcId',owner),('generationId',state['generationId']),
                          ('posePreset',pose),('presetId',motion),('direction',direction))),
                    'regeneration previous slot')
        basis={'generationId':state['generationId'],'portraitAssetId':aid,'resultBasisSha256':digest(old)}
        return docs,state,suffix,paths,basis,manifest

    @staticmethod
    def _regen_lineage(b):
        return {k:v for k,v in b.items() if k!='taskId'}

    def _regen_stage_path(self,oid,stem):
        # Animation and postprocess are independent residents. Several
        # directions/presets of the same parent can be staged simultaneously.
        return '.workflow-motion-regeneration/'+digest([oid,stem])+'/'

    def _regen_read_stage(self,b,parsed,target):
        oid,owner,motion,direction,pose,assets,stem=parsed
        _,_,_,_,basis,_=target
        folder=self._regen_stage_path(oid,stem)
        state=self.safe(folder+'raw.json')
        require(state.is_file() and 0<state.stat().st_size<=1024*1024,'regeneration staged manifest')
        staged=json.loads(state.read_bytes())
        require(type(staged) is dict and set(staged)=={'scope','basis','sequence'}
                and staged['scope']==self._regen_lineage(b) and staged['basis']==basis,'regeneration stage drift')
        raw=staged['sequence']
        require(type(raw) is dict and raw.get('schemaVersion')==RAW and raw.get('requestId')==task(oid,'animation_'+stem)
                and all(raw.get(k)==value for k,value in (('npcId',owner),('generationId',basis['generationId']),
                  ('posePreset',pose),('presetId',motion),('direction',direction),
                  ('sourceBindingHash',source_binding(oid,direction,motion,pose,assets)))),'regeneration raw lineage')
        rows=raw.get('frames')
        require(type(rows) is list and 2<=raw.get('frameCount',0)<=1000 and len(rows)==raw['frameCount'],
                'regeneration stored frames')
        for i,row in enumerate(rows):
            require(type(row) is dict and set(row)==FRAME and type(row['index']) is int and row['index']==i
                    and txt(PNG,row['assetId']) and row['sha256']==row['assetId'][:-4]
                    and type(row['bytes']) is int and 0<row['bytes']<=MAX_ASSET
                    and row['mimeType']=='image/png','regeneration stored frame')
        require(sum(row['bytes'] for row in rows)<=MAX_ASSETS,'regeneration stored frame aggregate')
        return folder,staged

    def _regen_bytes(self,folder,row):
        path=self.safe(folder+row['assetId'])
        require(path.is_file() and path.stat().st_size==row['bytes'],'regeneration frame length')
        raw=path.read_bytes()
        require(len(raw)==row['bytes'] and hashlib.sha256(raw).hexdigest()==row['sha256'],'regeneration frame bytes')
        return raw

    def workflow_media_asset(self,kind,b,artifact):
        require(kind=='frames' and type(artifact) is dict and set(artifact)=={'assetId'}
                and txt(PNG,artifact['assetId']),'regeneration asset input')
        parsed=self._regen_scope(kind,b)
        with self.lock():
            folder,staged=self._regen_read_stage(b,parsed,self._regen_target(parsed))
            row=next((row for row in staged['sequence']['frames'] if row['assetId']==artifact['assetId']),None)
            require(row is not None,'regeneration asset scope')
            raw=self._regen_bytes(folder,row)
            return {k:row[k] for k in ('assetId','sha256','bytes')}|{'base64':base64.b64encode(raw).decode('ascii')}

    def _regen_read(self,kind,b):
        parsed=self._regen_scope(kind,b)
        with self.lock():
            target=self._regen_target(parsed)
            result={k:b[k] for k in ('motion','direction','posePreset','assetSetSha256')}
            if kind=='animation':return {**result,**target[4]}
            _,staged=self._regen_read_stage(b,parsed,target)
            return {**result,'sequence':staged['sequence']}

    def _regen_publish(self,kind,b,artifact):
        parsed=self._regen_scope(kind,b)
        required={'fps','width','height','frames','source'} if kind=='animation' else {'alpha','pixel','assets'}
        require(type(artifact) is dict and set(artifact)==required,'regeneration artifact')
        with self.lock():
            target=self._regen_target(parsed)
            docs,state,suffix,paths,basis,previous=target
            oid,owner,_,_,_,_,stem=parsed
            if kind=='animation':
                source=artifact['source']
                require(type(source) is dict and set(source)==REGEN_SOURCE and source==basis,'regeneration source changed')
                folder=self._regen_stage_path(oid,stem)
                raw,by_id=self._workflow_raw(b,state,artifact,suffix)
                sequence=raw[paths[0]]
                staged={'scope':self._regen_lineage(b),'basis':basis,'sequence':sequence}
                if self.safe(folder+'raw.json').exists():
                    _,prior=self._regen_read_stage(b,parsed,target)
                    require(prior==staged and all(self._regen_bytes(folder,row)==by_id[row['assetId']] for row in sequence['frames']),
                            'regeneration staged replay drift')
                    return {'ok':True}
                return self.commit_workflow_native_bytes({
                    **{folder+aid:data for aid,data in by_id.items()},folder+'raw.json':encoded(staged)})

            tid=b['taskId']
            input_hash=digest([b,artifact])
            receipt_path=self.receipt_path(tid)
            if receipt_path.exists():
                receipt=json.loads(receipt_path.read_bytes())
                observed={f'{owner}/{path}':digest(docs[f'{owner}/{path}']) for path in paths if f'{owner}/{path}' in docs}
                require(type(receipt) is dict and receipt.get('schemaVersion')==REGEN_RECEIPT and receipt.get('status')=='committed'
                        and receipt.get('requestId')==tid and receipt.get('generationId')==basis['generationId']
                        and receipt.get('inputHash')==input_hash and len(observed)==3
                        and receipt.get('hashes')==observed,'regeneration committed replay drift')
                return {'ok':True}
            folder,staged=self._regen_read_stage(b,parsed,target)
            sequence=staged['sequence']
            changes,assets=self._workflow_corrected(b,sequence,artifact,suffix)
            by_id={**{row['assetId']:self._regen_bytes(folder,row) for row in sequence['frames']},**assets}
            changes={paths[0]:sequence,**changes}
            return self._regen_swap(b,docs,previous,changes,by_id,folder,staged,input_hash,suffix)

    def _regen_swap(self,b,docs,previous,changes,by_id,folder,staged,input_hash,suffix):
        owner=b['npcId'];prefix=owner+'/'
        before={k.removeprefix(prefix):v for k,v in docs.items() if k.startswith(prefix)}
        require(previous==plan(owner,before),'regeneration owner manifest inconsistent')
        following=plan(owner,{**before,**changes})
        def in_scope(path):
            return any(path.startswith('assets/'+layer+'/'+suffix+'/') for layer in REGEN_MODES)
        old=previous['slots'];new=following['slots']
        old_slots={k:v for k,v in old.items() if in_scope(k)}
        new_slots={k:v for k,v in new.items() if in_scope(k)}
        require({k:v for k,v in old.items() if not in_scope(k)}=={k:v for k,v in new.items() if not in_scope(k)}
                and previous['nonSlotReferences']==following['nonSlotReferences']
                and {v['assetId'] for v in new_slots.values()}==set(by_id),'regeneration write scope')
        for slot,item in old_slots.items():
            file=self.safe(prefix+slot)
            require(0<file.stat().st_size<=MAX_ASSET and hashlib.sha256(file.read_bytes()).hexdigest()==item['sha256'],
                    'regeneration prior bytes')
        require(all(not self.safe(prefix+slot).exists() for slot in new_slots if slot not in old_slots),
                'regeneration unexpected existing slot file')
        root=docs['metadata.json'];rev=root.get('_pdsCurrent',{}).get('revision')
        require(type(rev) is int and rev>=0,'regeneration current revision')
        after={**docs,**{prefix+k:v for k,v in changes.items()}}
        doc=decode(after);doc.pop('_pdsCurrent',None)
        metadata={'revision':rev+1,'savedAt':datetime.now(timezone.utc).isoformat(),
                  'note':'Workflow native PNG direction regeneration','operationHash':input_hash,'documentHash':digest(doc)}
        after_root={**root,'_pdsCurrent':metadata}
        self.check_document_hash({**after,'metadata.json':after_root})
        writes={prefix+k:by_id[v['assetId']] for k,v in new_slots.items()}
        writes.update({prefix+k:encoded(v) for k,v in changes.items()})
        writes.update({'metadata.json':encoded(after_root),prefix+'.owned-assets.json':encoded(following)})
        cleanup={prefix+k for k in set(old_slots)-set(new_slots)}
        cleanup.update({folder+row['assetId'] for row in staged['sequence']['frames']})
        cleanup.add(folder+'raw.json')
        receipt={'schemaVersion':REGEN_RECEIPT,'status':'committed','requestId':b['taskId'],
                 'generationId':staged['basis']['generationId'],'inputHash':input_hash,
                 'hashes':{prefix+k:digest(v) for k,v in changes.items()},'resultMetadata':metadata}
        self.commit_owned(b['taskId'],receipt,writes,cleanup)
        return {'ok':True}

    def workflow_media_read(self,kind,b):
        if type(b) is dict and 'regeneration' in b:return self._regen_read(kind,b)
        if kind=='portrait-pixel':
            with self.lock():
                _,_,raw,aid=self._workflow_static_pixel_current(b)
                return {'posePreset':'idle','direction':b['direction'],'source':{
                    'assetId':aid,'sha256':aid[:-4],'bytes':len(raw),'mimeType':'image/png'}}
        parsed=scope(kind,b)
        with self.lock():
            _,_,source,_=self._workflow_media_current(kind,parsed)
            return {'motion':b['motion'],'direction':b['direction'],
                    **({k:b[k] for k in ('posePreset','assetSetSha256')} if 'assetSetSha256' in b else {}),
                    **({'portraitAssetId':source['assetId']} if kind=='animation' else {'sequence':source})}

    def workflow_media_publish(self,kind,b,artifact):
        if type(b) is dict and 'regeneration' in b:return self._regen_publish(kind,b,artifact)
        if kind=='portrait-pixel':return self.workflow_static_pixel_publish(b,artifact)
        parsed=scope(kind,b);owner=parsed[1]
        # Structural checks precede lock; ownership and current dependencies
        # are checked under the existing Filex lock at commit, never at model start.
        require(type(artifact) is dict and set(artifact)==(
                {'fps','width','height','frames'} if kind=='animation' else {'alpha','pixel','assets'}),'artifact')
        with self.lock():
            docs,state,source,suffix=self._workflow_media_current(kind,parsed)
            prefix=owner+'/'
            if kind=='animation':
                changes,by_id=self._workflow_raw(b,state,artifact,suffix)
            else:
                changes,by_id=self._workflow_corrected(b,source,artifact,suffix)
            require(all(prefix+p not in docs for p in changes),'current target already exists')
            root=docs['metadata.json'];rev=root.get('_pdsCurrent',{}).get('revision')
            require(type(rev) is int and rev>=0,'legacy current metadata')
            before={k.removeprefix(prefix):v for k,v in docs.items() if k.startswith(prefix)}
            previous=self.manifests().get(owner)
            require(previous==plan(owner,before),'owner manifest inconsistent')
            following=plan(owner,{**before,**changes})
            old=previous['slots'];updated=following['slots']
            require(all(updated.get(k)==v for k,v in old.items()) and
                    previous['nonSlotReferences']==following['nonSlotReferences'],'unrelated asset slot')
            new_slots={slot:item for slot,item in updated.items() if slot not in old}
            require({v['assetId'] for v in new_slots.values()}==set(by_id) and
                    all(not self.safe(prefix+slot).exists() for slot in new_slots),'file write scope')
            after={**docs,**{prefix+k:v for k,v in changes.items()}}
            doc=decode(after);doc.pop('_pdsCurrent',None)
            after_root={**root,'_pdsCurrent':{'revision':rev+1,'savedAt':datetime.now(timezone.utc).isoformat(),
                'note':'Workflow native PNG '+kind,'operationHash':digest([b,changes]),'documentHash':digest(doc)}}
            self.check_document_hash({**after,'metadata.json':after_root})
            writes={prefix+slot:by_id[v['assetId']] for slot,v in new_slots.items()}
            writes.update({prefix+k:encoded(v) for k,v in changes.items()})
            writes.update({'metadata.json':encoded(after_root),prefix+'.owned-assets.json':encoded(following)})
            # Byte journal alone supports crash-consistency; not a task receipt.
            return self.commit_workflow_native_bytes(writes)

    @staticmethod
    def _workflow_raw(b,state,a,suffix):
        fps,w,h,frames=(a[k] for k in ('fps','width','height','frames'))
        require(type(fps) in (int,float) and math.isfinite(fps) and 0<fps<=120
                and dimensions(w,h) and type(frames) is list and 2<=len(frames)<=1000,'raw geometry')
        require(all(type(x) is str for x in frames) and sum(len(x) for x in frames)<=MAX_BODY,'raw aggregate transfer')
        by_id={};rows=[];total=0
        for idx,value in enumerate(frames):
            sha,raw=frame_bytes(value,w,h);aid=sha+'.png';total+=len(raw)
            require(total<=MAX_ASSETS,'raw aggregate bound')
            by_id[aid]=raw;rows.append(frame_row(idx,aid,raw))
        manifest={'schemaVersion':RAW,'requestId':b['taskId'],'npcId':b['npcId'],
                  'generationId':state['generationId'],'posePreset':b.get('posePreset','idle'),'presetId':b['motion'],
                  'direction':b['direction'],'sourceBindingHash':source_binding(b['officialRequestId'],
                      b['direction'],b['motion'],b.get('posePreset','idle'),b.get('assetSetSha256')),
                  'frameCount':len(rows),'fps':fps,'width':w,'height':h,'frames':rows}
        return {'animation-sequences/'+suffix+'.json':manifest},by_id

    @staticmethod
    def _workflow_corrected(b,raw,a,suffix):
        pixel,alpha,assets=(a[k] for k in ('pixel','alpha','assets'))
        pix_path='animation-processed-directions/'+suffix+'.json'
        alpha_path='animation-alpha/'+suffix+'.json'
        require(type(alpha) is dict and set(alpha)==ALPHA_KEYS and alpha['schemaVersion']==ALPHA
                and alpha['requestId']==b['taskId'] and alpha['rawRequestId']==raw['requestId']
                and alpha['rawManifestId']=='canonical-files/binari/npcs/'+b['npcId']+'/animation-sequences/'+suffix+'.json'
                and alpha['rawManifestHash']==digest(raw) and txt(SHA,alpha['dependencyHash'])
                and alpha['rawRetention']==RETENTION and all(alpha[k]==raw[k]
                for k in LINEAGE|{'frameCount','fps','width','height'}),'alpha lineage')
        prov=alpha['inferenceProvenance']
        require(type(prov) is dict and set(prov)=={'engine','modelId','modelDigest','policyHash','inferenceRequestId','fixture'}
                and prov['fixture'] is False and all(txt(IDENT,prov[k]) for k in ('engine','modelId','inferenceRequestId'))
                and all(txt(SHA,prov[k]) for k in ('modelDigest','policyHash')),'alpha provenance')
        pixels=pixel_contract.validate(b['npcId'],pix_path,pixel,workflow=True)
        canvas=pixel['canvas']
        require(pixel['requestId']==b['taskId'] and pixel['rawManifestHash']==digest(raw)
                and pixel['alphaManifestHash']==digest(alpha) and all(pixel[k]==raw[k]
                for k in LINEAGE|{'frameCount','fps'}) and (canvas['sourceWidth'],canvas['sourceHeight'])==(raw['width'],raw['height']),'pixel lineage')
        # Persist the digest of the actual stored JSON representation (a Node
        # transit may turn the producer's integral float fields into integers).
        pixel={**pixel,'normalizationHash':digest({'canvas':canvas,'pixelPolicy':pixel['pixelPolicy']})}
        mattes=alpha['frames'];sources=raw['frames']
        require(type(mattes) is list and len(mattes)==len(sources)==len(pixels),'alpha count')
        sizes={}
        for idx,(src,matte,px) in enumerate(zip(sources,mattes,pixels)):
            require(type(matte) is dict and set(matte)==(FRAME-{'assetId'})|{'rawAssetId','alphaAssetId'}
                    and type(matte['index']) is int and matte['index']==idx and matte['mimeType']=='image/png'
                    and type(matte['bytes']) is int and 0<matte['bytes']<=MAX_ASSET
                    and txt(SHA,matte['sha256']) and matte['alphaAssetId']==matte['sha256']+'.png'
                    and src['assetId']==matte['rawAssetId']==px['rawAssetId']
                    and matte['alphaAssetId']==px['alphaAssetId'],'alpha frame binding')
            for aid,w,h,count in ((matte['alphaAssetId'],raw['width'],raw['height'],matte['bytes']),
                                  (px['assetId'],canvas['width'],canvas['height'],None)):
                if aid in sizes:
                    sw,sh,sc=sizes[aid]
                    require((sw,sh)==(w,h) and (sc is None or count is None or sc==count),'asset alias')
                    count=sc if count is None else count
                sizes[aid]=(w,h,count)
        require(type(assets) is dict and set(assets)==set(sizes)
                and all(type(x) is str for x in assets.values()) and sum(len(x) for x in assets.values())<=MAX_BODY,
                'processed assets')
        by_id={};total=0
        for aid,value in assets.items():
            w,h,count=sizes[aid];sha,data=frame_bytes(value,w,h);total+=len(data)
            require(sha+'.png'==aid and (count is None or len(data)==count) and total<=MAX_ASSETS,'processed asset proof')
            by_id[aid]=data
        return {alpha_path:alpha,pix_path:pixel},by_id
