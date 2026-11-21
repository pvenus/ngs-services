import io
import os
from pathlib import Path
import plistlib
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import run_once as R


P='a'*40
N='b'*40
TREE='c'*40
NEW='d'*40
def result(**overrides):
    row={'head_sha':N,'event':'push','head_branch':'main','path':R.WORKFLOW,'repository':{'full_name':R.REPO},
         'head_repository':{'full_name':R.REPO},'id':12,'run_attempt':1,'status':'completed','conclusion':'success'}
    return {**row,**overrides}


def tar(members=None):
    data=io.BytesIO()
    with tarfile.open(fileobj=data,mode='w:') as handle:
        for name,raw in members or [('ops-service/server.py',b'example')]:
            meta=tarfile.TarInfo(name)
            if raw is None:
                meta.type=tarfile.SYMTYPE;meta.linkname='/etc/passwd';handle.addfile(meta)
            else:
                meta.size=len(raw);handle.addfile(meta,io.BytesIO(raw))
    return data.getvalue()


class Evidence(unittest.TestCase):
    def test_verified_fixed_main_and_most_recent_attempt(self):
        self.assertEqual(R.verify_workflow({'workflow_runs':[result()]},N),{'runId':'12','runAttempt':'1'})
        variants=[
            {'head_sha':P},{'event':'pull_request'},{'head_branch':'topic'},{'path':'ops-service/fake'},
            {'head_repository':{'full_name':'fork/ngs-services'}},{'repository':{'full_name':'nope/ngs-services'}},
            {'status':'queued'},{'conclusion':'failure'},{'run_attempt':0},
        ]
        for value in variants:
            self.assertIsNone(R.verify_workflow({'workflow_runs':[result(**value)]},N),value)
        self.assertIsNone(R.verify_workflow({'workflow_runs':[result(),result(run_attempt=2,conclusion=None,status='queued')]},N))
    def test_archive_constraint(self):
        def check(members):
            raw=tar(members)
            with tempfile.TemporaryDirectory() as d:
                R.materialize({'archiveSha256':R.digest(raw)},Path(d),raw)
        check(None)
        for path in ('../bad','ops-service/../../bad','request-service/server.py'):
            with self.assertRaises(RuntimeError):check([('ops-service/server.py',b'a'),(path,b'bad')])
        with self.assertRaises(RuntimeError):check([('ops-service/server.py',b'a'),('ops-service/link.py',None)])


class Cycle(unittest.TestCase):
    def setUp(self):
        s=tempfile.TemporaryDirectory();self.addCleanup(s.cleanup)
        self.path=Path(s.name).resolve()
        self.control=self.path/'private';self.control.mkdir(mode=0o700)
        self.releases=self.path/'releases';self.releases.mkdir()
        self.plist=self.path/'ops.plist'
        self.prior=self.releases/('ngs-services-github-'+P[:7])/'ops-service'
        self.prior.mkdir(parents=True);(self.prior/'server.py').write_text('original')
        self.baseline=plistlib.dumps({'Label':R.LABEL,'ProgramArguments':[R.PYTHON,'-B','-u',str(self.prior/'server.py')],
            'WorkingDirectory':str(self.prior),'EnvironmentVariables':{'KEEP':'VERIFIED'}})
        R.durable(self.plist,self.baseline)
        self.payload={'sha':P,'tree':TREE,'directory':str(self.prior),'plistSha256':R.digest(self.baseline)}
        R.write_json(self.control/'installed.json',self.payload)
        self.directory=self.releases/('ops-only-github-'+N)/'ops-service'
        self.body=tar()
        self.now=None
        self.prepare={'sha':N,'tree':NEW,'archiveSha256':R.digest(self.body),'proof':{'runId':'1','runAttempt':'1'}}
        self.effects=[]
        self.running=self.prior/'server.py'
        self.break_health=False
        def start(path,new_ui):
            self.effects.append(('start',path))
            self.running=path
            if self.break_health and new_ui:raise RuntimeError('FIXTURE_UNHEALTHY')
            return 22
        def stop(pid):
            self.effects.append(('stop',pid));self.running=None
        def fake_launch(*args,**kwargs):
            return subprocess.CompletedProcess(args,0 if self.running is not None else 1,b'',b'')
        for key,value in {'PLIST':self.plist,'RELEASES':self.releases,
                'prepare':lambda work,installed:(self.prepare,self.body),'launch':fake_launch,
                'command':lambda *args,**kwargs:subprocess.CompletedProcess(args,0,os.fsencode(N+'\trefs/heads/main\n'),b''),
                'start':start,'stop':stop,'port_listening':lambda:b'p22' if self.running else b'',
                'live':lambda p:22 if p==self.running else None,
                'health':lambda p,**_:22 if p==self.running else None}.items():
            hand=patch.object(R,key,value);hand.start();self.addCleanup(hand.stop)
    def test_one_label_new_verified_and_change_preserves_environment(self):
        seen=R.cycle(self.control)
        self.assertEqual((seen['state'],seen['sha']),('deployed',N))
        self.assertEqual(self.effects,[('stop',22),('start',self.directory/'server.py')])
        self.assertEqual(plistlib.loads(self.plist.read_bytes())['EnvironmentVariables'],{'KEEP':'VERIFIED'})
        self.assertEqual(R.read_json(self.control/'installed.json')['sha'],N)
        self.assertEqual(R.read_json(self.control/'transaction.json')['phase'],'deployed')
    def test_unchanged_tree_never_starts_other_or_current(self):
        self.prepare['tree']=TREE
        seen=R.cycle(self.control)
        self.assertEqual(seen['state'],'unchanged');self.assertEqual(self.effects,[])
        self.assertEqual(self.plist.read_bytes(),self.baseline)
    def test_health_failure_rolls_back_exact_and_does_not_retry_same_sha(self):
        self.break_health=True
        seen=R.cycle(self.control)
        self.assertEqual((seen['state'],seen['sha']),('rolled_back',P))
        self.assertEqual(self.effects,[('stop',22),('start',self.directory/'server.py'),
            ('stop',22),('start',self.prior/'server.py')])
        self.assertEqual(self.plist.read_bytes(),self.baseline)
        self.assertEqual(R.cycle(self.control)['state'],'previous_attempt_rolled_back')
        self.assertEqual(len(self.effects),4)
    def test_external_plist_change_blocks_use(self):
        self.plist.write_bytes(b'drift')
        self.assertEqual(R.cycle(self.control)['state'],'failed')
        self.assertEqual(self.effects,[])
    def test_overlapping_cycle_never_restarts(self):
        import fcntl
        with (self.control/'cycle.lock').open('wb') as h:
            os.fchmod(h.fileno(),0o600);fcntl.flock(h,fcntl.LOCK_EX)
            self.assertEqual(R.cycle(self.control)['state'],'busy')
        self.assertEqual(self.effects,[])
