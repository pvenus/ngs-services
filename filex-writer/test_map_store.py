"""Aborted group writes and legacy commits in a private filesystem."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from map_store import encode,map_revision
from media_store import CinematicProjectStore
from server import Conflict

class MapGroupStoreTest(TestCase):
    def setUp(self):
        tmp=TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root=Path(tmp.name)/'cinematics'; root.mkdir()
        self.store=CinematicProjectStore(root)
        self.mid='request.official.'+'a'*64
        self.base=self.store.root/'maps'/self.mid
        self.graph={'title':'굿터','regions':[{'id':'yard'}],'startRegionId':'yard','connections':[]}

    def state(self,n,**extra):
        return {'schemaVersion':'ngs.map-metadata.v1','projectId':'binari','mapId':self.mid,'revision':n,
                'officialRequestId':self.mid,'updatedAt':10,'graph':self.graph,**extra}

    def test_legacy_then_group_exact_replay_and_corrupt_parts(self):
        first=self.state(1)
        self.store.write(self.base/'1.json',encode(first))
        self.assertEqual(map_revision(self.store,first),first)
        next=self.state(2)
        self.assertEqual(map_revision(self.store,next,True),next)
        self.assertEqual((self.base/'2.collisions.json').read_bytes(),b'{}\n')
        files={f.name:f.read_bytes() for f in self.base.iterdir()}
        self.assertEqual(map_revision(self.store,{**next,'updatedAt':99},True),next)
        self.assertEqual({f.name:f.read_bytes() for f in self.base.iterdir()},files)
        (self.base/'2.start.json').write_bytes(b'{"startRegionId": "yard"}\n')
        with self.assertRaises(ValueError):map_revision(self.store,next)
        (self.base/'2.start.json').unlink()
        with self.assertRaises(ValueError):map_revision(self.store,next)

    def test_orphaned_parts_are_invisible_and_later_write_can_replace_them(self):
        first=self.state(1)
        write=self.store.write
        def interrupt(path,raw):
            if path.name=='1.json':raise OSError('injected interrupt before manifest')
            write(path,raw)
        with patch.object(self.store,'write',side_effect=interrupt):
            with self.assertRaises(OSError):map_revision(self.store,first,True)
        self.assertTrue((self.base/'1.start.json').exists())
        self.assertFalse((self.base/'1.json').exists())
        with self.assertRaises(FileNotFoundError):map_revision(self.store,first)
        with self.assertRaises(Conflict):map_revision(self.store,self.state(2),True)
        replacement=self.state(1,graph={**self.graph,'regions':[{'id':'gate'}],'startRegionId':'gate','colliders':[]})
        self.assertEqual(map_revision(self.store,replacement,True),replacement)
        self.assertEqual((self.base/'1.start.json').read_bytes(),b'{"startRegionId":"gate"}\n')
        self.assertEqual((self.base/'1.collisions.json').read_bytes(),b'{"colliders":[]}\n')

    def test_same_revision_race_commits_exactly_one_group(self):
        states=[self.state(1,graph={**self.graph,'title':str(i)}) for i in range(2)]
        def publish(state):
            try:return map_revision(self.store,state,True)
            except Conflict:return None
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(publish,states))
        self.assertEqual(sum(x is not None for x in results),1)
        self.assertEqual(map_revision(self.store,states[0]),next(x for x in results if x is not None))

    def test_perspective_survives_same_revision_reads_and_rejects_invalid_numbers(self):
        profile={'near':{'point':{'x':486,'y':645},'actorHeight':376,'viewWidth':872},
                 'far':{'point':{'x':308,'y':366},'actorHeight':87,'viewWidth':543}}
        good=self.state(1,graph={**self.graph,'regions':[{'id':'yard','perspective':profile}]})
        self.assertEqual(map_revision(self.store,good,True),good)
        self.assertEqual(map_revision(self.store,good),good)
        bad={**profile,'near':{**profile['near'],'viewWidth':True}}
        invalid=self.state(2,graph={**self.graph,'regions':[{'id':'yard','perspective':bad}]})
        with self.assertRaises(ValueError):map_revision(self.store,invalid,True)
        cleared=self.state(2,graph={**self.graph,'regions':[{'id':'yard','perspective':None}]})
        self.assertEqual(map_revision(self.store,cleared,True),cleared)
