import unittest
from types import SimpleNamespace
from project_data_service.owned_operation_http import configure
from project_data_service.filex_npc_authority import FilexCurrentNPC
class Tests(unittest.TestCase):
 def test_default_does_not_access_dependencies(self):
  s=SimpleNamespace();configure(s);self.assertFalse(hasattr(s,'owned_npc_operations'))
 def test_only_existing_services_local_opt_in(self):
  s=SimpleNamespace()
  with self.assertRaises(ValueError):configure(s,enabled=True)
  factory=lambda:None;authority=lambda *a:None
  source=FilexCurrentNPC(SimpleNamespace(factory=factory))
  s.app=SimpleNamespace(domains=SimpleNamespace(npcs=source));s.media_artifacts=SimpleNamespace(authority=authority)
  with self.assertRaises(ValueError):configure(s,enabled=True,host='0.0.0.0')
  configure(s,enabled=True);self.assertIs(s.owned_npc_operations.factory,factory);self.assertIs(s.owned_npc_operations.authority,authority)
  with self.assertRaises(ValueError):configure(s,enabled='true')
