import http.client,json,threading,unittest
from contextlib import contextmanager
import server as S

ROW=dict(zip(S.FIELDS,('official.x','source.x','binari','npc.workflow','v2','registered',
                       '2026-11-20T01:00:00Z','npc.x','테스트')))

@contextmanager
def started(server):
    t=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.01})
    t.start()
    try: yield server.server_port
    finally: server.shutdown();t.join();server.server_close()

def get(p,path,headers=None,method='GET'):
    c=http.client.HTTPConnection(S.HOST,p,timeout=2)
    try:
        c.request(method,path,headers=headers or {})
        r=c.getresponse();return r.status,r.read()
    finally:c.close()

class ViewerTests(unittest.TestCase):
    def test_only_known_filters(self):
        self.assertEqual(S.parameters('status=cancelled&limit=22&cursor=next%3D'),{'status':'cancelled','limit':22,'cursor':'next='})
        for q in ('status=all','status=','status=registered&status=cancelled','url=local',
                  'cursor=%00','limit=0','limit=1000','limit=1.2','cursor=x&limit=1&other=2'):
            with self.subTest(q=q),self.assertRaises(ValueError): S.parameters(q)

    def test_read_only_list_and_ui(self):
        seen=[]
        def list_requests(filters):
            seen.append(filters);return {'requests':[ROW],'nextCursor':'p2'}
        with started(S.server(0,list_requests)) as p:
            for path in ('/','/requests'):
                status,body=get(p,path);self.assertEqual(status,200)
                self.assertNotIn(b'http://127.0.0.1:8091/',body);self.assertEqual(S.PORT,8090)
            status,payload=get(p,'/api/official-requests?limit=25&status=registered')
            self.assertEqual((status,json.loads(payload)),(200,{'requests':[ROW],'nextCursor':'p2'}))
            self.assertEqual(seen,[{'limit':25,'status':'registered'}])
            self.assertEqual(get(p,'/api/official-requests?state=old')[0],400)
            self.assertEqual(get(p,'/api/official-requests',method='POST')[0],405)
            self.assertEqual(get(p,'/api/request?id=old')[0],404)

    def test_no_cross_origin_or_wrong_host(self):
        with started(S.server(0,lambda _:self.fail('upstream call')) ) as p:
            for headers in ({'Host':f'localhost:{p}'},{'Origin':'http://wrong.invalid'},
                            {'Origin':'null'},{'Sec-Fetch-Site':'cross-site'}):
                self.assertEqual(get(p,'/api/official-requests',headers)[0],403)

    def test_separate_ops_tabs(self):
        with started(S.server(0)) as p:
            for url in ('/','/requests'):
                status,body=get(p,url)
                self.assertEqual(status,200)
                self.assertIn(b'href="/requests" aria-current="page"',body)
                self.assertIn(b'id="status-filter"',body)
                self.assertNotIn(b'id="service-rows"',body)
            status,body=get(p,'/services')
            self.assertEqual(status,200)
            self.assertIn(b'href="/services" aria-current="page"',body)
            self.assertIn(b'id="service-rows"',body)
            self.assertNotIn(b'id="status-filter"',body)

    def test_heartbeat_first_receipt_thresholds_and_recovery(self):
        import datetime
        clock=[0]
        registry=S.Heartbeats(clock=lambda:clock[0],wall=lambda:datetime.datetime.fromtimestamp(clock[0],datetime.timezone.utc))
        self.assertEqual(registry.snapshot()['services'],[])
        clock[0]=999
        self.assertEqual(registry.snapshot()['services'],[])
        registry.record('second-NGS')
        registry.record('pds')
        clock[0]+=60
        registry.record('pds')
        for elapsed,state,missed in [(60,'connected',1),(119.9,'connected',1),(120,'warning',2),(179.9,'warning',2),(180,'stop',3)]:
            clock[0]=999+elapsed
            rows=registry.snapshot()['services']
            self.assertEqual(rows[1]['serverName'],'second-NGS')
            self.assertEqual((rows[1]['state'],rows[1]['missed']),(state,missed))
        registry.record('second-NGS')
        rows=registry.snapshot()['services']
        self.assertEqual(len(rows),2)
        self.assertEqual((rows[1]['state'],rows[1]['missed']),('connected',0))
        self.assertNotEqual(rows[0]['lastReceivedAt'], rows[1]['lastReceivedAt'])

    def test_dynamic_heartbeat_routes_independent_of_ledger(self):
        def post(port,path,value,headers=None):
            c=http.client.HTTPConnection(S.HOST,port,timeout=2)
            try:
                c.request('POST',path,json.dumps(value),{'Content-Type':'application/json',**(headers or {})})
                r=c.getresponse();return r.status,json.loads(r.read())
            finally:c.close()
        with started(S.server(0,lambda _:self.fail('ledger call'))) as p:
            def rows():
                status,body=get(p,'/api/service-status')
                self.assertEqual(status,200)
                return json.loads(body)['services']
            self.assertEqual(rows(),[])
            self.assertEqual(post(p,'/api/service-heartbeat',{'serverName':'pds-peer'}),(200,{'ok':True}))
            rpc={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':S.MCP_TOOL,'arguments':{'serverName':'ngs-fixture'}}}
            self.assertEqual(post(p,'/mcp',rpc)[1]['result']['structuredContent'],{'ok':True})
            self.assertEqual(post(p,'/mcp',{**rpc,'method':'tools/list'})[1]['result']['tools'][0]['name'],S.MCP_TOOL)
            for malformed in ({'serviceId':'previous-format'},{'serverName':'../x'},{'serverName':'test','other':True}):
                self.assertEqual(post(p,'/api/service-heartbeat',malformed)[0],400)
            self.assertEqual(post(p,'/api/service-heartbeat',{'serverName':'wrong'},{'Origin':'http://wrong.invalid'})[0],403)
            self.assertEqual(get(p,'/api/service-status?target=other')[0],404)
            self.assertEqual(get(p,'/api/service-status',method='POST')[0],405)
            self.assertEqual([(r['serverName'],r['state']) for r in rows()],[('ngs-fixture','connected'),('pds-peer','connected')])

    def test_ngs_observations_are_separate_and_only_refresh_on_valid_contact(self):
        import datetime
        clock=[0]
        registry=S.Heartbeats(clock=lambda:clock[0],wall=lambda:datetime.datetime.fromtimestamp(clock[0],datetime.timezone.utc))
        sha='a'*40
        route='/api/service-heartbeat'
        with started(S.server(0,lambda _:self.fail('ledger call'),heartbeats=registry)) as port:
            def post(value, headers=None, path=route):
                c=http.client.HTTPConnection(S.HOST,port,timeout=2)
                try:
                    c.request('POST',path,json.dumps(value),{'Content-Type':'application/json',**(headers or {})})
                    r=c.getresponse()
                    return r.status, json.loads(r.read())
                finally:c.close()
            def observations():
                status,body=get(port,'/api/service-status')
                self.assertEqual(status,200)
                return [row for row in json.loads(body)['services'] if 'commitSha' in row]
            self.assertEqual(observations(),[])
            self.assertEqual(post({'serverName':'ngs-local'}),(200,{'ok':True}))
            first,=observations()
            self.assertEqual((first['serverName'],first['commitSha'],first['state']),('ngs-local',None,'connected'))
            clock[0]=30
            self.assertEqual(post({'serverName':'ngs-local','commitSha':sha})[0],200)
            self.assertEqual(post({'serverName':'ngs-space','commitSha':'b'*40})[0],200)
            local,space=observations()
            self.assertEqual((local['commitSha'],space['commitSha']),(sha,'b'*40))
            self.assertNotEqual(local['lastReceivedAt'],first['lastReceivedAt'])
            for body in ({'serverName':'rs','commitSha':sha},{'serverName':'ngs-space','commitSha':None},{'serverName':'ngs-local','commitSha':'abc123'},
                         {'serverName':'ngs-local','commitSha':'A'*40},{'serverName':'ngs-local','source':'pds'},
                         {'serverName':'ngs-local','lastReceivedAt':'invented'},{}):
                self.assertEqual(post(body)[0],400)
            for headers in ({'Origin':f'http://{S.HOST}:{port}'},{'Sec-Fetch-Site':'same-origin'},
                            {'Host':f'localhost:{port}'}):
                self.assertEqual(post({'serverName':'ngs-local'},headers)[0],403)
            self.assertEqual(get(port,route)[0],404)
            self.assertEqual(post({'serverName':'ngs-local'},path=route+'?source=space')[0],405)
            self.assertEqual(observations(),[local,space])
            self.assertEqual(post({'serverName':'NGS Space'},path='/api/service-heartbeat')[0],400)
            self.assertEqual(post({'serverName':'pds'},path='/api/service-heartbeat')[0],200)
            rpc={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':S.MCP_TOOL,
                 'arguments':{'serverName':'ngs-space','commitSha':sha}}}
            self.assertEqual(post(rpc,path='/mcp')[1]['error']['code'],-32602)
            self.assertEqual(observations(),[local,space])
            clock[0]=150
            self.assertEqual([row['state'] for row in observations()],['warning','warning'])
            clock[0]=210
            self.assertEqual([row['state'] for row in observations()],['stop','stop'])
            self.assertEqual(post({'serverName':'ngs-local'})[0],200)
            local,space=observations()
            self.assertEqual((local['commitSha'],local['state'],space['commitSha'],space['state']),(None,'connected','b'*40,'stop'))

    def test_fixed_mcp_exact_read_tool_and_result_allowlist(self):
        seen=[]
        def call(method,params):
            seen.append((method,params));return {'isError':False,'structuredContent':
              {'requests':[{**ROW,'internal':'not returned'}],'nextCursor':'p2'}}
        self.assertEqual(S.request_list({'limit':25},call),{'requests':[ROW],'nextCursor':'p2'})
        self.assertEqual(seen,[('tools/call',{'name':'request_list','arguments':{'limit':25}})])

    def test_only_explicit_unsupported_failure_reason_is_forwarded(self):
        code='REQUEST_UNSUPPORTED_RECREATE'
        for state,reason,expected in [('failed',code,True),('registered',code,False),
                                      ('failed','unknown details',False),('failed',{'secret':'no'},False)]:
            row={**ROW,'status':state,'failureReason':reason,'payload':{'secret':'no'}}
            result=S.request_list({},lambda *_:{'isError':False,'structuredContent':{'requests':[row],'nextCursor':None}})
            clean={**ROW,'status':state}
            if expected:clean['failureReason']=code
            self.assertEqual(result['requests'],[clean])

    def test_workflow_statuses_pass_through_with_filters(self):
        for state in ('planned','completed','failed'):
            filters={'status':state}
            value={**ROW,'status':state}
            payload={'requests':[value],'nextCursor':None}
            self.assertEqual(S.parameters('status='+state),filters)
            self.assertEqual(S.request_list(filters,lambda *_:{'isError':False,'structuredContent':payload}),payload)

    def test_fails_closed(self):
        for value in ({'isError':True,'structuredContent':{'error':'sensitive'}},
          {'isError':False,'structuredContent':{'requests':[ROW]}},
          {'isError':False,'structuredContent':{'requests':[{**ROW,'status':'running'}],'nextCursor':None}}):
            with self.assertRaises(S.MCPFailure):S.request_list({},lambda *args:value)
        def unavailable(_):raise S.MCPFailure('REQUEST_MCP_LIST_UNAVAILABLE')
        with started(S.server(0,unavailable)) as p:
            self.assertEqual(get(p,'/api/official-requests')[0],502)

    def test_real_loopback_mcp_transport_outside_live_services(self):
        import unittest.mock
        calls=[]
        class LocalMCP(S.BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                calls.append((self.path,self.headers['Accept'],json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                reply={'jsonrpc':'2.0','id':1,'result':{'isError':False,'structuredContent':{'requests':[],'nextCursor':None}}}
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(reply).encode())
        with started(S.ThreadingHTTPServer((S.HOST,0),LocalMCP)) as p, unittest.mock.patch.object(S,'MCP_PORT',p):
            self.assertEqual(S.request_list({'status':'cancelled'}),{'requests':[],'nextCursor':None})
        self.assertEqual(calls,[('/mcp','application/json, text/event-stream',{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'request_list','arguments':{'status':'cancelled'}}})])

if __name__=='__main__': unittest.main()
