from datetime import datetime, timezone
from unittest import TestCase
from planner_cutover import fresh_heartbeat, loaded_selector, wait_planner

NOW = datetime(2026, 11, 22, tzinfo=timezone.utc)
SHA = 'a' * 40
ARGS = ('/node', '/release/workflow-planner/cli.mjs', '--db', '/private/workflow.sqlite')
WD = '/release/workflow-planner'
REPORT = '\targuments = {\n' + ''.join('\t\t' + a + '\n' for a in ARGS) + '\t}\n\tworking directory = ' + WD
DB = (1, 2)


def heartbeat(sha=SHA, at=NOW.isoformat(), state='connected'):
    return [{'serverName': 'workflow-planner', 'commitSha': sha, 'lastReceivedAt': at, 'state': state}]


class PlannerCutover(TestCase):
    def wait(self, live, status, sha=SHA, db=lambda:DB):
        time = [0]
        return wait_planner(live=live, status=status, expected=(ARGS, WD, DB, sha), since=NOW,
                            previous_pid=12,database_identity=db, clock=lambda:time[0],
                            sleep=lambda n:time.__setitem__(0,time[0]+n))

    def test_identity_and_exact_heartbeat(self):
        self.assertTrue(loaded_selector(REPORT, ARGS, WD))
        self.assertFalse(loaded_selector(REPORT.replace('--db','--once'), ARGS, WD))
        self.assertTrue(fresh_heartbeat(heartbeat(),SHA,NOW))
        for rows in (heartbeat('b'*40), heartbeat(at='2020-01-01T00:00:00+00:00'), heartbeat(state='warning'),
                     heartbeat(at='2026-11-22T00:00:00'), heartbeat()+heartbeat()):
            self.assertFalse(fresh_heartbeat(rows,SHA,NOW))
        self.wait(lambda:(13,REPORT),heartbeat)
        for live, status, db in ((lambda:(12,REPORT),heartbeat,lambda:DB),
                                  (lambda:(13,REPORT),lambda:heartbeat('b'*40),lambda:DB),
                                  (lambda:(13,REPORT),heartbeat,lambda:(1,8)),
                                  (lambda:(13,REPORT.replace(WD,'/different')),heartbeat,lambda:DB)):
            with self.assertRaisesRegex(ValueError,'TARGET_PLANNER_HEALTH_TIMEOUT'):
                self.wait(live,status,db=db)

    def test_legacy_rollback_without_business_request_or_heartbeat(self):
        self.wait(lambda:(13,REPORT),lambda:self.fail('legacy rollback cannot query heartbeat'),sha=None)
        with self.assertRaisesRegex(ValueError,'TARGET_PLANNER_HEALTH_TIMEOUT'):
            self.wait(lambda:(13,REPORT.replace('/private/workflow.sqlite','/other.sqlite')),
                      lambda:self.fail('legacy rollback cannot query heartbeat'),sha=None)
