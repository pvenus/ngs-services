"""Shape-only admission for supported aggregate envelopes; selection uses the workflow registry."""
import re

def validate_aggregate(request,request_type):
 schema=request.get('schemaVersion')
 if schema=='ngs.npc-portrait-cascade.v1':
  required={'schemaVersion','projectId','requestType','intent','characterRef','incarnation','resultCount','setActive','posePreset'}
  if ('includeCombat'in request and type(request['includeCombat'])is not bool) or set(request)-{'includeCombat'}!=required or request.get('requestType')!=request_type or request_type!='npc.regenerate-portrait-set' or request.get('projectId')!='binari' or request.get('posePreset')!='representative' or type(request.get('resultCount'))is not int or request['resultCount']!=1 or request.get('setActive')is not False or not isinstance(request.get('intent'),str) or len(request['intent'].strip())<5:raise ValueError('NPC_PORTRAIT_CASCADE_REQUEST_INVALID')
  if not isinstance(request.get('incarnation'),str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,180}',request['incarnation']):raise ValueError('NPC_PORTRAIT_CASCADE_INCARNATION_INVALID')
  ref=request.get('characterRef')
  if not isinstance(ref,dict) or set(ref)!={'npcId','npcRevision','snapshotId','characterHash','activePortraitId'}:raise ValueError('NPC_PORTRAIT_CASCADE_REFERENCE_INVALID')
  for field in ('npcId','snapshotId'):
   if not isinstance(ref.get(field),str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',ref[field]):raise ValueError('NPC_PORTRAIT_CASCADE_REFERENCE_INVALID')
  if type(ref.get('npcRevision'))is not int or ref['npcRevision']<0 or not isinstance(ref.get('characterHash'),str) or not re.fullmatch('[0-9a-f]{64}',ref['characterHash']) or (ref.get('activePortraitId')is not None and not isinstance(ref['activePortraitId'],str)):raise ValueError('NPC_PORTRAIT_CASCADE_REFERENCE_INVALID')
  return
 allowed={'schemaVersion','projectId','mode','intent','character','sourceRefs'}
 if set(request)-{'requestType'}!=allowed or ('requestType'in request and request['requestType']!=request_type) or request.get('mode')!='aggregate' or not isinstance(request.get('intent'),str) or not request['intent'].strip():raise ValueError('NPC_WORKFLOW_ACCEPTED_REQUEST_REQUIRED')

def validate_pose_selection(request, stages=None, mode='single'):
 """Structured pose is authoritative; legacy requests without it remain valid."""
 if 'posePreset' not in request:return
 pose=request['posePreset']
 if pose not in ('representative','combat','idle'):raise ValueError('NPC_PORTRAIT_POSE_INVALID')
 if request.get('schemaVersion')=='ngs.motion-preset-registration.v1':
  template=request.get('templateProof')
  if template is not None and (template!={'sourceHash': '69591853d817488edaa8fd9bf8fc1d821eaeaf789f8627b3cd23b41c4ed67997', 'rendererHash': '4b5407e0a5fa192c95fec5a1504b3608577a6f663c7ff89ce98f87d3cbb6cb32', 'templateId': 'quaternius-walk-loop-v1', 'requestHash': '4014365e0c7d1ce1a02709328625570f2323802cea2f358efe696846fe2000fc', 'sourceAction': 'Walk_Loop', 'sourceRange': [0, 32], 'cycleCount': 1, 'license': 'CC0-1.0', 'sourceUrl': 'https://quaternius.com/packs/universalanimationlibrary.html', 'visualQuality': 'requires-review-after-render', 'modelCalls': 0} or request.get('cycleConfirmed')is not False or request.get('presetId')!='walk' or pose!='idle'):raise ValueError('MOTION_TEMPLATE_PROOF_INVALID')
  fields={'schemaVersion','projectId','presetId','label','posePreset','intent','cycleConfirmed','directions'} | ({'templateProof'} if template is not None else set())
  if set(request)!=fields or request.get('projectId')!='binari' or pose not in ('combat','idle') or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',str(request.get('presetId',''))) or not isinstance(request.get('label'),str) or not request['label'].strip() or not isinstance(request.get('intent'),str) or not 3<=len(request['intent'].strip())<=4000 or (template is None and request.get('cycleConfirmed')is not True):raise ValueError('MOTION_PRESET_REGISTRATION_INVALID')
  rows=request['directions']
  if not isinstance(rows,list) or len(rows)!=8 or {v.get('direction')for v in rows if isinstance(v,dict)}!={'n','ne','e','se','s','sw','w','nw'}:raise ValueError('MOTION_PRESET_REGISTRATION_DIRECTIONS')
  for v in rows:
   if set(v)!={'direction','assetId','sha256','bytes','frameCount','fps','width','height','durationSeconds','cycleCount'} or not re.fullmatch('[a-f0-9]{64}',str(v.get('sha256',''))) or v['assetId']!=v['sha256']+'.mp4' or any(type(v[k])is not int or v[k]<=0 for k in ('bytes','frameCount','width','height')) or any(type(v[k])not in(int,float)or not v[k]>0 for k in ('fps','durationSeconds')) or type(v['cycleCount'])is not int or v['cycleCount']!=1:raise ValueError('MOTION_PRESET_REGISTRATION_CLIP')
  if template is not None and (len({v['sha256']for v in rows})!=8 or any(v['frameCount']!=33 or v['fps']!=24 or v['width']!=768 or v['height']!=432 or abs(v['durationSeconds']-33/24)>0.01 for v in rows)):raise ValueError('MOTION_TEMPLATE_TIMING_INVALID')
  if stages is not None and (mode!='single' or len(stages)!=1 or stages[0].get('id')!='motion-preset.register' or stages[0].get('workerId')!='motion-preset-registration'):raise ValueError('MOTION_PRESET_REGISTRATION_WORKER_MISMATCH')
  return
 if request.get('schemaVersion')=='ngs.npc-animation-frames-request.v1':
  if set(request)-{'scalePreset','sourceFrameSet'}!={'schemaVersion','projectId','npcId','posePreset','presetId','intent'} or request.get('scalePreset','full')not in ('full','half') or request.get('projectId')!='binari' or not isinstance(request.get('npcId'),str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',request['npcId']) or not isinstance(request.get('presetId'),str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',request['presetId']) or not isinstance(request.get('intent'),str) or len(request['intent'].strip())<3:raise ValueError('NPC_ANIMATION_FRAMES_REQUEST_INVALID')
  pin=request.get('sourceFrameSet')
  if request.get('scalePreset')=='half':
   if not isinstance(pin,dict) or set(pin)!={'slotKey','requestId','inputHash','contentHash'} or pin.get('slotKey')!=pose+'/'+request['presetId'] or not isinstance(pin.get('requestId'),str) or not pin['requestId'] or any(not re.fullmatch('[0-9a-f]{64}',str(pin.get(k,'')))for k in ('inputHash','contentHash')):raise ValueError('NPC_ANIMATION_FRAMES_SOURCE_SET_INVALID')
  elif pin is not None:raise ValueError('NPC_ANIMATION_FRAMES_SOURCE_SET_UNSUPPORTED')
  if stages is not None and (mode!='single' or len(stages)!=1 or stages[0].get('id')!='animation.extract-frames' or stages[0].get('workerId')!='npc-animation-frames'):raise ValueError('NPC_ANIMATION_FRAMES_WORKER_MISMATCH')
  return
 if request.get('schemaVersion')=='ngs.npc-pose-regeneration-request.v1':
  if set(request)!={'schemaVersion','projectId','npcId','posePreset','referenceType','intent','direction'} or request.get('projectId')!='binari' or not isinstance(request.get('npcId'),str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',request['npcId']) or request.get('referenceType')not in ('pixel','portrait') or request.get('direction')not in ('n','ne','e','se','s','sw','w','nw') or not isinstance(request.get('intent'),str) or not 3<=len(request['intent'].strip())<=2000:raise ValueError('NPC_POSE_REGENERATION_REQUEST_INVALID')
  if stages is not None and (mode!='single' or len(stages)!=1 or stages[0].get('id')!='pose.regenerate-one' or stages[0].get('workerId')!='npc-pose-expansion'):raise ValueError('NPC_POSE_REGENERATION_WORKER_MISMATCH')
  return
 if request.get('schemaVersion')=='ngs.npc-pose-expansion-request.v1':
  if set(request)!={'schemaVersion','projectId','npcId','posePreset','referenceType','intent'} or request.get('projectId')!='binari' or not isinstance(request.get('npcId'),str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',request['npcId']) or request.get('referenceType')not in ('pixel','portrait') or not isinstance(request.get('intent'),str) or not request['intent'].strip():raise ValueError('NPC_POSE_EXPANSION_REQUEST_INVALID')
  if stages is not None and (mode!='single' or len(stages)!=1 or stages[0].get('id')!='pose.expand-eight-directions' or stages[0].get('workerId')!='npc-pose-expansion'):raise ValueError('NPC_POSE_EXPANSION_WORKER_MISMATCH')
  return
 if request.get('schemaVersion')=='ngs.npc-animation-experiment-request.v1':
  if pose!='idle' or request.get('experimentId') not in ('eight-up-run-4x2','eight-up-run-4x2-matched-reference') or request.get('referenceType')!='pixel':raise ValueError('NPC_ANIMATION_EXPERIMENT_TARGET_INVALID')
  if stages is not None and (mode!='single' or len(stages)!=1 or stages[0].get('id')!='animation.experiment' or stages[0].get('workerId')!='npc-animation-experiment'):raise ValueError('NPC_ANIMATION_EXPERIMENT_WORKER_MISMATCH')
  return
 if request.get('schemaVersion')=='ngs.npc-animation-regeneration.v1':
  if request.get('referenceType')!='pixel':raise ValueError('ANIMATION_PIXEL_REFERENCE_REQUIRED')
  if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',str(request.get('presetId',''))) or request.get('direction')not in ('n','ne','e','se','s','sw','w','nw') or request.get('referenceType')not in ('pixel','portrait'):raise ValueError('NPC_ANIMATION_REGENERATION_TARGET_INVALID')
  if stages is not None and (mode!='single' or len(stages)!=1 or stages[0].get('id')!='animation.regenerate-from-pose' or stages[0].get('workerId')!='npc-animation'):raise ValueError('NPC_ANIMATION_REGENERATION_WORKER_MISMATCH')
  return
 if request.get('schemaVersion')=='ngs.npc-animation-request.v1':
  if request.get('referenceType')!='pixel':raise ValueError('ANIMATION_PIXEL_REFERENCE_REQUIRED')
  # Shape-only admission; the worker and PDS enforce the registered pose/preset binding.
  if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',str(request.get('presetId',''))) or request.get('direction') not in ('all','n','ne','e','se','s','sw','w','nw') or request.get('referenceType') not in ('pixel','portrait'):raise ValueError('NPC_ANIMATION_POSE_PRESET_INVALID')
  expected=('animation.generate-eight-directions','npc-animation-batch')if request.get('direction')=='all'else('animation.generate-from-pose','npc-animation')
  if stages is not None and (mode!='single' or len(stages)!=1 or (stages[0].get('id'),stages[0].get('workerId'))!=expected):raise ValueError('NPC_ANIMATION_POSE_WORKER_MISMATCH')
  return
 if stages is None:return
 portraits=[s for s in stages if s.get('workerId')=='npc-portrait']
 if mode=='single':
  if len(portraits)!=1:raise ValueError('NPC_PORTRAIT_POSE_WORKER_MISMATCH')
  if portraits[0].get('posePreset',pose)!=pose:raise ValueError('NPC_PORTRAIT_POSE_SELECTION_CONFLICT')
 elif request.get('schemaVersion')=='ngs.npc-portrait-cascade.v1':
  if [s.get('posePreset') for s in portraits]!=(['representative','combat','idle'] if request.get('includeCombat',True) else ['representative','idle']):raise ValueError('NPC_PORTRAIT_CASCADE_POSE_ORDER_INVALID')


def validate_route_request(request, contract):
 """Schema/target admission chosen by operator route metadata, never request prose.
 Source existence, authorization and CAS stay with PDS/the executing adapter.
 """
 if contract is None:return
 kind=contract.get('kind')
 def fail():raise ValueError('WORKFLOW_ROUTE_REQUEST_INVALID:'+str(kind))
 def ident(v):return isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',v) is not None
 def text(v,n=1):return isinstance(v,str) and n<=len(v.strip())<=4000
 if request.get('projectId')!='binari':fail()
 if kind=='category':
  required={'schemaVersion','projectId','npcId','categoryId','baseRevision','intent'}
  optional={'operation','idempotencyKey','mode','incarnationId','candidateCount','resultMode','planningRole'}
  if not required<=set(request) or set(request)-required-optional or request['schemaVersion']!='npc-category-request.v1' or not ident(request['npcId']) or type(request['baseRevision'])is not int or request['baseRevision']<1 or not text(request['intent'],5):fail()
  mode=request.get('mode','add' if request['categoryId'] is None else 'modify')
  if mode!=contract['mode'] or (mode=='add' and request['categoryId'] is not None) or (mode=='modify' and not ident(request['categoryId'])):fail()
  if 'operation'in request and request['operation']!='npc-category-submit':fail()
  if 'candidateCount'in request and (type(request['candidateCount'])is not int or request['candidateCount']!=1):fail()
  if 'incarnationId'in request and not ident(request['incarnationId']):fail()
  if 'planningRole'in request and request['planningRole']not in ('portrait.representative','portrait.combat','portrait.idle'):fail()
 elif kind=='category-delete':
  if set(request)!={'schemaVersion','projectId','npcId','categoryId','baseRevision'} or request['schemaVersion']!='npc-category-delete.v1' or not ident(request['npcId']) or not ident(request['categoryId']) or type(request['baseRevision'])is not int or request['baseRevision']<1:fail()
 elif kind=='npc-field-edit':
  fields={'schemaVersion','projectId','requestId','npcId','field','value','baseRevision','expectedCharacterHash','incarnationId','userEvidence'}
  if set(request)!=fields or request['schemaVersion']!='ngs.npc-field-edit-request.v1' or not ident(request['requestId']) or not ident(request['npcId']) or request['field']not in contract.get('allowedFields',[]) or not isinstance(request['value'],str) or not request['value'].strip() or len(request['value'])>300 or type(request['baseRevision'])is not int or request['baseRevision']<1 or not isinstance(request['expectedCharacterHash'],str) or not re.fullmatch('[a-f0-9]{64}',request['expectedCharacterHash']) or not isinstance(request['incarnationId'],str) or not request['incarnationId'].strip() or not isinstance(request['userEvidence'],str) or not request['userEvidence'].strip():fail()
 elif kind=='portrait':
  required={'schemaVersion','projectId','characterRef','intent','setActive'}
  optional={'posePreset','resultCount','outputMode','regeneratePixelOnly','reprocessPixelOnly','portraitScope'}
  if 'portraitScope'in request and (request['portraitScope']!='single' or 'posePreset'not in request):fail()
  if not required<=set(request) or set(request)-required-optional or request['schemaVersion']not in ('npc-portrait-simple.v1','npc-portrait-simple.v2') or request['setActive']is not False or not text(request['intent'],5):fail()
  if 'resultCount'in request and (type(request['resultCount'])is not int or request['resultCount']!=1):fail()
  pixel=contract.get('pixelOnly',False);reprocess=contract.get('reprocess',False)
  if any(k in request and type(request[k])is not bool for k in ('regeneratePixelOnly','reprocessPixelOnly')):fail()
  if (request.get('regeneratePixelOnly')is True)!=pixel or (request.get('reprocessPixelOnly')is True)!=reprocess:fail()
  if 'posePreset'in request:
   if request['schemaVersion']!='npc-portrait-simple.v1' or request['posePreset']not in ('representative','combat','idle') or request.get('resultCount')!=1:fail()
  elif pixel or request.get('outputMode')!='request-candidates.v2':fail()
  ref=request['characterRef']
  if not isinstance(ref,dict) or set(ref)!={'npcId','npcRevision','snapshotId','characterHash','activePortraitId'} or not ident(ref['npcId']) or type(ref['npcRevision'])is not int or ref['npcRevision']<0 or not ident(ref['snapshotId']) or not isinstance(ref['characterHash'],str) or not re.fullmatch('[a-f0-9]{64}',ref['characterHash']) or (ref['activePortraitId']is not None and not ident(ref['activePortraitId'])):fail()
 else:raise ValueError('WORKFLOW_ROUTE_CONTRACT_UNKNOWN')
