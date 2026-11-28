import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from project_data_service.manual_audio_import import MARKER,TOKEN_ENV,configured,owner_token,resolve_token_file


class ManualAudioConfigTests(unittest.TestCase):
    def setUp(self):
        folder=TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root=Path(folder.name).resolve()
        self.token=self.root/MARKER

    def issue(self):
        self.token.write_text('x'*64+'\n')
        self.token.chmod(0o600)

    def test_default_off_then_loads_opt_in_from_private_data_root(self):
        self.assertIsNone(resolve_token_file(self.root,None))
        self.issue()
        self.assertEqual(resolve_token_file(self.root,None),self.token)
        state=SimpleNamespace()
        configured(state,'127.0.0.1',resolve_token_file(self.root,None),None)
        self.assertEqual(len(state.manual_audio_import[0]),32)
        with self.assertRaisesRegex(ValueError,'LOCAL_ONLY'):
            configured(SimpleNamespace(),'0.0.0.0',self.token,None)
        self.token.unlink()
        self.assertIsNone(resolve_token_file(self.root,None))

    def test_insecure_root_symlink_fifo_hardlink_and_oversize_fail_closed(self):
        self.issue()
        self.root.chmod(0o755)
        with self.assertRaisesRegex(ValueError,'STORAGE_PRIVATE'):
            resolve_token_file(self.root,None)
        self.root.chmod(0o700)
        self.token.chmod(0o640)
        with self.assertRaisesRegex(ValueError,'TOKEN_PRIVATE'):owner_token(self.token)
        self.token.chmod(0o600)
        alias=self.root/'alias'
        os.link(self.token,alias)
        with self.assertRaisesRegex(ValueError,'TOKEN_PRIVATE'):owner_token(self.token)
        alias.unlink()
        self.token.write_text('x'*130)
        with self.assertRaisesRegex(ValueError,'TOKEN_PRIVATE'):owner_token(self.token)
        self.token.unlink()
        os.mkfifo(self.token,0o600)
        with self.assertRaisesRegex(ValueError,'TOKEN_PRIVATE'):owner_token(self.token)
        self.token.unlink()
        self.token.symlink_to(alias)
        self.assertEqual(resolve_token_file(self.root,None),self.token)
        with self.assertRaises((ValueError,FileNotFoundError)):owner_token(self.token)
        self.token.unlink()
        with TemporaryDirectory() as outside:
            outer=self.root/'outer';outer.symlink_to(Path(outside).resolve())
            self.token=outer/MARKER
            self.issue()
            with self.assertRaisesRegex(ValueError,'STORAGE_PATH'):
                resolve_token_file(outer,None)

    def test_override_never_falls_back_and_is_isolated_from_other_stack_roles(self):
        self.issue()
        wrong=self.root/'absent'
        self.assertEqual(resolve_token_file(self.root,str(wrong)),wrong)
        with self.assertRaises(FileNotFoundError):owner_token(wrong)
        from runtime.stack import child_environment
        env={TOKEN_ENV:str(self.token),'UNRELATED':'kept'}
        self.assertEqual(child_environment(env,'data',self.root)[TOKEN_ENV],str(self.token))
        self.assertNotIn(TOKEN_ENV,child_environment(env,'media',self.root))
