import json
import os
import sqlite3
import tempfile
import unittest
from functools import partial
from pathlib import Path

from request_progress import MAX_ALARMS, read_alarms
from test_server import get, started
import server as S

A='request.official.'+'a'*64
B='request.official.'+'b'*64
ROUTE='/api/alarms'


class AlarmTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name).resolve()/'workflow.sqlite'
        fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        os.close(fd)
        with sqlite3.connect(self.path) as db:
            db.executescript('''
                CREATE TABLE workflow_runs (official_request_id TEXT PRIMARY KEY,state TEXT,payload TEXT);
                CREATE TABLE workflow_tasks (task_id TEXT PRIMARY KEY,official_request_id TEXT,task_key TEXT,
                    task_type TEXT,worker_type TEXT,status TEXT,updated_at INTEGER,task_input TEXT);
            ''')
            db.execute("INSERT INTO workflow_runs VALUES(?,'planned','PRIVATE PAYLOAD')",(A,))

    def migrate(self,rows):
        with sqlite3.connect(self.path) as db:
            db.execute('ALTER TABLE workflow_tasks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0')
            db.executemany("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,?,'PRIVATE INPUT',?)",rows)

    def test_only_exhausted_failures_no_body_or_writes(self):
        self.migrate([
            ('exhausted',A,'portrait_n','npc.portrait-direction','image','failed',10,3),
            ('still-running',A,'portrait_e','npc.portrait-direction','image','running',80,3),
            ('completed',A,'frames_run_w','npc.frames-postprocess','postprocess','completed',70,4),
            ('not-exhausted',A,'frames_run_e','npc.frames-postprocess','postprocess','failed',99,2),
            ('queued',A,'frames_run_s','npc.frames-postprocess','postprocess','pending',77,3),
            ('exhausted2',B,'animation_walk_n','npc.animation-native-png','animation','failed',50,4),
        ])
        before=self.path.read_bytes();stamp=self.path.stat().st_mtime_ns
        with started(S.server(0,alarms=partial(read_alarms,path=self.path),
                              list_requests=lambda _:self.fail('unexpected request service call'))) as port:
            status,body=get(port,ROUTE)
            self.assertEqual((status,json.loads(body)),(200,{'alarms':[
                {'officialRequestId':B,'key':'animation_walk_n','type':'npc.animation-native-png',
                 'workerType':'animation','retryCount':4,'updatedAt':50,'hasRequest':False},
                {'officialRequestId':A,'key':'portrait_n','type':'npc.portrait-direction',
                 'workerType':'image','retryCount':3,'updatedAt':10,'hasRequest':True}
            ],'hasMore':False}))
            self.assertNotIn(b'PRIVATE',body)
            self.assertEqual(get(port,'/requests?requestId='+A)[0],200)
            self.assertEqual(get(port,'/requests?requestId='+A+'&extra=1')[0],400)
            self.assertEqual(get(port,ROUTE+'?status=failed')[0],400)
            self.assertEqual(get(port,ROUTE,{'Origin':'http://other.invalid'})[0],403)
            self.assertEqual(get(port,ROUTE,method='POST')[0],405)
        self.assertEqual((self.path.read_bytes(),self.path.stat().st_mtime_ns),(before,stamp))
        self.assertEqual(list(self.path.parent.iterdir()),[self.path])

    def test_old_schema_empty_only_when_other_schema_is_sound(self):
        with started(S.server(0,alarms=partial(read_alarms,path=self.path))) as port:
            status,body=get(port,ROUTE)
            self.assertEqual((status,json.loads(body)),(200,{'alarms':[],'hasMore':False}))
            with sqlite3.connect(self.path) as db:
                db.execute('DROP TABLE workflow_tasks')
            status,body=get(port,ROUTE)
            self.assertEqual((status,json.loads(body)),(503,{'error':'WORKFLOW_ALARMS_UNAVAILABLE'}))
        missing=self.path.parent/'missing.sqlite'
        with started(S.server(0,alarms=partial(read_alarms,path=missing))) as port:
            self.assertEqual(get(port,ROUTE)[0],503)
        self.assertFalse(missing.exists())

    def test_alarm_tab_is_distinct_and_caps_ordered_rows(self):
        self.migrate([(str(i),A,'portrait_n','npc.portrait-direction','image','failed',i,3)
                      for i in range(MAX_ALARMS+1)])
        res=read_alarms(self.path)
        self.assertTrue(res['hasMore'])
        self.assertEqual([r['updatedAt'] for r in res['alarms']],list(range(MAX_ALARMS,0,-1)))
        with started(S.server(0)) as port:
            for path in ('/','/requests','/services'):
                status,body=get(port,path)
                self.assertEqual(status,200);self.assertIn(b'href="/alarms"',body)
                self.assertNotIn(b'id="alarm-rows"',body)
            status,body=get(port,'/alarms')
            self.assertEqual(status,200);self.assertIn(b'href="/alarms" aria-current="page"',body)
            self.assertIn(b'id="alarm-rows"',body)
