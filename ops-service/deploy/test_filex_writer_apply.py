import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import filex_writer_apply as m

SHA = "a" * 40
DIGEST = "b" * 64
OLD = "sha256:" + "c" * 64
NEW = "sha256:" + "d" * 64
OLDER = "sha256:" + "e" * 64
BASE = "python:3.12-slim@sha256:" + "f" * 64


class Host:
    def __init__(self, home, control):
        self.home, self.control = home, control
        self.row = self.model(OLD, "a", False)
        self.calls = []
        self.fail_after = False
        self.fail_rollback = False
        self.candidate_healthy = True
        self.candidate_risky = False
        self.base_image = OLD
        self.profiles = {OLD:m.LEGACY_CAP_DIGEST, NEW:m.LEGACY_CAP_DIGEST}

    def model(self, image, iid, active):
        base = str(m.filex_code(self.home) / "compose.yaml")
        return {"Name": "/" + m.CONTAINER, "Image": image, "Id": iid * 64,
                "State": {"Running": True}, "Config": {
                    "Labels": {"com.docker.compose.project": m.PROJECT, "com.docker.compose.service": m.SERVICE,
                               "com.docker.compose.project.config_files": base + ("," + str(m.selector_file(self.control)) if active else "")},
                    "User": "501:20", "Entrypoint": None, "Cmd": ["python", "-B", "/app/server.py"],
                    "WorkingDir": "/app", "Env": [f"{k}={v}" for k, v in m.WRITER_ENV.items()],
                }, "HostConfig": {"Privileged": False, "ReadonlyRootfs": True, "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges:true"],
                    "PortBindings": {"8093/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8095"}]},
                    "RestartPolicy": {"Name": "unless-stopped"}},
                "NetworkSettings": {"Networks": {"ngs-filex-local_writer_loopback": {}}},
                "Mounts": [{"Destination": "/canonical", "Type": "bind", "Source": str(m.PROJECT_PATH / "concept"), "RW": True},
                           {"Destination": "/audio", "Type": "bind", "Source": str(m.PROJECT_PATH / "audio"), "RW": True},
                           {"Destination": "/run/secrets", "Type": "volume", "Name": "ngs-concept-writer-secret", "RW": False}]}

    def current(self):
        return copy.deepcopy(self.row)

    def image(self, ref):
        return ref

    def selected_image(self, active):
        return m.selector(self.control)[0] if active else self.base_image

    def healthy(self, row):
        if row["Image"] == NEW and not self.candidate_healthy:
            raise m.ApplyFailure("health", "TARGET_HEALTH_HTTP")
        return self.profiles[row["Image"]]

    def recreate(self, active):
        image = self.selected_image(active)
        self.calls.append((active, image))
        if self.fail_rollback and image == OLD:
            raise OSError("private fixture path")
        self.row = self.model(image, chr(ord("a") + len(self.calls)), active)
        if self.candidate_risky and image == NEW:
            self.row["HostConfig"]["Privileged"] = True
        if self.fail_after and image == NEW:
            raise subprocess.CalledProcessError(5, ["private fixture path"], output="never disclose")


class TestApply(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.home = Path(t.name).resolve()
        self.control = self.home / "Workspaces/OPS/runtime/deploy-control"
        self.control.mkdir(parents=True, mode=0o700)
        self.directory = m.target_directory(self.control)
        self.directory.mkdir(mode=0o700, parents=True)
        self.code = m.filex_code(self.home)
        self.code.mkdir(parents=True)
        for name in m.NAMES:
            (self.code / name).write_bytes(m.startup_bridge(self.home, self.control)
                                           if name == "start-filex.sh" else b"pinned contents")
        self.runtime = self.home / "Workspaces/OPS/runtime/managed-runtime/ops-runtime-config.json"
        self.runtime.parent.mkdir()
        self.runtime_doc = {"registeredFilex": {
            "schemaVersion": "ngs.ops-filex.v1", "container": "ngs-filex-local-filex-1",
            "codeRoot": str(self.code), "conceptWriter": True,
            "hashes": {key: m.digest((self.code / key).read_bytes()) for key in m.NAMES}}}
        m.atomic_write(self.runtime, m.json_bytes(self.runtime_doc))
        self.host = Host(self.home, self.control)
        self.proofs = self.control / "filex-writer-prepared"
        self.proofs.mkdir()
        self.prepared = {"schemaVersion": "ops.filex-writer-candidate.v1", "target": "filex-concept-writer",
                         "state": "image-verified", "source": {"headSha": SHA},
                         "provenance": {"artifactSha256": DIGEST}, "imageId": NEW, "base": BASE}
        self.candidate = {"schemaVersion": m.CANDIDATE, "target": m.TARGET, "sha": SHA,
                          "artifactSha256": DIGEST, "imageId": NEW,
                          **m.filex_prior(self.home, self.control, host=self.host)}
        self.policy = self.control / "filex-writer-preparation.json"
        m.atomic_write(self.policy, m.json_bytes({
            "schemaVersion": m.POLICY, "base": BASE, "runnerSha256": m.digest(Path(m.__file__).read_bytes()),
            **{k: self.candidate[k] for k in ("composeSha256", "startupSha256")},
        }))
        self.write_candidate()

    def write_candidate(self):
        m.atomic_write(self.proofs / (SHA + ".json"), m.json_bytes(self.prepared))
        for path in (self.directory / "candidate.json", self.proofs / (SHA + ".apply.json")):
            m.atomic_write(path, m.json_bytes(self.candidate))

    def apply(self):
        return m.apply_target(SHA, DIGEST, self.home, self.control, host=self.host)



    def configure_prop(self):
        directory=self.home/'canonical/props'
        directory.mkdir(parents=True,mode=0o700)
        scope={**m.PROP_VOLUME,'source':str(directory)}
        context=patch.multiple(m,PROP_SOURCE=str(directory),PROP_VOLUME=scope)
        context.start();self.addCleanup(context.stop)
        # Existing bridge function embeds the exact approved scope.
        bridge=self.code/'start-filex.sh';bridge.write_bytes(m.startup_bridge(self.home,self.control))
        override_slot=self.runtime_doc['registeredFilex']['hashes']
        override_slot['start-filex.sh']=m.digest(bridge.read_bytes())
        m.atomic_write(self.runtime,m.json_bytes(self.runtime_doc))
        self.candidate['startupSha256']=override_slot['start-filex.sh']
        policy=m.json_load(self.policy.read_bytes())
        policy['startupSha256']=self.candidate['startupSha256'];m.atomic_write(self.policy,m.json_bytes(policy))
        self.prepared['capabilitiesSha256']=m.PROP_CAP_DIGEST
        self.candidate['capabilitiesSha256']=m.PROP_CAP_DIGEST
        self.host.profiles[NEW]=m.PROP_CAP_DIGEST
        self.write_candidate()
        original=self.host.model
        def model(image,iid,active):
            row=original(image,iid,active)
            if active and m.selector(self.control)[1]==m.override_bytes(image,prop=True):
                row['Config']['Env'].extend(f'{k}={v}' for k,v in m.PROP_ENV.items())
                row['Mounts'].append({'Destination':'/props','Type':'bind','Source':str(directory),'RW':True,'Propagation':'rprivate'})
                row['HostConfig']['Mounts']=[{'Type':'bind','Source':str(directory),'Target':'/props','ReadOnly':False}]
            return row
        self.host.model=model
        return directory

    def test_exact_prop_profile_applies_same_writer_and_keeps_other_runtime_controls(self):
        directory=self.configure_prop()
        self.assertEqual(self.apply()['state'],'deployed')
        self.assertEqual(self.host.calls,[(True,NEW)])
        self.assertEqual(m.selector(self.control),(NEW,m.override_bytes(NEW,prop=True)))
        self.assertEqual([x for x in self.host.row['Mounts'] if x['Destination']=='/props'][0]['Source'],str(directory))
        proof=m.json_load((self.directory/'applied'/(SHA+'.json')).read_bytes())
        self.assertTrue(m.verify_active_candidate(self.home,self.control,proof,host=self.host))
        self.assertEqual((self.code/'compose.yaml').read_bytes(),b'pinned contents')

    def test_prop_mount_activation_fails_closed_without_extra_host_binding(self):
        directory=self.configure_prop()
        directory.rmdir()
        with self.assertRaises(m.ApplyFailure) as e:self.apply()
        self.assertEqual(e.exception.phase,'preflight')
        self.assertEqual(self.host.calls,[])
        self.assertFalse(m.selector_file(self.control).exists())

    def test_prop_runtime_drift_restores_original_image_and_absence_of_prop_mount(self):
        self.configure_prop()
        model=self.host.model
        def changed(image,iid,active):
            row=model(image,iid,active)
            if image==NEW:row['HostConfig']['UnexpectedOption']=True
            return row
        self.host.model=changed
        with patch.object(m.time,'sleep',lambda _x:None),patch.object(m.time,'monotonic',side_effect=(0,21,22)):
            with self.assertRaises(m.ApplyFailure) as e:self.apply()
        self.assertEqual(e.exception.rollback,'restored')
        self.assertEqual(self.host.calls,[(True,NEW),(False,OLD)])
        self.assertEqual(m.selector(self.control),(None,None))
        self.assertNotIn('/props',[x['Destination'] for x in self.host.row['Mounts']])

    def test_success_changes_only_owned_selector_writer_and_private_receipt(self):
        base = (self.code / "compose.yaml").read_bytes()
        register = self.runtime.read_bytes()
        proof = self.apply()
        self.assertEqual(self.host.calls, [(True, NEW)])
        self.assertEqual(proof, {"schemaVersion": "ops.deploy-target-receipt.v1", "target": m.TARGET,
                                 "sha": SHA, "deployedAt": proof["deployedAt"], "state": "deployed", "health": "verified"})
        private = m.json_load((self.directory / "applied" / (SHA + ".json")).read_bytes())
        self.assertTrue(m.verify_active_candidate(self.home, self.control, private, host=self.host))
        self.assertEqual((self.code / "compose.yaml").read_bytes(), base)
        self.assertEqual(self.runtime.read_bytes(), register)
        self.assertFalse((self.directory / "in-progress.json").exists())
        self.assertEqual(m.selector(self.control), (NEW, m.override_bytes(NEW)))

    def test_exact_new_capability_image_and_active_evidence(self):
        self.prepared["capabilitiesSha256"] = m.SEPARATION_CAP_DIGEST
        self.candidate["capabilitiesSha256"] = m.SEPARATION_CAP_DIGEST
        self.host.profiles[NEW] = m.SEPARATION_CAP_DIGEST
        self.write_candidate()
        self.apply()
        proof = m.json_load((self.directory/"applied"/(SHA+".json")).read_bytes())
        self.assertEqual(proof["capabilitiesSha256"],m.SEPARATION_CAP_DIGEST)
        self.assertTrue(m.verify_active_candidate(self.home,self.control,proof,host=self.host))
        self.host.profiles[NEW] = m.LEGACY_CAP_DIGEST
        with self.assertRaisesRegex(ValueError,"TARGET_CANDIDATE_MISMATCH"):
            m.verify_active_candidate(self.home,self.control,proof,host=self.host)

    def test_new_image_with_old_capabilities_rolls_back_to_exact_prior(self):
        self.prepared["capabilitiesSha256"] = m.SEPARATION_CAP_DIGEST
        self.candidate["capabilitiesSha256"] = m.SEPARATION_CAP_DIGEST
        self.host.profiles[OLD] = m.SEPARATION_CAP_DIGEST
        self.candidate["priorCapabilitiesSha256"] = m.SEPARATION_CAP_DIGEST
        self.write_candidate()
        with patch.object(m.time,"monotonic",side_effect=[0,21,22,43]):
            with self.assertRaises(m.ApplyFailure) as caught:self.apply()
        self.assertEqual((caught.exception.phase,caught.exception.rollback),("health","restored"))
        self.assertEqual(self.host.calls,[(True,NEW),(False,OLD)])
        self.assertEqual(m.filex_prior(self.home,self.control,host=self.host)["priorCapabilitiesSha256"],
                         m.SEPARATION_CAP_DIGEST)

    def test_maps_are_exact_not_allowlisted_subsets(self):
        self.assertEqual(m.capabilities_digest(m.CAPS),m.LEGACY_CAP_DIGEST)
        self.assertEqual(m.capabilities_digest(m.SEPARATION_CAPS),m.SEPARATION_CAP_DIGEST)
        for drift in ({"workflowAudioSeparateV1":False},{"workflowAudioSeparateV1":1},
                      {"workflowAudioSeparateV1":True,"unknown":True},{"workflowAudioDeleteV1":False}):
            self.assertIsNone(m.capabilities_digest({**m.CAPS,**drift}))

    def test_after_effect_command_failure_restores_legacy_selector_and_writer(self):
        self.host.fail_after = True
        with self.assertRaises(m.ApplyFailure) as err:
            self.apply()
        self.assertEqual((err.exception.phase, err.exception.code, err.exception.rollback),
                         ("start", "TARGET_APPLY_REFUSED", "restored"))
        self.assertEqual(self.host.calls, [(True, NEW), (False, OLD)])
        self.assertEqual(m.selector(self.control), (None, None))
        self.assertFalse((self.directory / "in-progress.json").exists())
        self.assertEqual(list((self.directory / "applied").glob("*.json")), [])

    def test_rollback_preserves_existing_override_exactly(self):
        m.atomic_write(m.selector_file(self.control), m.override_bytes(OLD))
        self.host.row = self.host.model(OLD, "a", True)
        self.host.fail_after = True
        with self.assertRaises(m.ApplyFailure) as err:
            self.apply()
        self.assertEqual(err.exception.rollback, "restored")
        self.assertEqual(self.host.calls, [(True, NEW), (True, OLD)])
        self.assertEqual(m.selector(self.control)[1], m.override_bytes(OLD))

    def test_failed_rollback_blocks_another_apply(self):
        self.host.fail_after = self.host.fail_rollback = True
        with self.assertRaises(m.ApplyFailure) as err:
            self.apply()
        self.assertEqual(err.exception.rollback, "failed")
        self.assertTrue((self.directory / "in-progress.json").exists())
        calls = self.host.calls[:]
        with self.assertRaises(m.ApplyFailure) as err:
            self.apply()
        self.assertEqual(err.exception.rollback, "not_attempted")
        self.assertEqual(self.host.calls, calls)

    def test_candidate_runtime_privilege_drift_is_not_healthy(self):
        self.host.candidate_risky = True
        with patch.object(m.time, "monotonic", side_effect=[0, 30, 30, 60]):
            with self.assertRaises(m.ApplyFailure) as err:
                self.apply()
        self.assertEqual((err.exception.phase, err.exception.rollback), ("health", "restored"))
        self.assertEqual(self.host.calls, [(True, NEW), (False, OLD)])

    def test_pin_and_candidate_version_mismatch_refuse_before_mutation(self):
        for name in ("start-filex.sh", "compose.yaml"):
            file = self.code / name
            body = file.read_bytes()
            file.write_bytes(b"changed")
            with self.assertRaises(m.ApplyFailure) as error:
                self.apply()
            self.assertEqual(error.exception.phase, "preflight")
            file.write_bytes(body)
        self.prepared["imageId"] = OLDER
        self.write_candidate()
        with self.assertRaises(m.ApplyFailure):
            self.apply()
        self.assertEqual(self.host.calls, [])
        self.assertEqual(m.selector(self.control), (None, None))

    def test_revoked_preparation_policy_blocks_a_stale_ready_candidate(self):
        self.policy.unlink()
        with self.assertRaises(m.ApplyFailure) as error:
            self.apply()
        self.assertEqual(error.exception.phase, "preflight")
        self.assertEqual(self.host.calls, [])
        self.assertEqual(m.selector(self.control), (None, None))

    def test_active_check_rejects_new_selector_without_live_match(self):
        self.apply()
        proof = m.json_load((self.directory / "applied" / (SHA + ".json")).read_bytes())
        self.host.row["Image"] = OLD
        with self.assertRaisesRegex(ValueError, "TARGET_CANDIDATE_MISMATCH"):
            m.verify_active_candidate(self.home, self.control, proof, host=self.host)

    def test_fixed_docker_commands_cannot_apply_other_roles(self):
        docker = m.DockerHost(self.home, self.control)
        calls = []
        docker.call = lambda *a, **_kw: calls.append(a)
        docker.recreate(True)
        self.assertEqual(calls[0][-6:], ("--no-deps", "--no-build", "--pull", "never", "--force-recreate", "concept-writer"))
        self.assertIn(str(m.selector_file(self.control)), calls[0])
        self.assertFalse(any(x in calls[0] for x in ("filex", "media-writer", "npc-writer", "motion-writer")))

    def test_startup_bridge_fixture_dispatch_preserves_other_scope_and_lock(self):
        script = m.startup_bridge(self.home, self.control).decode()
        inline = script.split("<<'FILEX_OPS_STARTUP'\n")[1].rsplit("FILEX_OPS_STARTUP\n")[0]
        calls = []
        with patch("subprocess.run", side_effect=lambda args, **kwargs: calls.append(args)):
            exec(compile(inline, "startup", "exec"), {})
            self.assertEqual(calls[0][-5:], ["filex", "npc-writer", "concept-writer", "motion-writer", "media-writer"])
            calls.clear()
            m.atomic_write(m.selector_file(self.control), m.override_bytes(NEW))
            exec(compile(inline, "startup", "exec"), {})
            self.assertEqual(calls[0][-4:], ["filex", "npc-writer", "motion-writer", "media-writer"])
            self.assertEqual(calls[1][-5:], ["--no-deps", "--no-build", "--pull", "never", "concept-writer"])
            self.assertIn(str(m.selector_file(self.control)), calls[1])
            calls.clear()
            m.atomic_write(self.directory / "in-progress.json", b"{}")
            with self.assertRaises(SystemExit):
                exec(compile(inline, "startup", "exec"), {})
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
