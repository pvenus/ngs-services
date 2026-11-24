/** Check Filex publication using the same configured PDS; no generic direct file reads. */
export async function readPropPublication(candidate){
 const {scope,manifestSha256}=candidate;
 const pin={propId:scope.propId,revision:scope.source?scope.source.revision+1:1,sha256:manifestSha256};
 const r=await fetch('http://127.0.0.1:8030/v1/props/ownership/workflow-package/read',{
  method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(pin),
  signal:AbortSignal.timeout(5000),redirect:'error',cache:'no-store',
 });
 if(r.status!==200||!/^application\/json(?:;|$)/iu.test(r.headers.get('content-type')??'')){
  await r.body?.cancel();throw Error('PROP_PACKAGE_READBACK_UNAVAILABLE');
 }
 let size=0;const chunks=[];
 for await(const chunk of r.body){
  size+=chunk.length;if(size>1200*1024)throw Error('PROP_PACKAGE_READBACK_TOO_LARGE');chunks.push(chunk);
 }
 return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks,size)));
}
