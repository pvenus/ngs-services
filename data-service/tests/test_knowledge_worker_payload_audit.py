"""Contract audit: complete rich input is preserved, not AI-authored here."""
import copy
import unittest
from project_data_service.knowledge_worker import build_files
from project_data_service.filex_knowledge import entry_path,category_path

class RichPayloadAudit(unittest.TestCase):
 def setUp(self):
  self.entry={'id':'workflow-demo','categoryId':'workflows','type':'workflow','title':'풍부한 워크플로 지식','purpose':'요청을 정확한 워커로 연결','content':'긴 분석 본문 및 끝표식\n'*6000,'data':{'inputs':{'required':['request','subject']},'outputs':['file'],'dependencies':['planning'],'failure':{'retry':False}},'triggers':['인물 생성','NPC 통합 생성'],'status':'active','authority':'authoritative','maxReadChars':12000}
  self.snapshot={'documents':{'categories/workflows/metadata.json':{'id':'workflows'}},'hashes':{}}
 def test_large_content_and_keywords_preserved_without_truncation(self):
  original=copy.deepcopy(self.entry)
  result=build_files({'operation':'create','entryId':self.entry['id'],'expectedVersion':0,'entry':self.entry},self.snapshot)[0]
  for key,value in original.items():self.assertEqual(result['body'][key],value)
  self.assertGreater(len(result['body']['content']),50000)
  self.assertEqual(self.entry,original)
  self.assertEqual(result['path'],'categories/workflows/entries/workflow-demo.json')
  self.assertEqual(set(self.snapshot['documents']),{'categories/workflows/metadata.json'})
 def test_update_preserves_unspecified_rich_data(self):
  current={**self.entry,'version':1};self.snapshot['documents']['categories/workflows/entries/workflow-demo.json']=current;self.snapshot['hashes']['categories/workflows/entries/workflow-demo.json']='a'*64
  result=build_files({'operation':'update','entryId':self.entry['id'],'expectedVersion':1,'changes':{'title':'변경 제목'}},self.snapshot)[0]
  for key in ('content','data','triggers','purpose'):self.assertEqual(result['body'][key],current[key])
  self.assertEqual(result['expectedHash'],'a'*64)
 def test_missing_content_is_defaulted_not_generated(self):
  entry={k:self.entry[k] for k in ('id','categoryId','type','title','purpose')}
  result=build_files({'operation':'create','entryId':entry['id'],'expectedVersion':0,'entry':entry},self.snapshot)[0]['body']
  self.assertEqual(result['content'],'');self.assertEqual(result['triggers'],[]);self.assertEqual(result['data'],{})

if __name__=='__main__':unittest.main()
