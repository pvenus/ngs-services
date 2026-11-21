import unittest
from workflow_decision_service.feature_selection import FeatureSelectionService, SelectionError
from workflow_decision_service.catalog import CatalogClient, InMemoryCatalogClient


def fixture():
    services = [{'id':'npc-planning','status':'active'}, {'id':'video-alpha','status':'active'}]
    actions = [
        {'id':'npc-planning.prepare-context','service_id':'npc-planning','status':'active','permission':'read_only','operation':'npc_context_prepare','required_inputs':['projectId']},
        {'id':'video-alpha.compose-spec','service_id':'video-alpha','status':'active','permission':'compose','operation':'compose','required_inputs':['jobId'],'prerequisite_get':'video-alpha.get'},
        {'id':'video-alpha.get','service_id':'video-alpha','status':'active','permission':'read_only','operation':'job_get','required_inputs':['jobId']},
    ]
    return InMemoryCatalogClient(services,actions,{a['id']:{'action':a,'template':None} for a in actions})


class Features(unittest.TestCase):
    def test_explicit_features_keep_read_only_contract(self):
        service=FeatureSelectionService(fixture())
        for feature in ['portrait.register','portrait.replace','portrait.generate','story.node.image','episode.visual.asset']:
            result=service.select('prepare',{'featureId':feature,'projectId':'binari'})
            self.assertEqual(result['actionId'],'npc-planning.prepare-context')
            self.assertEqual(result['nextStep'],'ready-for-selected-action')
            self.assertEqual(result['contextEntryIds'],[])
        self.assertEqual(service.select('prepare',{'featureId':'npc-settings-draft','projectId':'binari'})['workerSelection']['workerId'],'npc-planning')

    def test_missing_unknown_and_ambiguous_fail_closed(self):
        service=FeatureSelectionService(fixture())
        self.assertEqual(service.select('prepare',{'featureId':'story.node.image'})['missingInputs'],['projectId'])
        with self.assertRaises(SelectionError):service.select('prepare',{'featureId':'unknown'})
        service.catalog.actions.append(service.catalog.actions[0])
        with self.assertRaises(SelectionError):service.select('prepare',{'featureId':'story.node.image'})

    def test_video_prerequisite(self):
        service=FeatureSelectionService(fixture())
        self.assertEqual(service.select('video 처리',{})['nextStep'],'collect-input')
        result=service.select('video 처리',{'jobId':'fixture'})
        self.assertEqual(result['nextStep'],'call-prerequisite-get')
        self.assertEqual(result['firstTool'],'job_get')
        self.assertEqual(service.select('video 상태',{'jobId':'fixture'})['actionId'],'video-alpha.get')

    def test_portrait_selection_requires_canonical_source(self):
        with self.assertRaisesRegex(SelectionError,'CANONICAL_SOURCE_UNAVAILABLE'):
            FeatureSelectionService(fixture()).select('prepare',{'featureId':'story.node.image.portrait-reference'})

    def test_metadata_client_never_fetches_knowledge(self):
        client=CatalogClient('http://fixture.invalid')
        calls=[]
        def get(path, query=None):
            calls.append(path)
            if path=='/v1/catalog/execution-context/test':return {'action':{'id':'test'},'template':{'id':'spec'},'executionContracts':[{'id':'fixture-contract'}]}
            self.fail('unexpected content access '+path)
        client._get=get
        result=client.execution_context('test')
        self.assertEqual(result['template']['id'],'spec')
        self.assertEqual(calls,['/v1/catalog/execution-context/test'])
