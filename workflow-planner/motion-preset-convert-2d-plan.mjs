/** Registered DAG for the formal non-NPC 2D motion-preset route. */
import {canonical,sha} from './envelope.mjs';
import {motion2DRequestType} from './motion-preset-convert-2d-envelope.mjs';

export const motion2DDirections=Object.freeze(['n','ne','e','se','s','sw','w','nw']);
export const motion2DImageKey='motion_2d_image';
export const motion2DFinalizeKey='motion_2d_finalize';
export const motion2DKey=direction=>'motion_2d_'+direction;
export const motion2DId=(officialRequestId,key)=>'workflow.task.'+sha(canonical([officialRequestId,key]));

export function buildMotion2DTasks({officialRequestId,payload:{sourcePresetId,variantPresetId}}){
 const id=key=>motion2DId(officialRequestId,key);
 const input={sourcePresetId,variantPresetId},imageId=id(motion2DImageKey);
 const image={id:imageId,key:motion2DImageKey,type:motion2DRequestType+'.image',workerType:'image',dependsOn:null,input:{...input}};
 const clips=motion2DDirections.map(direction=>{
  const key=motion2DKey(direction);
  return {id:id(key),key,type:motion2DRequestType+'.clip',workerType:'animation',dependsOn:imageId,input:{...input,direction}};
 });
 // The scalar column is deliberately null: the worker claim and the canonical
 // publish fence must check every ID in this persisted same-run dependency list.
 const final={id:id(motion2DFinalizeKey),key:motion2DFinalizeKey,type:motion2DRequestType+'.finalize',
  workerType:'animation',dependsOn:null,input:{...input,dependsOnTaskIds:clips.map(clip=>clip.id)}};
 return [image,...clips,final];
}

/** Completion projection requires the exact graph and each typed stage receipt. */
export function hasCompleteMotion2DGraph(run,rows){
 let tasks;
 try{tasks=buildMotion2DTasks(run);}catch{return false;}
 if(rows.length!==tasks.length)return false;
 const byId=new Map(rows.map(row=>[row.task_id,row]));
 return tasks.every(task=>{
  const row=byId.get(task.id);
  if(!row||row.status!=='completed'||row.task_key!==task.key||row.task_type!==task.type||
     row.worker_type!==task.workerType||row.depends_on_task_id!==task.dependsOn||row.task_input!==canonical(task.input))return false;
  const prefix = task.key===motion2DImageKey?'motion.2d.image.':task.key===motion2DFinalizeKey?
   'motion.2d.published.':'motion.2d.clip.'+task.input.direction+'.';
  const ref=row.result_ref;
  return typeof ref==='string'&&ref.length===prefix.length+64&&ref.startsWith(prefix)&&/^[0-9a-f]{64}$/.test(ref.slice(prefix.length));
 });
}
