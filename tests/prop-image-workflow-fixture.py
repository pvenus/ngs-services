"""Disposable harness: actual category writer, PDS Handler and scoped Filex media HTTP."""
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import runpy
import sys
import threading
from project_data_service.http_server import Handler
from project_data_service.media_artifacts import MediaArtifacts,Transport
from project_data_service.workflow_prop_image import configured

fixture=Path(__file__).with_name('prop-design-local-fixture.py')
with redirect_stdout(io.StringIO()):
    scope=runpy.run_path(str(fixture))
root=Path(sys.argv[1])
sys.path.insert(0,str(fixture.parents[1]/'filex-writer'))
from media_store import MediaStore
from server import handler
media=root/'filex-media';media.mkdir(mode=0o700)
token=os.urandom(32).hex()
secret=root/'synthetic.token'
with os.fdopen(os.open(secret,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:f.write(token)
writer=ThreadingHTTPServer(('127.0.0.1',0),handler(MediaStore(media),token))
transport=Transport('http://127.0.0.1:'+str(writer.server_port),str(secret))
threading.Thread(target=writer.serve_forever,daemon=True).start()
def no_legacy(*_):raise AssertionError('Planner claims must not hit the unrelated legacy authority')
pds=ThreadingHTTPServer(('127.0.0.1',0),Handler)
media_artifacts=MediaArtifacts(no_legacy,transport,token)
concept=scope['context'].source
verifier=scope['workflow'].verifier
assert configured('0.0.0.0',concept,verifier,media_artifacts) is None
assert configured('127.0.0.1',concept,verifier,None) is None
assert configured('127.0.0.1',concept,None,media_artifacts) is None
pds.workflow_prop_image=configured('127.0.0.1',concept,verifier,media_artifacts)
assert pds.workflow_prop_image is not None
print(json.dumps({'port':pds.server_port,'context':scope['context'].read('binari')}),flush=True)
pds.serve_forever()
