"""Opt-in exact official audio workflow. No reservations, provider or file access."""
import contextlib,hashlib,re,sqlite3,json
from .concept_filex import FilexConcept
from .database import DataError
from .filex_read import canonical,digest
from .workflow_delete_claim import PrivatePlannerClaim,ClaimDenied

PATH='/v1/audio/ownership/workflow-generate'
WRITER_PATH='/v1/workflow/audio'
SCHEMA='workflow.audio-generate-write.v1'
PAYLOAD='ngs.audio-generate.v1'
PREFIX='canonical-files/binari/audio/'
MAX_WAV=24*1024*1024
MAX_BODY=((MAX_WAV+2)//3)*4+16384
MAX_SOURCE=256*1024
DIRECT='direct'
CID=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
SHA=re.compile(r'[a-f0-9]{64}')
OFFICIAL=re.compile(r'request\.official\.[a-f0-9]{64}')
TASK=re.compile(r'workflow\.task\.[a-f0-9]{64}')
CONTROL=re.compile(r'[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\ud800-\udfff]')
JS_TRIM='\t\n\r\v\f \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
REJECTION='WORKFLOW_AUDIO_REJECTED_BEFORE_WRITE'

class Rejected(ValueError):pass

def match(v,p):return type(v) is str and p.fullmatch(v) is not None

def need(ok):
    if not ok:raise Rejected(REJECTION)

def payload(value):
    def nonblank(v,n):return type(v) is str and 1<=len(v)<=n and bool(v.strip(JS_TRIM)) and not CONTROL.search(v)
    def text(v,n):return nonblank(v,n) and v==v.strip(JS_TRIM)
    fields={'schemaVersion','projectId','kind','title','conceptId','brief','text','durationSeconds'}
    need(type(value) is dict and set(value)-{'targetDurationSeconds','conceptText','lyricsRequest'}==fields
         and value['schemaVersion']==PAYLOAD and value['projectId']=='binari'
         and value['kind']=='bgm' and text(value['title'],120)
         and not re.search(r'[\t\r\n\u2028\u2029]',value['title']) and match(value['conceptId'],CID)
         and text(value['brief'],4000))
    need((value['conceptId']==DIRECT)==('conceptText' in value)
         and ('conceptText' not in value or text(value['conceptText'],4000)))
    duration=value['durationSeconds']
    if 'lyricsRequest' in value:
        need(duration is None and value['text']=='' and text(value['lyricsRequest'],4000))
    if 'targetDurationSeconds' in value:
        target=value['targetDurationSeconds']
        need(duration is None and type(target) is int and target==60)
    need((value['text']=='' or nonblank(value['text'],4000)) if duration is None else
         value['text']=='' and type(duration) in (int,float) and 10<=duration<=30)
    return value

from .filex_writer_binding import AUDIO_NAMESPACE
LEGACY_NAMESPACE='canonical-files/binari/concept/'

class WorkflowAudio:
    def __init__(self,concept,verifier,*,writer_factory=None):
        if not isinstance(concept,FilexConcept) or not isinstance(verifier,PrivatePlannerClaim):
            raise ValueError('WORKFLOW_AUDIO_AUTHORITY_REQUIRED')
        self.concept,self.verifier=concept,verifier
        self.writer_factory=writer_factory or concept.factory
        self.writer_namespace=AUDIO_NAMESPACE if writer_factory is not None else LEGACY_NAMESPACE

    def capability(self):
        with self.verifier.snapshot(),self.writer_factory() as t:
            c=t.capabilities()
            if c.get('namespace')!=self.writer_namespace or c.get('workflowAudioResultV1') is not True:
                raise ValueError('WORKFLOW_AUDIO_WRITER_UNAVAILABLE')
        return {'enabled':True,'schemaVersion':SCHEMA}

    @contextlib.contextmanager
    def selected(self,claim):
        need(type(claim) is dict and set(claim)=={'officialRequestId','taskId','projectId','payloadSha256','claimStamp'}
             and match(claim['officialRequestId'],OFFICIAL) and match(claim['taskId'],TASK)
             and claim['taskId']=='workflow.task.'+digest([claim['officialRequestId'],'audio_generate'])
             and claim['projectId']=='binari' and match(claim['payloadSha256'],SHA)
             and type(claim['claimStamp']) is int and 0<=claim['claimStamp']<=2**53-1)
        with self.verifier.snapshot() as db:
            row=db.execute("""SELECT r.*,t.task_input,t.updated_at AS stamp FROM workflow_tasks t
                JOIN workflow_runs r USING(official_request_id)
                WHERE t.task_id=? AND r.official_request_id=? AND r.state='planned' AND r.reason IS NULL
                AND t.status='running' AND t.result_ref IS NULL AND t.depends_on_task_id IS NULL
                AND t.task_key='audio_generate' AND t.task_type='audio.generate' AND t.worker_type='audio'""",
                (claim['taskId'],claim['officialRequestId'])).fetchone()
            need(row is not None and row['project_id']=='binari' and row['request_type']=='audio.generate'
                 and row['schema_version']==PAYLOAD and row['stamp']==claim['claimStamp']
                 and type(row['registered_at']) is int and 0<=row['registered_at']<=row['stamp']
                 and row['payload_sha256']==claim['payloadSha256'])
            p=payload(json.loads(row['payload']))
            need(row['payload']==canonical(p).decode() and digest(p)==claim['payloadSha256']
                 and row['task_input']==canonical({k:p[k] for k in ('kind','conceptId')}).decode())
            yield ({**{k:claim[k] for k in ('officialRequestId','taskId','projectId','payloadSha256')},
                    **{k:p[k] for k in ('kind','title','conceptId','durationSeconds')}},p.get('conceptText'))

    def run(self,body,*,reading,started):
        need(type(body) is dict and set(body)==({'claim'} if reading else {'claim','source','artifact'}))
        with self.selected(body['claim']) as (binding,concept_text),contextlib.ExitStack() as stack:
            direct_sha=hashlib.sha256(concept_text.encode('utf-8')).hexdigest() if concept_text is not None else None
            if reading:
                if direct_sha is not None:
                    return {'conceptId':DIRECT,'sectionSha256':direct_sha,'title':'직접 입력','content':concept_text}
                t=stack.enter_context(self.writer_factory())
                output=t.call('POST',WRITER_PATH+'/read',{'conceptId':binding['conceptId']})
                if (type(output) is not dict or set(output)!={'conceptId','sectionSha256','title','content'}
                    or output['conceptId']!=binding['conceptId'] or not match(output['sectionSha256'],SHA)
                    or type(output['title']) is not str or not output['title'].strip()
                    or type(output['content']) is not str or not 0<len(output['content'].encode())<=MAX_SOURCE):
                    raise ValueError('WORKFLOW_AUDIO_SOURCE_RESPONSE')
                return output
            s=body['source'];a=body['artifact']
            need(type(s) is dict and set(s)=={'conceptId','sectionSha256'} and s['conceptId']==binding['conceptId']
                 and match(s['sectionSha256'],SHA) and (direct_sha is None or s['sectionSha256']==direct_sha)
                 and type(a) is dict and set(a) in ({'mimeType','sha256','bytes','base64'},{'mimeType','sha256','bytes','base64','bpm'})
                 and (a.get('bpm') is None or type(a['bpm']) is int and 30<=a['bpm']<=300) and a['mimeType']=='audio/wav'
                 and match(a['sha256'],SHA) and type(a['bytes']) is int and 44<=a['bytes']<=MAX_WAV
                 and type(a['base64']) is str and len(a['base64'])==((a['bytes']+2)//3)*4)
            if direct_sha is not None:
                binding['directConceptSha256']=direct_sha
            t=stack.enter_context(self.writer_factory())
            started.append(True)
            result=t.call('POST',WRITER_PATH,{'binding':binding,'source':s,'artifact':a})
            if result!={'ok':True,'resultRef':PREFIX+binding['officialRequestId']+'/metadata.json'}:
                raise ValueError('WORKFLOW_AUDIO_RESULT_UNCONFIRMED')
            return result

def serve(handler):
    service=getattr(handler.server,'workflow_audio',None)
    if not isinstance(service,WorkflowAudio):
        return handler.respond(404,{'error':'WORKFLOW_AUDIO_DISABLED'})
    if (handler.server.server_address[0]!='127.0.0.1' or handler.client_address[0]!='127.0.0.1'
         or handler.headers.get_all('Host')!=['127.0.0.1:'+str(handler.server.server_port)]
         or handler.headers.get('Origin') is not None or handler.headers.get('Transfer-Encoding') is not None
         or (handler.command,handler.path) not in (('GET',PATH),('POST',PATH),('POST',PATH+'/read'))):
        return handler.respond(403,{'error':'WORKFLOW_AUDIO_INTERNAL_ONLY'})
    started=[]
    try:
        if handler.command=='GET':return handler.respond(200,service.capability())
        reading=handler.path.endswith('/read')
        handler.connection.settimeout(15)
        return handler.respond(200,service.run(handler.read_json(max_body=8192 if reading else MAX_BODY),reading=reading,started=started))
    except (Rejected,ClaimDenied,DataError):
        return handler.respond(409,{'error':'WORKFLOW_AUDIO_OUTCOME_UNCONFIRMED'}) if started else handler.respond(422,{'error':REJECTION})
    except (OSError,sqlite3.Error):
        return handler.respond(503,{'error':'WORKFLOW_AUDIO_OUTCOME_UNCONFIRMED'})
    except (ValueError,KeyError,TypeError) as exc:
        # Exact verifier returned only the authenticated writer's approved
        # pre-write rejection code. Unknown/delivery errors are never definite.
        from .filex_cas_transport import AudioPrewriteRejected
        if isinstance(exc,AudioPrewriteRejected):
            return handler.respond(422,{'error':REJECTION})
        return handler.respond(409,{'error':'WORKFLOW_AUDIO_OUTCOME_UNCONFIRMED'})
