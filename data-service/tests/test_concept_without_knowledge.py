import builtins
import importlib
import os
import unittest
from unittest.mock import patch

class ConceptIndependence(unittest.TestCase):
    def test_concept_imports_without_knowledge_modules(self):
        original=builtins.__import__
        def guard(name,*args,**kwargs):
            if 'knowledge' in name:raise AssertionError('knowledge dependency: '+name)
            return original(name,*args,**kwargs)
        with patch('builtins.__import__',side_effect=guard):
            for name in ['concept_requests','concept_authoring','concept_filex','concept_migration']:
                importlib.reload(importlib.import_module('project_data_service.'+name))

    def test_selector_uses_new_service_boundary(self):
        from project_data_service import concept_requests
        from io import BytesIO
        class Opener:
            def open(self,request,timeout):
                assert request.full_url=='http://127.0.0.1:18040/v1/workflow-selections'
                return BytesIO(b'{"fixture":true}')
        with patch.dict(os.environ,{'WORKFLOW_DECISION_URL':'http://127.0.0.1:18040/'}),patch.object(concept_requests.urllib.request,'build_opener',return_value=Opener()):
            self.assertEqual(concept_requests.select({'requestId':'fixture'}),{'fixture':True})
