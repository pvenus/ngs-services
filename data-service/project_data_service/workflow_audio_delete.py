"""Exact Planner-selected removal through the common one-target storage path."""
from contextlib import contextmanager
import json

from .concept_filex import FilexConcept
from .filex_read import canonical,digest
from .filex_writer_binding import AUDIO_NAMESPACE
from .single_target_delete import AuthorizedStorage,ClaimDenied,PATH
from .workflow_delete_claim import PrivatePlannerClaim,ClaimDenied as PlannerDenied
from .workflow_audio import OFFICIAL,TASK,SHA,match
from .workflow_audio_separate import published_song

ROOT='canonical-files/binari/audio'
SCHEMA='ngs.audio-delete.v1'
TYPE='audio.delete'
KEY='audio_delete'

def require(ok):
    if not ok:raise ClaimDenied()

def storage_provider(concept,planner,*,writer_factory=None):
    if not isinstance(concept,FilexConcept) or not isinstance(planner,PrivatePlannerClaim):
        raise ValueError('WORKFLOW_AUDIO_DELETE_AUTHORITY_REQUIRED')

    factory=writer_factory or concept.factory
    namespace=AUDIO_NAMESPACE if writer_factory is not None else 'canonical-files/binari/concept/'

    def ready():
        with planner.snapshot():pass
        with factory() as tx:
            cap=tx.capabilities()
            return cap.get('namespace')==namespace and cap.get('workflowAudioDeleteV1') is True

    @contextmanager
    def authorize(c):
        require(type(c) is dict and set(c)=={'officialRequestId','taskId','projectId','payloadSha256','claimStamp','sourceRequestId'}
                and match(c['officialRequestId'],OFFICIAL) and match(c['taskId'],TASK) and match(c['sourceRequestId'],OFFICIAL)
                and c['officialRequestId']!=c['sourceRequestId'] and match(c['payloadSha256'],SHA)
                and c['projectId']=='binari' and type(c['claimStamp']) is int and 0<=c['claimStamp']<=2**53-1)
        oid,tid,source=c['officialRequestId'],c['taskId'],c['sourceRequestId']
        require(tid=='workflow.task.'+digest([oid,KEY]))
        payload={'schemaVersion':SCHEMA,'projectId':'binari','officialRequestId':source}
        expected={'officialRequestId':source,'target':{'type':'directory','path':ROOT+'/'+source}}
        try:
            with planner.snapshot() as db:
                row=db.execute("""SELECT r.*,t.task_input,t.updated_at AS stamp FROM workflow_tasks t
                    JOIN workflow_runs r USING(official_request_id)
                    WHERE t.task_id=? AND t.official_request_id=? AND r.state='planned' AND r.reason IS NULL
                    AND t.status='running' AND t.result_ref IS NULL AND t.depends_on_task_id IS NULL
                    AND t.task_key=? AND t.task_type=? AND t.worker_type='file'""",(tid,oid,KEY,TYPE)).fetchone()
                require(row is not None and row['project_id']=='binari' and row['request_type']==TYPE and row['schema_version']==SCHEMA
                        and row['stamp']==c['claimStamp'] and type(row['registered_at']) is int and 0<=row['registered_at']<=row['stamp']
                        and row['payload_sha256']==digest(payload)==c['payloadSha256'] and row['payload']==canonical(payload).decode()
                        and row['task_input']==canonical(expected).decode())
                require(published_song(db,source) or published_song(db,source,derived=True))
            yield {'operationId':tid,'target':{'root':ROOT,'path':source,'kind':'directory'}}
        except PlannerDenied as exc:raise ClaimDenied() from exc

    def execute(command):
        with factory() as tx:return tx.call('POST',PATH,command)

    return AuthorizedStorage(authorize,execute,ready)
