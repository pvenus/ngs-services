from __future__ import annotations

import argparse
import hashlib
import json
import re
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .database import DataError, RevisionConflict
from .service import ProjectDataService
from .storage import StorageError
from .domain_contracts.legacy_http import Handler as DomainHandler
from .domains import native


MAX_BODY = 2 * 1024 * 1024
# Full canonical NPC documents include frame references. Keep only this existing
# category CAS endpoint aligned with its 32 MiB client read envelope.
MAX_CATEGORY_APPLY_BODY = 32 * 1024 * 1024


class Handler(DomainHandler):
    server_version = "ProjectDataService/0.1"

    def respond(self, status: int, value: dict | list | None, extra_headers: dict | None = None):
        data = b"" if value is None else json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for key, value_ in (extra_headers or {}).items():
            self.send_header(key, value_)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def read_json(self, max_body=MAX_BODY) -> dict:
        length = self.headers.get("Content-Length", "")
        if not length.isdecimal() or int(length) <= 0 or (max_body is not None and int(length) > max_body):
            raise DataError("invalid request body size")
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            raise DataError("application/json is required")
        payload = json.loads(self.rfile.read(int(length)))
        if not isinstance(payload, dict):
            raise DataError("JSON object is required")
        return payload

    def route(self):
        from .workflow_native_media import PREFIX as native_media_prefix, SCHEMAS as native_media_kinds, serve as native_media
        media_path=self.path.split('?',1)[0]
        from .workflow_npc_planning_update import PATH as npc_planning_update_path,serve as npc_planning_update
        if media_path in (npc_planning_update_path,npc_planning_update_path+'/read'):
            return npc_planning_update(self)

        from .workflow_npc_planning_add import PATH as npc_planning_add_path,serve as npc_planning_add
        if media_path in (npc_planning_add_path,npc_planning_add_path+'/read'):
            return npc_planning_add(self)

        from .single_target_delete import PATH as single_delete_path,serve as single_delete
        if media_path==single_delete_path:return single_delete(self)

        from .workflow_motion_2d import ROUTES as motion_2d_routes,serve as motion_2d
        if media_path in motion_2d_routes:return motion_2d(self)

        from .workflow_cinematic import ROUTES as cinematic_routes,serve as workflow_cinematic
        if media_path in cinematic_routes:return workflow_cinematic(self)
        from .workflow_cinematic_sequence import ROUTES as sequence_routes,serve as cinematic_sequence
        if media_path in sequence_routes:return cinematic_sequence(self)
        from .workflow_cinematic_playback import ROUTES as playback_routes,serve as cinematic_playback
        if media_path in playback_routes:return cinematic_playback(self)

        from .workflow_prop_stage import ROUTES as prop_stage_routes,serve as workflow_prop_stage
        if media_path in prop_stage_routes:return workflow_prop_stage(self)

        from .workflow_prop_package import ROUTES as prop_package_paths,serve as workflow_prop_package
        if media_path in prop_package_paths:return workflow_prop_package(self)

        from .workflow_prop_image import ROUTES as prop_image_paths,serve as workflow_prop_image
        if media_path in prop_image_paths:return workflow_prop_image(self)

        from .workflow_audio_separate import PATH as separate_path,serve as audio_separate
        if media_path in (separate_path,separate_path+'/read',separate_path+'/asset'):return audio_separate(self)

        from .workflow_audio import PATH as audio_workflow_path,serve as audio_workflow
        if media_path in (audio_workflow_path,audio_workflow_path+'/read'):return audio_workflow(self)

        from .workflow_concept_category import PATH as category_workflow_path,serve as category_workflow
        if media_path in (category_workflow_path,category_workflow_path+'/read'):
            return category_workflow(self)

        for kind in native_media_kinds:
            base=native_media_prefix+kind
            if media_path in (base,base+'/read',base+'/asset'):
                return native_media(self,kind)
        for stage in ('overview','portrait-art','combat'):
            path='/v1/npcs/ownership/workflow-plan-'+stage
            if self.path.split('?',1)[0] in (path,path+'/read'):
                from .workflow_plan_idle import serve as workflow_plan_current
                return workflow_plan_current(self,stage)
        idle_plan='/v1/npcs/ownership/workflow-plan-idle'
        if self.path.split('?',1)[0] in (idle_plan,idle_plan+'/read'):
            from .workflow_plan_idle import serve as workflow_plan_idle
            return workflow_plan_idle(self)
        for mode,suffix in (('representative','representative'),('idle','idle-reference'),('direction','direction'),('combat','combat-reference'),('combat_direction','combat-direction')):
            portrait='/v1/npcs/ownership/workflow-portrait-'+suffix
            if self.path.split('?',1)[0] in (portrait,portrait+'/read'):
                from .workflow_portrait_representative import serve as workflow_portrait
                return workflow_portrait(self,mode)
        representative='/v1/npcs/ownership/workflow-plan-representative'
        if self.path.split('?', 1)[0] in (representative,representative+'/read'):
            from .workflow_plan_representative import serve as workflow_plan_representative
            return workflow_plan_representative(self)
        if self.path.split('?', 1)[0]=='/v1/npcs/ownership/workflow-preset':
            from .workflow_preset import serve as workflow_preset
            return workflow_preset(self)
        if self.path.split('?', 1)[0]=='/v1/npcs/ownership/workflow-delete':
            from .workflow_delete import serve as workflow_delete
            return workflow_delete(self)
        folders = getattr(self.server, 'project_folders', None)
        if folders is not None:
            parsed = urlsplit(self.path)
            if parsed.path == '/mcp':
                from .project_folders_mcp import serve as folder_mcp
                return folder_mcp(self, parsed, folders)
            from .project_folders import PREFIX, route as folder_route
            if parsed.path == PREFIX or parsed.path.startswith(PREFIX + '/'):
                return folder_route(self, parsed, folders)
        from .npc_ownership import request_scope
        try:
            with request_scope(self.headers): return self._route_owned()
        except (ValueError,KeyError,TypeError) as exc:
            return self.respond(422,{'error':str(exc)})

    def _route_owned(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        try:
            if path=='/v1/npcs/ownership/operations':
                from .owned_operation_http import serve
                service=getattr(self.server,'owned_npc_operations',None)
                if service is None:return self.respond(503,{'error':'OWNED_OPERATIONS_NOT_CONFIGURED'})
                return serve(self,service)
            if path in ('/v1/npc-portrait-pairs/publish','/v1/npc-portrait-pairs/read','/v1/npc-pose-directions/publish','/v1/npc-pose-directions/read'):
                from .portrait_pair_direct import MAX_BODY as PAIR_MAX, MAX_META
                pose=path.startswith('/v1/npc-pose-directions/')
                if pose:
                    from .pose_direct import MAX_BODY as PAIR_MAX
                pair=getattr(self.server,'direct_pose_directions'if pose else'direct_portrait_pairs',None)
                if pair is None:return self.respond(503,{'error':'DIRECT_PAIR_NOT_CONFIGURED'})
                if self.command!='POST' or parsed.query or self.headers.get('Origin') is not None or self.headers.get('Transfer-Encoding'):return self.respond(403,{'error':'DIRECT_PAIR_AUTH_REQUIRED'})
                from .direct_guard import ingress
                pair.guard_ingress=True
                try:
                    with ingress(self):
                        return self._direct_image_body(pair,path,PAIR_MAX,MAX_META)
                except (ValueError,TypeError,KeyError):return self.respond(403,{'error':'DIRECT_IMAGE_GUARD_REQUIRED'})
                except OSError:return self.respond(503,{'error':'PAIR_OUTCOME_UNCERTAIN'})
            if path.startswith('/v1/npc-portrait-pairs/receipts/'):
                registrar=getattr(self.server,'portrait_registrar',None)
                if registrar is None:return self.respond(503,{'error':'PORTRAIT_REGISTRAR_NOT_CONFIGURED'})
                if self.command!='GET' or parsed.query or self.headers.get('Origin')is not None or not registrar.authenticated(self.headers.get('Authorization')):return self.respond(403,{'error':'PORTRAIT_SERVICE_AUTH_REQUIRED'})
                cid=path.removeprefix('/v1/npc-portrait-pairs/receipts/')
                if not re.fullmatch(r'npc\.portrait\.[a-f0-9]{64}',cid):raise ValueError('PORTRAIT_COMMIT_ID')
                with registrar.source.store.factory()as t:r=t.receipt(cid)
                return self.respond(404 if r is None else 200,r)
            if path.startswith('/v1/portrait-assets/'):
                registrar=getattr(self.server,'portrait_registrar',None)
                if registrar is None:return self.respond(503,{'error':'PORTRAIT_REGISTRAR_NOT_CONFIGURED'})
                if self.headers.get('Origin') is not None or not registrar.authenticated(self.headers.get('Authorization')):return self.respond(403,{'error':'PORTRAIT_SERVICE_AUTH_REQUIRED'})
                if parsed.query or self.headers.get('Transfer-Encoding'):raise ValueError('PORTRAIT_QUERY_OR_TRANSFER')
                if self.command=='POST' and path=='/v1/portrait-assets/register':
                    import base64
                    h=self.headers.get('X-Portrait-Metadata','');n=self.headers.get('Content-Length','')
                    if not 0<len(h)<=16384 or self.headers.get('Content-Type')!='image/png' or not n.isdecimal() or not 0<int(n)<=32*1024*1024:raise ValueError('PORTRAIT_BODY')
                    meta=json.loads(base64.b64decode(h,validate=True));self.connection.settimeout(30);data=self.rfile.read(int(n))
                    if len(data)!=int(n):raise ValueError('PORTRAIT_TRUNCATED')
                    try:return self.respond(200,registrar.register(meta,data))
                    except OSError:return self.respond(503,{'error':'PORTRAIT_OUTCOME_UNCERTAIN'})
                m=re.fullmatch(r'/v1/portrait-assets/(asset\.[a-f0-9]{48})(/content)?',path)
                if m and self.command=='GET':
                    r=registrar.get(m[1])
                    if r is None:return self.respond(404,{'error':'ASSET_NOT_FOUND'})
                    if not m[2]:return self.respond(200,r)
                    data=registrar.source.asset_get(r['asset']['canonicalAssetId'])
                    self.send_response(200);self.send_header('Content-Type','image/png');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
                return self.respond(404,{'error':'PORTRAIT_ROUTE'})
            if path.startswith('/v1/media-artifacts/'):
                media=getattr(self.server,'media_artifacts',None)
                if media is None:return self.respond(503,{'error':'MEDIA_NOT_CONFIGURED'})
                if self.headers.get('Origin') is not None or not media.authenticated(self.headers.get('Authorization')):return self.respond(403,{'error':'MEDIA_SERVICE_AUTH_REQUIRED'})
                if parsed.query or self.headers.get('Transfer-Encoding'):return self.respond(422,{'error':'MEDIA_QUERY_OR_TRANSFER_ENCODING'})
                parts=path.strip('/').split('/')
                try:
                    if path=='/v1/media-artifacts/publish' and self.command=='POST':return self.respond(200,media.publish(self.read_json(max_body=46*1024*1024)))
                    if self.command=='GET' and len(parts)==5 and parts[:3]==['v1','media-artifacts','receipts']:
                        value=media.receipt(parts[3],parts[4]);return self.respond(404 if value is None else 200,value)
                    if self.command=='GET' and len(parts)==4 and parts[:3]==['v1','media-artifacts','assets']:return self.respond(200,media.asset(parts[3]))
                    return self.respond(404,{'error':'MEDIA_ROUTE'})
                except OSError:return self.respond(503,{'error':'MEDIA_OUTCOME_UNCERTAIN'})
            if path in ('/api/knowledge','/v1/knowledge','/v1/data/knowledge','/knowledge.html','/knowledge.js','/knowledge.css') or path.startswith(('/api/knowledge/','/v1/knowledge/','/v1/data/knowledge/')):
                return self.respond(410, {'error':'knowledge_feature_removed'})
            if path.startswith('/v1/npcs/ownership/'):
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port) or self.headers.get('Origin') is not None:
                    return self.respond(403,{'error':'ownership_server_only'})
                from .pds_owned_transport import OwnedNPCTransport
                source=self.server.app.domains.npcs
                with source.store.factory() as transport:
                    if not isinstance(transport,OwnedNPCTransport):return self.respond(409,{'error':'npc_owned_mode_not_enabled'})
                    if path=='/v1/npcs/ownership/frame-maintenance/capability' and self.command=='GET':
                        if parsed.query:raise ValueError('maintenance_query')
                        return self.respond(200,transport.frame_maintenance_capability())
                    if path.startswith('/v1/npcs/ownership/frame-maintenance/'):
                        if self.command!='POST' or parsed.query:return self.respond(405,{'error':'maintenance_method'})
                        action=path.rsplit('/',1)[-1];p=self.read_json()
                        schemas={'begin':{'npcId','generationId','originalRequestId','maintenanceId','expectedSourceVersion','expectedManifestHash'},'reserve':{'guard','sha256','reservationId'},'release':{'guard','reservationId','reservationToken'},'finish':{'guard'}}
                        if action not in schemas or set(p)!=schemas[action]:raise ValueError('maintenance_schema')
                        return self.respond(200,transport.frame_maintenance(action,p))
                    if path=='/v1/npcs/ownership/migration/status' and self.command=='GET':
                        q=parse_qs(parsed.query,keep_blank_values=True)
                        if set(q)!={'requestId'} or len(q['requestId'])!=1 or not q['requestId'][0]:raise ValueError('migration_query')
                        return self.respond(200,transport.migration_status(q['requestId'][0]))
                    if path.startswith('/v1/npcs/ownership/migration/'):
                        if self.command!='POST' or parsed.query:return self.respond(405,{'error':'migration_method'})
                        p=self.read_json(max_body=64*1024*1024)
                        if path.endswith('/prepare'):
                            required={'npcId','requestId','expectedSourceVersion','planHash','changedDocuments','importAssets'}
                            if not required<=set(p) or set(p)-required-{'reviewedPlanHash'}:raise ValueError('migration_prepare_schema')
                            return self.respond(200,transport.migration_prepare(p))
                        if path.endswith('/asset'):
                            import base64,hashlib
                            if set(p)!={'requestId','token','assetId','base64'}:raise ValueError('migration_asset_schema')
                            data=base64.b64decode(p['base64'],validate=True)
                            if hashlib.sha256(data).hexdigest()!=p['assetId'].split('.')[0]:raise ValueError('migration_asset_hash')
                            return self.respond(200,transport.migration_asset(p['requestId'],p['token'],p['assetId'],data))
                        if path.endswith('/activate'):
                            if set(p)!={'requestId','expectedSourceVersion','planHash','backupManifestHash'}:raise ValueError('migration_activate_schema')
                            return self.respond(200,transport.migration_activate(p))
                        if path.endswith('/commit'):
                            if set(p)!={'requestId','token'}:raise ValueError('migration_commit_schema')
                            return self.respond(200,transport.migration_commit(p['requestId'],p['token']))
                        return self.respond(404,{'error':'migration_route'})
                    if path=='/v1/npcs/ownership/staged-delete-fence' and self.command=='GET':
                        q=parse_qs(parsed.query,keep_blank_values=True)
                        if set(q)!={'npcId','generationId','requestId'} or any(len(v)!=1 or not v[0] for v in q.values()):raise ValueError('ownership_query')
                        return self.respond(200,transport.staged_delete_fence(q['npcId'][0],q['generationId'][0],q['requestId'][0]))
                    if path=='/v1/npcs/ownership/generation' and self.command=='GET':
                        q=parse_qs(parsed.query,keep_blank_values=True)
                        if set(q)!={'npcId'} or len(q['npcId'])!=1:raise ValueError('ownership_query')
                        return self.respond(200,transport.get_generation(q['npcId'][0]))
                    if path=='/v1/npcs/ownership/publications' and self.command=='GET':
                        q=parse_qs(parsed.query,keep_blank_values=True)
                        if set(q)!={'npcId','generationId','parentRequestId'} or any(len(v)!=1 or not v[0] for v in q.values()):raise ValueError('ownership_query')
                        return self.respond(200,transport.publication_receipts(q['npcId'][0],q['generationId'][0],q['parentRequestId'][0]))
                    if path=='/v1/npcs/ownership/equivalent' and self.command=='GET':
                        q=parse_qs(parsed.query,keep_blank_values=True)
                        if set(q)!={'npcId','generationId','parentRequestId','sha256'} or any(len(v)!=1 or not v[0] for v in q.values()):raise ValueError('ownership_query')
                        return self.respond(200,transport.current_equivalent(q['npcId'][0],q['generationId'][0],q['parentRequestId'][0],q['sha256'][0]))
                    if path=='/v1/npcs/ownership/deletion-receipt' and self.command=='GET':
                        q=parse_qs(parsed.query,keep_blank_values=True)
                        if set(q)!={'requestId'} or len(q['requestId'])!=1 or not q['requestId'][0]:raise ValueError('ownership_query')
                        return self.respond(200,transport.get_deletion_receipt(q['requestId'][0]))
                    if parsed.query:raise ValueError('ownership_query')
                    p=self.read_json()
                    if path=='/v1/npcs/ownership/reserve-equivalent' and self.command=='POST':
                        if set(p)!={'npcId','generationId','parentRequestId','sha256','reservationId'}:raise ValueError('ownership_reservation_schema')
                        return self.respond(200,transport.reserve_equivalent(p['npcId'],p['generationId'],p['parentRequestId'],p['sha256'],p['reservationId']))
                    if path=='/v1/npcs/ownership/release-equivalent' and self.command=='POST':
                        if set(p)!={'npcId','generationId','parentRequestId','reservationId','reservationToken'}:raise ValueError('ownership_reservation_schema')
                        return self.respond(200,transport.release_equivalent(p['npcId'],p['generationId'],p['parentRequestId'],p['reservationId'],p['reservationToken']))
                    if path=='/v1/npcs/ownership/restore' and self.command=='POST':
                        if set(p)!={'requestId','inputHash','backupId','expectedRevision'}:raise ValueError('ownership_restore_commit_schema')
                        return self.respond(200,source.restore_character(p['requestId'],p['inputHash'],p['backupId'],p['expectedRevision']))
                    if path=='/v1/npcs/ownership/abandon-creation' and self.command=='POST':
                        if set(p)!={'guard'}:raise ValueError('ownership_abandon_creation_schema')
                        return self.respond(200,transport.abandon_creation(p['guard']))
                    if path=='/v1/npcs/ownership/prepare-create' and self.command=='POST':
                        if set(p)!={'npcId','requestId','expectedSourceVersion'}:raise ValueError('ownership_create_schema')
                        return self.respond(200,transport.prepare_create(p['npcId'],p['requestId'],p['expectedSourceVersion']))
                    if path=='/v1/npcs/ownership/prepare-restore' and self.command=='POST':
                        if set(p)!={'npcId','requestId','backupId','expectedSourceVersion'}:raise ValueError('ownership_restore_schema')
                        return self.respond(200,transport.prepare_restore(p['npcId'],p['requestId'],p['backupId'],p['expectedSourceVersion']))
                    if path=='/v1/npcs/ownership/admit' and self.command=='POST':
                        if set(p)!={'npcId','generationId','requestId'}:raise ValueError('ownership_admission_schema')
                        return self.respond(200,transport.admit(p['npcId'],p['generationId'],p['requestId']))
                    if path=='/v1/npcs/ownership/finish' and self.command=='POST':
                        if set(p)!={'guard'}:raise ValueError('ownership_finish_schema')
                        transport.guard=p['guard'];return self.respond(200,transport.finish())
                    return self.respond(405,{'error':'ownership_method'})
            if path in ('/api/worker-context/catalog','/api/worker-context/read','/api/worker-context/create-read'):
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port) or self.headers.get('Origin') is not None:
                    return self.respond(403, {'error':'worker_context_server_only'})
                from .planning_items import ContextConflict, ContextMissing
                try:
                    if path.endswith('/catalog') and self.command=='GET':
                        q=parse_qs(parsed.query,keep_blank_values=True)
                        if not {'projectId','npcId'}<=set(q) or set(q)-{'projectId','npcId','workerId'} or any(len(v)!=1 for v in q.values()):raise DataError('worker_context_query')
                        return self.respond(200,self.server.planning_items.catalog(q['projectId'][0],q['npcId'][0],q.get('workerId',['npc-portrait'])[0]))
                    if path.endswith('/create-read') and self.command=='POST' and not parsed.query:
                        return self.respond(200,self.server.planning_items.create_read(self.read_json()))
                    if path.endswith('/read') and self.command=='POST' and not parsed.query:
                        return self.respond(200,self.server.planning_items.read(self.read_json()))
                    return self.respond(405, {'error':'worker_context_read_only'})
                except ContextConflict as exc:return self.respond(409, {'error':str(exc)})
                except ContextMissing as exc:return self.respond(404, {'error':str(exc)})
                except OSError:return self.respond(503, {'error':'worker_context_source_unavailable'})
            if path == '/v1/published-files/status':
                if self.client_address[0] != '127.0.0.1' or self.headers.get('Host') != '127.0.0.1:' + str(self.server.server_port) or self.headers.get('Origin') is not None:
                    return self.respond(403, {'error':'bridge_loopback_server_only'})
                if parsed.query or self.command != 'GET':
                    return self.respond(405, {'error':'bridge_read_only'})
                return self.respond(200, self.server.filex_bridge.status())
            if path in ('/v1/concept/lifecycle','/v1/concept/lifecycle/recover'):
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port) or self.headers.get('Origin') is not None:
                    return self.respond(403,{'error':'concept_lifecycle_server_only'})
                from .concept_filex import FilexConcept
                from .concept_lifecycle import execute
                if not isinstance(self.server.app.concept,FilexConcept):return self.respond(503,{'error':'concept_filex_cutover_required'})
                if parsed.query:return self.respond(422,{'error':'concept_query_unsupported'})
                if self.command!='POST':return self.respond(405,{'error':'method_not_allowed'})
                try:return self.respond(200,{'receipt':execute(self.server.app.concept,self.read_json(),recover_only=path.endswith('/recover'))})
                except (ValueError,KeyError,TypeError) as exc:return self.respond(422,{'error':str(exc)})
            if path in ('/v1/concept/publications','/v1/concept/publications/recover'):
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port) or self.headers.get('Origin') is not None:
                    return self.respond(403,{'error':'concept_publication_server_only'})
                from .concept_filex import FilexConcept
                from .concept_publications import publish,recover_publication
                if not isinstance(self.server.app.concept,FilexConcept):return self.respond(503,{'error':'concept_filex_cutover_required'})
                if parsed.query:return self.respond(422,{'error':'concept_query_unsupported'})
                if self.command!='POST':return self.respond(405,{'error':'method_not_allowed'})
                try:
                    payload=self.read_json()
                    return self.respond(200,{'receipt':recover_publication(self.server.app.concept,payload)} if path.endswith('/recover') else publish(self.server.app.concept,payload))
                except (ValueError,KeyError,TypeError) as exc:return self.respond(422,{'error':str(exc)})
            if path=='/v1/concept/requests' or path.startswith('/v1/concept/requests/'):
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port) or self.headers.get('Origin') is not None:
                    return self.respond(403,{'error':'concept_formal_server_only'})
                from .concept_requests import ConceptRequests
                from .concept_authoring import CodexConceptAuthor
                from .concept_filex import FilexConcept
                from .request_primitives import RequestJournal
                if not isinstance(self.server.app.concept,FilexConcept):return self.respond(503,{'error':'concept_filex_cutover_required'})
                journal=RequestJournal(self.server.app.domains.root.parent/'concept-requests.sqlite3')
                service=ConceptRequests(journal,self.server.app.concept,CodexConceptAuthor())
                try:
                    if parsed.query:return self.respond(422,{'error':'concept_query_unsupported'})
                    if path=='/v1/concept/requests' and self.command=='POST':return self.respond(200,service.submit(self.read_json(max_body=None)))
                    if self.command=='POST' and path.endswith('/resume'):
                        payload=self.read_json()
                        if set(payload)!={'inputHash'}:return self.respond(422,{'error':'concept_resume_binding_required'})
                        return self.respond(200,service.resume(unquote(path.split('/')[-2]),payload['inputHash']))
                    if self.command=='GET':
                        value=service.view(unquote(path.rsplit('/',1)[-1]))
                        return self.respond(200,value) if value else self.respond(404,{'error':'concept_request_not_found'})
                    return self.respond(405,{'error':'method_not_allowed'})
                except (ValueError,KeyError,TypeError) as exc:return self.respond(422,{'error':str(exc)})
                finally:journal.close()
            if path in ('/api/concept','/api/concept/publication-capability','/api/concept/publication-preview','/api/revisions','/docs/MasterConcept.schema.json','/v1/concept/export') or path.startswith('/api/revisions/'):
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host') not in ('127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port)) or self.headers.get('Origin') not in (None,'http://127.0.0.1:'+str(self.server.server_port),'http://localhost:'+str(self.server.server_port)):
                    return self.respond(403,{'error':'concept_loopback_required'})
                if parsed.query:return self.respond(422,{'error':'concept_query_unsupported'})
                module=self.server.app.concept
                if path=='/api/concept/publication-preview' and self.command=='POST':
                    return self.respond(200,module.publication_preview(self.read_json()))
                if path=='/api/concept' and self.command=='PUT':
                    value,replayed=module.save(self.read_json());return self.respond(200,value,{'Idempotency-Replayed':str(replayed).lower()})
                if self.command not in ('GET','HEAD'):return self.respond(405,{'error':'method_not_allowed'})
                if path=='/api/concept/publication-capability':value=module.publication_capability()
                elif path=='/api/concept':value=module.current()
                elif path=='/api/revisions':value={'revisions':module.history()}
                elif path=='/v1/concept/export':value=module.export()
                elif path=='/docs/MasterConcept.schema.json':value=json.loads((Path(__file__).parent/'concept_validation/schema.json').read_text())
                else:
                    n=path.rsplit('/',1)[-1]
                    if not n.isdecimal():return self.respond(404,{'error':'not_found'})
                    value=module.revision(int(n))
                return self.respond(200,value) if value is not None else self.respond(404,{'error':'concept_not_found'})
            if path=='/v1/domains/export':
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port) or self.headers.get('Origin') not in (None,'http://127.0.0.1:'+str(self.server.server_port)):return self.respond(403,{'error':'loopback_required'})
                if self.command not in ('GET','HEAD'):return self.respond(405,{'error':'method_not_allowed'})
                if parsed.query not in ('','history=all'):return self.respond(422,{'error':'invalid_export_scope'})
                return self.respond(200,self.server.app.domains.capture(parsed.query=='history=all'))
            if path == '/v1/motion-presets' or path.startswith('/v1/motion-presets/'):
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port) or self.headers.get('Origin') is not None:return self.respond(403,{'error':'motion_presets_server_only'})
                if parsed.query:return self.respond(422,{'error':'motion_presets_query_unsupported'})
                from .motion_presets import MotionPresets
                service=MotionPresets()
                try:
                    if path=='/v1/motion-presets' and self.command=='GET':return self.respond(200,service.list())
                    if path=='/v1/motion-presets/register' and self.command=='POST':return self.respond(200,service.register(self.read_json(max_body=256*1024*1024)))
                    parts=path.split('/')
                    if len(parts)==5 and self.command=='GET':return self.respond(200,service.get(parts[3],parts[4]))
                    return self.respond(405,{'error':'method_not_allowed'})
                except (ValueError,KeyError,TypeError) as exc:return self.respond(422,{'error':str(exc)})
            if path.startswith('/v1/npcs/animation-frames/'):
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port) or self.headers.get('Origin') is not None:return self.respond(403,{'error':'animation_frames_server_only'})
                if parsed.query:return self.respond(422,{'error':'animation_frames_query_unsupported'})
                from .npc_animation_frames import AnimationFramePublisher
                try:
                    service=AnimationFramePublisher(self.server.app.domains)
                    if path=='/v1/npcs/animation-frames/publish' and self.command=='POST':return self.respond(200,service.publish(self.read_json(max_body=256*1024*1024)))
                    if path.startswith('/v1/npcs/animation-frames/requests/') and self.command=='GET':
                        value=service.receipt(path.rsplit('/',1)[-1]);return self.respond(200,value) if value else self.respond(404,{'error':'animation_frames_request_not_found'})
                    return self.respond(405,{'error':'method_not_allowed'})
                except (ValueError,KeyError,TypeError,DataError) as exc:return self.respond(422,{'error':str(exc)})
            if path.startswith('/v1/npcs/pose-views/'):
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port) or self.headers.get('Origin') is not None:return self.respond(403,{'error':'pose_views_server_only'})
                if parsed.query:return self.respond(422,{'error':'pose_views_query_unsupported'})
                from .npc_pose_views import PoseViewPublisher
                try:
                    service=PoseViewPublisher(self.server.app.domains)
                    if path=='/v1/npcs/pose-views/publish' and self.command=='POST':return self.respond(200,service.publish(self.read_json(max_body=None)))
                    if path.startswith('/v1/npcs/pose-views/requests/') and self.command=='GET':
                        value=service.receipt(path.rsplit('/',1)[-1]);return self.respond(200,value)if value else self.respond(404,{'error':'pose_view_request_not_found'})
                    return self.respond(405,{'error':'method_not_allowed'})
                except (ValueError,KeyError,TypeError,DataError)as exc:return self.respond(422,{'error':str(exc)})
            if path.startswith('/v1/npcs/animation-experiments/') or path=='/v1/npcs/animations/publish' or path.startswith('/v1/npcs/animations/requests/') or path.startswith('/assets/animations/'):
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port) or self.headers.get('Origin') is not None:return self.respond(403,{'error':'animation_server_only'})
                if parsed.query:return self.respond(422,{'error':'animation_query_unsupported'})
                from .npc_animations import AnimationPublisher,ASSET
                try:
                    if path.startswith('/assets/animations/') and self.command in ('GET','HEAD'):
                        aid=path.rsplit('/',1)[-1]
                        if not ASSET.fullmatch(aid):return self.respond(422,{'error':'animation_asset_invalid'})
                        data=self.server.app.domains.npcs.asset_get(aid)
                        self.send_response(200);self.send_header('Content-Type','video/mp4');self.send_header('Content-Length',str(len(data)));self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store');self.end_headers()
                        if self.command=='GET':self.wfile.write(data)
                        return
                    if path.startswith('/v1/npcs/animation-experiments/'):
                        from .npc_animation_experiments import AnimationExperimentPublisher
                        service=AnimationExperimentPublisher(self.server.app.domains)
                        if path=='/v1/npcs/animation-experiments/publish' and self.command=='POST':return self.respond(200,service.publish(self.read_json(max_body=None)))
                        if path.startswith('/v1/npcs/animation-experiments/requests/') and self.command=='GET':
                            result=service.receipt(path.rsplit('/',1)[-1]);return self.respond(200,result)if result else self.respond(404,{'error':'experiment_request_not_found'})
                        return self.respond(405,{'error':'method_not_allowed'})
                    service=AnimationPublisher(self.server.app.domains)
                    if path=='/v1/npcs/animations/publish' and self.command=='POST':return self.respond(200,service.publish(self.read_json(max_body=None)))
                    if path.startswith('/v1/npcs/animations/requests/') and self.command=='GET':
                        result=service.receipt(path.rsplit('/',1)[-1]);return self.respond(200,result)if result else self.respond(404,{'error':'animation_request_not_found'})
                    return self.respond(405,{'error':'method_not_allowed'})
                except (ValueError,KeyError,TypeError)as exc:return self.respond(422,{'error':str(exc)})
            if path=='/v1/npcs/files':
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port) or self.headers.get('Origin') is not None:return self.respond(403,{'error':'npc_files_server_only'})
                if self.command not in ('GET','HEAD'):return self.respond(405,{'error':'method_not_allowed'})
                if parsed.query:return self.respond(422,{'error':'npc_query_unsupported'})
                try:return self.respond(200,self.server.app.domains.npc_files())
                except (DataError,ValueError,KeyError,TypeError,OSError) as exc:return self.respond(503,{'error':'npc_filex_source_unavailable','detail':type(exc).__name__})
            if path in ('/v1/npc-category/file','/v1/npc-category/file-replace'):
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port) or self.headers.get('Origin') is not None:return self.respond(403,{'error':'category_file_server_only'})
                from .npc_category_files import CategoryFiles, MAX_FILE, header, parse_header
                service=CategoryFiles(self.server.app.domains)
                if path.endswith('/file') and self.command=='GET':
                    q=parse_qs(parsed.query,keep_blank_values=True)
                    if set(q)-{'mode'}!={'npcId','categoryId','expectedRevision'} or any(len(v)!=1 for v in q.values()):raise DataError('CATEGORY_FILE_QUERY_INVALID')
                    raw,basis=service.download(q['npcId'][0],q['categoryId'][0],int(q['expectedRevision'][0]),q.get('mode',['modify'])[0])
                    self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Length',str(len(raw)));self.send_header('X-NPC-File-Basis',header(basis));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw);return
                if path.endswith('/file-replace') and self.command=='POST':
                    if parsed.query:raise DataError('CATEGORY_FILE_QUERY_INVALID')
                    length=self.headers.get('Content-Length','')
                    if not length.isdecimal() or not 0<int(length)<=MAX_FILE:raise DataError('CATEGORY_FILE_SIZE_INVALID')
                    if self.headers.get('Content-Type')!='application/octet-stream':raise DataError('CATEGORY_FILE_CONTENT_TYPE_INVALID')
                    raw=self.rfile.read(int(length))
                    if len(raw)!=int(length):raise DataError('CATEGORY_FILE_TRUNCATED')
                    return self.respond(200,service.replace(parse_header(self.headers.get('X-NPC-File-Replace')),raw))
                return self.respond(405,{'error':'method_not_allowed'})
            if path=='/v1/npc-category/apply' or path.startswith('/v1/npc-category/receipts/'):
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port) or self.headers.get('Origin') not in (None,'http://127.0.0.1:'+str(self.server.server_port)):return self.respond(403,{'error':'loopback_required'})
                if path=='/v1/npc-category/apply' and self.command=='POST':return self.respond(200,self.server.app.domains.category(self.read_json(max_body=MAX_CATEGORY_APPLY_BODY)))
                if self.command=='GET':
                    value=self.server.app.domains.category_receipt(path.rsplit('/',1)[-1]);return self.respond(200,value) if value else self.respond(404,{'error':'receipt_not_found'})
                return self.respond(405,{'error':'method_not_allowed'})
            if path.startswith(('/api/npcs','/api/story','/assets/')) or path=='/api/portraits' or path in native.ASSETS:
                return self.server.app.domains.dispatch(self)
            if path in ('/v1/project-display/binari','/v1/project-display/binari/icon') or path.startswith('/v1/project-display/binari/requests/'):
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port) or self.headers.get('Origin') not in (None,'http://127.0.0.1:'+str(self.server.server_port)):
                    return self.respond(403, {'error':'display_loopback_required'})
                if path.endswith('/icon') and self.command=='GET':
                    q=parse_qs(parsed.query,keep_blank_values=True)
                    if set(q)!={'sha256'} or len(q['sha256'])!=1: raise DataError('one icon hash required')
                    value=self.server.app.get_project_display_icon(q['sha256'][0])
                    return self.respond(200,value) if value else self.respond(404,{'error':'display_asset_not_found'})
                if path=='/v1/project-display/binari/requests/history' and self.command=='GET':
                    if parsed.query: raise DataError('display query unsupported')
                    return self.respond(200,self.server.app.get_project_display_history())
                if path.startswith('/v1/project-display/binari/requests/') and self.command=='GET':
                    request_id=path.split('/')[-1]
                    if parsed.query or not re.fullmatch(r'icon\.[a-f0-9]{64}',request_id): raise DataError('invalid display request id')
                    value=self.server.app.get_project_display_receipt(request_id)
                    return self.respond(200,value) if value else self.respond(404,{'error':'display_receipt_not_found'})
                if path!='/v1/project-display/binari': return self.respond(405,{'error':'method_not_allowed'})
                if parsed.query: raise DataError('display query unsupported')
                if self.command in ('GET','HEAD'): return self.respond(200,self.server.app.get_project_display())
                if self.command=='POST':
                    status,value,replayed=self.server.app.put_project_display(self.read_json())
                    return self.respond(status,value,{'Idempotency-Replayed':str(replayed).lower()})
                return self.respond(405, {'error':'method_not_allowed'})
            if path == "/health" and self.command in ("GET", "HEAD"):
                health={"ok": True, "service": "project-data-service",
                        "schemaVersion": self.server.app.store.schema_version()}
                source=getattr(getattr(self.server,'direct_portrait_pairs',None),'representative_first_source',None)
                if source is not None:health['oneStopRepresentativePortraitSourceSha256']=source
                binding=getattr(self.server,'filex_writer_binding_sha256',None)
                if binding is not None:health['filexWriterScopesSha256']=binding
                return self.respond(200,health)
            if path == "/api/storage/status" and self.command in ("GET", "HEAD"):
                return self.respond(200, self.server.app.storage.status())
            if path == "/api/schema/version" and self.command in ("GET", "HEAD"):
                return self.respond(200, {"version": self.server.app.store.schema_version()})
            if path == "/v1/workflows/sessions" and self.command in ("GET", "HEAD"):
                query = parse_qs(parsed.query, keep_blank_values=True)
                if set(query) - {"status", "limit"}:
                    raise DataError("unsupported workflow list filter")
                status_filter = query.get("status", [None])[0]
                limit_value = query.get("limit", ["100"])[0]
                if not str(limit_value).isdigit():
                    raise DataError("limit must be an integer")
                return self.respond(200, {"items": self.server.app.list_workflow_sessions(
                    status=status_filter, limit=int(limit_value)
                )})
            workflow_events_match = re.fullmatch(r"/v1/workflows/sessions/([^/]+)/events", path)
            if workflow_events_match and self.command in ("GET", "HEAD"):
                session_id = unquote(workflow_events_match.group(1))
                if self.server.app.get_workflow_session(session_id) is None:
                    return self.respond(404, {"error": "session_not_found"})
                return self.respond(200, {"items": self.server.app.workflow_events(session_id)})
            workflow_artifact_match = re.fullmatch(
                r"/v1/workflows/sessions/([^/]+)/artifacts/([^/]+)", path
            )
            if workflow_artifact_match and self.command in ("GET", "HEAD"):
                value = self.server.app.get_workflow_artifact(
                    unquote(workflow_artifact_match.group(1)), unquote(workflow_artifact_match.group(2))
                )
                return self.respond(200, value) if value else self.respond(404, {"error": "artifact_not_found"})
            workflow_steps_match = re.fullmatch(r"/v1/workflows/sessions/([^/]+)/steps", path)
            if workflow_steps_match and self.command in ("GET", "HEAD"):
                value = self.server.app.get_workflow_steps(unquote(workflow_steps_match.group(1)))
                return self.respond(200, value) if value else self.respond(404, {"error": "session_not_found"})
            workflow_session_match = re.fullmatch(r"/v1/workflows/sessions/([^/]+)", path)
            if workflow_session_match and self.command in ("GET", "HEAD"):
                value = self.server.app.get_workflow_session(unquote(workflow_session_match.group(1)))
                return self.respond(200, value) if value else self.respond(404, {"error": "session_not_found"})
            if workflow_session_match and self.command == "PUT":
                status, value, replayed = self.server.app.put_workflow_session(
                    unquote(workflow_session_match.group(1)), self.read_json()
                )
                return self.respond(status, value, {"Idempotency-Replayed": str(replayed).lower()})
            data_item_match = re.fullmatch(r"/v1/data/([^/]+)/([^/]+)", path)
            if data_item_match and self.command in ("GET", "HEAD"):
                value = self.server.app.get_data_resource(
                    unquote(data_item_match.group(1)), unquote(data_item_match.group(2))
                )
                return self.respond(200, value) if value else self.respond(404, {"error": "resource_not_found"})
            if data_item_match and self.command == "PUT":
                status, value, replayed = self.server.app.put_data_resource(
                    unquote(data_item_match.group(1)), unquote(data_item_match.group(2)), self.read_json()
                )
                return self.respond(status, value, {"Idempotency-Replayed": str(replayed).lower()})
            data_list_match = re.fullmatch(r"/v1/data/([^/]+)", path)
            if data_list_match and self.command in ("GET", "HEAD"):
                query = parse_qs(parsed.query, keep_blank_values=True)
                if set(query) - {"status"} or len(query.get("status", [])) > 1:
                    raise DataError("only one status filter is supported")
                status_filter = query.get("status", [None])[0]
                values = self.server.app.list_data_resources(unquote(data_list_match.group(1)), status_filter)
                return self.respond(200, {"items": values})
            if path == "/api/projects" and self.command == "POST":
                status, value, replayed = self.server.app.create_project(self.read_json())
                return self.respond(status, value, {"Idempotency-Replayed": str(replayed).lower()})
            project_match = re.fullmatch(r"/api/projects/([^/]+)", path)
            if project_match and self.command in ("GET", "HEAD"):
                value = self.server.app.get_project(unquote(project_match.group(1)))
                return self.respond(200, value) if value else self.respond(404, {"error": "project_not_found"})
            if project_match and self.command == "PUT":
                status, value, replayed = self.server.app.update_project(
                    unquote(project_match.group(1)), self.read_json()
                )
                return self.respond(status, value, {"Idempotency-Replayed": str(replayed).lower()})
            if path == "/api/assets/register" and self.command == "POST":
                status, value, replayed = self.server.app.register_asset(self.read_json())
                return self.respond(status, value, {"Idempotency-Replayed": str(replayed).lower()})
            asset_match = re.fullmatch(r"/api/assets/([^/]+)", path)
            if asset_match and self.command in ("GET", "HEAD"):
                registrar=getattr(self.server,'portrait_registrar',None)
                if registrar is not None and re.fullmatch(r'asset\.[a-f0-9]{48}',unquote(asset_match.group(1))):
                    r=registrar.get(unquote(asset_match.group(1)))
                    if r is not None:return self.respond(200,r['asset'])
                value = self.server.app.get_asset(unquote(asset_match.group(1)))
                return self.respond(200, value) if value else self.respond(404, {"error": "asset_not_found"})
            audit_match = re.fullmatch(r"/api/audit/([^/]+)/([^/]+)", path)
            if audit_match and self.command in ("GET", "HEAD"):
                value = self.server.app.audit_events(unquote(audit_match.group(1)), unquote(audit_match.group(2)))
                return self.respond(200, {"events": value})
            return self.respond(404, {"error": "not_found"})
        except RevisionConflict as exc:
            return self.respond(409, {"error": str(exc)})
        except (DataError, StorageError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return self.respond(422, {"error": str(exc)})

    def _direct_image_body(self,pair,path,maximum,meta_max):
        if path.endswith('/read'):return self.respond(200,pair.read(self.read_json(meta_max)))
        n=self.headers.get('Content-Length','')
        if self.headers.get('Content-Type')!='application/octet-stream' or not n.isdecimal() or not 0<int(n)<=maximum:raise ValueError('PAIR_BODY_LIMIT')
        self.connection.settimeout(60);body=self.rfile.read(int(n))
        if len(body)!=int(n):raise ValueError('PAIR_TRUNCATED')
        return self.respond(200,pair.publish(body))

    def do_GET(self): self.route()
    def do_HEAD(self): self.route()
    def do_POST(self): self.route()
    def do_PUT(self): self.route()


def serve(storage_root: Path, host: str, port: int):
    import os
    from .filex_writer_binding import CONFIG_ENV, fingerprint
    writer_binding_sha256=fingerprint() if os.environ.get(CONFIG_ENV) else None
    app = ProjectDataService(storage_root)
    server = ThreadingHTTPServer((host, port), Handler)
    server.app = app
    server.filex_writer_binding_sha256=writer_binding_sha256
    claim_keys=('PDS_WORKFLOW_DELETE_PLANNER_DB','PDS_WORKFLOW_DELETE_PLANNER_DEV','PDS_WORKFLOW_DELETE_PLANNER_INODE')
    if any(key in os.environ for key in claim_keys):
        from .workflow_delete import WorkflowDelete,PrivatePlannerClaim
        if host!='127.0.0.1' or not all(os.environ.get(key) for key in claim_keys):
            raise ValueError('WORKFLOW_DELETE_LOCAL_CONFIG_REQUIRED')
        path,dev,inode=(os.environ[key] for key in claim_keys)
        planner=PrivatePlannerClaim(path,int(dev),int(inode))
        server.workflow_delete=WorkflowDelete(app.domains.npcs,planner)
    npc_folder_delete=os.environ.get('PDS_WORKFLOW_NPC_FOLDER_DELETE','0')
    if npc_folder_delete not in ('0','1'):raise ValueError('WORKFLOW_NPC_FOLDER_DELETE_OPT_IN')
    concept_workflow=os.environ.get('PDS_WORKFLOW_CONCEPT_CATEGORY','0')
    if concept_workflow not in ('0','1'):raise ValueError('CONCEPT_WORKFLOW_OPT_IN')
    if concept_workflow=='1':
        from .workflow_concept_category import CategoryWorkflow
        if not hasattr(server,'workflow_delete'):raise ValueError('CONCEPT_WORKFLOW_PLANNER_PIN_REQUIRED')
        server.workflow_concept_category=CategoryWorkflow(app.concept,planner)
    from .workflow_motion_2d import configured as configure_motion_2d
    server.workflow_motion_2d=configure_motion_2d(
        planner if hasattr(server,'workflow_delete') else None,storage_root,host,
        os.environ.get('PDS_WORKFLOW_MOTION_2D','auto'))
    planning_update=os.environ.get('PDS_WORKFLOW_NPC_PLANNING_UPDATE','0')
    if planning_update not in ('0','1'):raise ValueError('NPC_PLANNING_UPDATE_OPT_IN')
    if planning_update=='1':
        from .workflow_npc_planning_update import PlanningUpdate
        if not hasattr(server,'workflow_delete'):raise ValueError('NPC_PLANNING_UPDATE_PLANNER_PIN_REQUIRED')
        server.workflow_npc_planning_update=PlanningUpdate(app.domains.npcs,planner)
    bgm=os.environ.get('PDS_WORKFLOW_BGM','0')
    if bgm not in ('0','1'):raise ValueError('WORKFLOW_BGM_OPT_IN')
    if bgm=='1':
        from .workflow_audio import WorkflowAudio
        if not hasattr(server,'workflow_delete'):raise ValueError('WORKFLOW_BGM_PLANNER_PIN_REQUIRED')
        from .filex_writer_binding import writer
        server.workflow_audio=WorkflowAudio(app.concept,planner,writer_factory=lambda:writer('audio'))
        from .workflow_audio_separate import WorkflowAudioSeparate
        server.workflow_audio_separate=WorkflowAudioSeparate(app.concept,planner,writer_factory=lambda:writer('audio'))
    planning_add=os.environ.get('PDS_WORKFLOW_NPC_PLANNING_ADD','0')
    if planning_add not in ('0','1'):raise ValueError('NPC_PLANNING_ADD_OPT_IN')
    if planning_add=='1':
        from .workflow_npc_planning_add import PlanningAdd
        if not hasattr(server,'workflow_delete'):raise ValueError('NPC_PLANNING_ADD_PLANNER_PIN_REQUIRED')
        server.workflow_npc_planning_add=PlanningAdd(app.domains.npcs,planner)
    preset_enabled=os.environ.get('PDS_WORKFLOW_PRESET','0')
    if preset_enabled not in ('0','1'):raise ValueError('WORKFLOW_PRESET_OPT_IN')
    if preset_enabled=='1':
        from .workflow_preset import WorkflowPreset
        if not hasattr(server,'workflow_delete'):raise ValueError('WORKFLOW_PRESET_PLANNER_PIN_REQUIRED')
        server.workflow_preset=WorkflowPreset(app.domains.npcs,planner)
    concept_folder_delete=os.environ.get('PDS_WORKFLOW_CONCEPT_FOLDER_DELETE','0')
    if concept_folder_delete not in ('0','1'):raise ValueError('WORKFLOW_CONCEPT_FOLDER_DELETE_OPT_IN')
    audio_delete=os.environ.get('PDS_WORKFLOW_AUDIO_DELETE','0')
    if audio_delete not in ('0','1'):raise ValueError('WORKFLOW_AUDIO_DELETE_OPT_IN')
    if npc_folder_delete=='1' or concept_folder_delete=='1' or audio_delete=='1':
        from .single_target_delete import SingleTargetDelete
        roots={}
        if npc_folder_delete=='1':
            from .workflow_delete import NPC_ROOT
            if not hasattr(server,'workflow_preset'):raise ValueError('WORKFLOW_NPC_FOLDER_DELETE_PRESET_REQUIRED')
            roots[NPC_ROOT]=server.workflow_delete.storage_provider()
        if concept_folder_delete=='1':
            from .workflow_concept_category import CONCEPT_ROOT
            if not hasattr(server,'workflow_concept_category'):raise ValueError('WORKFLOW_CONCEPT_FOLDER_DELETE_CATEGORY_REQUIRED')
            roots[CONCEPT_ROOT]=server.workflow_concept_category.storage_provider()
        if audio_delete=='1':
            from .workflow_audio_delete import ROOT,storage_provider
            if not hasattr(server,'workflow_delete'):raise ValueError('WORKFLOW_AUDIO_DELETE_PLANNER_PIN_REQUIRED')
            from .filex_writer_binding import writer
            roots[ROOT]=storage_provider(app.concept,planner,writer_factory=lambda:writer('audio'))
        server.single_target_delete=SingleTargetDelete(roots)
    representative_enabled=os.environ.get('PDS_WORKFLOW_PLAN_REPRESENTATIVE','0')
    if representative_enabled not in ('0','1'):raise ValueError('WORKFLOW_REPRESENTATIVE_OPT_IN')
    if representative_enabled=='1':
        from .workflow_plan_representative import RepresentativePlan
        if not hasattr(server,'workflow_preset'):raise ValueError('WORKFLOW_REPRESENTATIVE_PRESET_REQUIRED')
        server.workflow_plan_representative=RepresentativePlan(app.domains.npcs,planner,app.concept)
    native_enabled=os.environ.get('PDS_WORKFLOW_NATIVE_MEDIA','0')
    if native_enabled not in ('0','1'):raise ValueError('WORKFLOW_MEDIA_OPT_IN')
    if native_enabled=='1':
        from .workflow_native_media import NativeMedia
        if not hasattr(server,'workflow_delete'):raise ValueError('WORKFLOW_MEDIA_PLANNER_PIN_REQUIRED')
        server.workflow_native_media=NativeMedia(app.domains.npcs,planner)
    idle_plan_enabled=os.environ.get('PDS_WORKFLOW_PLAN_IDLE','0')
    if idle_plan_enabled not in ('0','1'):raise ValueError('WORKFLOW_IDLE_PLAN_OPT_IN')
    if idle_plan_enabled=='1':
        from .workflow_plan_idle import IdlePlan
        if not hasattr(server,'workflow_delete'):raise ValueError('WORKFLOW_IDLE_PLAN_PLANNER_PIN_REQUIRED')
        server.workflow_plan_idle=IdlePlan(app.domains.npcs,planner)
    shared_plan=os.environ.get('PDS_WORKFLOW_PLAN_SHARED','0')
    if shared_plan not in ('0','1'):raise ValueError('WORKFLOW_SHARED_PLAN_OPT_IN')
    if shared_plan=='1':
        from .workflow_plan_idle import IdlePlan
        if not hasattr(server,'workflow_delete'):raise ValueError('WORKFLOW_SHARED_PLAN_PLANNER_PIN_REQUIRED')
        for stage in ('overview','portrait-art'):
            setattr(server,'workflow_plan_'+stage.replace('-','_'),IdlePlan(app.domains.npcs,planner,stage=stage))
    portrait_enabled=os.environ.get('PDS_WORKFLOW_PORTRAIT_REPRESENTATIVE','0')
    if portrait_enabled not in ('0','1'):raise ValueError('WORKFLOW_PORTRAIT_OPT_IN')
    if portrait_enabled=='1':
        from .workflow_portrait_representative import RepresentativePortrait
        if not hasattr(server,'workflow_delete'):raise ValueError('WORKFLOW_PORTRAIT_PLANNER_PIN_REQUIRED')
        server.workflow_portrait_representative=RepresentativePortrait(app.domains.npcs,planner)
    for mode,suffix in (('idle','IDLE_REFERENCE'),('direction','DIRECTION')):
        enabled=os.environ.get('PDS_WORKFLOW_PORTRAIT_'+suffix,'0')
        if enabled not in ('0','1'):raise ValueError('WORKFLOW_PORTRAIT_OPT_IN')
        if enabled=='1':
            from .workflow_portrait_representative import RepresentativePortrait
            if not hasattr(server,'workflow_delete'):raise ValueError('WORKFLOW_PORTRAIT_PLANNER_PIN_REQUIRED')
            setattr(server,'workflow_portrait_'+mode,RepresentativePortrait(app.domains.npcs,planner,mode=mode))
    if (shared_plan==native_enabled==portrait_enabled=='1'
        and hasattr(server,'workflow_portrait_idle') and hasattr(server,'workflow_portrait_direction')):
        from .workflow_plan_idle import IdlePlan
        from .workflow_portrait_representative import RepresentativePortrait
        server.workflow_plan_combat=IdlePlan(app.domains.npcs,planner,stage='combat')
        for mode in ('combat','combat_direction'):
            setattr(server,'workflow_portrait_'+mode,RepresentativePortrait(app.domains.npcs,planner,mode=mode))
        server.workflow_delete.pose_recipes_ready=True
    project_folders_root = os.environ.get('PDS_PROJECT_FOLDERS_CANONICAL_ROOT')
    if project_folders_root:
        from .project_folders import configured as configure_project_folders
        server.project_folders = configure_project_folders(project_folders_root, host, validate_mount=False)
        checkpoints=[]
        if npc_folder_delete=='1' or planning_update=='1' or planning_add=='1' or shared_plan=='1':
            checkpoints.append((('binari','npcs'),'.owned-tx.json'))
        if concept_folder_delete=='1':checkpoints.append((('binari','concept'),'.concept-markdown.json'))
        if checkpoints:server.project_folders.read_checkpoints=tuple(checkpoints)
    if os.environ.get('PDS_MEDIA_ARTIFACT_CONFIG'):
        from .media_artifacts import MediaArtifacts
        server.media_artifacts=MediaArtifacts.configured(os.environ['PDS_MEDIA_ARTIFACT_CONFIG'])
    prop_packages=os.environ.get('PDS_WORKFLOW_PROP_STATIC','0')
    if prop_packages not in ('0','1'):raise ValueError('PDS_WORKFLOW_PROP_STATIC_INVALID')
    if prop_packages=='1':
        from .workflow_prop_stage import configure_static
        stage_root=os.environ.get('PDS_WORKFLOW_PROP_STAGE_ROOT')
        if not stage_root or not hasattr(server,'workflow_delete'):raise ValueError('PDS_WORKFLOW_PROP_AUTHORITY_REQUIRED')
        configure_static(server,host,planner,app.concept,stage_root,
                         ready_file=os.environ.get('PDS_WORKFLOW_PROP_ANIMATION_READY_FILE'))
    if host=='127.0.0.1' and hasattr(server,'workflow_delete'):
        from .workflow_cinematic import WorkflowCinematic
        server.workflow_cinematic=WorkflowCinematic(planner)
        from .workflow_cinematic_sequence import WorkflowCinematicSequence
        server.workflow_cinematic_sequence=WorkflowCinematicSequence(planner)
        from .workflow_cinematic_playback import WorkflowCinematicPlayback
        server.workflow_cinematic_playback=WorkflowCinematicPlayback(planner)
    from .workflow_prop_image import configured as configure_prop_image
    server.workflow_prop_image=configure_prop_image(host,app.concept,
        planner if hasattr(server,'workflow_delete') else None,getattr(server,'media_artifacts',None))
    owned_opt_in=os.environ.get('PDS_OWNED_OPERATIONS','0')
    if owned_opt_in not in ('0','1'):raise ValueError('OWNED_OPT_IN')
    from .owned_operation_http import configure as configure_owned_operations
    configure_owned_operations(server,enabled=owned_opt_in=='1',host=host)
    if os.environ.get('PDS_PORTRAIT_SERVICE_TOKEN_FILE'):
        from .portrait_assets import PortraitRegistrar
        from .filex_read import private_read
        server.portrait_registrar=PortraitRegistrar(app.domains.npcs,private_read(os.environ['PDS_PORTRAIT_SERVICE_TOKEN_FILE']).decode().strip())
    from .planning_items import PlanningItems
    server.planning_items = PlanningItems(concept_reader=app.concept.current,npc_reader=(app.domains.npcs.current if app.domains.enabled else None))
    if os.environ.get('PDS_DIRECT_PORTRAIT_PAIR_CONFIG'):
        from .portrait_pair_configuration import configured
        server.direct_portrait_pairs=configured(os.environ['PDS_DIRECT_PORTRAIT_PAIR_CONFIG'],media=getattr(server,'media_artifacts',None),registrar=getattr(server,'portrait_registrar',None),planning=server.planning_items)
    if os.environ.get('PDS_DIRECT_POSE_DIRECTION_CONFIG'):
        from .pose_configuration import configured
        server.direct_pose_directions=configured(os.environ['PDS_DIRECT_POSE_DIRECTION_CONFIG'],media=getattr(server,'media_artifacts',None),domains=app.domains,planning=server.planning_items)
    from .bridge_runtime import FilexBridge
    server.filex_bridge = FilexBridge()
    if server.filex_bridge.enabled and host != '127.0.0.1':
        server.server_close()
        app.close()
        raise ValueError('BRIDGE_LOOPBACK_ONLY')
    stopped = threading.Event()
    previous_signals = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous_signals[sig] = signal.signal(sig, lambda *_: stopped.set())
    server.timeout = .5
    stop_heartbeat = None
    try:
        server.filex_bridge.start()
        from .ops_heartbeat import start as start_heartbeat
        stop_heartbeat = start_heartbeat("pds")
        print(f"Project Data Service: http://{host}:{server.server_port}", flush=True)
        while not stopped.is_set():
            server.handle_request()
    finally:
        if stop_heartbeat is not None:
            stop_heartbeat()
        server.filex_bridge.close()
        server.server_close()
        app.close()
        for sig, handler in previous_signals.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description="Portable ProjectBS data service")
    parser.add_argument("--storage-root", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8030)
    args = parser.parse_args()
    serve(args.storage_root, args.host, args.port)


if __name__ == "__main__":
    main()
