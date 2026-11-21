"""Check full registered four-token stack command, without launching any children."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ENABLE = 'PROJECTBS_ONE_STOP_DIRECTION_STREAMING'
TARGET = 'PROJECTBS_ONE_STOP_DIRECTION_TARGET'

class Tests(unittest.TestCase):
    def test_registered_boundary(self):
        stack = Path(__file__).resolve().parents[2] / 'runtime/stack.py'
        argv = [sys.executable, str(stack), '--storage-root', '/tmp/filex-pilot-fixture']
        clean = {k: v for k, v in os.environ.items() if k not in (ENABLE, TARGET)}
        def run(env, *extra):
            return subprocess.run([*argv, '--print-plan', *extra],
                                  env={**clean, **env}, capture_output=True, check=False)
        plain = run({})
        self.assertEqual(plain.returncode, 0, plain.stderr)
        pilot = run({ENABLE: '1', TARGET: 'npc.fixture'})
        self.assertEqual(pilot.returncode, 0, pilot.stderr)
        old, new = [json.loads(x.stdout)['services'] for x in (plain,pilot)]
        for a,b in zip(old,new,strict=True):
            if b[0] == 'workflow-decision':
                args=b[2]
                ix=args.index('--one-stop-direction-streaming')
                self.assertEqual(args[ix:ix+3], ['--one-stop-direction-streaming',
                                                 '--one-stop-direction-target','npc.fixture'])
                del args[ix:ix+3]
            self.assertEqual(a,b)
        for env, flags in [
            ({ENABLE:'1'},()), ({TARGET:'npc.fixture'},()),
            ({ENABLE:'0',TARGET:'npc.fixture'},()),
            ({ENABLE:'1',TARGET:'../untrusted'},()),
            ({ENABLE:'1',TARGET:'npc.fixture'},('--one-stop-direction-streaming',)),
        ]:
            refused=run(env,*flags)
            self.assertEqual(refused.returncode,2,refused.stderr)
            self.assertIn(b'invalid registered OneStop direction pilot',refused.stderr)
            self.assertEqual(refused.stdout,b'')
