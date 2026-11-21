"""Stateless loopback Streamable HTTP MCP facade over the canonical file readers."""
import base64
import json
from .project_folders import FolderError
from . import recursive_folders as files

VERSION='2025-06-18'
_FILE='npc_folder_file_chunk'
_COMPARE='npc_folders_compare'
_LIST='npc_project_folders_list'


def object_(**fields):
    return {'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}


def array(items,max_=None):
    return {'type':'array','items':items,**({'maxItems':max_}if max_ is not None else {})}


def integer(max_=None):
    return {'type':'integer','minimum':0,**({'maximum':max_}if max_ is not None else {})}


_TEXT={'type':'string'}
_PATH={'type':'string','maxLength':files.MAX_PATH_BYTES}
_HASH={'type':'string','pattern':'^[a-f0-9]{64}$'}
_ANNOTATIONS={'readOnlyHint':True,'destructiveHint':False,'idempotentHint':True,'openWorldHint':False}
_LIST_ARGS=object_()
_COMPARE_ARGS=object_(scope=_PATH,files=array(object_(path=_PATH,sha256=_HASH),files.MAX_ENTRIES))
_COMPARE_ARGS['properties']['includeDeleted']={'type':'boolean','const':True}
_COMPARE_OUT=object_(scope=_PATH,groups=array(object_(folder=_PATH,files=array(object_(path=_PATH,sha256=_HASH,size=integer(files.MAX_FILE))))))
_COMPARE_OUT['properties']['deleted']=array(object_(path=_PATH,sha256=_HASH),files.MAX_ENTRIES)
_FILE_ARGS=object_(scope=_PATH,path=_PATH,sha256=_HASH,offset=integer(files.MAX_FILE))
TOOLS=[
    {'name':_LIST,'description':'List active canonical project folders. Read only.',
     'inputSchema':_LIST_ARGS,'outputSchema':object_(projects=array(_TEXT)),'annotations':_ANNOTATIONS},
    {'name':_COMPARE,'description':'Compare public canonical files recursively under one scope. Returns changed or missing files; opt in to also report supplied paths absent from the source. This tool does not delete.',
     'inputSchema':_COMPARE_ARGS,
     'outputSchema':_COMPARE_OUT,
     'annotations':_ANNOTATIONS},
    {'name':_FILE,'description':'Read one hash-pinned canonical file chunk. Whole-file SHA is verified before returning up to 1 MiB. Read only.',
     'inputSchema':_FILE_ARGS,
     'outputSchema':object_(scope=_PATH,path=_PATH,sha256=_HASH,size=integer(files.MAX_FILE),
                          offset=integer(files.MAX_FILE),nextOffset={'type':['integer','null']},base64=_TEXT),
     'annotations':_ANNOTATIONS},
]


def _result(handler,id,value):
    return handler.respond(200,{'jsonrpc':'2.0','id':id,'result':value})


def _error(handler,id,code,message,status=200):
    return handler.respond(status,{'jsonrpc':'2.0','id':id,'error':{'code':code,'message':message}})


def _text(value):
    return [{'type':'text','text':json.dumps(value,ensure_ascii=False,separators=(',',':'))}]


def _call(handler,id,folders,name,args):
    mapping={_LIST:_LIST_ARGS,_COMPARE:_COMPARE_ARGS,_FILE:_FILE_ARGS}
    if not isinstance(name,str) or name not in mapping:return _error(handler,id,-32602,'Unknown tool')
    required=set(mapping[name]['required'])
    if (type(args) is not dict or (set(args)!=required and
        not (name==_COMPARE and set(args)==required|{'includeDeleted'} and args['includeDeleted'] is True))):
        return _error(handler,id,-32602,'Invalid tool arguments')
    try:
        if name==_LIST:value={'projects':folders.projects()}
        elif name==_COMPARE:value=files.compare(folders,args)
        else:
            with files._capacity(files._FETCH):
                raw,size=files.fetch_chunk(folders,args['scope'],args['path'],args['sha256'],args['offset'])
                after=args['offset']+len(raw)
                value={**args,'size':size,'nextOffset':after if after<size else None,
                       'base64':base64.b64encode(raw).decode('ascii')}
                return _result(handler,id,{'content':_text(value),'structuredContent':value,'isError':False})
    except FolderError as exc:
        return _result(handler,id,{'content':_text({'error':str(exc),'status':exc.status}),'isError':True})
    except OSError:
        return _result(handler,id,{'content':_text({'error':'FOLDER_SOURCE_UNAVAILABLE','status':503}),'isError':True})
    return _result(handler,id,{'content':_text(value),'structuredContent':value,'isError':False})


def serve(handler,parsed,folders):
    if (handler.server.server_address[0]!='127.0.0.1' or handler.client_address[0]!='127.0.0.1'
            or handler.headers.get('Host')!='127.0.0.1:'+str(handler.server.server_port)
            or handler.headers.get('Origin') is not None or handler.headers.get('Transfer-Encoding') is not None):
        return handler.respond(403,{'error':'FOLDER_MCP_LOOPBACK_REQUIRED'})
    if handler.command!='POST' or parsed.query:
        return handler.respond(405,{'error':'FOLDER_MCP_POST_ONLY'},{'Allow':'POST'})
    if handler.headers.get('Mcp-Session-Id') is not None:
        return handler.respond(400,{'error':'FOLDER_MCP_STATELESS'})
    if handler.headers.get('MCP-Protocol-Version') not in (None,VERSION):
        return handler.respond(400,{'error':'FOLDER_MCP_VERSION'})
    accepts={v.partition(';')[0].strip().lower()for v in handler.headers.get('Accept','').split(',')}
    if not {'application/json','text/event-stream'}<=accepts:
        return handler.respond(406,{'error':'FOLDER_MCP_ACCEPT_REQUIRED'})
    handler.connection.settimeout(15)
    try:request=handler.read_json(max_body=files.MAX_BODY+4096)
    except (ValueError,RecursionError,OSError):return _error(handler,None,-32700,'Invalid or oversized JSON',400)
    id=request.get('id')
    if (request.get('jsonrpc')!='2.0' or not isinstance(request.get('method'),str)
            or 'id'in request and type(id) not in (str,int) or set(request)-{'jsonrpc','method','params','id'}):
        return _error(handler,None,-32600,'Invalid request',400)
    args=request.get('params',{})
    if not isinstance(args,dict):return _error(handler,id,-32602,'Invalid parameters')
    name=request['method']
    if 'id' not in request:
        # Notifications are accepted but never execute a read or other call.
        return handler.respond(202,None)
    if name=='initialize':
        if (not {'protocolVersion','capabilities','clientInfo'}<=set(args)
                or not isinstance(args['protocolVersion'],str) or not isinstance(args['capabilities'],dict)
                or not isinstance(args['clientInfo'],dict) or not all(isinstance(args['clientInfo'].get(k),str) for k in ('name','version'))):
            return _error(handler,id,-32602,'Invalid initialize parameters')
        return _result(handler,id,{'protocolVersion':VERSION,'capabilities':{'tools':{}},
                                  'serverInfo':{'name':'pds-project-folders','version':'1.0.0'}})
    if name=='ping' and not args:return _result(handler,id,{})
    if name=='tools/list' and not args:return _result(handler,id,{'tools':TOOLS})
    if name=='tools/call':
        if set(args)-{'name','arguments','_meta'} or 'name' not in args:return _error(handler,id,-32602,'Invalid tool call')
        return _call(handler,id,folders,args['name'],args.get('arguments',{}))
    return _error(handler,id,-32601,'Method not found')
