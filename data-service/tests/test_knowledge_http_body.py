import io,json,unittest
from project_data_service.http_server import Handler
from project_data_service.database import DataError
class BodyTests(unittest.TestCase):
 def handler(self,body):
  h=object.__new__(Handler);h.headers={'Content-Length':str(len(body)),'Content-Type':'application/json'};h.rfile=io.BytesIO(body);return h
 def test_knowledge_full_source_without_truncation(self):
  raw='한글 분석\n'*200000;body=json.dumps({'authoring':{'description':raw}},ensure_ascii=False).encode()
  self.assertEqual(self.handler(body).read_json(max_body=None)['authoring']['description'],raw)
  with self.assertRaises(DataError):self.handler(body).read_json()
 def test_invalid_length_remains_rejected(self):
  h=self.handler(b'{}');h.headers['Content-Length']='bad'
  with self.assertRaises(DataError):h.read_json(max_body=None)
