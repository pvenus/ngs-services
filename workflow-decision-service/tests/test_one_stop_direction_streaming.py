import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from workflow_decision_service.http_api import WorkflowDecisionApi
from workflow_decision_service.one_stop import digest, select, stages_from
from workflow_decision_service.service import WorkflowDecisionService

DIRECTIONS = ('n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw')
ROOT = Path(__file__).resolve().parents[2]


def request(presets=None):
    return {'schemaVersion': 'ngs.npc-one-stop.v1', 'projectId': 'binari',
            'includeCombat': presets is not None,
            **({'combatMotionPresets': presets} if presets is not None else {})}


class Tests(unittest.TestCase):
    def plan(self, r):
        p = select(r, direction_streaming=True)
        self.assertEqual((p['schemaVersion'], p['version']), ('ngs.npc-one-stop-plan.v2', '4'))
        self.assertEqual(p['requestHash'], digest(r))
        self.assertEqual(p['planHash'], digest(p['stages']))
        self.assertNotEqual(p['registryHash'], select(r)['registryHash'])
        self.assertEqual((p['modelCalls'], p['workerCalls']), (0, 0))
        self.assertEqual(p, select(r, direction_streaming=True))
        graph = {}
        for step in p['stages']:
            self.assertEqual(set(step), {'id', 'dependsOn', 'completion'})
            self.assertEqual(step['completion'], 'verified-publication')
            self.assertNotIn(step['id'], graph)
            self.assertLessEqual(set(step['dependsOn']), graph.keys())
            graph[step['id']] = step['dependsOn']
        return graph

    def independent_chain(self, graph, variant, pose):
        for d in DIRECTIONS:
            self.assertEqual(graph[f'animation_{variant}_{d}'], [f'pose_{pose}_{d}'])
            self.assertEqual(graph[f'frames_{variant}_{d}'], [f'animation_{variant}_{d}'])

    def test_exact_noncombat_graph(self):
        r = request()
        graph = self.plan(r)
        legacy = {s['id']:s['dependsOn'] for s in select(r)['stages']
                  if not s['id'].startswith(('animation_', 'frames_'))}
        self.assertEqual({stage:graph[stage] for stage in legacy}, legacy)
        self.assertEqual(len(graph), 48)
        for d in DIRECTIONS:
            self.assertEqual(graph[f'pose_idle_{d}'], ['portrait_idle'])
            self.assertLess(list(graph).index('idle_8way'), list(graph).index(f'pose_idle_{d}'))
        self.independent_chain(graph, 'walk', 'idle')
        self.independent_chain(graph, 'run', 'idle')

    def test_combat_keeps_global_writers_ordered_then_streams_per_preset(self):
        r = request(['fixture.motion-a', 'fixture.motion-b'])
        graph = self.plan(r)
        legacy = {s['id']:s['dependsOn'] for s in select(r)['stages']
                  if not s['id'].startswith(('animation_', 'frames_'))}
        self.assertEqual({stage:graph[stage] for stage in legacy}, legacy)
        self.assertEqual(len(graph), 91)
        for d in DIRECTIONS:
            for variant in ('idle', 'combat'):
                self.assertEqual(graph[f'pose_{variant}_{d}'], ['portrait_combat'])
                self.assertLess(list(graph).index(f'{variant}_8way'), list(graph).index(f'pose_{variant}_{d}'))
        for variant in ('walk', 'run'):
            self.independent_chain(graph, variant, 'idle')
        for i in range(2):
            self.independent_chain(graph, f'combat_{i}', 'combat')
        reversed_r = request(list(reversed(r['combatMotionPresets'])))
        self.assertNotEqual(select(reversed_r, direction_streaming=True)['requestHash'],
                            select(r, direction_streaming=True)['requestHash'])
        self.assertEqual(len(self.plan(request([f'motion.{i}' for i in range(16)]))), 315)

    def test_fixed_baseline_hashes_when_disabled_and_payload_cannot_opt_in(self):
        self.assertEqual(digest(select(request())),
                         'c6305c021b3abbf77e2df99f650d5a371038ef54082bbc7c3f43f2894312e519')
        self.assertEqual(digest(select(request(['fixture.motion-a','fixture.motion-b']))),
                         '280f951c76cb579fe0991a2e35bd636e042eda58bf3c11fecc7b58c28f321591')
        for flag, version in ((False,'3'), (True,'4')):
            api = WorkflowDecisionApi(WorkflowDecisionService(one_stop_direction_streaming=flag))
            payload = json.dumps({**request(), 'directionStreaming': not flag}).encode()
            status = []
            body = api({'PATH_INFO':'/v1/npc-one-stop-plans','REQUEST_METHOD':'POST',
                        'REMOTE_ADDR':'127.0.0.1','HTTP_HOST':'127.0.0.1:8040',
                        'CONTENT_LENGTH':str(len(payload)),'wsgi.input':io.BytesIO(payload)},
                       lambda code,headers:status.append(code))
            self.assertEqual(status,['200 OK'])
            self.assertEqual(json.loads(b''.join(body))['version'],version)

    def test_registry_requires_exact_stream_profile_and_combat_validation(self):
        profile = ROOT/'workflow-decision-service/workflow_decision_service/one_stop_registry.json'
        config = json.loads(profile.read_text())
        config.update(version='4', directionStreaming={'directions': ['n']})
        with self.assertRaisesRegex(ValueError,'ONE_STOP_DIRECTION_REGISTRY_INVALID'):
            stages_from(config,request())
        for presets in ([], ['same','same'], ['../motion']):
            with self.assertRaisesRegex(ValueError,'ONE_STOP_COMBAT_PRESETS_REQUIRED'):
                select(request(presets),direction_streaming=True)

    def test_stack_print_plan_changes_only_decision_argv_without_starting_services(self):
        with tempfile.TemporaryDirectory() as dirname:
            storage = Path(dirname)/'uncreated'
            base = [sys.executable, '-B',str(ROOT/'runtime/stack.py'),
                    '--storage-root',str(storage),'--print-plan']
            old = json.loads(subprocess.check_output(base))
            new = json.loads(subprocess.check_output([*base,'--one-stop-direction-streaming']))
            for name, _, argv, _ in new['services']:
                if name=='workflow-decision':
                    argv.remove('--one-stop-direction-streaming')
                else:
                    self.assertNotIn('--one-stop-direction-streaming',argv)
            self.assertEqual(new,old)
            self.assertFalse(storage.exists())
