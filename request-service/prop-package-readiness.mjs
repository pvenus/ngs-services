// Same-Mac PDS service route, no caller-controlled origin, URL, redirect or header.
export async function propPackageReady(fetcher=fetch){
 try{
  const result=await fetcher('http://127.0.0.1:8030/v1/props/ownership/workflow-package',{
    redirect:'error',cache:'no-store',signal:AbortSignal.timeout(3000),
  });
  if(result.status!==200||!/^application\/json(?:;|$)/iu.test(result.headers.get('content-type')??'')){
   await result.body?.cancel();return false;
  }
  let bytes=0;const parts=[];
  for await(const part of result.body){
   bytes+=part.length;if(bytes>512)return false;parts.push(part);
  }
  const v=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(parts,bytes)));
  return v&&Object.keys(v).sort().join(',')==='enabled,modes,schemaVersion'&&v.enabled===true&&
   v.schemaVersion==='workflow.prop-package-write.v1'&&Array.isArray(v.modes)&&v.modes.length===1&&v.modes[0]==='still';
 }catch{return false;}
}
