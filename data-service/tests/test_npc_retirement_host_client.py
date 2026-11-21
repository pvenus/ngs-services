import json, os, socketserver, tempfile, threading, unittest
from pathlib import Path
from project_data_service.npc_retirement_host_client import client
class Handler(socketserver.StreamRequestHandler):
 def handle(self):
  body=json.loads(self.rfile.readline());body['verified']=True
  if self.server.bad:body['requestId']='other'
  self.wfile.write(json.dumps({'result':body}).encode()+b'\n')
class Tests(unittest.TestCase):
 def test_real_private_socket_binding(self):
  with tempfile.TemporaryDirectory(dir='/tmp') as d:
   p=Path(d).resolve();p.chmod(0o700);path=p/'s'
   server=socketserver.UnixStreamServer(str(path),Handler);server.bad=False;path.chmod(0o600)
   thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   try:
    read=client(path);self.assertTrue(read('n','g','r')['verified'])
    server.bad=True
    with self.assertRaisesRegex(ValueError,'RESPONSE_BINDING'):read('n','g','r')
    path.chmod(0o666)
    with self.assertRaisesRegex(ValueError,'UNTRUSTED'):read('n','g','r')
   finally:server.shutdown();server.server_close();thread.join()
if __name__=='__main__':unittest.main()
