"""Fixed loopback capability for the existing owner's Space publisher."""
import hmac
import re

from deploy_control import KEY, bounded_private, is_sha, reject, secure_directory
from deploy_cycle import space_handoff, space_publication
from space_candidate import SpaceCandidate
from space_publish_queue import SpacePublishQueue

THREAD_ID='6b01f7fc-973c-83ee-bc93-8fc96a1c3039'
METHODS=('ops_space_publish_peek','ops_space_publish_claim','ops_space_publish_finish')
REQUEST_ID=r'[A-Za-z0-9._-]{1,128}'


def exact(value,*keys):
    return type(value) is dict and set(value)==set(keys)


class SpacePublisher:
    def __init__(self,control,candidate=None):
        self.control=control
        self.root=control.root
        self.config=self.root/'targets/ngs-space'
        self.candidate=candidate if candidate is not None else SpaceCandidate()
        self.queue=SpacePublishQueue(self.root)

    def key(self):
        for directory in (self.root,self.config.parent,self.config):
            secure_directory(directory)
        record=space_handoff(self.config)
        if not space_publication(self.config) or not record or record['threadId']!=THREAD_ID:
            raise ValueError('SPACE_PUBLISH_CONFIGURATION_INVALID')
        key=bounded_private(self.config/'publisher-key',65).decode('ascii').removesuffix('\n')
        if not KEY.fullmatch(key):
            raise ValueError('SPACE_PUBLISH_CONFIGURATION_INVALID')
        return key

    def available(self):
        try:
            self.key()
            return True
        except (OSError,ValueError):
            return False

    @staticmethod
    def public(ticket):
        if not ticket:
            return None
        return {'requestId':ticket['requestId'],'sha':ticket['commitSha'],'state':ticket['state'],
                'requestedAt':ticket['queuedAt']}

    def status(self):
        ticket=self.queue.status()
        if ticket is not None and ticket['state'] in ('pending','claimed','uncertain'):
            return {'request':self.public(ticket),'candidateSha':ticket['commitSha'],'canRequest':False}
        head=self.candidate.current()
        blocked=ticket is not None and ticket['commitSha']==head
        return {'request':self.public(ticket),'candidateSha':head,'canRequest':is_sha(head) and not blocked}

    def native_observation(self,commit_sha):
        if not is_sha(commit_sha):
            return None
        receipt=self.queue.last_success()
        if receipt and receipt['commitSha']==commit_sha:
            return {'sha':commit_sha,'deployedAt':receipt['deployedAt'],
                    'providerSha':receipt['providerSha'],'deploymentId':receipt['deploymentId']}
        return None

    def submit(self,sid,csrf,sha):
        with self.control.lock:
            self.control._session(sid,csrf)
        self.key()
        if not is_sha(sha):
            reject(409,'SPACE_PUBLICATION_CANDIDATE_CHANGED')
        verified=self.candidate.current(force=True)
        if verified!=sha:
            reject(409,'SPACE_PUBLICATION_CANDIDATE_CHANGED')
        with self.control.lock:
            self.control._session(sid,csrf)
        return self.public(self.queue.enqueue(sha,verified_sha=verified))

    def authenticated(self,token):
        try:
            return type(token) is str and bool(KEY.fullmatch(token)) and hmac.compare_digest(self.key(),token)
        except (OSError,ValueError):
            return False

    def call(self,token,method,args):
        if not self.authenticated(token):
            reject(403,'SPACE_PUBLISH_AUTH_REQUIRED')
        if method=='ops_space_publish_peek' and exact(args):
            ticket=self.queue.for_thread(thread_id=THREAD_ID)
            return {'request':ticket if ticket and ticket['state']=='pending' else None}
        if not (type(args) is dict and type(args.get('requestId')) is str and
                re.fullmatch(REQUEST_ID,args['requestId'])):
            reject(400,'SPACE_PUBLISH_ARGUMENTS_INVALID')
        if method=='ops_space_publish_claim' and exact(args,'requestId'):
            return {'request':self.queue.claim(args['requestId'],thread_id=THREAD_ID)}
        if method=='ops_space_publish_finish' and exact(args,'requestId','report'):
            return {'request':self.queue.finish(args['requestId'],args['report'],thread_id=THREAD_ID)}
        reject(400,'SPACE_PUBLISH_ARGUMENTS_INVALID')
