import json
import os
from pathlib import Path
import plistlib
import sqlite3
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import prepare_ops_candidates_once as p

A, B, C = 'a'*40, 'b'*40, 'c'*40


class CoordinatorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.control = self.home/'control'
        self.control.mkdir(mode=0o700)
        self.calls = []
        self.heads = {p.SERVICES:A,p.WORKERS:B}

    def deps(self):
        def main(repo):
            self.calls.append(('main',repo))
            return self.heads[repo]

        def acquire(repo):
            self.calls.append(('evidence',repo))
            return dict(head=self.heads[repo],workflow={},run={},artifact={},raw=b'sealed ZIP',
                        verified={'head':self.heads[repo]})

        def service(verified, git, home, control, *, assert_current, filex_ready, host):
            self.calls.append(('service',git.name))
            self.assertEqual(verified,{'head':A})
            self.assertFalse(filex_ready(A))
            self.assertFalse(hasattr(host,'start'))
            self.assertIsNone(assert_current(A))
            return {key:dict(target=key,sha=A,state='registered') for key in p.SERVICE_TARGETS}

        def workers(evidence, *, repo, home, control, read_main, idle, trusted_deploy):
            self.calls.append(('worker',repo.name))
            self.assertEqual(set(evidence),{'head','workflow','run','artifact','raw'})
            self.assertEqual(evidence['raw'],b'sealed ZIP')
            self.assertEqual(read_main(),B)
            self.assertEqual(trusted_deploy,control/'bin')
            self.assertFalse(idle('unknown','f'*64))
            return {'sha':B,'targets':{key:{'state':'already_ready'} for key in p.WORKER_TARGETS}}

        def relay(control,home,head,*,assert_current,host):
            self.calls.append(('relay',head))
            self.assertIsNone(assert_current(head))
            return {'target':p.RELAY,'sha':C,'state':'pending','code':'SERVICE_LOCAL_ARTIFACT_UNAVAILABLE'}

        return SimpleNamespace(canonical=main,acquire=acquire,prepare_services=service,prepare_workers=workers,
                               local_main=lambda:C,prepare_relay=relay)

    def fetch(self, repo, where, head):
        self.calls.append(('fetch',repo))
        self.assertEqual(where.parent.parent,self.control)
        self.assertEqual(head,self.heads[repo])
        self.assertEqual(os.environ['GIT_CONFIG_GLOBAL'],'/dev/null')
        where.mkdir(mode=0o700)
        return where

    def test_independent_sources_ready_and_manual_only_host(self):
        states = p.run_once(self.deps(),self.home,self.control,fetch=self.fetch)
        self.assertEqual(set(states),set(p.TARGETS))
        for key in p.SERVICE_TARGETS:
            self.assertEqual(states[key],{'state':'ready','sha':A})
        for key in p.WORKER_TARGETS:
            self.assertEqual(states[key],{'state':'ready','sha':B})
        self.assertEqual(states[p.RELAY],{'state':'pending','sha':C,'code':'SERVICE_LOCAL_ARTIFACT_UNAVAILABLE'})
        self.assertEqual(states[p.FILEX],{'state':'not_configured','sha':A,'code':'FILEX_PREPARER_NOT_PROVISIONED'})
        self.assertEqual(list(self.control.iterdir()),[])

    def test_auth_failure_isolated_and_unsanitized_fields_never_escape(self):
        deps=self.deps()
        normal=deps.acquire
        def selective(repo):
            if repo==p.SERVICES:
                raise RuntimeError('GITHUB_ACTIONS_READ_DENIED')
            return normal(repo)
        deps.acquire=selective
        deps.prepare_relay=lambda *a,**k: (_ for _ in ()).throw(ValueError('private path /token/password'))
        states=p.run_once(deps,self.home,self.control,fetch=self.fetch)
        for key in p.SERVICE_TARGETS:
            self.assertEqual(states[key]['state'],'auth_required')
        self.assertEqual(states['worker-audio']['state'],'ready')
        self.assertEqual(states[p.RELAY],{'state':'failed','sha':C,'code':'PREP_FAILED'})
        output=p.install_status(self.control,states,'2026-11-22T00:00:00Z')
        self.assertEqual(set(output),{'schemaVersion','checkedAt','targets'})
        self.assertEqual(stat.S_IMODE((self.control/p.STATUS).stat().st_mode),0o600)
        self.assertNotIn('password',(self.control/p.STATUS).read_text())
        self.assertNotIn(('fetch',p.SERVICES),self.calls)

    def test_moved_main_never_dispatches_obsolete_candidate(self):
        deps=self.deps()
        acquire=deps.acquire
        def moving(repo):
            response=acquire(repo)
            if repo==p.SERVICES:
                response['head']=B
            return response
        deps.acquire=moving
        states=p.run_once(deps,self.home,self.control,fetch=self.fetch)
        self.assertNotIn(('fetch',p.SERVICES),self.calls)
        self.assertNotIn(('service','ngs-services'),self.calls)
        self.assertEqual(states['request-service'],{'sha':A,'state':'pending','code':'MAIN_ADVANCED'})
        self.assertEqual(states['worker-file']['state'],'ready')

    def test_same_owner_job_lock_prevents_overlapping_work(self):
        with p.private_job_lock(self.control) as first:
            self.assertTrue(first)
            with p.private_job_lock(self.control) as second:
                self.assertFalse(second)
        with p.private_job_lock(self.control) as third:
            self.assertTrue(third)
        self.assertEqual(stat.S_IMODE((self.control/p.LOCK).stat().st_mode),0o600)

    def test_status_link_refused_and_actual_file_untouched(self):
        other=self.control/'existing'
        other.write_text('keep')
        (self.control/p.STATUS).symlink_to(other)
        with self.assertRaises(OSError):
            p.install_status(self.control,{},'2026-11-22T00:00:00Z')
        self.assertEqual(other.read_text(),'keep')

    def test_bare_fetch_prohibits_inherited_config_and_new_main(self):
        calls=[]
        def fake(args, *, cwd, env, **kwargs):
            calls.append((args,cwd,env))
            return SimpleNamespace(returncode=0,stdout=(A+'\n').encode())
        where=self.control/'git'
        with patch.dict(os.environ, {'GIT_DIR':'private','GIT_CONFIG_KEY_0':'alias.danger'}):
            self.assertEqual(p.fetch_git(p.SERVICES,where,A,run=fake),where)
        self.assertIn('--bare',calls[0][0])
        self.assertIn('--template=',calls[0][0])
        self.assertIn('https://github.com/pvenus/ngs-services.git',calls[1][0])
        self.assertTrue(all('GIT_DIR' not in env and env['GIT_TERMINAL_PROMPT']=='0'
                            for _,_,env in calls))
        self.assertTrue(all('checkout' not in args for args,_,_ in calls))
        with self.assertRaisesRegex(p.Refusal,'GIT_OBJECTS_UNVERIFIED'):
            p.fetch_git(p.SERVICES,where,B,run=fake)
        with self.assertRaises(p.Refusal):
            p.fetch_git('other/ngs-services',where,A,run=fake)

    def test_worker_native_exact_pid_selector_and_sqlite_readonly_authority(self):
        native=self.home/'Library/LaunchAgents'
        native.mkdir(parents=True)
        label='com.vinus.workflow.audio-worker'
        target='worker-audio'
        dbpath=self.home/'Workspaces/Workers/data/workflow-planner/workflow.sqlite'
        dbpath.parent.mkdir(parents=True)
        with sqlite3.connect(dbpath) as db:
            db.execute('CREATE TABLE workflow_tasks(worker_type TEXT,status TEXT)')
            db.execute("INSERT INTO workflow_tasks VALUES ('file','running')")
        dbpath.chmod(0o600)
        args=['/bin/tool','/owned/audio.mjs','--db',str(dbpath)]
        wd='/owned'
        selector=native/(label+'.plist')
        selector.write_bytes(plistlib.dumps(dict(Label=label,ProgramArguments=args,WorkingDirectory=wd)))
        selector.chmod(0o600)
        pin=p.sha(selector.read_bytes())
        deps=SimpleNamespace(target_specs={target:SimpleNamespace(label=label)},
                             loaded_selector=lambda report,argv,cwd: report=='exact' and args==argv and wd==cwd)
        self.assertTrue(p.worker_idle(deps,self.home,SimpleNamespace(launch_report=lambda label:(42,'exact')),target,pin))
        count=iter((42,43))
        self.assertFalse(p.worker_idle(deps,self.home,SimpleNamespace(launch_report=lambda label:(next(count),'exact')),target,pin))
        self.assertFalse(p.worker_idle(deps,self.home,SimpleNamespace(launch_report=lambda label:(42,'other')),target,pin))
        self.assertFalse(p.worker_idle(deps,self.home,SimpleNamespace(launch_report=lambda label:(42,'exact')),target,'f'*64))
        with sqlite3.connect(dbpath) as db:
            db.execute("INSERT INTO workflow_tasks VALUES ('audio','running')")
        self.assertFalse(p.worker_idle(deps,self.home,SimpleNamespace(launch_report=lambda label:(42,'exact')),target,pin))

    def test_private_helper_pin_refuses_drift_and_template_never_applies(self):
        bin=self.control/'bin'
        bin.mkdir(mode=0o700)
        pins={}
        for name in p.HELPERS:
            f=bin/(name+'.py')
            f.write_bytes(b'VALUE = True\n')
            f.chmod(0o600)
            pins[f.name]=p.sha(f.read_bytes())
        marker=bin/p.PIN_NAME
        marker.write_text(json.dumps({'schemaVersion':p.PIN_SCHEMA,'files':pins}))
        marker.chmod(0o600)
        loader=p.PinnedHelpers(bin)
        mod=SimpleNamespace(__name__=p.HELPERS[0])
        loader.exec_module(mod)
        self.assertTrue(mod.VALUE)
        f.write_bytes(b'VALUE=False\n')
        with self.assertRaises(p.Refusal):
            p.PinnedHelpers(bin)
        template=plistlib.loads(Path(__file__).with_name('ops-candidate-prepare.launchagent.template.plist').read_bytes())
        self.assertEqual(template['StartInterval'],300)
        self.assertEqual(template['ProgramArguments'][1:3],['-I','-B'])
        self.assertFalse(template['RunAtLoad'])
        self.assertFalse(template['KeepAlive'])
        self.assertNotIn('EnvironmentVariables',template)


if __name__=='__main__':
    unittest.main()
