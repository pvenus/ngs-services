/** Only the launcher can inject the existing owner-authenticated Site read. */
import {canonical,sha} from './one-stop-envelope.mjs';
import {validateVerifiedSpaceSubmit} from './request-envelope.mjs';

export const spaceQueueIdPattern='^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$';
const queueIdPattern=new RegExp(spaceQueueIdPattern);
export const allowedWorkflowSourceSql="(source_id='local' OR source_id='space')";
export const officialIdFor=({sourceId,projectId,idempotencyKey})=>
 'request.official.'+sha(canonical(['request.official.v1',sourceId,projectId,idempotencyKey]));
const exact=(value,keys)=>!!value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length===keys.length&&keys.every(k=>Object.hasOwn(value,k));

export async function verifiedSpaceRequest(args,readSpaceRequest){
 if(!exact(args,['queueId'])||typeof args.queueId!=='string'||!queueIdPattern.test(args.queueId))
  throw Error('REQUEST_SPACE_INVALID');
 // The configured adapter must use the independently owner-authenticated
 // space_requests_get tool on the approved Site, not caller-supplied values.
 const snapshot=await readSpaceRequest(args.queueId);
 if(!exact(snapshot,['queueId','requestType','idempotencyKey','payload'])||
  snapshot.queueId!==args.queueId||snapshot.idempotencyKey!==args.queueId)
  throw Error('REQUEST_SPACE_NOT_VERIFIED');
 return validateVerifiedSpaceSubmit({
  sourceId:'space',idempotencyKey:args.queueId,requestType:snapshot.requestType,payload:snapshot.payload,
 });
}
