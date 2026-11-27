"""Scoped canonical-song read and derived instrumental publication."""
from contextlib import contextmanager
import json
import sqlite3

from .workflow_save_reservation import optionally_save
from .concept_filex import FilexConcept
from .database import DataError
from .filex_cas_transport import AudioPrewriteRejected
from .filex_writer_binding import AUDIO_NAMESPACE
from .filex_read import canonical, digest
from .workflow_audio import MAX_BODY, MAX_WAV, OFFICIAL, PREFIX, REJECTION, SHA, TASK, Rejected, match, need
from .workflow_delete_claim import ClaimDenied, PrivatePlannerClaim

PATH='/v1/audio/ownership/workflow-separate'
WRITER_PATH='/v1/workflow/audio-separate'
SCHEMA='workflow.audio-separate-write.v1'
PAYLOAD='ngs.audio-separate.v1'
TYPE='audio.separate'
KEY='audio_separate'
SOURCE_FIELDS={'sourceOfficialRequestId','sourceSha256'}
CLAIM_FIELDS={'officialRequestId','taskId','projectId','payloadSha256','claimStamp'}


def published_song(db,oid, *, derived=False):
    """Exact completed audio task whose resultRef is the corresponding canonical row."""
    kind,key,schema=(TYPE,KEY,PAYLOAD) if derived else ('audio.generate','audio_generate','ngs.audio-generate.v1')
    row=db.execute("""SELECT r.payload_sha256 FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
        WHERE t.official_request_id=? AND t.task_id=? AND r.project_id='binari'
        AND r.request_type=? AND r.schema_version=? AND r.state='planned'
        AND t.task_key=? AND t.task_type=? AND t.worker_type='audio'
        AND t.status='completed' AND t.result_ref=?""",
        (oid,'workflow.task.'+digest([oid,key]),kind,schema,key,kind,PREFIX+oid+'/metadata.json')).fetchone()
    return row['payload_sha256'] if row is not None and match(row['payload_sha256'],SHA) else None


class WorkflowAudioSeparate:
    def __init__(self,concept,verifier,*,writer_factory=None):
        need(isinstance(concept,FilexConcept) and isinstance(verifier,PrivatePlannerClaim))
        self.concept,self.verifier=concept,verifier
        self.writer_factory=writer_factory or concept.factory
        self.writer_namespace=AUDIO_NAMESPACE if writer_factory is not None else 'canonical-files/binari/concept/'

    def writer(self,tx):
        caps=tx.capabilities()
        if caps.get('namespace')!=self.writer_namespace or caps.get('workflowAudioSeparateV1') is not True:
            raise ValueError('WORKFLOW_AUDIO_SEPARATE_WRITER_UNAVAILABLE')

    def capability(self):
        with self.verifier.snapshot():pass
        with self.writer_factory() as tx:
            self.writer(tx)
        return {'enabled':True,'schemaVersion':SCHEMA}

    @contextmanager
    def selected(self,claim):
        need(type(claim) is dict and set(claim)==CLAIM_FIELDS
             and match(claim['officialRequestId'],OFFICIAL) and match(claim['taskId'],TASK)
             and claim['taskId']=='workflow.task.'+digest([claim['officialRequestId'],KEY])
             and claim['projectId']=='binari' and match(claim['payloadSha256'],SHA)
             and type(claim['claimStamp']) is int and 0<=claim['claimStamp']<=2**53-1)
        with self.verifier.snapshot() as db:
            row=db.execute("""SELECT r.*,t.task_input,t.updated_at AS stamp FROM workflow_tasks t
                JOIN workflow_runs r USING(official_request_id)
                WHERE t.task_id=? AND r.official_request_id=? AND r.state='planned' AND r.reason IS NULL
                AND t.status='running' AND t.result_ref IS NULL AND t.depends_on_task_id IS NULL
                AND t.task_key=? AND t.task_type=? AND t.worker_type='audio'""",
                (claim['taskId'],claim['officialRequestId'],KEY,TYPE)).fetchone()
            need(row is not None and row['project_id']=='binari' and row['request_type']==TYPE
                 and row['schema_version']==PAYLOAD and row['stamp']==claim['claimStamp']
                 and type(row['registered_at']) is int and 0<=row['registered_at']<=row['stamp']
                 and row['payload_sha256']==claim['payloadSha256'])
            p=json.loads(row['payload'])
            need(type(p) is dict and set(p)=={'schemaVersion','projectId'}|SOURCE_FIELDS and
                 p['schemaVersion']==PAYLOAD and p['projectId']=='binari' and
                 match(p['sourceOfficialRequestId'],OFFICIAL) and p['sourceOfficialRequestId']!=claim['officialRequestId']
                 and match(p['sourceSha256'],SHA) and row['payload']==canonical(p).decode()
                 and digest(p)==claim['payloadSha256'])
            source={k:p[k] for k in SOURCE_FIELDS}
            source_payload=published_song(db,p['sourceOfficialRequestId'])
            need(row['task_input']==canonical(source).decode() and source_payload is not None)
        yield {**{k:claim[k] for k in CLAIM_FIELDS if k!='claimStamp'},**source,
               'sourcePayloadSha256':source_payload}

    def run(self,body,action,started):
        need(type(body) is dict and set(body)==({'claim','artifact'} if action=='publish' else {'claim'}))
        if action=='publish':
            a=body['artifact']
            need(type(a) is dict and set(a)=={'mimeType','sha256','bytes','base64'} and a['mimeType']=='audio/wav'
                 and match(a['sha256'],SHA) and type(a['bytes']) is int and 44<=a['bytes']<=MAX_WAV
                 and type(a['base64']) is str and len(a['base64'])==((a['bytes']+2)//3)*4)
        # Same authenticated Planner read transaction as ordinary audio publication,
        # fencing state changes for the short scoped Filex transaction, not model work.
        with self.selected(body['claim']) as b, optionally_save(self.verifier,body['claim'],action=='publish'),self.writer_factory() as tx:
            self.writer(tx)
            if action=='asset':
                return tx.audio_separate_asset(WRITER_PATH+'/asset',b,maximum=MAX_WAV)
            if action=='read':
                value=tx.call('POST',WRITER_PATH+'/read',{'binding':b})
                if (type(value) is not dict or set(value)!=SOURCE_FIELDS|{'bytes','mimeType'}
                    or any(value[k]!=b[k] for k in SOURCE_FIELDS)
                    or value['mimeType']!='audio/wav' or type(value['bytes']) is not int
                    or not 44<=value['bytes']<=MAX_WAV):
                    raise ValueError('WORKFLOW_AUDIO_SEPARATE_SOURCE_RESPONSE')
                return value
            started.append(True)
            result=tx.call('POST',WRITER_PATH,{'binding':b,'artifact':a})
            if result!={'ok':True,'resultRef':PREFIX+b['officialRequestId']+'/metadata.json'}:
                raise ValueError('WORKFLOW_AUDIO_SEPARATE_RESULT_UNCONFIRMED')
            return result


def serve(handler):
    service=getattr(handler.server,'workflow_audio_separate',None)
    if not isinstance(service,WorkflowAudioSeparate):
        return handler.respond(404,{'error':'WORKFLOW_AUDIO_SEPARATE_DISABLED'})
    actions={('GET',PATH):'capability',('POST',PATH):'publish',('POST',PATH+'/read'):'read',
             ('POST',PATH+'/asset'):'asset'}
    action=actions.get((handler.command,handler.path))
    if (handler.server.server_address[0]!='127.0.0.1' or handler.client_address[0]!='127.0.0.1'
        or handler.headers.get_all('Host')!=['127.0.0.1:'+str(handler.server.server_port)]
        or handler.headers.get('Origin') is not None or handler.headers.get('Transfer-Encoding') is not None
        or action is None or action!='capability' and handler.headers.get('Content-Type')!='application/json'):
        return handler.respond(403,{'error':'WORKFLOW_AUDIO_SEPARATE_INTERNAL_ONLY'})
    started=[]
    try:
        if action=='capability':return handler.respond(200,service.capability())
        handler.connection.settimeout(15)
        result=service.run(handler.read_json(max_body=MAX_BODY if action=='publish' else 8192),action,started)
        if action=='asset':
            handler.send_response(200);handler.send_header('Content-Type','audio/wav')
            handler.send_header('Content-Length',str(len(result)));handler.send_header('Cache-Control','no-store')
            handler.send_header('X-Content-Type-Options','nosniff');handler.end_headers();handler.wfile.write(result)
            return
        return handler.respond(200,result)
    except (Rejected,ClaimDenied,DataError):
        return handler.respond(409,{'error':'WORKFLOW_AUDIO_OUTCOME_UNCONFIRMED'}) if started else handler.respond(422,{'error':REJECTION})
    except AudioPrewriteRejected:
        return handler.respond(422,{'error':REJECTION})
    except (OSError,sqlite3.Error):
        return handler.respond(503,{'error':'WORKFLOW_AUDIO_OUTCOME_UNCONFIRMED'})
    except (ValueError,KeyError,TypeError):
        return handler.respond(409,{'error':'WORKFLOW_AUDIO_OUTCOME_UNCONFIRMED'})
