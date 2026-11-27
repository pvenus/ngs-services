"""Formal one-prop worker handoff, using the existing immutable Filex media writer."""
import base64
from contextlib import contextmanager
import json
import re
import sqlite3
import struct
import unicodedata
import zlib

from .workflow_save_reservation import save_handoff
from .concept_filex import FilexConcept
from .filex_read import canonical,digest,require
from .media_artifacts import MediaArtifacts
from .media_validation import png,validate
from .prop_design_context import PropDesignContext
from .workflow_delete_claim import ClaimDenied,PrivatePlannerClaim
from .workflow_motion_2d import strict_json

PATH='/v1/props/ownership/workflow-image'
ROUTES=(PATH,PATH+'/read')
SCHEMA='workflow.prop-image-write.v1'
TYPE='prop.image-generate'
PAYLOAD='ngs.prop-image-generate.v1'
KEY='prop_image_generate'
P='binari'
CLAIM={'officialRequestId','taskId','projectId','payloadSha256','claimStamp'}
PAYLOAD_FIELDS={'schemaVersion','projectId','propId','state','lane','intent','designContextSha256'}
SHA=r'[a-f0-9]{64}'
MAX=8*1024*1024
MAX_BODY=((MAX+2)//3)*4+8192

def match(v,pattern):
    return type(v) is str and re.fullmatch(pattern,v) is not None
def need(v):
    require(v,'WORKFLOW_PROP_IMAGE_DENIED')
def keys(v,want):
    return type(v) is dict and set(v)==want
def intent(v):
    return (type(v) is str and v and v==v.strip().strip('\ufeff').strip() and
        all(unicodedata.category(c) not in ('Cc','Cs') or c in '\t\r\n' for c in v) and
        len(v.encode())<=4000)

def alpha_png(raw):
    # Already decoded/validated by the existing shared media validator. Recheck
    # the bounded 8-bit RGBA delivery profile and inspect actual alpha samples.
    png(raw)
    w,h,depth,color,_,_,_=struct.unpack_from('!IIBBBBB',raw,16)
    need(1<=w<=2048 and 1<=h<=2048 and depth==8 and color==6)
    at=8;parts=[]
    while at<len(raw):
        size,kind=struct.unpack_from('!I4s',raw,at)
        need(kind not in (b'acTL',b'fcTL',b'fdAT',b'tRNS'))
        if kind==b'IDAT':parts.append(raw[at+8:at+8+size])
        at+=size+12
    stride=1+w*4
    pixels=zlib.decompressobj().decompress(b''.join(parts),stride*h+1)
    need(len(pixels)==stride*h)
    previous=bytearray(w)
    transparent=visible=False
    for y in range(h):
        row=pixels[y*stride:(y+1)*stride]
        typ=row[0];cur=bytearray(w)
        for i,v in enumerate(row[4::4]):
            left=cur[i-1] if i else 0
            top=previous[i]
            corner=previous[i-1] if i else 0
            if typ==1:v+=left
            elif typ==2:v+=top
            elif typ==3:v+=(top+left)//2
            elif typ==4:
                estimate=left+top-corner
                a,b,c=abs(estimate-left),abs(estimate-top),abs(estimate-corner)
                v+=left if a<=b and a<=c else top if b<=c else corner
            v&=255;cur[i]=v
            transparent|=v==0;visible|=v>0
            if transparent and visible:return
        previous=cur
    need(False)


class WorkflowPropImage:
    def __init__(self,source,verifier,media):
        need(isinstance(source,PropDesignContext) and isinstance(verifier,PrivatePlannerClaim)
             and isinstance(media,MediaArtifacts))
        self.source,self.verifier,self.media=source,verifier,media

    def capability(self):
        with self.verifier.snapshot():pass
        self.source.read(P)
        return {'enabled':True,'schemaVersion':SCHEMA}

    @contextmanager
    def selected(self,claim):
        if not (keys(claim,CLAIM) and claim['projectId']==P and
                match(claim['officialRequestId'],r'request\.official\.[a-f0-9]{64}') and
                claim['taskId']=='workflow.task.'+digest([claim['officialRequestId'],KEY]) and
                match(claim['payloadSha256'],SHA) and type(claim['claimStamp']) is int and
                0<=claim['claimStamp']<2**53):
            raise ClaimDenied('WORKFLOW_PROP_IMAGE_CLAIM_DENIED')
        with self.verifier.snapshot() as db:
            r=db.execute("""SELECT r.payload,r.payload_sha256,r.registered_at,t.task_input
             FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
             WHERE r.official_request_id=? AND r.project_id=? AND r.request_type=? AND r.schema_version=?
             AND r.state='planned' AND r.reason IS NULL AND t.task_id=? AND t.task_key=?
             AND t.task_type=? AND t.worker_type='image' AND t.updated_at=? AND t.status='running'
             AND t.result_ref IS NULL AND t.depends_on_task_id IS NULL""",
             (claim['officialRequestId'],P,TYPE,PAYLOAD,claim['taskId'],KEY,TYPE,claim['claimStamp'])).fetchone()
            if r is None or r['payload_sha256']!=claim['payloadSha256'] or not 0<=r['registered_at']<=claim['claimStamp']:
                raise ClaimDenied('WORKFLOW_PROP_IMAGE_CLAIM_DENIED')
            p=json.loads(r['payload'])
            need(keys(p,PAYLOAD_FIELDS) and p['schemaVersion']==PAYLOAD and p['projectId']==P and
                 p['lane'] in ('detail','pixel') and intent(p['intent']) and match(p['designContextSha256'],SHA) and
                 all(match(p[k],r'[a-z][a-z0-9_-]{0,127}') for k in ('propId','state')) and
                 r['payload']==r['task_input']==canonical(p).decode() and digest(p)==claim['payloadSha256'])
        yield p

    def run(self,body,reading):
        need(keys(body,{'claim'} if reading else {'claim','artifact'}))
        claim=body['claim']
        with self.selected(claim) as p:pass
        context=self.source.read(P)
        need(context['contextSha256']==p['designContextSha256'])
        if reading:
            with self.selected(claim) as current, save_handoff(self.verifier,claim):need(current==p)
            return context
        a=body['artifact']
        need(keys(a,{'base64','sha256','bytes','mimeType','contextSha256'}) and
             a['contextSha256']==p['designContextSha256'] and match(a['sha256'],SHA) and a['mimeType']=='image/png' and
             type(a['bytes']) is int and 0<a['bytes']<=MAX and
             type(a['base64']) is str and len(a['base64'])==((a['bytes']+2)//3)*4)
        # Existing MediaStore role authorizes PDS service bearer. The exact
        # Planner claim here is the business authority, not legacy claims.
        # No generic publish endpoint or caller-supplied destination.
        attempt='workflow.attempt.'+str(claim['claimStamp'])
        packet={'requestId':claim['officialRequestId'],'attemptId':attempt,
            'fencingToken':str(claim['claimStamp']),'capability':'image.generate',
            'projectId':P,'targetId':digest([p[k] for k in ('propId','state','lane')]),
            'expectedRevision':a['contextSha256'],'requestHash':claim['payloadSha256'],
            'artifact':{k:v for k,v in a.items() if k!='contextSha256'},
            'provenance':{'model':'pinned-native-image-provider'},'claim':claim}
        raw,ext=validate(packet);need(ext=='png');alpha_png(raw)
        with self.selected(claim) as current:
            need(current==p)
            receipt=self.media.writer('POST','/v1/media-artifacts/publish',packet)
        r=self.media.verify(receipt,packet)
        need(self.media.receipt(claim['officialRequestId'],attempt)==r)
        return {'ok':True,'resultRef':'media-artifacts/assets/'+r['artifact']['assetId']}


def configured(host,source,verifier,media):
    if (host=='127.0.0.1' and isinstance(source,FilexConcept) and
            isinstance(verifier,PrivatePlannerClaim) and isinstance(media,MediaArtifacts)):
        return WorkflowPropImage(PropDesignContext(source),verifier,media)
    return None


def serve(handler):
    obj=getattr(handler.server,'workflow_prop_image',None)
    if not isinstance(obj,WorkflowPropImage):
        return handler.respond(404,{'error':'WORKFLOW_PROP_IMAGE_DISABLED'})
    route=(handler.command,handler.path)
    if (route not in (('GET',PATH),('POST',PATH),('POST',PATH+'/read')) or
        handler.server.server_address[0]!='127.0.0.1' or handler.client_address[0]!='127.0.0.1' or
        handler.headers.get_all('Host')!=['127.0.0.1:'+str(handler.server.server_port)] or
        any(handler.headers.get_all(name) is not None for name in ('Origin','Cookie','Transfer-Encoding','Content-Encoding'))):
        return handler.respond(403,{'error':'WORKFLOW_PROP_IMAGE_INTERNAL_ONLY'})
    try:
        if handler.command=='GET':return handler.respond(200,obj.capability())
        reading=handler.path.endswith('/read')
        h=handler.headers; sizes=h.get_all('Content-Length') or []
        need(h.get_all('Content-Type')==['application/json'] and len(sizes)==1 and
             match(sizes[0],r'[1-9][0-9]{0,8}') and int(sizes[0])<=(8192 if reading else MAX_BODY))
        handler.connection.settimeout(30)
        size=int(sizes[0]);raw=handler.rfile.read(size);need(len(raw)==size)
        return handler.respond(200,obj.run(strict_json(raw),reading))
    except ClaimDenied:return handler.respond(403,{'error':'WORKFLOW_PROP_IMAGE_CLAIM_DENIED'})
    except (OSError,sqlite3.Error):return handler.respond(503,{'error':'WORKFLOW_PROP_IMAGE_UNAVAILABLE'})
    except (ValueError,TypeError,KeyError,zlib.error):return handler.respond(409,{'error':'WORKFLOW_PROP_IMAGE_UNCONFIRMED'})
