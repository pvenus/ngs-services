import json
from pathlib import Path
import tempfile
import unittest

from project_data_service.prop_animation_ready import renderer_ready, SCHEMA
from project_data_service.workflow_cinematic import READY_SCHEMA


class ReadinessTests(unittest.TestCase):
    def test_exact_a14b_provider_and_separate_live_markers(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve()
            root.chmod(0o700)
            file=root/'marker.json'
            def note(schema,provider,at=5000):
                file.write_text(json.dumps({'schemaVersion':schema,'provider':provider,'checkedAt':at}))
                file.chmod(0o600)
            for schema in (SCHEMA,READY_SCHEMA):
                with self.subTest(schema=schema):
                    note(schema,'wan-ti2v-q8_0')
                    self.assertFalse(renderer_ready(file,now=5005,schema=schema))
                    note(schema,'wan-i2v-a14b-q4_k_m')
                    self.assertTrue(renderer_ready(file,now=5005,schema=schema))
                    self.assertFalse(renderer_ready(file,now=5005,schema=READY_SCHEMA if schema==SCHEMA else SCHEMA))
                    self.assertFalse(renderer_ready(file,now=130001,schema=schema))
                    self.assertFalse(renderer_ready(file,now=4999,schema=schema))
                    file.chmod(0o644)
                    self.assertFalse(renderer_ready(file,now=5005,schema=schema))
