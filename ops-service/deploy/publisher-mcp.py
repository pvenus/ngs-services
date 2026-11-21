"""Fixed owner-local operator client. The separate Sites login is never read."""
import http.client
import json
import os
from pathlib import Path
import re
import stat
import sys

ORIGIN='127.0.0.1'
PORT=8090
ROOT=Path.home()/'Workspaces/OPS/runtime/deploy-control/targets/ngs-space'
HEX=re.compile(r'[0-9a-f]{64}\Z')
REQUEST=re.compile(r'[A-Za-z0-9._-]{1,128}\Z')
METHODS={name:'ops_space_publish_'+name for name in ('peek','claim','finish')}


def key():
    for p in (ROOT.parent.parent,ROOT.parent,ROOT):
        meta=p.lstat()
        if p.resolve()!=p or meta.st_uid!=os.getuid() or not stat.S_ISDIR(meta.st_mode) or stat.S_IMODE(meta.st_mode)!=0o700:
            raise ValueError('PUBLISHER_KEY_UNAVAILABLE')
    with os.fdopen(os.open(ROOT/'publisher-key',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as src:
        meta=os.fstat(src.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=os.getuid() or meta.st_nlink!=1 or stat.S_IMODE(meta.st_mode)!=0o600 or not 64<=meta.st_size<=65:
            raise ValueError('PUBLISHER_KEY_UNAVAILABLE')
        token=src.read(66).decode('ascii').removesuffix('\n')
    if not HEX.fullmatch(token):
        raise ValueError('PUBLISHER_KEY_UNAVAILABLE')
    return token


def call(command,args):
    body={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':METHODS[command],'arguments':args}}
    c=http.client.HTTPConnection(ORIGIN,PORT,timeout=5)
    try:
        c.request('POST','/mcp',json.dumps(body,separators=(',',':')).encode(),{
            'Content-Type':'application/json','X-OPS-Space-Publisher':key(),'Accept':'application/json'})
        r=c.getresponse(); raw=r.read(8193)
        if r.status!=200 or len(raw)>8192 or r.getheader('Content-Type','').split(';')[0]!='application/json':
            raise ValueError('PUBLISHER_MCP_UNAVAILABLE')
        response=json.loads(raw)
        if response.get('jsonrpc')!='2.0' or response.get('id')!=1 or 'error' in response:
            raise ValueError('PUBLISHER_MCP_REFUSED')
        result=response['result']
        data=result['structuredContent']
        if result.get('isError') is not False or type(data) is not dict or set(data)!={'request'}:
            raise ValueError('PUBLISHER_MCP_UNVERIFIED')
        return data
    finally:
        c.close()


def main(argv):
    if len(argv) not in (1,2) or argv[0] not in METHODS:
        raise ValueError('PUBLISHER_COMMAND_INVALID')
    mode=argv[0]
    if (mode=='peek')!=(len(argv)==1):
        raise ValueError('PUBLISHER_COMMAND_INVALID')
    args={}
    if mode!='peek':
        if not REQUEST.fullmatch(argv[1]):
            raise ValueError('PUBLISHER_COMMAND_INVALID')
        args['requestId']=argv[1]
    if mode=='finish':
        raw=sys.stdin.buffer.read(2049)
        if not raw or len(raw)>2048:
            raise ValueError('PUBLISHER_REPORT_INVALID')
        args['report']=json.loads(raw)
    return call(mode,args)


if __name__=='__main__':
    try:
        print(json.dumps(main(sys.argv[1:]),separators=(',',':')))
    except (OSError,ValueError,KeyError,TypeError):
        print('OPS_SPACE_PUBLISH_MCP_UNAVAILABLE',file=sys.stderr)
        sys.exit(1)
