import {propDeleteRoot,npcDeleteRoot} from './file-delete-envelope.mjs';
export async function fileDeleteReady(root=propDeleteRoot,fetcher=fetch){
 try{
  if(![propDeleteRoot,npcDeleteRoot].includes(root))return false;
  const result=await fetcher('http://127.0.0.1:8030/v1/storage/delete-target'+(root===npcDeleteRoot?'/npc-standalone':''),
   {redirect:'error',cache:'no-store',signal:AbortSignal.timeout(3000)});
  if(result.status!==200||!/^application\/json(?:;|$)/iu.test(result.headers.get('content-type')??'')){
   await result.body?.cancel();return false;
  }
  const parts=[];let size=0;
  for await(const part of result.body){size+=part.length;if(size>2048)return false;parts.push(part);}
  const data=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(parts,size)));
  return data?.enabled===true&&data.schemaVersion==='workflow.single-target-delete.v1'&&
   Array.isArray(data.roots)&&data.roots.includes(root);
 }catch{return false;}
}
