const origin='http://127.0.0.1:8030/v1/cinematics/ownership/workflow';
async function probe(path,body,fetcher){
 const result=await fetcher(origin+path,{redirect:'error',cache:'no-store',signal:AbortSignal.timeout(3000),
  ...(body!==undefined?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
 if(result.status!==200||!/^application\/json(?:;|$)/iu.test(result.headers.get('content-type')??'')){
  await result.body?.cancel();
  return result.status===404?null:false;
 }
 let bytes=0;const parts=[];
 for await(const p of result.body){bytes+=p.length;if(bytes>1024)return false;parts.push(p);}
 return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(parts,bytes)));
}
export async function cinematicReady(fetcher=fetch){
 try{
  const v=await probe('',undefined,fetcher);
  return v&&Object.keys(v).sort().join(',')==='enabled,schemaVersion'&&v.enabled===true&&v.schemaVersion==='workflow.cinematic-write.v1';
 }catch{return false;}
}
export async function cinematicSourceReady(sourceSha256,fetcher=fetch){
 try{
  const v=await probe('/source-status',{sourceSha256},fetcher);
  if(v===null)return null;
  return v&&Object.keys(v).sort().join(',')==='ok,sourceSha256'&&v.ok===true&&v.sourceSha256===sourceSha256;
 }catch{return false;}
}
