"""Fixed owner-private Unix IPC reader. No listener, credentials, or HTTP input.
The socket path is supplied only by the private PDS provider configuration.
"""
import json, os, re, socket, stat, struct, sys
from pathlib import Path
from .filex_read import require
MAX = 4 * 1024 * 1024

def peer_uid(conn):
    if hasattr(conn,'getpeereid'):
        return conn.getpeereid()[0]
    if hasattr(socket,'SO_PEERCRED'):
        return struct.unpack('3i',conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]
    if sys.platform=='darwin':
        return struct.unpack_from('I',conn.getsockopt(0,1,76),4)[0]
    raise ValueError('NPC_RETIRE_PEER_CREDENTIALS_UNSUPPORTED')

def client(path, *, replacement=False):
    p = Path(path)
    require(p.is_absolute() and p.resolve() == p and not p.is_symlink(), 'NPC_RETIRE_SOCKET_PATH')
    def read(npc,generation,request,source=None):
        parent = p.parent.stat(); target = p.stat()
        require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid() and parent.st_mode & 0o077 == 0, 'NPC_RETIRE_PRIVATE_SOCKET_PARENT')
        require(stat.S_ISSOCK(target.st_mode) and target.st_uid == os.getuid() and target.st_mode & 0o077 == 0 and not p.is_symlink(), 'NPC_RETIRE_SOCKET_UNTRUSTED')
        body={'npcId':npc,'generationId':generation,'requestId':request}
        require(all(isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9._:-]{1,200}',v) for v in body.values()),'NPC_RETIRE_RPC_ID')
        binding=dict(body)
        if replacement:
            require(isinstance(source,str) and re.fullmatch('[a-f0-9]{64}',source),'NPC_RETIRE_RPC_SOURCE')
            binding['expectedSourceVersion']=source
            body={**binding,'operation':'replacement-preflight'}
        else:
            require(source is None,'NPC_RETIRE_RPC_SOURCE')
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
            conn.settimeout(30);conn.connect(str(p))
            require(peer_uid(conn)==os.getuid(),'NPC_RETIRE_RPC_SERVER_PEER')
            conn.sendall(json.dumps(body).encode()+b'\n');buf=bytearray()
            while not buf.endswith(b'\n'):
                chunk=conn.recv(min(65536,MAX+1-len(buf)))
                require(chunk,'NPC_RETIRE_RPC_TRUNCATED');buf.extend(chunk)
                require(len(buf)<=MAX,'NPC_RETIRE_RPC_RESPONSE_SIZE')
        result=json.loads(buf)
        require('error'not in result,result.get('error','NPC_RETIRE_RPC_FAILURE'))
        proof=result['result']
        require(isinstance(proof,dict) and all(proof.get(k)==v for k,v in binding.items()),'NPC_RETIRE_RPC_RESPONSE_BINDING')
        return proof
    return read
