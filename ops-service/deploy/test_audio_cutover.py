from pathlib import Path
import plistlib
import tempfile
import unittest

import audio_cutover as audio
from mac_target_apply import apply_target
from prepare_ngs_release import atomic_write, digest, json_bytes, json_load, tree_sha
from test_mac_target_apply import FakeHost, TargetTests


def make_file(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(data)
    path.chmod(0o600)


def pin(path):
    s = path.stat()
    return {'device':s.st_dev,'inode':s.st_ino}


class AudioCutoverTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        self.folder,self.selector,_,entry = TargetTests().make(self.home,'worker-audio')
        self.release = entry.parents[1]
        self.runtime = self.home/'Workspaces/Workers/runtime'
        self.runtime.mkdir(mode=0o700)
        self.sha = 'a'*40
        self.prior = self.release.parent/'workflow-native-audio-resident-github-bbbbbbb'
        self.prior.mkdir(mode=0o700)
        self.native = self.release/audio.NATIVE
        self.native.mkdir(mode=0o700)
        for rel in audio.CODE:
            make_file(self.prior/rel,b'prior '+rel.encode())
            make_file(self.native/rel,b'candidate '+rel.encode())
        ace = self.runtime/'ace'
        artifact = self.runtime/'scratch'
        artifact.mkdir(mode=0o700)
        python = ace/'source/.venv/bin/python'
        cfg = ace/'source/.venv/pyvenv.cfg'
        shared = self.runtime/'shared.lock'
        legacy = self.runtime/'legacy.lock'
        for path in (python,cfg,shared,legacy):
            make_file(path,b'private fixture')
        pins = {str(p):audio._hash(p) for p in (*(self.prior/rel for rel in audio.CODE),python,cfg)}
        bound = {'schema':audio.INNER,'release_root':str(self.prior),'runtime_root':str(ace),
                 'artifact_root':str(artifact),'runner_path':str(self.prior/audio.RUNNER),'python_path':str(python),
                 'shared_lock':{'path':str(shared),**pin(shared)},'legacy_lock':{'path':str(legacy),**pin(legacy)},
                 'directory_pins':{'runtime_root':pin(ace),'artifact_root':pin(artifact)},'file_pins':pins}
        self.old_outer = self.runtime/'prior-adapter.json'
        self.old_inner = self.runtime/'prior-binding.json'
        atomic_write(self.old_inner,json_bytes(bound))
        atomic_write(self.old_outer,json_bytes(dict(schemaVersion=audio.OUTER,hqBinding=str(self.old_inner))))
        selector = plistlib.loads(self.selector.read_bytes())
        self.args = selector['ProgramArguments']
        self.args[self.args.index('--adapter-config')+1] = str(self.old_outer)
        atomic_write(self.selector,plistlib.dumps(selector))

    def test_exact_repinned_source_and_apply(self):
        prior_before = self.old_inner.read_bytes()
        proof = audio.stage_audio_binding(self.args,self.release,self.sha)
        self.assertEqual(audio.stage_audio_binding(self.args,self.release,self.sha),proof)
        outer, proof2 = audio.verify_audio_binding(self.release,self.sha)
        self.assertEqual(proof,proof2)
        self.assertEqual(self.old_inner.read_bytes(),prior_before)
        cfg = json_load(Path(json_load(outer.read_bytes())['hqBinding']).read_bytes())
        self.assertEqual(cfg['runtime_root'],json_load(prior_before)['runtime_root'])
        self.assertEqual(cfg['shared_lock'],json_load(prior_before)['shared_lock'])
        manifest = self.folder/'candidate.json'
        old = json_load(manifest.read_bytes())
        atomic_write(manifest,json_bytes({**old,'treeSha256':tree_sha(self.release),
          'expectedPriorPlistSha256':digest(self.selector.read_bytes()),'audioBindingSha256':proof}))
        host = FakeHost()
        result = apply_target('worker-audio',self.sha,'b'*64,self.folder,self.home,host)
        self.assertEqual(result['health'],'verified')
        new = plistlib.loads(self.selector.read_bytes())['ProgramArguments']
        self.assertEqual(new[new.index('--adapter-config')+1],str(outer))

    def test_rejects_staging_drift_and_manifest_without_binding(self):
        audio.stage_audio_binding(self.args,self.release,self.sha)
        (self.native/audio.RUNNER).write_bytes(b'again')
        with self.assertRaisesRegex(ValueError,'TARGET_AUDIO_BINDING_TREE'):
            audio.verify_audio_binding(self.release,self.sha)
        host = FakeHost()
        with self.assertRaisesRegex(ValueError,'TARGET_CANDIDATE_MISMATCH'):
            apply_target('worker-audio',self.sha,'b'*64,self.folder,self.home,host)
        self.assertEqual(host.events,[])


if __name__ == '__main__':
    unittest.main()
