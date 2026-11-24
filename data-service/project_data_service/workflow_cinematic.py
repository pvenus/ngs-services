"""Claim-bound full-scene I2V input and immutable MP4 publication, via Filex only."""
import base64
import hashlib
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from .filex_read import canonical,digest
from .filex_writer_binding import writer as scoped_writer
from .prop_animation_ready import renderer_ready
from .workflow_delete_claim import ClaimDenied,SAFE_INT
from .workflow_motion_2d import strict_json

PATH='/v1/cinematics/ownership/workflow'
WRITER='/v1/workflow/cinematics'
ROUTES=(PATH,PATH+'/source-status',PATH+'/read',PATH+'/asset',PATH+'/result')
CAPABILITY='workflow.cinematic-write.v1'
READY_FILE=Path.home()/'Workspaces/Workers/runtime/prop-animation/cinematic-animation-ready.json'
READY_SCHEMA='workflow.cinematic-animation-renderer-ready.v1'
FAMILY='cinematic.generate'
SCHEMA='ngs.cinematic-generate.v1'
TASK_KEY='cinematic_render'
TASK_TYPE='cinematic.generate.render'
OFFICIAL=r'request\.official\.[a-f0-9]{64}'
HASH=r'[a-f0-9]{64}'
MAX_PNG=8*1024*1024
MAX_BODY=12*1024*1024
CLAIM={'officialRequestId','taskId','projectId','payloadSha256','claimStamp'}
ARTIFACT={'sourceSha256','sha256','bytes','mimeType','width','height','fps','frameCount','base64'}
MANIFEST_KEYS=(ARTIFACT-{'base64'})|{'schemaVersion','projectId','officialRequestId'}
BAD_TEXT=re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]|\bembedding:',re.I|re.ASCII)
JS_ENDS='\t\n\v\f\r \xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'


class Denied(ValueError):pass
class CinematicConflict(ValueError):pass


def need(ok):
    if not ok:raise Denied('WORKFLOW_CINEMATIC_DENIED')


def keys(v,want):return type(v)is dict and set(v)==set(want)
def match(v,p):return type(v)is str and re.fullmatch(p,v) is not None
def result_ref(oid):return 'binari/cinematics/results/'+oid+'.json'


def checked_graph(db,oid):
    need(match(oid,OFFICIAL))
    r=db.execute('SELECT * FROM workflow_runs WHERE official_request_id=?',(oid,)).fetchone()
    need(r is not None and r['request_type']==FAMILY and r['schema_version']==SCHEMA and
         r['project_id']=='binari' and r['state']=='planned' and r['reason'] in (None,'RS_COMPLETED'))
    p=strict_json(r['payload'].encode())
    need(keys(p,('schemaVersion','projectId','sourceSha256','motionPrompt')) and p['schemaVersion']==SCHEMA and
         p['projectId']=='binari' and match(p['sourceSha256'],HASH) and canonical(p).decode()==r['payload'] and
         digest(p)==r['payload_sha256'])
    prompt=p['motionPrompt']
    need(type(prompt)is str and 1<=len(prompt)<=1000 and len(prompt.encode('utf-8'))<=2000 and
         prompt.strip(JS_ENDS)==prompt and BAD_TEXT.search(prompt) is None)
    rows=db.execute('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT 2',(oid,)).fetchall()
    need(len(rows)==1);task=rows[0]
    inp={k:p[k] for k in ('sourceSha256','motionPrompt')}
    need(task['task_id']=='workflow.task.'+digest([oid,TASK_KEY]) and task['task_key']==TASK_KEY and
         task['task_type']==TASK_TYPE and task['worker_type']=='animation' and
         task['depends_on_task_id'] is None and task['task_input']==canonical(inp).decode())
    return r,task,inp


def verify_manifest(m,oid,source):
    need(keys(m,MANIFEST_KEYS) and m['schemaVersion']=='ngs.cinematic-result.v1' and
         m['projectId']=='binari' and m['officialRequestId']==oid and m['sourceSha256']==source and
         match(m['sha256'],HASH) and m['mimeType']=='video/mp4' and type(m['bytes'])is int and
         0<m['bytes']<=8*1024*1024 and type(m['fps'])is int and m['fps']==24 and
         type(m['frameCount'])is int and m['frameCount']==45 and
         all(type(m[k])is int and 128<=m[k]<=512 and m[k]%32==0 for k in ('width','height')))


class WorkflowCinematic:
    def __init__(self,planner,*,writer_factory=None,readiness=None):
        self.planner=planner
        self.writer_factory=writer_factory or (lambda:scoped_writer('cinematics'))
        self.readiness=readiness or (lambda:renderer_ready(
            os.environ.get('WORKFLOW_CINEMATIC_ANIMATION_READY_FILE',READY_FILE),schema=READY_SCHEMA))

    def ready(self):
        if self.readiness() is not True:return False
        try:
            with self.planner.snapshot():pass
            with self.writer_factory() as tx:
                caps=tx.capabilities()
                return (type(caps)is dict and caps.get('namespace')=='canonical-files/binari/cinematics/' and
                        caps.get('workflowCinematicV1') is True)
        except (ValueError,OSError,ClaimDenied):return False

    def call(self,path,p,*,missing=False):
        need(path in ('','/asset','/source-status','/result'))
        with self.writer_factory() as tx:
            result=tx.call('POST',WRITER+path,p,missing=missing)
        if result is None:raise FileNotFoundError('authorized cinematic asset not found')
        return result

    def source_status(self,p):
        need(keys(p,('sourceSha256',)) and match(p['sourceSha256'],HASH))
        need(self.call('/source-status',p,missing=True)==p)
        return {'ok':True,**p}

    def asset_bytes(self,source):
        v=self.call('/asset',{'sourceSha256':source},missing=True)
        need(keys(v,('sourceSha256','base64')) and v['sourceSha256']==source and
             type(v['base64'])is str and len(v['base64'])<=4*((MAX_PNG+2)//3))
        raw=base64.b64decode(v['base64'],validate=True)
        need(0<len(raw)<=MAX_PNG and raw.startswith(b'\x89PNG\r\n\x1a\n') and hashlib.sha256(raw).hexdigest()==source)
        return raw

    def operate(self,op,packet):
        if op=='result':return self.result(packet)
        if op=='source-status':return self.source_status(packet)
        need(op in ('read','asset','publish') and keys(packet,('claim','artifact') if op=='publish' else ('claim',)))
        c=packet['claim']
        need(keys(c,CLAIM) and c['projectId']=='binari' and match(c['officialRequestId'],OFFICIAL) and
             match(c['taskId'],r'workflow\.task\.[a-f0-9]{64}') and match(c['payloadSha256'],HASH) and
             type(c['claimStamp'])is int and 0<c['claimStamp']<=SAFE_INT)
        oid=c['officialRequestId']
        # Keep the shared read transaction held until Filex read/write proof is returned.
        with self.planner.snapshot() as db:
            r,t,inp=checked_graph(db,oid)
            need(r['reason'] is None and r['payload_sha256']==c['payloadSha256'] and
                 t['task_id']==c['taskId'] and t['status']=='running' and t['result_ref'] is None and
                 t['updated_at']==c['claimStamp'] and 0<=r['registered_at']<=c['claimStamp'])
            source=inp['sourceSha256']
            if op=='read':
                self.source_status({'sourceSha256':source})
                return inp
            if op=='asset':return self.asset_bytes(source)
            a=packet['artifact'];need(keys(a,ARTIFACT) and type(a['base64'])is str)
            m={**{k:a[k] for k in ARTIFACT-{'base64'}},'schemaVersion':'ngs.cinematic-result.v1',
               'projectId':'binari','officialRequestId':oid}
            verify_manifest(m,oid,source)
            need(self.call('',{'manifest':m,'base64':a['base64']})==m)
            need(self.call('/result',{'officialRequestId':oid})==m)
            return {'ok':True,'resultRef':result_ref(oid)}

    def result(self,packet):
        need(keys(packet,('officialRequestId',)) and match(packet['officialRequestId'],OFFICIAL))
        oid=packet['officialRequestId']
        with self.planner.snapshot() as db:
            _,t,inp=checked_graph(db,oid)
            need(t['status']=='completed' and t['result_ref']==result_ref(oid))
            m=self.call('/result',packet,missing=True)
            verify_manifest(m,oid,inp['sourceSha256'])
        return {'ok':True,'resultRef':result_ref(oid),'manifest':m}


def serve(handler,*,attribute='workflow_cinematic',path=PATH,capability=CAPABILITY,body_limits=None):
    obj=getattr(handler.server,attribute,None)
    if not isinstance(obj,WorkflowCinematic):return handler.respond(404,{'error':'WORKFLOW_CINEMATIC_DISABLED'})
    parsed=urlsplit(handler.path)
    if (handler.client_address[0]!='127.0.0.1' or handler.headers.get_all('Host')!=['127.0.0.1:'+str(handler.server.server_port)] or
        handler.headers.get('Origin') is not None or parsed.query or handler.headers.get('Transfer-Encoding') is not None):
        return handler.respond(403,{'error':'WORKFLOW_CINEMATIC_INTERNAL_ONLY'})
    try:
        if handler.command=='GET' and parsed.path==path:
            if not obj.ready():return handler.respond(503,{'error':'WORKFLOW_CINEMATIC_NOT_READY'})
            return handler.respond(200,{'enabled':True,'schemaVersion':capability})
        if handler.command!='POST':return handler.respond(404,{'error':'WORKFLOW_CINEMATIC_ROUTE'})
        n=handler.headers.get('Content-Length','')
        maximum=(body_limits or {path:MAX_BODY}).get(parsed.path,4096)
        if (handler.headers.get_all('Content-Length')!=[n] or handler.headers.get('Content-Type')!='application/json' or
            not n.isascii() or not n.isdecimal() or len(n)>9 or not 0<int(n)<=maximum):
            return handler.respond(400,{'error':'WORKFLOW_CINEMATIC_BAD_BODY'})
        handler.connection.settimeout(20)
        raw=handler.rfile.read(int(n))
        if len(raw)!=int(n):return handler.respond(400,{'error':'WORKFLOW_CINEMATIC_BAD_BODY'})
        result=obj.operate('publish' if parsed.path==path else parsed.path.rsplit('/',1)[-1],strict_json(raw))
        if parsed.path==path+'/asset':
            handler.send_response(200);handler.send_header('Content-Type','image/png')
            handler.send_header('Content-Length',str(len(result)));handler.send_header('Cache-Control','no-store')
            handler.send_header('X-Content-Type-Options','nosniff');handler.end_headers();handler.wfile.write(result);return None
        return handler.respond(200,result)
    except CinematicConflict as exc:return handler.respond(409,{'error':str(exc)})
    except (Denied,ClaimDenied):return handler.respond(403,{'error':'WORKFLOW_CINEMATIC_DENIED'})
    except FileNotFoundError:return handler.respond(404,{'error':'WORKFLOW_CINEMATIC_NOT_FOUND'})
    except (OSError,ValueError):return handler.respond(503,{'error':'WORKFLOW_CINEMATIC_OUTCOME_UNCERTAIN'})
