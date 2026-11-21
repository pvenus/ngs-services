"""Explicit post-migration admin activation. Never used by worker startup/intake."""
import argparse,json
from migrate_npc_owned import Client

def main():
 p=argparse.ArgumentParser();p.add_argument('--pds',default='http://127.0.0.1:8030');p.add_argument('--migration-request-id',required=True);p.add_argument('--source-version',required=True);p.add_argument('--plan-hash',required=True);p.add_argument('--backup-manifest-hash',required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
 payload={'requestId':a.migration_request_id,'expectedSourceVersion':a.source_version,'planHash':a.plan_hash,'backupManifestHash':a.backup_manifest_hash}
 if not a.apply:print(json.dumps({'applied':False,'activation':payload}));return
 result=Client(a.pds).call('activate',payload)
 if result.get('adminActivated') is not True or result.get('state')!='active':raise ValueError('ADMIN_ACTIVATION_UNVERIFIED')
 print(json.dumps({'applied':True,'activation':result}))
if __name__=='__main__':main()
