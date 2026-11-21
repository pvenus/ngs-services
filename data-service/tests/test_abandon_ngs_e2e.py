"""Opt-in actual NGS + PDS + Filex temporary-store integration; no live data."""
import os,sys,importlib.util,json,threading,unittest,urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from test_npc_owned_authority import Tests as PDSFixture,make_transport
from project_data_service.http_server import Handler
class NGSAbandonE2E(unittest.TestCase):
 setUp=PDSFixture.setUp
 tearDown=PDSFixture.tearDown
 @unittest.skipUnless(os.environ.get('NGS_ABANDON_SOURCE'),'explicit NGS candidate required')
 def test_actual_ngs_abandon_lost_response_and_new_generation(self):
  ngs=Path(os.environ['NGS_ABANDON_SOURCE']);sys.path.insert(0,str(ngs))
  spec=importlib.util.spec_from_file_location('ngs_abandon_fixture',ngs/'tests/test_one_stop_abandon.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  f=module.Tests();f.setUp();self.addCleanup(f.doCleanups)
  source=self.source.current()['sourceVersion']
  with make_transport(self.config)as t:g=t.prepare_create('npc.test',f.child,source)
  f.guard=g;f.pin.update(guard=g,generationId=g['generationId'],sourceVersion=source);f.pinpath.write_text(module.canonical(f.pin));pin=f.pinpath.read_bytes()
  http=ThreadingHTTPServer(('127.0.0.1',0),Handler);http.app=SimpleNamespace(domains=SimpleNamespace(npcs=self.source));thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
  def call(method,path,body):
   return json.load(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:'+str(http.server_port)+path,data=json.dumps(body).encode(),method=method,headers={'Content-Type':'application/json'})))
  def lost(*args):call(*args);raise TimeoutError('isolated lost response')
  try:
   with self.assertRaises(TimeoutError):f.run_abandon(lost)
   self.assertEqual(f.c.row(f.rid)['state'],'blocked');self.assertEqual(f.c.row(f.child)['state'],'discarded')
   self.assertEqual(self.writer.generation('npc.test')['state'],'creation-abandoned')
   f.run_abandon(call);self.assertEqual(f.c.row(f.rid)['state'],'discarded');self.assertEqual(f.pinpath.read_bytes(),pin)
   fresh=f.s.accept('owner','new-independent',f.body);self.assertNotEqual(fresh['requestId'],f.rid)
   # Run the real formal skeleton intake, whose fixture supplies the persisted binding.
   original=f.batch.root/'ownership-lifecycle';original.mkdir(exist_ok=True)
   # Fixture intake uses mkdir without exist_ok; retain old pin but permit its exact directory.
   import unittest.mock
   mkdir=Path.mkdir
   def compatible_mkdir(path,*a,**kw):
    if path==original:kw['exist_ok']=True
    return mkdir(path,*a,**kw)
   with unittest.mock.patch.object(Path,'mkdir',compatible_mkdir):f.s.tick(fresh['requestId'],'owner')
   newchild=f.child
   with make_transport(self.config)as t:g2=t.prepare_create('npc.test',newchild,source)
   self.assertNotEqual(g2['generationId'],g['generationId']);self.assertNotEqual(newchild,g['requestId'])
   from server import Conflict
   with self.assertRaises(Conflict):self.writer.checked_lifecycle(g,'create',g['requestId'],source)
   self.assertEqual(self.source.current()['document'],self.doc)
  finally:http.shutdown();http.server_close();thread.join()
del PDSFixture
