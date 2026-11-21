"""Retirement does not open or mutate retained knowledge data."""
import builtins
import http.client
import importlib
import sqlite3
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from project_data_service.http_server import Handler
from project_data_service.domains import DomainModule

class Retirement(unittest.TestCase):
 def test_all_domain_routes_are_gone_without_app_or_body_read(self):
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   for method in ['GET','POST','PUT','DELETE','HEAD']:
    for path in ['/v1/data/knowledge','/v1/data/knowledge/fixture','/api/knowledge','/api/knowledge/overview','/api/knowledge/entries/x','/v1/knowledge/snapshot','/v1/knowledge/laya-catalog','/v1/knowledge/requests','/v1/knowledge/requests/x/resume','/knowledge.html','/knowledge.js','/knowledge.css']:
     connection=http.client.HTTPConnection(*server.server_address)
     connection.request(method,path)
     response=connection.getresponse();response.read();connection.close()
     self.assertEqual(response.status,501 if method=='DELETE' else 410,(method,path))
  finally:server.shutdown();server.server_close();thread.join()

 def test_domains_work_without_knowledge_and_preserve_retained_file(self):
  with tempfile.TemporaryDirectory() as root:
   domains=Path(root)/'domains';domains.mkdir()
   for name in ['npc','story']:
    with sqlite3.connect(domains/(name+'.sqlite3')) as db:db.execute('CREATE TABLE revisions(revision INTEGER,saved_at TEXT,note TEXT,document TEXT)')
   retained=domains/'knowledge.sqlite3';retained.write_bytes(b'retained-not-a-sqlite-file')
   with patch('project_data_service.domains.FilexCurrentNPC.configured',return_value=None):
    module=DomainModule(root,None)
   try:
    self.assertTrue(module.enabled)
    self.assertFalse(hasattr(module,'knowledge'))
    self.assertFalse(hasattr(module.server(SimpleNamespace(server_port=1234)),'knowledge_store'))
   finally:module.close()
   self.assertEqual(retained.read_bytes(),b'retained-not-a-sqlite-file')
   retained.unlink() # synthetic fixture only
   with patch('project_data_service.domains.FilexCurrentNPC.configured',return_value=None):module=DomainModule(root,None)
   self.assertTrue(module.enabled);module.close()
   self.assertFalse(retained.exists())

 def test_boot_and_export_modules_do_not_import_or_open_knowledge(self):
  original=builtins.__import__
  def guard(name,*args,**kwargs):
   if 'knowledge' in name:raise AssertionError('knowledge import: '+name)
   return original(name,*args,**kwargs)
  names=['project_data_service.http_server','project_data_service.domains','project_data_service.domain_contracts.legacy_http','project_data_service.domain_export.source','project_data_service.domain_export.binari_current']
  with patch('builtins.__import__',side_effect=guard):
   for name in names:
    module=importlib.reload(importlib.import_module(name))
    source=Path(module.__file__).read_text()
    self.assertNotIn("connect('knowledge.sqlite3')",source)
    self.assertNotIn('KnowledgeCleanupRuntime',source)
