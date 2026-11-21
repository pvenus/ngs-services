import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from owned_store import OwnedNPCStore
from npc_store import encoded
from server import Conflict

class SnapshotManifestIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = OwnedNPCStore(self.root)
        (self.root / 'npc').mkdir()
        (self.root / 'metadata.json').write_bytes(encoded({'characterOrder': ['npc']}))
        (self.root / 'npc/metadata.json').write_bytes(encoded({'id': 'npc'}))
        self.slots = {}
        for i in range(40):
            data = ('asset-' + str(i)).encode()
            aid = hashlib.sha256(data).hexdigest() + '.png'
            path = 'assets/' + str(i) + '.png'
            (self.root / 'npc/assets').mkdir(exist_ok=True)
            (self.root / 'npc' / path).write_bytes(data)
            self.slots[path] = {'assetId': aid}
        self.manifest = self.root / 'npc/.owned-assets.json'
        self.manifest.write_bytes(encoded({'slots': self.slots}))
    def tearDown(self):
        self.tmp.cleanup()
    def test_one_manifest_read_and_all_bytes_verified(self):
        with patch.object(self.store, 'manifests', wraps=self.store.manifests) as read:
            value = self.store.snapshot()
        self.assertEqual(read.call_count, 1)
        self.assertEqual(len(value['assets']), 40)
        (self.root / 'npc/assets/39.png').write_bytes(b'corrupt')
        with self.assertRaises(Conflict): self.store.snapshot()
    def test_no_stale_index_after_manifest_change(self):
        self.store.snapshot()
        del self.slots['assets/39.png']
        self.manifest.write_bytes(encoded({'slots': self.slots}))
        self.assertEqual(len(self.store.snapshot()['assets']), 39)
    def test_symlink_still_rejected(self):
        path = self.root / 'npc/assets/39.png'
        path.unlink()
        path.symlink_to(self.root / 'npc/assets/0.png')
        with self.assertRaises(ValueError): self.store.snapshot()
    def test_duplicate_preserves_first_lookup(self):
        result = self.store.asset_slot_paths({'first': {'slots': {'one': {'assetId': 'same'}}}, 'second': {'slots': {'two': {'assetId': 'same'}}}})
        self.assertEqual(result, {'same': 'first/one'})

if __name__ == '__main__': unittest.main()
