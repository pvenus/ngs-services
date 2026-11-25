import base64,hashlib,json,os,tempfile,threading,unittest
from unittest.mock import patch
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen

from http.server import ThreadingHTTPServer
from project_data_service.http_server import Handler
from project_data_service.project_folders import configured
from project_data_service.project_folders_mcp import VERSION
from project_data_service import recursive_folders
from project_data_service.recursive_folders import CHUNK,MAX_LEGACY_BUFFERED_FILE


class Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        root=Path(tmp.name).resolve()/'canonical-files';project=root/'binari';project.mkdir(parents=True)
        (project/'project.json').write_bytes(b'{}')
        (project/'.internal').mkdir();(project/'.internal/private').write_bytes(b'secret')
        self.file=project/'guide.bin';self.data=b'a'*CHUNK+b'last';self.file.write_bytes(self.data)
        self.sha=hashlib.sha256(self.data).hexdigest()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.project_folders=configured(root,'127.0.0.1',validate_mount=False);t=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);t.start()
        self.addCleanup(t.join);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        self.url='http://127.0.0.1:'+str(server.server_port)+'/mcp'

    def http(self,body=None,**headers):
        h={'Content-Type':'application/json','Accept':'application/json, text/event-stream',
           'MCP-Protocol-Version':VERSION,**headers}
        req=Request(self.url,data=None if body is None else json.dumps(body).encode(),headers=h)
        try:r=urlopen(req,timeout=4)
        except HTTPError as e:r=e
        with r:
            raw=r.read()
            return r.code,json.loads(raw)if raw else None,r.headers

    def rpc(self,method,args=None):
        status,body,headers=self.http({'jsonrpc':'2.0','id':7,'method':method,**({'params':args}if args is not None else {})})
        self.assertEqual((status,body['id'],body['jsonrpc']),(200,7,'2.0'))
        self.assertIsNone(headers['Mcp-Session-Id'])
        return body

    def tool(self,name,**arguments):
        return self.rpc('tools/call',{'name':name,'arguments':arguments})['result']

    def value(self,name,**args):
        r=self.tool(name,**args)
        self.assertIs(r['isError'],False)
        self.assertEqual(json.loads(r['content'][0]['text']),r['structuredContent'])
        return r['structuredContent']

    def test_genuine_stateless_initialize_tool_list_and_discovery(self):
        base=self.rpc('initialize',{'protocolVersion':VERSION,'capabilities':{},'clientInfo':{'name':'fixture','version':'1'}})['result']
        self.assertEqual(base['protocolVersion'],VERSION)
        self.assertIn('tools',base['capabilities'])
        self.assertEqual(self.http({'jsonrpc':'2.0','method':'notifications/initialized'})[:2],(202,None))
        self.assertEqual(self.rpc('ping')['result'],{})
        tools=self.rpc('tools/list')['result']['tools']
        self.assertEqual({t['name']for t in tools},{'npc_project_folders_list','npc_folders_compare','npc_folder_file_chunk'})
        self.assertTrue(all(t['annotations']['readOnlyHint'] and t['outputSchema']['type']=='object' for t in tools))
        self.assertEqual(self.value('npc_project_folders_list'),{'projects':['binari']})

    def test_compare_contention_names_mcp_holder_and_rejected_request(self):
        entered=threading.Event();release=threading.Event();completed=[]
        original=recursive_folders._inventory
        def inventory(*args,**kwargs):
            if kwargs.get('hash_files'):
                entered.set()
                if not release.wait(4):raise AssertionError('compare holder not released')
            return original(*args,**kwargs)
        def acquire_compare():
            completed.append(self.tool('npc_folders_compare',scope='binari',files=[]))
        with patch.object(recursive_folders,'_inventory',side_effect=inventory), \
             patch.object(recursive_folders,'_log_capacity') as log:
            holder=threading.Thread(target=acquire_compare,daemon=True);holder.start()
            try:
                self.assertTrue(entered.wait(3))
                rejected=self.tool('npc_folders_compare',scope='binari/concept',files=[])
                self.assertTrue(rejected['isError'])
                self.assertEqual(json.loads(rejected['content'][0]['text']),{'error':'FOLDER_BUSY','status':503})
                busy=log.call_args
                self.assertEqual(busy.args,('pds_folder_busy',))
                data=busy.kwargs
                self.assertEqual((data['pool'],data['limit']),('compare',1))
                self.assertEqual((data['request']['operation'],data['request']['scope']),('mcp_compare','binari/concept'))
                first,=data['holders']
                self.assertEqual((first['operation'],first['scope']),('mcp_compare','binari'))
                self.assertNotEqual(first['requestId'],data['request']['requestId'])
            finally:
                release.set();holder.join(6)
            self.assertEqual(len(completed),1)
            self.assertIs(completed[0]['isError'],False)
            self.assertEqual(log.call_count,2)
            last=log.call_args
            self.assertEqual(last.args,('pds_folder_busy_holder_released',))
            self.assertEqual(last.kwargs['request']['requestId'],first['requestId'])
            self.assertGreaterEqual(last.kwargs['heldMs'],first['heldMs'])
            self.value('npc_folders_compare',scope='binari',files=[])
            self.assertEqual(log.call_count,2)

    def test_fetch_contention_reports_both_holders_and_releases_after_error(self):
        capacity=recursive_folders._FolderCapacity('fetch',2)
        with patch.object(recursive_folders,'_log_capacity') as log:
            with capacity.hold('http_whole_file','binari/a'):
                with self.assertRaisesRegex(RuntimeError,'test exit'):
                    with capacity.hold('mcp_file_chunk','binari/b'):
                        with self.assertRaisesRegex(recursive_folders.FolderError,'FOLDER_BUSY'):
                            with capacity.hold('mcp_file_chunk','binari/c'):self.fail('acquired third slot')
                        raise RuntimeError('test exit')
                with capacity.hold('mcp_file_chunk','binari/c'):pass
            busy,*released=log.call_args_list
            self.assertEqual(busy.args,('pds_folder_busy',))
            self.assertEqual([h['scope'] for h in busy.kwargs['holders']],['binari/a','binari/b'])
            self.assertEqual({c.kwargs['request']['requestId'] for c in released},
                             {h['requestId'] for h in busy.kwargs['holders']})
            self.assertTrue(all(c.args==('pds_folder_busy_holder_released',) for c in released))

    def test_mount_missing_then_recovered_without_listener_restart(self):
        root=self.file.parent.parent
        away=root.with_name('not-mounted')
        root.rename(away)
        try:
            failure=self.tool('npc_project_folders_list')
            self.assertTrue(failure['isError'])
            self.assertEqual(json.loads(failure['content'][0]['text'])['status'],503)
            with self.assertRaises(HTTPError) as err:
                urlopen(self.url.removesuffix('/mcp')+'/v1/project-folders',timeout=3)
            self.assertEqual(err.exception.code,503)
        finally: away.rename(root)
        self.assertEqual(self.value('npc_project_folders_list'),{'projects':['binari']})

    def test_grouped_recursive_compare_and_hash_bound_bounded_chunks(self):
        b=self.value('npc_folders_compare',scope='binari',files=[])
        self.assertEqual(b['scope'],'binari')
        visible={x['path']:x for g in b['groups']for x in g['files']}
        self.assertEqual(set(visible),{'guide.bin','project.json'})
        self.assertEqual(self.value('npc_folders_compare',scope='binari',
            files=[{k:v for k,v in x.items()if k!='size'}for x in visible.values()]),{'scope':'binari','groups':[]})
        args={'scope':'binari','path':'guide.bin','sha256':self.sha}
        head=self.value('npc_folder_file_chunk',**args,offset=0)
        tail=self.value('npc_folder_file_chunk',**args,offset=head['nextOffset'])
        self.assertEqual((head['size'],head['offset'],head['nextOffset'],tail['offset'],tail['nextOffset']),
                         (len(self.data),0,CHUNK,CHUNK,None))
        self.assertEqual(base64.b64decode(head['base64'])+base64.b64decode(tail['base64']),self.data)
        self.file.write_bytes(self.data[:-1]+b'X')
        r=self.tool('npc_folder_file_chunk',**args,offset=CHUNK)
        self.assertTrue(r['isError']);self.assertEqual(json.loads(r['content'][0]['text'])['status'],409)

    def test_large_sparse_file_comparison_and_pinned_chunks_beyond_former_limit(self):
        size=MAX_LEGACY_BUFFERED_FILE+CHUNK+4
        with self.file.open('wb') as stream:
            stream.seek(size-4)
            stream.write(b'tail')
        digest=hashlib.sha256()
        with self.file.open('rb') as stream:
            while data:=stream.read(CHUNK):digest.update(data)
        h=digest.hexdigest()
        compare=self.value('npc_folders_compare',scope='binari',files=[])
        large=next(row for group in compare['groups'] for row in group['files'] if row['path']=='guide.bin')
        self.assertEqual(large,{'path':'guide.bin','sha256':h,'size':size})
        args={'scope':'binari','path':'guide.bin','sha256':h}
        with patch.object(recursive_folders.hashlib,'sha256',side_effect=AssertionError('file rehashed per chunk')):
            head=self.value('npc_folder_file_chunk',**args,offset=0)
            middle=self.value('npc_folder_file_chunk',**args,offset=MAX_LEGACY_BUFFERED_FILE)
            tail=self.value('npc_folder_file_chunk',**args,offset=middle['nextOffset'])
        self.assertEqual((len(base64.b64decode(head['base64'])),head['nextOffset']), (CHUNK,CHUNK))
        self.assertEqual((tail['size'],base64.b64decode(tail['base64']),tail['nextOffset']), (size,b'tail',None))
        pin=self.file.stat()
        with self.file.open('r+b') as stream:
            stream.seek(size-4);stream.write(b'edit')
        # Even a preserved mtime cannot reuse a stale identity/pin.
        os.utime(self.file,ns=(pin.st_atime_ns,pin.st_mtime_ns))
        self.assertTrue(self.tool('npc_folder_file_chunk',**args,offset=size-4)['isError'])
        self.assertEqual(json.loads(self.tool('npc_folder_file_chunk',**args,offset=size+1)['content'][0]['text'])['status'],416)

    def test_rejects_web_origins_and_unverified_inputs(self):
        empty={'jsonrpc':'2.0','id':7,'method':'tools/list'}
        for key,val,status in (('Origin','http://evil.invalid',403),('Host','bad',403),
                               ('Mcp-Session-Id','unexpected',400),('Accept','application/json',406)):
            self.assertEqual(self.http(empty,**{key:val})[0],status)
        self.assertEqual(self.http()[0],405)
        # Old display REST remains compatible, new recursive routes belong only to MCP here.
        for path,expected in (('/v1/project-folders',200),('/v1/folders/compare',404)):
            req=Request(self.url.removesuffix('/mcp')+path)
            try:r=urlopen(req,timeout=3)
            except HTTPError as e:r=e
            with r:self.assertEqual(r.code,expected)
        r=self.tool('npc_folders_compare',scope='binari/.internal',files=[])
        self.assertTrue(r['isError']);self.assertNotIn('structuredContent',r)
        self.assertEqual(self.rpc('tools/call',{'name':'execute','arguments':{}})['error']['code'],-32602)
        self.assertEqual(self.rpc('tools/call',{'name':'npc_project_folders_list','arguments':{'root':'/tmp'}})['error']['code'],-32602)

    def test_opt_in_deletion_delta_is_sorted_local_hashes_and_legacy_unchanged(self):
        name='npc_folders_compare';project=self.file.parent
        (project/'folder').mkdir();(project/'folder/new').write_bytes(b'new')
        supplied=[{'path':path,'sha256':sha} for path,sha in
                  [('z.png','c'*64),('guide.bin',self.sha),('a/one.png','a'*64),
                   ('a.png','b'*64),('project.json','d'*64)]]
        flags=self.rpc('tools/list')['result']['tools'][1]
        self.assertEqual(flags['inputSchema']['properties']['includeDeleted'],{'type':'boolean','const':True})
        self.assertEqual(flags['inputSchema']['required'],['scope','files'])
        self.assertNotIn('deleted',flags['outputSchema']['required'])
        legacy=self.value(name,scope='binari',files=supplied)
        result=self.value(name,scope='binari',files=supplied,includeDeleted=True)
        self.assertEqual(legacy,{k:v for k,v in result.items() if k!='deleted'})
        self.assertEqual(result['deleted'],[
            {'path':'a.png','sha256':'b'*64},{'path':'a/one.png','sha256':'a'*64},{'path':'z.png','sha256':'c'*64}])
        self.assertEqual({r['path'] for g in result['groups'] for r in g['files']},{'project.json','folder/new'})
        self.assertEqual(self.value(name,scope='binari/folder',files=[],includeDeleted=True)['deleted'],[])
        self.assertFalse((project/'a.png').exists())
        self.assertEqual(self.file.read_bytes(),self.data)

    def test_confirmed_named_missing_empty_or_racing_scopes(self):
        from unittest.mock import patch
        from project_data_service import recursive_folders as rf
        name='npc_folders_compare';scope='binari/npcs/npc.test/animation-sequences'
        delta=[{'path':'idle/walk/nw.json','sha256':'a'*64}]
        legacy=self.tool(name,scope=scope,files=delta)
        self.assertEqual(json.loads(legacy['content'][0]['text']),
                         {'error':'FOLDER_SCOPE_NOT_FOUND','status':404})
        want={'scope':scope,'groups':[],'deleted':delta}
        self.assertEqual(self.value(name,scope=scope,files=delta,includeDeleted=True),want)
        directory=self.file.parent/'npcs/npc.test/animation-sequences';directory.mkdir(parents=True)
        self.assertEqual(self.value(name,scope=scope,files=delta,includeDeleted=True),want)
        again=rf._inventory
        def race(fd,**options):
            result=again(fd,**options)
            if options['hash_files']:directory.rename(directory.with_name('moved'))
            return result
        with patch.object(rf,'_inventory',side_effect=race):
            raced=self.tool(name,scope=scope,files=delta,includeDeleted=True)
        self.assertTrue(raced['isError']);self.assertNotIn('structuredContent',raced)
        self.assertEqual(json.loads(raced['content'][0]['text']),{'error':'FOLDER_SOURCE_CHANGED','status':409})
        configured_root=self.file.parent.parent
        away=configured_root.with_name('old-mount')
        configured_root.rename(away)
        try:
            failed=self.tool(name,scope=scope,files=delta,includeDeleted=True)
            self.assertTrue(failed['isError']);self.assertNotIn('structuredContent',failed)
            self.assertEqual(json.loads(failed['content'][0]['text'])['status'],503)
        finally:away.rename(configured_root)

    def test_no_tombstones_from_detached_canonical_root(self):
        from unittest.mock import patch
        from project_data_service import recursive_folders as rf
        root=self.file.parent.parent
        away=root.with_name('detached')
        old=rf._inventory
        def replace(fd,**options):
            result=old(fd,**options)
            if not options['hash_files']:
                root.rename(away)
                root.mkdir()
            return result
        with patch.object(rf,'_inventory',side_effect=replace):
            r=self.tool('npc_folders_compare',scope='binari',files=[{'path':'old.png','sha256':'a'*64}],
                        includeDeleted=True)
        self.assertTrue(r['isError']);self.assertNotIn('structuredContent',r)
        self.assertEqual(json.loads(r['content'][0]['text']),{'error':'FOLDER_SOURCE_CHANGED','status':409})

    def test_delete_delta_rejects_invalid_mixed_and_oversize_inputs(self):
        from project_data_service.recursive_folders import MAX_ENTRIES
        name='npc_folders_compare'
        for invalid in (False,1,None,'true'):
            rejected=self.rpc('tools/call',{'name':name,'arguments':{
                'scope':'binari','files':[],'includeDeleted':invalid}})
            self.assertEqual(rejected['error']['code'],-32602)
        pair={'path':'legitimate.png','sha256':'a'*64}
        for rows in ([pair,pair], [{**pair,'path':'../outside'}], [{**pair,'path':'.private'}],
                     [{**pair,'sha256':'invalid'}], [pair]*(MAX_ENTRIES+1)):
            failed=self.tool(name,scope='binari',files=rows,includeDeleted=True)
            self.assertTrue(failed['isError']);self.assertNotIn('structuredContent',failed)
            self.assertEqual(json.loads(failed['content'][0]['text'])['status'],400)
