"""Only the four direct image ingress routes use this request capability boundary."""
from contextvars import ContextVar
from contextlib import contextmanager
from .npc_ownership import parse_headers
from .filex_read import require
INGRESS=ContextVar('direct_image_ingress_guard',default=None)
@contextmanager
def ingress(handler):
 require(handler.client_address[0]=='127.0.0.1'and handler.headers.get('Host')=='127.0.0.1:'+str(handler.server.server_port)and handler.headers.get('Origin')is None and not handler.headers.get('Transfer-Encoding')and '?'not in handler.path,'DIRECT_IMAGE_LOCAL_ONLY')
 guard=parse_headers(handler.headers)
 require(isinstance(guard,dict)and set(guard)=={'npcId','generationId','requestId','leaseToken','stageRequestId'}and handler.headers.get('X-NPC-Stage-Request-Id')==guard['stageRequestId'],'DIRECT_IMAGE_GUARD_REQUIRED')
 token=INGRESS.set(guard)
 try:yield
 finally:INGRESS.reset(token)
def verify_binding(claim,binding):
 guard=INGRESS.get()
 require(isinstance(guard,dict)and guard.get('stageRequestId')==claim.get('request_id')and {k:v for k,v in guard.items()if k!='stageRequestId'}==binding.get('parentGuard'),'DIRECT_IMAGE_GUARD_BINDING')
