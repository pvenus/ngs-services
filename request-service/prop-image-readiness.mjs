// Existing same-Mac PDS capability; no caller-supplied endpoint, token or redirect.
export async function propImageReady(){
 try{
  const response=await fetch('http://127.0.0.1:8030/v1/props/ownership/workflow-image',{
   redirect:'error',signal:AbortSignal.timeout(2000),cache:'no-store',
  });
  if(response.status!==200||!/^application\/json(?:;|$)/iu.test(response.headers.get('content-type')??'')){
   await response.body?.cancel();return false;
  }
  let raw='';
  for await(const bytes of response.body){raw+=Buffer.from(bytes).toString('ascii');if(raw.length>512)return false;}
  const value=JSON.parse(raw);
  return value?.enabled===true&&value.schemaVersion==='workflow.prop-image-write.v1'&&Object.keys(value).length===2;
 }catch{return false;}
}
