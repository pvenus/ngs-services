import unittest

from workflow_decision_service.one_stop import digest, select
from workflow_decision_service.service import WorkflowDecisionService


def body(combat):
    return {'schemaVersion':'ngs.npc-one-stop.v1','projectId':'binari',
            'character':{'npcId':'npc.fixture'},'includeCombat':combat,
            **({'combatMotionPresets':['weapon.entry']} if combat else {})}


class Tests(unittest.TestCase):
    def test_representative_first_for_both_variants(self):
        for combat in (False,True):
            r=body(combat)
            old=select(r,direction_streaming=True)
            new=select(r,direction_streaming=True,representative_first=True)
            self.assertEqual((old['version'],new['version']),('4','5'))
            self.assertEqual(new['requestHash'],digest(r))
            self.assertEqual(new['planHash'],digest(new['stages']))
            self.assertNotEqual(old['registryHash'],new['registryHash'])
            steps={x['id']:x['dependsOn'] for x in new['stages']}
            self.assertEqual(set(steps),{x['id'] for x in old['stages']})
            self.assertEqual(len(steps),75 if combat else 48)
            done={'skeleton','overview','art','planning_representative'}
            self.assertEqual({k for k,v in steps.items() if k not in done and set(v)<=done},
                             {'portrait_representative','planning_idle'}|({'planning_combat'} if combat else set()))
            for pose in ('idle','combat') if combat else ('idle',):
                self.assertEqual(steps[f'portrait_{pose}'],[f'planning_{pose}','portrait_representative'])
                self.assertEqual(steps[f'{pose}_8way'],[f'portrait_{pose}'])
                for direction in ('n','ne','e','se','s','sw','w','nw'):
                    gate=f'pose_{pose}_{direction}'
                    self.assertEqual(steps[gate],[f'portrait_{pose}'])
                    self.assertLess(list(steps).index(f'{pose}_8way'),list(steps).index(gate))
                    for motion in ('walk','run') if pose=='idle' else ('combat_0',):
                        animation=f'animation_{motion}_{direction}'
                        self.assertEqual(steps[animation],[gate])
                        self.assertEqual(steps[f'frames_{motion}_{direction}'],[animation])

    def test_internal_v5_opt_in_does_not_change_service_output(self):
        r=body(True)
        with self.assertRaisesRegex(ValueError,'REPRESENTATIVE_DIRECTION_REQUIRED'):
            select(r,representative_first=True)
        service=WorkflowDecisionService(one_stop_direction_streaming=True,one_stop_direction_target='npc.fixture')
        self.assertEqual(service.one_stop_plan(r),select(r,direction_streaming=True))
        self.assertEqual(service.one_stop_plan({**r,'representativeFirst':True})['version'],'4')
