"""Scoped current motion preset documents and immutable MP4 assets, no delete API."""
import base64,hashlib,re
from concept_store import ConceptStore
from server import Conflict
class MotionStore(ConceptStore):
    namespace='canonical-files/binari/motion-presets/'
    def modern_concept_authority(self):
        # Motion preset documents have no concept-wide metadata index. The
        # shared ConceptStore batch transaction still handles their exact files.
        return False
    def path(self,key):
        if not isinstance(key,str) or not key.startswith(self.namespace):raise ValueError('namespace')
        relative=key[len(self.namespace):]
        if not re.fullmatch(r'presets/[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json',relative):raise ValueError('path')
        path=self.root
        for part in relative.split('/'):
            path=path/part
            if path.is_symlink():raise ValueError('symlink')
        return path
    def asset_path(self,aid):
        if not isinstance(aid,str)or not re.fullmatch(r'[a-f0-9]{64}\.mp4',aid):raise ValueError('asset id')
        path=self.internal('assets')/aid
        if path.is_symlink():raise ValueError('symlink')
        return path
    def asset(self,aid):
        with self.lock():
            data=self.asset_path(aid).read_bytes()
            if hashlib.sha256(data).hexdigest()!=aid[:-4]:raise Conflict('asset integrity')
            return {'assetId':aid,'sha256':aid[:-4],'bytes':len(data),'base64':base64.b64encode(data).decode()}
    def put_asset(self,aid,data):
        with self.lock():
            p=self.asset_path(aid)
            if len(data)<12 or data[4:8]!=b'ftyp' or hashlib.sha256(data).hexdigest()!=aid[:-4]:raise ValueError('asset invalid')
            if p.exists() and p.read_bytes()!=data:raise Conflict('asset collision')
            if not p.exists():self.write(p,data)
            return {'assetId':aid,'sha256':aid[:-4],'bytes':len(data)}
