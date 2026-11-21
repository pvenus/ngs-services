"""Cleanup-only authority for a completed frame publication. No TTL/lease steal.
Only authenticated PDS uses this protocol. Tokens grant no publishing authority.
"""
import json,re,secrets
from npc_store import encoded,digest,NPC_PREFIX
from server import Conflict

class FrameMaintenanceMixin:
    def maintenance_guard(self,s,m):
        return {'npcId':s['npcId'],'generationId':s['generationId'],'maintenanceId':m['maintenanceId'],'originalRequestId':m['originalRequestId'],'maintenanceToken':m['token'],'scopeHash':m['scopeHash'],'operation':'frame-cleanup'}
    def maintenance_checked(self,guard,allow_finished=False):
        required={'npcId','generationId','maintenanceId','originalRequestId','maintenanceToken','scopeHash','operation'}
        if not isinstance(guard,dict) or set(guard)!=required or guard['operation']!='frame-cleanup':raise ValueError('maintenance guard')
        s=self.state(guard['npcId'])
        if s['generationId']!=guard['generationId']:raise Conflict('maintenance generation changed')
        m=s.get('frameMaintenance')
        if not m or m['maintenanceId']!=guard['maintenanceId']:
            m=s.get('finishedFrameMaintenance',{}).get(guard['maintenanceId']) if allow_finished else None
        if not m or self.maintenance_guard(s,m)!=guard:raise Conflict('maintenance authority mismatch')
        return s,m
    def begin_frame_maintenance(self,command):
        required={'npcId','generationId','originalRequestId','maintenanceId','expectedSourceVersion','expectedManifestHash'}
        if not isinstance(command,dict)or set(command)!=required or any(not isinstance(v,str)or not v for v in command.values()):raise ValueError('maintenance command')
        if not re.fullmatch(r'frames\.req\.[A-Za-z0-9._:-]{1,160}',command['originalRequestId']) or not re.fullmatch(r'frame\.cleanup\.[A-Za-z0-9._:-]{1,160}',command['maintenanceId']):raise ValueError('maintenance ids')
        if not all(re.fullmatch('[a-f0-9]{64}',command[k])for k in ('expectedSourceVersion','expectedManifestHash')):raise ValueError('maintenance hashes')
        with self.lock():
            self.require_no_migration();s=self.state(command['npcId']);key=command['maintenanceId'];scope=digest(command)
            if s['state']!='active'or s['generationId']!=command['generationId']:raise Conflict('maintenance generation changed')
            prior=s.get('finishedFrameMaintenance',{}).get(key)
            if prior:
                if prior['scopeHash']!=scope:raise Conflict('maintenance command changed')
                return {'guard':self.maintenance_guard(s,prior),'released':True}
            prior=s.get('frameMaintenance')
            if prior:
                if prior['maintenanceId']!=key or prior['scopeHash']!=scope:raise Conflict('maintenance busy or command changed')
                return {'guard':self.maintenance_guard(s,prior),'released':False}
            if s.get('leases')or s.get('cleanupReservations'):raise Conflict('maintenance requires idle generation')
            original=command['originalRequestId']
            if original not in s.get('finished',{}):raise Conflict('maintenance original not finished')
            receipts=self.publication_receipts(s['npcId'],s['generationId'],original)['receipts']
            if not receipts or any(r.get('status')!='committed'or not r.get('inputHash')for r in receipts):raise Conflict('maintenance publication missing')
            snap=self.snapshot_unlocked()
            if snap['sourceVersion']!=command['expectedSourceVersion']:raise Conflict('maintenance source changed')
            prefix=s['npcId']+'/animation-frames/'
            matches=[(path,m)for path,m in snap['documents'].items()if path.startswith(prefix)and path.endswith('.json')and m.get('requestId')==original]
            if len(matches)!=1:raise Conflict('maintenance current frame manifest required')
            slot,manifest=matches[0]
            if digest(manifest)!=command['expectedManifestHash']:raise Conflict('maintenance manifest changed')
            bound=[r for r in receipts if r.get('hashes',{}).get(NPC_PREFIX+slot)==digest(manifest)]
            if not bound:raise Conflict('maintenance manifest not original publication')
            hashes=set();directions=manifest.get('directions')
            if not isinstance(directions,dict)or not directions:raise Conflict('maintenance frame directions required')
            for direction in directions.values():
                frames=direction.get('frames')
                if not isinstance(frames,list)or not frames:raise Conflict('maintenance frames required')
                for frame in frames:
                    for field in ('assetId','rawAssetId','alphaAssetId'):
                        asset=frame.get(field)
                        if not isinstance(asset,str)or not re.fullmatch('[a-f0-9]{64}\\.png',asset):raise Conflict('maintenance independent input required')
                        hashes.add(asset[:-4])
            m={'maintenanceId':key,'originalRequestId':original,'scopeHash':scope,'sourceVersion':snap['sourceVersion'],'manifestHash':digest(manifest),'slotKey':slot,'publicationReceiptId':bound[-1]['requestId'],'allowedHashes':sorted(hashes),'token':secrets.token_hex(32),'reservations':{},'releasedReservations':{}}
            s['frameMaintenance']=m;self.write(self.safe(self.state_path(s['npcId'])),encoded(s))
            return {'guard':self.maintenance_guard(s,m),'released':False}
    def reserve_frame_maintenance(self,guard,sha256,reservation_id):
        if not re.fullmatch('[a-f0-9]{64}',str(reservation_id))or not re.fullmatch('[a-f0-9]{64}',str(sha256)):raise ValueError('maintenance reservation hash')
        with self.lock():
            self.require_no_migration();s,m=self.maintenance_checked(guard)
            if s['state']!='active'or s.get('leases')or s.get('cleanupReservations'):raise Conflict('maintenance generation not idle')
            if sha256 not in m['allowedHashes']:raise Conflict('maintenance hash outside manifest')
            if reservation_id in m['releasedReservations']:raise Conflict('maintenance reservation already released')
            old=m['reservations'].get(reservation_id)
            if old and old['sha256']!=sha256:raise Conflict('maintenance reservation changed')
            # Same-owner mutations are fenced for this lease; unrelated NPC
            # updates may legitimately change global sourceVersion. Recheck the
            # exact bound document, not every owner's assets for every file.
            manifest=json.loads(self.safe(m['slotKey']).read_bytes())
            if manifest.get('requestId')!=m['originalRequestId']or digest(manifest)!=m['manifestHash']:raise Conflict('maintenance source changed')
            proof=self.current_equivalent(s['npcId'],s['generationId'],m['originalRequestId'],sha256)
            if proof.get('currentSlot')is not True:raise Conflict('maintenance current equivalent missing')
            if not old:
                old={'sha256':sha256,'token':secrets.token_hex(32)};m['reservations'][reservation_id]=old
                self.write(self.safe(self.state_path(s['npcId'])),encoded(s))
            proof['publicationReceiptId']=m['publicationReceiptId']
            return dict(proof,maintenanceId=m['maintenanceId'],originalRequestId=m['originalRequestId'],reservationId=reservation_id,reservationToken=old['token'])
    def release_frame_maintenance(self,guard,reservation_id,token):
        with self.lock():
            s,m=self.maintenance_checked(guard,True)
            prior=m['releasedReservations'].get(reservation_id)
            if prior:
                if prior['token']!=token:raise Conflict('maintenance release token mismatch')
                return {'released':True}
            old=m['reservations'].get(reservation_id)
            if not old or old['token']!=token:raise Conflict('maintenance release token mismatch')
            del m['reservations'][reservation_id];m['releasedReservations'][reservation_id]=old
            self.write(self.safe(self.state_path(s['npcId'])),encoded(s));return {'released':True}
    def finish_frame_maintenance(self,guard):
        with self.lock():
            s,m=self.maintenance_checked(guard,True)
            if m['reservations']:raise Conflict('maintenance reservations outstanding')
            if m['maintenanceId']in s.get('finishedFrameMaintenance',{}):return {'released':True}
            s.setdefault('finishedFrameMaintenance',{})[m['maintenanceId']]=m;del s['frameMaintenance']
            self.write(self.safe(self.state_path(s['npcId'])),encoded(s));return {'released':True}
