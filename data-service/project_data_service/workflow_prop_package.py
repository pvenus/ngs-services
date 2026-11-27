"""Private PDS bridge; binds final publish and authorized pinned reads to Filex."""
import re
from urllib.parse import urlsplit
from .filex_read import canonical,digest
from .workflow_delete_claim import ClaimDenied
from .workflow_motion_2d import strict_json

PATH='/v1/props/ownership/workflow-package'
ROUTES=(PATH,PATH+'/prepare',PATH+'/read',PATH+'/asset',PATH+'/head')
WRITER='/v1/workflow/props'
CAPABILITY='workflow.prop-package-write.v1'
MAX_MANIFEST=512*1024
MAX_REVISION=2**53-2
KEYS=('scope','members','manifestSha256','resultRef')

class Denied(ValueError):pass
class Stale(ValueError):pass
def need(v):
    if not v:raise Denied('WORKFLOW_PROP_PACKAGE_DENIED')
def keys(v,k):return type(v) is dict and set(v)==set(k)
def match(v,p):return type(v)is str and re.fullmatch(p,v) is not None
def prop_id(v):return type(v)is str and 2<=len(v)<=80 and match(v,r'[a-z0-9]+(?:[-._][a-z0-9]+)*')
def pin(v):
    need(keys(v,('propId','revision','sha256')) and prop_id(v['propId']) and type(v['revision']) is int and
         1<=v['revision']<=MAX_REVISION and match(v['sha256'],r'[a-f0-9]{64}'))
    return v

def bound_prop_writer(factory,*,namespace='canonical-files/binari/concept/'):
    routes={WRITER,WRITER+'/head',WRITER+'/read',WRITER+'/asset'}
    def call(path,body):
        need(path in routes)
        with factory() as transport:
            result=transport.call('POST',path,body,missing=path in (WRITER+'/read',WRITER+'/asset'))
        if result is None:raise FileNotFoundError('pinned prop missing')
        return result
    def ready():
        with factory() as transport:
            c=transport.capabilities()
            return c.get('namespace')==namespace and c.get('workflowPropPackageV1') is True
    call.ready=ready
    return call

class WorkflowPropPackage:
    def __init__(self,authority,writer):
        need(all(callable(getattr(authority,name,None)) for name in ('snapshot','authorize_read','read_media')) and callable(writer))
        self.authority,self.writer=authority,writer

    def readiness(self):
        probe=getattr(self.authority,'readiness',None)
        return callable(probe) and probe() is True

    def publish(self,packet):
        need(keys(packet,('claim','manifest')) and type(packet['manifest']) is dict and len(canonical(packet['manifest']))<=MAX_MANIFEST)
        m=packet['manifest'];claim=packet['claim']
        need(keys(claim,('officialRequestId','projectId','taskId','payloadSha256','claimStamp')) and prop_id(m.get('propId')) and
          m.get('projectId')=='binari'==claim['projectId'] and m.get('officialRequestId')==claim['officialRequestId'] and
          m.get('requestSha256')==claim['payloadSha256'])
        # The production authority reserves the exact Planner claim before Filex
        # I/O. The trusted authority supplies the plan-bound proof;
        # callers cannot supply candidate receipts, directories, or media paths.
        with self.authority.snapshot(claim) as prepare:
            head=self.writer(WRITER+'/head',{'propId':m['propId']})['pointer']
            current={k:head[k] for k in ('revision','sha256')} if head is not None else None
            prior=None
            if type(m.get('revision')) is int and m['revision']>1:
                q=pin({'propId':m['propId'],'revision':m['revision']-1,'sha256':m.get('parentSha256')})
                prior=self.writer(WRITER+'/read',q)['manifest']
            # Preserve idempotency after losing the final HTTP response. If a
            # same-ID revision is already public, validate the still-exact
            # claim/candidate using its recorded pre-commit head, then return
            # only the matching durable receipt; never write that version again.
            replay=None
            if head is not None and type(m.get('revision')) is int and head['revision']>=m['revision']:
                digest_m=digest(m)
                try:replay=self.writer(WRITER+'/read',pin({'propId':m['propId'],'revision':m['revision'],'sha256':digest_m}))
                except FileNotFoundError:raise Stale() from None
                need(replay['manifest']==m)
                current={'revision':prior['revision'],'sha256':digest(prior)} if prior else None
            elif prior is not None and current!={'revision':prior['revision'],'sha256':digest(prior)}:
                raise Stale()
            c=prepare(m,prior,current,replayed=replay is not None)
            need(keys(c,KEYS) and c['manifestSha256']==digest(m) and c['resultRef']=='prop.package.'+c['manifestSha256'])
            if replay is not None:
                receipt=replay['receipt']
            else:
                media=self.authority.read_media(claim,c,m,prior)
                need(type(media)is dict and len(media)<=(5 if c['scope'].get('presentationProfile') in ('spirit-v1','spirit-v2') else 4))
                receipt=self.writer(WRITER,{'candidate':c,'manifest':m,'media':media})
            need(keys(receipt,(*KEYS,'schemaVersion','authority','status')) and
              receipt['schemaVersion']=='ngs.prop-package-publication-receipt.v1' and receipt['authority']=='filex' and
              receipt['status']=='committed' and all(receipt[k]==c[k] for k in KEYS))
            read=self.writer(WRITER+'/read',{'propId':m['propId'],'revision':m['revision'],'sha256':c['manifestSha256']})
            need(read.get('receipt')==receipt and read.get('manifest')==m and read.get('prior')==prior)
        return {'ok':True,'resultRef':c['resultRef']}

    def read(self,operation,packet):
        if operation=='head':
            need(keys(packet,('propId',)) and prop_id(packet['propId']))
            self.authority.authorize_read(operation,packet)
        elif operation=='read':
            pin(packet);self.authority.authorize_read(operation,packet)
        else:
            need(keys(packet,('pin','assetId')) and match(packet['assetId'],r'[a-f0-9]{64}\.png'))
            pin(packet['pin']);self.authority.authorize_read(operation,packet)
        return self.writer(WRITER+'/'+operation,packet)

def serve(handler):
    obj=getattr(handler.server,'workflow_prop_package',None)
    if not isinstance(obj,WorkflowPropPackage):return handler.respond(404,{'error':'WORKFLOW_PROP_PACKAGE_DISABLED'})
    parsed=urlsplit(handler.path)
    if (handler.client_address[0]!='127.0.0.1' or handler.headers.get('Host')!='127.0.0.1:'+str(handler.server.server_port) or
        handler.headers.get('Origin') is not None or parsed.query or handler.headers.get('Transfer-Encoding') is not None):
        return handler.respond(403,{'error':'WORKFLOW_PROP_PACKAGE_INTERNAL_ONLY'})
    try:
        if handler.command=='GET' and parsed.path==PATH:
            # An explicit injected instance is a development/test boundary;
            # production never configures one until every adapter is verified.
            if not obj.readiness():return handler.respond(503,{'error':'WORKFLOW_PROP_PACKAGE_NOT_READY'})
            get_modes=getattr(obj.authority,'package_modes',None)
            modes=get_modes() if callable(get_modes) else ['still']
            return handler.respond(200,{'enabled':True,'schemaVersion':CAPABILITY,'modes':modes})
        if handler.command!='POST':return handler.respond(404,{'error':'WORKFLOW_PROP_PACKAGE_ROUTE'})
        n=handler.headers.get('Content-Length','')
        maximum=MAX_MANIFEST+8192 if parsed.path==PATH else 4096
        if handler.headers.get('Content-Type')!='application/json' or not n.isdecimal() or len(n)>10 or not 0<int(n)<=maximum:
            return handler.respond(400,{'error':'WORKFLOW_PROP_PACKAGE_BAD_BODY'})
        handler.connection.settimeout(20)
        raw=handler.rfile.read(int(n))
        if len(raw)!=int(n):return handler.respond(400,{'error':'WORKFLOW_PROP_PACKAGE_BAD_BODY'})
        body=strict_json(raw)
        if parsed.path==PATH+'/prepare':
            prepare=getattr(obj.authority,'prepare_manifest',None)
            need(callable(prepare) and keys(body,('claim',)))
            return handler.respond(200,{'manifest':prepare(body['claim'])})
        result=obj.publish(body) if parsed.path==PATH else obj.read(parsed.path.rsplit('/',1)[-1],body)
        return handler.respond(200,result)
    except Stale:return handler.respond(409,{'error':'WORKFLOW_PROP_PACKAGE_HEAD_CHANGED'})
    except (Denied,ClaimDenied):return handler.respond(403,{'error':'WORKFLOW_PROP_PACKAGE_DENIED'})
    except FileNotFoundError:return handler.respond(404,{'error':'WORKFLOW_PROP_PACKAGE_NOT_FOUND'})
    except (OSError,ValueError):return handler.respond(503,{'error':'WORKFLOW_PROP_PACKAGE_OUTCOME_UNCERTAIN'})
