import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zlib
from http.server import ThreadingHTTPServer

from project_data_service.http_server import Handler
from project_data_service.project_folders import configured


def png():
    def chunk(label,value):
        return struct.pack('>I',len(value))+label+value+struct.pack('>I',zlib.crc32(label+value))
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,6,0,0,0))
            +chunk(b'IDAT',zlib.compress(b'\0\xff\0\0\xff'))+chunk(b'IEND',b''))


def entry(name,raw):return {'name':name,'sha256':hashlib.sha256(raw).hexdigest()}


class Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name).resolve()/'canonical-files'
        self.project=self.root/'binari';self.project.mkdir(parents=True)
        self.config=b'{"name":"binari"}\n';self.icon=png()
        (self.project/'project.json').write_bytes(self.config)
        (self.project/'icon.png').write_bytes(self.icon)
        (self.project/'private.json').write_bytes(b'private')
        (self.root/'unrelated').mkdir()
        http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.http=http
        http.project_folders=configured(self.root,'127.0.0.1')
        t=threading.Thread(target=http.serve_forever,kwargs={'poll_interval':.01},daemon=True);t.start()
        self.addCleanup(t.join);self.addCleanup(http.server_close);self.addCleanup(http.shutdown)
        self.url='http://127.0.0.1:'+str(http.server_port)+'/v1/project-folders'
        self.best=[entry('project.json',self.config),entry('icon.png',self.icon)]

    def call(self,path='',body=None,headers=None,method=None):
        h=headers or {}
        request=urllib.request.Request(self.url+path,
            data=None if body is None else json.dumps(body).encode(),
            headers={**({'Content-Type':'application/json'}if body is not None else {}),**h},method=method)
        try:response=urllib.request.urlopen(request)
        except urllib.error.HTTPError as failure:response=failure
        with response:
            raw=response.read()
            return response.code,raw,response.headers

    def groups(self,*groups):
        status,raw,_=self.call('/compare',{'groups':[{'projectId':p,'files':f} for p,f in groups]})
        return status,json.loads(raw)

    def compare(self,files,project='binari'):
        return self.groups((project,files))

    def test_empty_compare_and_changed_only_with_separate_exact_reads(self):
        self.assertEqual(json.loads(self.call()[1]),{'projects':['binari']})
        self.assertEqual(self.groups(),(200,{'groups':[],'unavailable':[]}))
        self.assertEqual(self.compare([]),(200,{'groups':[{'projectId':'binari','files':self.best}],'unavailable':[]}))
        self.assertEqual(self.compare(self.best),(200,{'groups':[],'unavailable':[]}))
        self.assertEqual(self.compare([self.best[0],{'name':'icon.png','sha256':'a'*64}]),
                         (200,{'groups':[{'projectId':'binari','files':[self.best[1]]}],'unavailable':[]}))
        for info,expected in zip(self.best,(self.config,self.icon)):
            status,raw,headers=self.call('/binari/files/'+info['name']+'?sha256='+info['sha256'])
            self.assertEqual((status,raw,headers['Cache-Control']),(200,expected,'no-store'))
        self.assertEqual((self.project/'private.json').read_bytes(),b'private')

    def test_group_result_is_independent_and_unchanged_omitted(self):
        second=self.root/'second';second.mkdir();(second/'project.json').write_bytes(b'{"second":true}')
        expected={'projectId':'second','files':[entry('project.json',b'{"second":true}')]}
        self.assertEqual(self.groups(('binari',self.best),('second',[])),
                         (200,{'groups':[expected],'unavailable':[]}))
        (self.project/'icon.png').write_bytes(b'bad PNG')
        self.assertEqual(self.groups(('absent',[]),('binari',[]),('second',[]),('unrelated',[])),
                         (200,{'groups':[expected],'unavailable':['absent','binari','unrelated']}))
        self.assertEqual(self.groups(('second',[]),('second',[]))[0],400)
        self.assertEqual(self.groups(('second',[]),('../binari',[]))[0],400)
        self.assertEqual(self.groups(*[(f'project{i}',[])for i in range(65)])[0],400)
        self.root.rename(self.root.with_name('offline'))
        self.assertEqual(self.groups(('binari',[]),('second',[])),
                         (200,{'groups':[],'unavailable':['binari','second']}))

    def test_changed_or_removed_after_compare_and_no_delete(self):
        (self.project/'project.json').write_bytes(b'{"name":"changed"}')
        (self.project/'icon.png').unlink()
        found=self.compare(self.best)[1]
        self.assertEqual(found,{'groups':[{'projectId':'binari','files':[entry('project.json',b'{"name":"changed"}')]}],'unavailable':[]})
        for info in self.best:
            status,raw,_=self.call('/binari/files/'+info['name']+'?sha256='+info['sha256'])
            self.assertEqual((status,json.loads(raw)['error']),(409,'PROJECT_FILE_CHANGED'))

    def test_no_links_special_files_invalid_format_or_arbitrary_names(self):
        for name in ('private.json','../project.json','%2e%2e','%69con.png'):
            self.assertIn(self.call('/binari/files/'+name+'?sha256='+'a'*64)[0],(400,404))
        self.assertEqual(self.compare([self.best[0],self.best[0]])[0],400)
        self.assertEqual(self.compare([{'name':'private.json','sha256':'a'*64}])[0],400)
        (self.root/'linked-project').symlink_to(self.project)
        self.assertEqual(self.compare([],'linked-project'),(200,{'groups':[],'unavailable':['linked-project']}))
        self.assertEqual(json.loads(self.call()[1]),{'projects':['binari']})
        icon=self.project/'icon.png';icon.unlink();icon.symlink_to(self.project/'private.json')
        unresolved=(200,{'groups':[],'unavailable':['binari']})
        self.assertEqual(self.compare([]),unresolved)
        icon.unlink();os.link(self.project/'private.json',icon)
        self.assertEqual(self.compare([]),unresolved)
        icon.unlink();os.mkfifo(icon)
        self.assertEqual(self.compare([]),unresolved)
        icon.unlink();icon.write_bytes(b'not a png')
        self.assertEqual(self.compare([]),unresolved)

    def test_off_loopback_origins_wrong_methods_and_config_refused(self):
        for h in ({'Host':'wrong'},{'Origin':self.url},{'Transfer-Encoding':'chunked'}):
            self.assertEqual(self.call(headers=h)[0],403)
        self.assertEqual(self.call('/binari/files/project.json')[0],400)
        self.assertEqual(self.call('/binari/files/project.json?sha256='+'a'*64+'&extra=yes')[0],400)
        self.assertEqual(self.call('/binari/compare',{'files':[]})[0],404)
        self.assertEqual(self.call('/binari/files/project.json',{'bad':'write'},method='PUT')[0],404)
        self.assertEqual(self.call('/compare',{'groups':[],'root':str(self.root)})[0],400)
        self.assertEqual(self.call('/compare',{'groups':[],'large':'x'*32768})[0],400)
        with self.assertRaisesRegex(ValueError,'LOOPBACK'):configured(self.root,'0.0.0.0')
        self.http.project_folders=None
        self.assertEqual(self.call()[0],404)
