import copy
import json
import unittest

from filex_unified_runtime import (NAME, PROJECT, VOLUMES, CutoverFailure, compose,
                                   cutover, guard_mounts, registry)
from prepare_ngs_release import Refused

IMAGE = 'sha256:' + 'f'*64
REGISTRY = json.dumps({'schemaVersion':'ngs.filex-writer-scopes.v1','scopes':[
    {'id':i, 'folder':folder, 'credential':key, 'profile':i}
    for i,folder,key in [('npc','npcs','npc'),('concept','concept','concept'),
                       ('motion','motion-presets','motion'),('media','media-artifacts','media'),
                       ('audio','audio','concept'),('props','props','concept')]]})


def container():
    o = compose(IMAGE, REGISTRY)
    service = o['services'][NAME]
    return {'Name':'/'+PROJECT+'-'+NAME+'-1','Image':IMAGE,
            'Config':{'Labels':{'com.docker.compose.project':PROJECT,'com.docker.compose.service':NAME},
                      'User':'501:20','Cmd':['python','-B','/app/server.py'],'WorkingDir':'/app','Env':[]},
            'HostConfig':{'ReadonlyRootfs':True,'Privileged':False,'CapDrop':['ALL'],
                          'SecurityOpt':['no-new-privileges:true'],'RestartPolicy':{'Name':'unless-stopped'},
                          'PortBindings':{'8093/tcp':[{'HostIp':'127.0.0.1','HostPort':'8095'}]}},
            'NetworkSettings':{'Networks':{PROJECT+'_writer_loopback':{}}},
            'Mounts':[{'Type':x['type'],'Destination':x['target'],'Source':x['source'],
                       'Name':o['volumes'][x['source']]['name'] if x['type']=='volume' else '',
                       'RW':not x['read_only']} for x in service['volumes']]}


class Fake:
    def __init__(self, fail=None):
        self.calls=[]
        self.pds='legacy'
        self.writers='legacy'
        self.failed=False
        self.fail=fail
        self.journal=False

    def __getattr__(self, method):
        def action(*args):
            self.calls.append(method)
            if method=='arm': self.journal=True
            if method=='pause_backend': self.pds=None
            if method in ('stop_legacy','stop_writer'):
                assert self.pds is None
                if method=='stop_legacy' or self.writers=='new': self.writers=None
            if method=='start_writer':
                assert self.pds is None and self.writers is None
                self.writers='new'
            if method=='restore_legacy':
                assert self.pds is None and self.writers in (None,'legacy')
                self.writers='legacy'
            if method=='start_backend': self.pds=self.writers
            if method=='commit': assert self.pds==self.writers=='new'
            if method in ('commit','clear_rolled_back'): self.journal=False
            if method==self.fail and not self.failed:
                self.failed=True
                raise TimeoutError('ambiguous applied/response lost')
            return object()
        return action


class UnifiedFilex(unittest.TestCase):
    def test_generated_scopes_and_four_readonly_existing_tokens(self):
        o=compose(IMAGE,REGISTRY)
        self.assertEqual(set(o['services']),{NAME})
        self.assertEqual({x['name'] for x in o['volumes'].values()},set(VOLUMES.values()))
        self.assertEqual(sum(x['type']=='bind' for x in o['services'][NAME]['volumes']),6)
        guard_mounts(container(), REGISTRY)

    def test_scope_path_ambiguity_rejected(self):
        r=json.loads(REGISTRY)
        for v in ('../anything','npcs','bad folder'):
            a=copy.deepcopy(r)
            a['scopes'][5]['folder']=v
            with self.assertRaises(Refused):
                registry(json.dumps(a))

    def test_live_container_guard_refuses_extra_wrong_or_wider_mounts(self):
        for change in (
          lambda m:m['Mounts'].append({'Destination':'/canonical','Type':'bind','Source':'/','RW':True}),
          lambda m:m['Mounts'][0].update(Source='/other/npcs'),
          lambda m:m['Mounts'][-1].update(RW=True),
          lambda m:m['HostConfig']['PortBindings']['8093/tcp'][0].update(HostIp='0.0.0.0'),
          lambda m:m['Config']['Env'].append('WRITER_SCOPE=concept'),
        ):
            m=container()
            change(m)
            with self.assertRaises(Refused):
                guard_mounts(m,REGISTRY)

    def test_success_proves_all_dependent_health_then_records(self):
        h=Fake()
        cutover(h,{})
        self.assertEqual((h.pds,h.writers,h.journal),('new','new',False))
        self.assertEqual(h.calls, ['capture','arm','pause_backend','prove_backend_held','stop_legacy',
                                   'prove_writers_held','select_writer','start_writer','prove_writer',
                                   'select_backend','start_backend','prove_backend','prove_writer','commit'])

    def test_ambiguous_stops_or_new_health_failure_restore_exact_old_state(self):
        for action in ('pause_backend','stop_legacy','start_writer','start_backend','prove_backend'):
            with self.subTest(action=action):
                h=Fake(action)
                with self.assertRaises(CutoverFailure) as thrown:
                    cutover(h,{})
                self.assertEqual(thrown.exception.rollback,'restored')
                self.assertEqual((h.pds,h.writers,h.journal),('legacy','legacy',False))
                self.assertLess(h.calls.index('prove_successor_held'),h.calls.index('restore_legacy'))

    def test_no_old_writer_started_if_candidate_stop_unproved(self):
        h=Fake('start_backend')
        def denied(*args):
            raise ValueError('still holding the listener')
        h.stop_writer=denied
        with self.assertRaises(CutoverFailure) as thrown:
            cutover(h,{})
        self.assertEqual(thrown.exception.rollback,'failed')
        self.assertEqual((h.pds,h.writers,h.journal),(None,'new',True))
        self.assertNotIn('restore_legacy',h.calls)
