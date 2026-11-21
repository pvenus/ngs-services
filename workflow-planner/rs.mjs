import {request} from 'node:http';
import {exact} from './planner.mjs';

export const endpoint='http://127.0.0.1:8770/mcp';
const protocol='2025-06-18';
const tools=new Set(['request_workflow_list','request_update_status']);
const maxReply=2*1024*1024;

// The optional fixture port is for library tests only. The CLI never reads a
// host, URL, credential, route or port from flags or the environment.
export function makeLoopbackTransport(port=8770){
 if(!Number.isInteger(port)||port<1||port>65535)throw Error('WORKFLOW_RS_PORT_INVALID');
 return wire=>new Promise((resolve,reject)=>{
  const bytes=Buffer.from(JSON.stringify(wire));
  const fail=()=>reject(Error('WORKFLOW_RS_TRANSPORT'));
  const req=request({hostname:'127.0.0.1',port,path:'/mcp',method:'POST',headers:{
   'Content-Type':'application/json','Content-Length':bytes.length,
   'MCP-Protocol-Version':protocol,'Accept':'application/json, text/event-stream',
  }},res=>{
   if(res.statusCode!==200||!/^application\/json(?:\s*;|$)/i.test(String(res.headers['content-type']??''))){res.destroy();fail();return;}
   const chunks=[];let size=0;
   res.on('data',b=>{size+=b.length;if(size>maxReply){req.destroy();fail();return;}chunks.push(b);});
   res.on('error',fail);res.on('aborted',fail);
   res.on('end',()=>{try{resolve(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks))));}catch{fail();}});
  });
  const timer=setTimeout(()=>{req.destroy();fail();},5000);
  req.on('close',()=>clearTimeout(timer));
  req.on('error',fail);req.end(bytes);
 });
}

export function createRSClient({transport=makeLoopbackTransport()}={}){
 let sequence=0;
 return {async call(name,args){
  if(!tools.has(name))throw Error('WORKFLOW_RS_TOOL_DENIED');
  const id=++sequence;
  const r=await transport({jsonrpc:'2.0',id,method:'tools/call',params:{name,arguments:args}});
  if(!exact(r,['jsonrpc','id','result'])||r.jsonrpc!=='2.0'||r.id!==id||typeof r.result?.isError!=='boolean')
   throw Error('WORKFLOW_RS_PROTOCOL');
  const value=r.result.structuredContent;
  if(!value||typeof value!=='object'||Array.isArray(value))throw Error('WORKFLOW_RS_PROTOCOL');
  if(r.result.isError){
   const e=Error('WORKFLOW_RS_TOOL_ERROR');
   if(Number.isInteger(value.status))e.rsStatus=value.status;
   throw e;
  }
  return value;
 }};
}
