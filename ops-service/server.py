"""Owner-local request viewer and service heartbeat receiver."""
import http.client
import json
import os
import threading
from http.cookies import SimpleCookie, CookieError
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from deploy_control import COOKIE, TTL, KEY, Rejected, from_environment
from heartbeat import Heartbeats, MCP_TOOL, SERVER_NAME_PATTERN
from ngs_source_compare import NgsSourceCompare
from space_publish_api import METHODS as SPACE_PUBLISH_TOOLS, SpacePublisher
from space_publish_queue import QueueRejected
from request_progress import ProgressUnavailable, query_id, read_alarms, read_progress

HOST, PORT, MCP_PORT = '127.0.0.1', 8090, 8770
MAX_REPLY = 1024 * 1024
FIELDS = ('officialRequestId','sourceId','projectId','requestType','schemaVersion',
          'status','registeredAt','targetNpcId','targetName')
STATES = ('registered','planned','completed','failed','cancelled')
ASSETS = {'/': ('index.html','text/html; charset=utf-8'),
          '/requests': ('index.html','text/html; charset=utf-8'),
          '/services': ('services.html','text/html; charset=utf-8'),
          '/alarms': ('alarms.html','text/html; charset=utf-8'),
          '/deployments': ('deployments.html','text/html; charset=utf-8'),
          '/deployments.js': ('deployments.js','text/javascript; charset=utf-8'),
          '/app.js': ('app.js','text/javascript; charset=utf-8'),
          '/style.css': ('style.css','text/css; charset=utf-8')}


def parameters(query):
    if not query: return {}
    data = parse_qs(query, keep_blank_values=True, strict_parsing=True, max_num_fields=3)
    if set(data)-{'status','limit','cursor'} or any(len(v)!=1 for v in data.values()):
        raise ValueError('INVALID_QUERY')
    result = {k:v[0] for k,v in data.items()}
    if 'status' in result and result['status'] not in STATES:
        raise ValueError('INVALID_STATUS')
    if 'limit' in result:
        v=result['limit']
        if not re.fullmatch(r'[1-9][0-9]{0,2}',v) or int(v)>100:
            raise ValueError('INVALID_LIMIT')
        result['limit']=int(v)
    if 'cursor' in result and (not 1<=len(result['cursor'])<=1024 or any(ord(c)<33 or ord(c)>126 for c in result['cursor'])):
        raise ValueError('INVALID_CURSOR')
    return result


class MCPFailure(Exception):
    pass


def mcp_call(method, params, *, client=http.client.HTTPConnection, port=None):
    conn=client(HOST,MCP_PORT if port is None else port,timeout=4)
    try:
        body=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params})
        conn.request('POST','/mcp',body,{'Accept':'application/json, text/event-stream','Content-Type':'application/json'})
        reply=conn.getresponse()
        if reply.status!=200 or reply.getheader('Content-Type','').split(';',1)[0].strip().lower()!='application/json':
            raise MCPFailure('REQUEST_MCP_UNAVAILABLE')
        raw=reply.read(MAX_REPLY+1)
        if len(raw)>MAX_REPLY:
            raise MCPFailure('REQUEST_MCP_INVALID_RESPONSE')
        value=json.loads(raw)
        if not isinstance(value,dict) or value.get('jsonrpc')!='2.0' or value.get('id')!=1:
            raise MCPFailure('REQUEST_MCP_INVALID_RESPONSE')
        if 'error' in value or not isinstance(value.get('result'),dict):
            raise MCPFailure('REQUEST_MCP_LIST_UNAVAILABLE')
        return value['result']
    except (OSError,http.client.HTTPException,ValueError) as exc:
        raise MCPFailure('REQUEST_MCP_UNAVAILABLE') from exc
    finally:
        conn.close()


def request_list(filters, call=mcp_call):
    result=call('tools/call',{'name':'request_list','arguments':filters})
    if result.get('isError') is not False:
        raise MCPFailure('REQUEST_MCP_LIST_UNAVAILABLE')
    payload=result.get('structuredContent')
    if not isinstance(payload,dict) or set(payload)!={'requests','nextCursor'}:
        raise MCPFailure('REQUEST_MCP_INVALID_RESPONSE')
    rows=payload['requests']; next_cursor=payload['nextCursor']
    if not isinstance(rows,list) or len(rows)>filters.get('limit',100) or (
        next_cursor is not None and (not isinstance(next_cursor,str) or not 1<=len(next_cursor)<=1024
        or any(ord(c)<33 or ord(c)>126 for c in next_cursor)
        or next_cursor==filters.get('cursor'))):
        raise MCPFailure('REQUEST_MCP_INVALID_RESPONSE')
    clean=[]
    for row in rows:
        if (not isinstance(row,dict) or not set(FIELDS)<=set(row) or row['status'] not in STATES
            or ('status' in filters and row['status']!=filters['status'])
            or any(not isinstance(row[k],str) or len(row[k])>1024 for k in FIELDS[:6])
            or not (isinstance(row['registeredAt'],str) or type(row['registeredAt']) in (int,float))
            or any(row[k] is not None and (not isinstance(row[k],str) or len(row[k])>1024) for k in FIELDS[7:])):
            raise MCPFailure('REQUEST_MCP_INVALID_RESPONSE')
        clean.append({k:row[k] for k in FIELDS})
    return {'requests':clean,'nextCursor':next_cursor}


HEARTBEAT_TOOL = {
    'name': MCP_TOOL,
    'description': 'Register or refresh a local service heartbeat by its unique server name.',
    'inputSchema': {'type':'object','properties':{'serverName':{'type':'string','pattern':'^'+SERVER_NAME_PATTERN+'$'}},
                    'required':['serverName'],'additionalProperties':False},
    'outputSchema': {'type':'object','properties':{'ok':{'const':True}},
                     'required':['ok'],'additionalProperties':False},
}


def name_from(value):
    if not isinstance(value,dict) or set(value)!={'serverName'}:
        raise ValueError('INVALID_HEARTBEAT')
    return value['serverName']


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_): pass

    def send(self,status,body,media='application/json',*,cookie=None):
        if not isinstance(body,bytes):
            body=json.dumps(body,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',media)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        if cookie is not None:
            self.send_header('Set-Cookie',cookie)
        self.send_header('Content-Security-Policy',"default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError): pass

    def do_GET(self):
        host=f'{HOST}:{self.server.server_port}'
        if (self.client_address[0]!=HOST or self.headers.get_all('Host')!=[host]
            or self.headers.get_all('Origin',[]) not in ([],['http://'+host])
            or self.headers.get('Sec-Fetch-Site')=='cross-site'):
            return self.send(403,{'error':'LOCAL_ORIGIN_REQUIRED'})
        try:
            url=urlsplit(self.path)
            if url.netloc or url.scheme or len(self.path)>4096: raise ValueError()
        except ValueError: return self.send(400,{'error':'INVALID_QUERY'})
        if url.path=='/api/deployments':
            if url.query or self.path!='/api/deployments':return self.send(400,{'error':'INVALID_QUERY'})
            return self.deploy_api('status')
        if url.path=='/api/service-status' and not url.query:
            return self.send(200,self.server.heartbeats.snapshot())
        if url.path=='/api/alarms':
            if url.query: return self.send(400,{'error':'INVALID_QUERY'})
            try: return self.send(200,self.server.alarms())
            except ProgressUnavailable: return self.send(503,{'error':'WORKFLOW_ALARMS_UNAVAILABLE'})
        if url.path=='/api/request-progress':
            try: request_id=query_id(url.query)
            except ValueError: return self.send(400,{'error':'INVALID_REQUEST_ID'})
            try: return self.send(200,self.server.request_progress(request_id))
            except ProgressUnavailable: return self.send(503,{'error':'WORKFLOW_PROGRESS_UNAVAILABLE'})
        if url.path=='/api/official-requests':
            try: filters=parameters(url.query)
            except ValueError: return self.send(400,{'error':'INVALID_QUERY'})
            try: return self.send(200,self.server.list_requests(filters))
            except MCPFailure as exc: return self.send(502,{'error':str(exc)})
        if url.path=='/requests' and url.query:
            try: query_id(url.query)
            except ValueError: return self.send(400,{'error':'INVALID_REQUEST_ID'})
        elif url.query:
            return self.send(404,{'error':'NOT_FOUND'})
        if url.path not in ASSETS:
            return self.send(404,{'error':'NOT_FOUND'})
        name,media=ASSETS[url.path]
        self.send(200,(Path(__file__).parent/name).read_bytes(),media)

    def do_POST(self):
        if self.path in ('/api/deployments','/api/deployments/session'):
            return self.deploy_api('session' if self.path.endswith('/session') else 'wake')
        if self.path not in ('/mcp','/api/service-heartbeat'):
            return self.send(405,{'error':'READ_ONLY'})
        host=f'{HOST}:{self.server.server_port}'
        if (self.client_address[0]!=HOST or self.headers.get_all('Host')!=[host]
            or self.headers.get_all('Origin') or self.headers.get('Sec-Fetch-Site')):
            return self.send(403,{'error':'LOCAL_SERVICE_REQUIRED'})
        try:
            sizes=self.headers.get_all('Content-Length',[])
            if (self.headers.get('Transfer-Encoding') or len(sizes)!=1 or
                not re.fullmatch(r'[0-9]{1,4}',sizes[0]) or not 0<int(sizes[0])<=4096 or
                self.headers.get_content_type()!='application/json'):
                raise ValueError('INVALID_BODY')
            self.connection.settimeout(3)
            value=json.loads(self.rfile.read(int(sizes[0])))
        except (ValueError,OSError):
            return self.send(400,{'error':'INVALID_BODY'})
        if self.path=='/mcp':
            return self.handle_mcp(value)
        try:
            if isinstance(value,dict) and set(value)=={'serverName','commitSha'} and isinstance(value['commitSha'],str):
                self.server.heartbeats.record(value['serverName'],commit_sha=value['commitSha'])
            else:
                self.server.heartbeats.record(name_from(value))
        except ValueError:
            return self.send(400,{'error':'INVALID_HEARTBEAT'})
        return self.send(200,{'ok':True})

    def active_control(self):
        control=self.server.deploy_control
        if control is None and self.server.native_deployment_discovery:
            with self.server.deploy_initialization_lock:
                if self.server.deploy_control is None:
                    self.server.deploy_control=from_environment()
                control=self.server.deploy_control
        return control

    def space_publisher(self, control):
        with self.server.space_publisher_lock:
            if self.server.current_space_publisher is None or self.server.current_space_publisher.control is not control:
                self.server.current_space_publisher=SpacePublisher(control,self.server.space_candidate)
            return self.server.current_space_publisher

    def deploy_api(self, action):
        host=f'{HOST}:{self.server.server_port}'
        origins=self.headers.get_all('Origin',[])
        if (self.client_address[0]!=HOST or self.headers.get_all('Host')!=[host] or
            self.headers.get_all('Sec-Fetch-Site',[])!=['same-origin'] or
            (origins not in ([],['http://'+host]) if action=='status' else origins!=['http://'+host])):
            return self.send(403,{'error':'LOCAL_ORIGIN_REQUIRED'})
        control=self.active_control()
        if control is None:
            return self.send(503,{'error':'OPS_DEPLOY_NOT_CONFIGURED'})
        sid=None
        cookie=self.headers.get_all('Cookie',[])
        if len(cookie)>1:
            return self.send(400,{'error':'INVALID_COOKIE'})
        if cookie:
            try:
                parsed=SimpleCookie();parsed.load(cookie[0])
                if COOKIE in parsed and KEY.fullmatch(parsed[COOKIE].value):
                    sid=parsed[COOKIE].value
            except CookieError:
                return self.send(400,{'error':'INVALID_COOKIE'})
        try:
            if action=='status':
                state=control.status(sid)
                # RS-MCP relays the hosted application's observed reply to the
                # existing OPS API. Contact time is not provider publish time.
                live=next((row for row in self.server.heartbeats.snapshot()['services']
                           if row['serverName']=='ngs-space'),None)
                for row in state['cycle'].get('targets',[]):
                    if row['id']!='ngs-space' or not row.get('publication'):
                        continue
                    if live:
                        row['publication']['live']={key:live[key] for key in
                            ('state','commitSha','lastReceivedAt') if key in live}
                    publisher=self.space_publisher(control)
                    if publisher.available():
                        try:
                            row['publication'].update(publisher.status())
                            observation=publisher.native_observation(live.get('commitSha') if live and live['state']=='connected' else None)
                            if observation:
                                row['observed']=observation
                                local=next((r.get('observed') for r in state['cycle'].get('targets',[]) if r['id']=='ngs-local'),None)
                                if local:state['cycle']['comparison']='match' if local['sha']==observation['sha'] else 'mismatch'
                        except (Rejected,OSError,ValueError):
                            row['publication'].update(canRequest=False,candidateSha=None)
                cycle=state['cycle']
                rows={row['id']:row for row in cycle.get('targets',[])}
                pair=[rows.get(name,{}) for name in ('ngs-local','ngs-space')]
                if all(row and row.get('state') not in ('running','queued','failed','unknown') for row in pair):
                    baseline=state.get('observed',{})
                    shas=[(row.get('observed') or baseline.get(name) or {}).get('sha')
                          for row,name in zip(pair,('local','space'))]
                    cycle['sourceComparison']=self.server.ngs_source_compare.classify(*shas)
                else:
                    cycle['sourceComparison']='unknown'
                return self.send(200,state)
            sizes=self.headers.get_all('Content-Length',[])
            if (self.headers.get_all('Transfer-Encoding') or self.headers.get_all('Content-Encoding') or
                self.headers.get_all('Content-Type')!=['application/json'] or len(sizes)!=1 or
                not re.fullmatch(r'[1-9][0-9]{0,2}',sizes[0]) or int(sizes[0])>512):
                raise ValueError('INVALID_BODY')
            def unique(pairs):
                result=dict(pairs)
                if len(result)!=len(pairs):raise ValueError('DUPLICATE_FIELD')
                return result
            self.connection.settimeout(3)
            raw=self.rfile.read(int(sizes[0]))
            if len(raw)!=int(sizes[0]):raise ValueError('INCOMPLETE_BODY')
            value=json.loads(raw.decode('utf-8'),object_pairs_hook=unique)
            if not isinstance(value,dict):raise ValueError('INVALID_BODY')
            if action=='session' and set(value)=={'ownerKey'}:
                session=control.begin(value['ownerKey'])
                return self.send(200,{'ok':True},cookie=f'{COOKIE}={session}; HttpOnly; SameSite=Strict; Path=/api/deployments; Max-Age={TTL}')
            if action=='wake' and set(value)=={'action','target','sha'} and value['action']=='queue-space' and value['target']=='ngs-space':
                token=self.headers.get_all('X-OPS-Deploy-CSRF',[])
                if len(token)!=1 or not KEY.fullmatch(token[0]):
                    return self.send(403,{'error':'OPS_DEPLOY_CSRF_DENIED'})
                return self.send(202,{'request':self.space_publisher(control).submit(sid,token[0],value['sha'])})
            if (action=='wake' and set(value)=={'action','target','sha','expectedPreviousAttemptId'} and
                    value['action']=='retry-local' and value['target']=='ngs-local'):
                token=self.headers.get_all('X-OPS-Deploy-CSRF',[])
                if len(token)!=1 or not KEY.fullmatch(token[0]):
                    return self.send(403,{'error':'OPS_DEPLOY_CSRF_DENIED'})
                return self.send(202,control.retry_local(sid,token[0],value['sha'],value['expectedPreviousAttemptId']))
            if (action=='wake' and set(value)=={'action','target'} and value['action']=='wake' and
                    type(value['target']) is str and re.fullmatch(r'[a-z][a-z0-9-]{0,40}',value['target'])):
                token=self.headers.get_all('X-OPS-Deploy-CSRF',[])
                if len(token)!=1 or not KEY.fullmatch(token[0]):
                    return self.send(403,{'error':'OPS_DEPLOY_CSRF_DENIED'})
                return self.send(202,control.wake(sid,token[0],value['target']))
            raise ValueError('INVALID_BODY')
        except (Rejected,QueueRejected) as exc:
            return self.send(exc.status,{'error':exc.code})
        except (OSError,ValueError):
            return self.send(400,{'error':'INVALID_BODY'})

    def handle_mcp(self,value):
        def reply(key,body):
            return self.send(200,{'jsonrpc':'2.0','id':request_id,key:body})
        request_id=value.get('id') if isinstance(value,dict) else None
        if type(request_id) not in (int,str):
            request_id=None
        if not isinstance(value,dict) or value.get('jsonrpc')!='2.0':
            return reply('error',{'code':-32600,'message':'Invalid Request'})
        method=value.get('method')
        params=value.get('params',{})
        if method=='notifications/initialized' and 'id' not in value:
            return self.send(202,b'')
        if request_id is None:
            return reply('error',{'code':-32600,'message':'Invalid Request'})
        if method=='initialize' and isinstance(params,dict):
            version=params.get('protocolVersion')
            if version not in ('2024-11-05','2025-03-26','2025-06-18','2025-11-25'):
                version='2025-03-26'
            return reply('result',{'protocolVersion':version,'capabilities':{'tools':{}},
                                   'serverInfo':{'name':'ops-service-health','version':'1'}})
        if method=='ping':
            return reply('result',{})
        if method=='tools/list':
            tools=[HEARTBEAT_TOOL]
            tokens=self.headers.get_all('X-OPS-Space-Publisher',[])
            if len(tokens)==1 and KEY.fullmatch(tokens[0]):
                control=self.active_control()
                if control and self.space_publisher(control).authenticated(tokens[0]):
                    for name in SPACE_PUBLISH_TOOLS:
                        props={} if name.endswith('_peek') else {'requestId':{'type':'string'}}
                        if name.endswith('_finish'):props['report']={'type':'object'}
                        tools.append({'name':name,'description':'Owner-approved fixed Space publication handoff',
                                      'inputSchema':{'type':'object','properties':props,
                                                     'required':list(props),'additionalProperties':False}})
            return reply('result',{'tools':tools})
        if method!='tools/call':
            return reply('error',{'code':-32601,'message':'Method not found'})
        if isinstance(params,dict) and params.get('name') in SPACE_PUBLISH_TOOLS:
            keys=self.headers.get_all('X-OPS-Space-Publisher',[])
            if len(keys)!=1 or not KEY.fullmatch(keys[0]):
                return reply('error',{'code':-32001,'message':'SPACE_PUBLISH_AUTH_REQUIRED'})
            control=self.active_control()
            if not control:
                return reply('error',{'code':-32002,'message':'SPACE_PUBLISH_NOT_CONFIGURED'})
            try:
                data=self.space_publisher(control).call(keys[0],params['name'],params.get('arguments'))
                return reply('result',{'isError':False,'structuredContent':data,
                                       'content':[{'type':'text','text':json.dumps(data,separators=(',',':'))}]})
            except (Rejected,QueueRejected,OSError,ValueError) as exc:
                return reply('error',{'code':-32003,'message':exc.code if isinstance(exc,(Rejected,QueueRejected)) else 'SPACE_PUBLISH_UNAVAILABLE'})
        try:
            if not isinstance(params,dict) or params.get('name')!=MCP_TOOL:
                raise ValueError('INVALID_HEARTBEAT')
            self.server.heartbeats.record(name_from(params.get('arguments')))
        except ValueError:
            return reply('error',{'code':-32602,'message':'Invalid heartbeat tool or arguments'})
        return reply('result',{'isError':False,'structuredContent':{'ok':True},
                               'content':[{'type':'text','text':'{"ok":true}'}]})


def server(port=PORT,list_requests=request_list,heartbeats=None,request_progress=read_progress,alarms=read_alarms,deploy_control=None,native_deployment_discovery=False,space_candidate=None):
    http=ThreadingHTTPServer((HOST,port),Handler)
    http.list_requests=list_requests
    http.request_progress=request_progress
    http.alarms=alarms
    http.deploy_control=deploy_control
    http.native_deployment_discovery=native_deployment_discovery
    http.deploy_initialization_lock=threading.Lock()
    http.space_publisher_lock=threading.Lock()
    http.current_space_publisher=None
    http.space_candidate=space_candidate
    http.ngs_source_compare=NgsSourceCompare()
    http.heartbeats=heartbeats if heartbeats is not None else Heartbeats()
    return http


def run_deploy_schedule(cycle, stop, interval=60):
    # The manual wake and this timer use the same coordinator and lock.
    while not stop.is_set():
        try:
            cycle.sweep()
        except (Rejected,OSError,ValueError):
            pass  # The next check can succeed once a trusted candidate exists.
        if stop.wait(interval):
            return


if __name__=='__main__':
    control=from_environment()
    stop=threading.Event()
    with server(deploy_control=control,native_deployment_discovery=True) as app:
        if control is not None and os.environ.get('NGS_OPS_DEPLOY_AUTOMATIC')=='1':
            threading.Thread(target=run_deploy_schedule,args=(control.cycle,stop),daemon=True).start()
        try:
            app.serve_forever()
        finally:
            stop.set()
