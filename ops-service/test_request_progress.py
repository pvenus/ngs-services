from functools import partial
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from request_progress import read_progress
from test_server import started, get
import server as S

ROOT='/api/request-progress'
A='request.official.'+'a'*64
B='request.official.'+'b'*64
UNKNOWN='request.official.'+'c'*64


class ProgressTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db=Path(tmp.name).resolve()/'workflow.sqlite'
        fd=os.open(self.db,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        os.close(fd)
        db=sqlite3.connect(self.db)
        try:
            db.executescript('''
              CREATE TABLE workflow_runs (official_request_id TEXT PRIMARY KEY,state TEXT,payload TEXT);
              CREATE TABLE workflow_tasks (
                task_id TEXT PRIMARY KEY,official_request_id TEXT,task_key TEXT,task_type TEXT,worker_type TEXT,
                status TEXT,depends_on_task_id TEXT,updated_at INTEGER,task_input TEXT,result_ref TEXT
              );
            ''')
            db.executemany('INSERT INTO workflow_runs VALUES (?,?,?)',
                           [(A,'planned','PRIVATE BODY'),(B,'awaiting_rs','NEIGHBOR BODY')])
            db.executemany('INSERT INTO workflow_tasks VALUES (?,?,?,?,?,?,?,?,?,?)',[
                ('a.z',A,'delete','npc.delete-folder','file','completed',None,40,'PRIVATE TASK INPUT','PRIVATE RESULT'),
                ('b.p',B,'different','npc.preset','document','pending',None,20,'NEIGHBOR INPUT',None),
                ('a.a',A,'preset','npc.preset','document','running','a.z',30,'PRIVATE TASK INPUT',None),
                ('a.b',A,'planning_representative','npc.plan-representative','document','pending','a.a',10,'PRIVATE TASK INPUT',None)])
            db.commit()
        finally: db.close()
        self.reader=partial(read_progress,path=self.db)

    def test_ordered_exact_projection_and_no_storage_writes(self):
        original=self.db.read_bytes()
        mtime=self.db.stat().st_mtime_ns
        with started(S.server(0,list_requests=lambda _:self.fail('unexpected upstream'),request_progress=self.reader)) as p:
            status,body=get(p,ROOT+'?requestId='+A)
            self.assertEqual(status,200)
            self.assertEqual(json.loads(body),{'workflowState':'planned','tasks':[
                {'key':'delete','type':'npc.delete-folder','workerType':'file','status':'completed','dependsOn':None,'updatedAt':40},
                {'key':'preset','type':'npc.preset','workerType':'document','status':'running','dependsOn':'delete','updatedAt':30},
                {'key':'planning_representative','type':'npc.plan-representative','workerType':'document','status':'pending','dependsOn':'preset','updatedAt':10},
            ]})
            self.assertNotIn(b'PRIVATE',body)
            self.assertNotIn(b'NEIGHBOR',body)
            self.assertNotIn(b'a.z',body)
            status,body=get(p,ROOT+'?requestId='+B)
            self.assertEqual(status,200)
            self.assertEqual([t['key'] for t in json.loads(body)['tasks']],['different'])
            self.assertEqual(json.loads(body)['workflowState'],'awaiting_rs')
            self.assertEqual(get(p,ROOT+'?requestId='+UNKNOWN),(200,b'{"workflowState": null, "tasks": []}'))
        self.assertEqual(self.db.read_bytes(),original)
        self.assertEqual(self.db.stat().st_mtime_ns,mtime)
        self.assertEqual(list(self.db.parent.iterdir()),[self.db])

    def test_audio_workflow_is_projected_without_revealing_payload(self):
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO workflow_runs VALUES (?,?,?)",(UNKNOWN,'planned','PRIVATE SOUND BRIEF'))
            db.execute("INSERT INTO workflow_tasks VALUES (?,?,?,?,?,?,?,?,?,?)",
                       ('a.music',UNKNOWN,'audio_generate','audio.generate','audio','running',None,77,'PRIVATE INPUT',None))
        with started(S.server(0,request_progress=self.reader)) as p:
            status,body=get(p,ROOT+'?requestId='+UNKNOWN)
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body),{'workflowState':'planned','tasks':[
            {'key':'audio_generate','type':'audio.generate','workerType':'audio','status':'running','dependsOn':None,'updatedAt':77}
        ]})
        self.assertNotIn(b'PRIVATE',body)

    def test_invalid_queries_origin_and_non_get_never_read(self):
        def fail(_): self.fail('unexpected planner read')
        with started(S.server(0,request_progress=fail)) as p:
            for query in ['', '?requestId=', '?requestId='+A.upper(), '?requestId='+A+'&requestId='+B,
                          '?requestId='+A+'&limit=1', '?requestId='+A+'%00', '?id='+A, '?requestId='+A[:-1]]:
                with self.subTest(query=query):
                    self.assertEqual(get(p,ROOT+query)[0],400)
            self.assertEqual(get(p,ROOT+'?requestId='+A,method='POST')[0],405)
            self.assertEqual(get(p,ROOT+'?requestId='+A,{'Host':f'localhost:{p}'})[0],403)
            self.assertEqual(get(p,ROOT+'?requestId='+A,{'Origin':'http://elsewhere.invalid'})[0],403)

    def test_cross_run_parent_missing_database_and_unsafe_input_fail_closed(self):
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET depends_on_task_id='b.p' WHERE task_id='a.a'")
        for reader in [self.reader, partial(read_progress,path=self.db.parent/'missing.sqlite')]:
            with started(S.server(0,request_progress=reader)) as p:
                status,body=get(p,ROOT+'?requestId='+A)
                self.assertEqual((status,json.loads(body)),(503,{'error':'WORKFLOW_PROGRESS_UNAVAILABLE'}))
        self.assertFalse((self.db.parent/'missing.sqlite').exists())

    def test_wal_refused_before_open(self):
        db=sqlite3.connect(self.db)
        try:
            self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone()[0],'wal')
        finally: db.close()
        before=list(self.db.parent.iterdir())
        with started(S.server(0,request_progress=self.reader)) as p:
            self.assertEqual(get(p,ROOT+'?requestId='+A)[0],503)
        self.assertEqual(list(self.db.parent.iterdir()),before)
