import {closeSync,constants,mkdirSync,openSync,fstatSync,realpathSync,lstatSync} from 'node:fs';
import {basename,dirname,isAbsolute,resolve} from 'node:path';
import {setTimeout as sleep} from 'node:timers/promises';
import {PlannerStore,pollOnce} from './planner.mjs';
import {readPropPublication} from './prop-package-readback.mjs';
import {createRSClient} from './rs.mjs';
import {startPlannerHeartbeat} from './service-heartbeat.mjs';

const args=process.argv.slice(2);
if((args.length!==2&&args.length!==3)||args[0]!=='--db'||!isAbsolute(args[1])||(args.length===3&&args[2]!=='--once')){
 console.error('Usage: node cli.mjs --db <absolute dedicated planner SQLite path> [--once]');
 process.exit(1);
}
const file=resolve(args[1]),parent=dirname(file);
let store;
try{
 if(basename(file)==='official-requests.sqlite')throw Error();
 if(realpathSync(dirname(parent))!==dirname(parent))throw Error();
 try{mkdirSync(parent,{mode:0o700});}catch(e){if(e.code!=='EEXIST')throw e;}
 const dir=lstatSync(parent);
 if(realpathSync(parent)!==parent||!dir.isDirectory()||dir.uid!==process.getuid()||(dir.mode&0o077)!==0)throw Error();
 const fd=openSync(file,constants.O_CREAT|constants.O_RDWR|constants.O_NOFOLLOW|constants.O_NONBLOCK,0o600);
 try{const s=fstatSync(fd);if(!s.isFile()||s.uid!==process.getuid()||s.nlink!==1||(s.mode&0o777)!==0o600)throw Error();}
 finally{closeSync(fd);}
 // Request Service admits prop work only when the canonical PDS capability is ready.
 store=new PlannerStore(file,{propImageEnabled:true,propGenerateEnabled:true,propMutationsEnabled:true,readPropPublication});
}catch{
 console.error('WORKFLOW_PLANNER_DATABASE_INVALID');process.exit(1);
}

const rs=createRSClient();
let stop=false,waiting;
function halt(){stop=true;waiting?.abort();}
process.on('SIGINT',halt);process.on('SIGTERM',halt);
const stopHeartbeat=args.length===2?startPlannerHeartbeat({revision:process.env.NGS_WORKFLOW_PLANNER_REVISION}):()=>{};
try{
 do{
  const result=await pollOnce(store,rs);
  if(args.length===3||result.staged||result.invalid||result.unsupported||result.acknowledged||result.completed||result.failed||result.conflicts||result.pollError)
   console.log(JSON.stringify({service:'workflow-planner',...result}));
  if(args.length===3){if(result.pollError||result.pending)process.exitCode=1;break;}
  waiting=new AbortController();
  if(!stop)await sleep(3000,undefined,{signal:waiting.signal}).catch(e=>{if(e.name!=='AbortError')throw e;});
 }while(!stop);
}catch{console.error('WORKFLOW_PLANNER_RUN_FAILED');process.exitCode=1;}
finally{stopHeartbeat();store.close();}
