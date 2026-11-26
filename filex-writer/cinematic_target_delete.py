"""Exact cinematic object removal using the common single-target writer move."""
import json
import re

from single_target_delete import PinnedDeleteRejected, SingleTargetDelete, validate_target
from server import Conflict

ROOT='canonical-files/binari/cinematics'
DIGEST=r'[a-f0-9]{64}'
OID=r'request\.official\.'+DIGEST
SOURCE=re.compile(r'sources/('+DIGEST+r')\.(json|png)')
SEQUENCE=re.compile(r'sequences/('+OID+r')')
RESULT=re.compile(r'results/('+OID+r')\.(json|mp4)')


class CinematicTargetDelete:
    def __init__(self,store):
        self.store=store

    def _marker(self,source):
        return self.store._path('sources', '.removed-'+source+'.json')

    def ensure_source_active(self,source):
        # Invoked while the existing cinematic writer lock is held.
        marker=self._marker(source)
        if marker.exists():raise Conflict('cinematic source retired')

    def _legacy(self,oid):
        from media_store import validate_cinematic_manifest,wire
        raw=self.store._read(self.store._path('results',oid+'.json'),8192)
        manifest=json.loads(raw)
        validate_cinematic_manifest(manifest)
        if manifest['officialRequestId']!=oid or wire(manifest)!=raw:
            raise ValueError('cinematic result ownership')
        return manifest

    def _uncorrelated_sources(self,source,owner):
        from media_store import source_entry,wire
        receipts=self.store.root/'.source-receipts'
        if receipts.is_symlink():raise ValueError('source receipt scope')
        if not receipts.exists():return
        for entry in receipts.iterdir():
            if not re.fullmatch(OID+r'\.json',entry.name):continue
            raw=self.store._read(entry,8192)
            m=source_entry(json.loads(raw))
            if wire(m)!=raw or entry.name!=m['officialRequestId']+'.json':
                raise ValueError('source receipt integrity')
            if m['sha256']==source and m['officialRequestId']!=owner:
                raise PinnedDeleteRejected('shared cinematic source; cannot delete scene')

    def _remaining_children(self,source):
        found=[]
        for folder in ('sequences','results'):
            path=self.store.root/folder
            if path.is_symlink():raise ValueError('cinematic child scope')
            if not path.exists():continue
            for entry in path.iterdir():
                if folder=='sequences' and re.fullmatch(OID,entry.name):
                    _,manifest=self.store._playback_base(entry.name)
                elif folder=='results' and re.fullmatch(OID+r'\.json',entry.name):
                    manifest=self._legacy(entry.stem)
                elif folder=='results' and re.fullmatch(OID+r'\.mp4',entry.name):
                    # An interrupted/legacy write with no sidecar cannot be
                    # safely assigned to a scene. Do not guess from filenames.
                    if not entry.with_suffix('.json').is_file():
                        raise PinnedDeleteRejected('unbound cinematic result file')
                    continue
                else:continue
                if manifest['sourceSha256']==source:
                    if folder=='sequences':
                        found.append({'root':ROOT,'path':'sequences/'+entry.name,'kind':'directory'})
                    else:
                        video=self.store._path('results',entry.stem+'.mp4')
                        if video.exists():
                            found.append({'root':ROOT,'path':'results/'+video.name,'kind':'file'})
                        found.append({'root':ROOT,'path':'results/'+entry.name,'kind':'file'})
        return sorted(found,key=lambda t:(t['path'].split('/')[0]!='sequences',t['path'].rsplit('.',1)[0],not t['path'].endswith('.mp4')))

    def inventory(self,source):
        from media_store import CINEMATIC_MAX,source_entry,source_image,wire
        if type(source)is not str or not re.fullmatch(DIGEST,source):raise ValueError('cinematic delete source')
        with self.store.lock():
            marker=self._marker(source)
            metadata=self.store._path('sources',source+'.json')
            image=self.store._path('sources',source+'.png')
            raw=self.store._read(metadata if metadata.exists() else marker,8192)
            m=source_entry(json.loads(raw))
            if wire(m)!=raw or m['sha256']!=source:raise PinnedDeleteRejected('cinematic source alias changed')
            source_image(self.store._read(image,CINEMATIC_MAX),m)
            self._uncorrelated_sources(source,m['officialRequestId'])
            targets=self._remaining_children(source)
            if metadata.exists():targets.append({'root':ROOT,'path':'sources/'+metadata.name,'kind':'file'})
            targets.append({'root':ROOT,'path':'sources/'+image.name,'kind':'file'})
            return {'sourceSha256':source,'targets':targets}

    def remove(self,command):
        from media_store import CINEMATIC_MAX,source_entry,source_image,wire
        if type(command)is not dict or set(command)!={'operationId','target','expected'}:raise ValueError('cinematic delete command')
        t=validate_target(command['target']);expected=command['expected']
        if t['root']!=ROOT or type(expected)is not dict or set(expected)!={'sourceSha256'}:
            raise ValueError('cinematic delete pin')
        source=expected['sourceSha256']
        if type(source)is not str or not re.fullmatch(DIGEST,source):raise ValueError('cinematic delete source')
        path=t['path']
        alias=SOURCE.fullmatch(path) if t['kind']=='file' else None
        seq=SEQUENCE.fullmatch(path) if t['kind']=='directory' else None
        result=RESULT.fullmatch(path) if t['kind']=='file' else None
        if not (alias or seq or result) or alias and alias[1]!=source:raise ValueError('cinematic delete target')
        mover=SingleTargetDelete(self.store,ROOT)
        move={k:command[k] for k in ('operationId','target')}
        with self.store.lock():
            if seq:
                leaf=self.store._seq_dir(seq[1])
            else:
                folder,name=path.split('/')
                leaf=self.store._path(folder,name)
            # True replay is verified by the common move record; absence alone
            # must never be reported as successfully deleted.
            try:leaf.lstat()
            except FileNotFoundError:return mover.remove_locked(move)
            if seq:
                _,m=self.store._playback_base(seq[1])
                if m['sourceSha256']!=source:raise PinnedDeleteRejected('cinematic sequence source changed')
            elif result:
                m=self._legacy(result[1])
                if m['sourceSha256']!=source:raise PinnedDeleteRejected('cinematic result source changed')
                if result[2]=='mp4':self.store._result(result[1])
                else:
                    video=self.store._path('results',result[1]+'.mp4')
                    if video.exists():raise PinnedDeleteRejected('delete legacy video before its sidecar')
            elif alias[2]=='json':
                raw=self.store._read(leaf,8192)
                m=source_entry(json.loads(raw))
                if wire(m)!=raw or m['sha256']!=source:raise PinnedDeleteRejected('cinematic source alias changed')
                png=self.store._path('sources',source+'.png')
                source_image(self.store._read(png,CINEMATIC_MAX),m)
                self._uncorrelated_sources(source,m['officialRequestId'])
                if self._remaining_children(source):raise PinnedDeleteRejected('scene still has cinematic children')
                marker=self._marker(source)
                if marker.exists():
                    if self.store._read(marker,8192)!=raw:raise PinnedDeleteRejected('cinematic retirement marker changed')
                else:self.store.write(marker,raw)
            else:
                raw=self.store._read(self._marker(source),8192)
                m=source_entry(json.loads(raw))
                if wire(m)!=raw or m['sha256']!=source:raise PinnedDeleteRejected('cinematic retirement marker mismatch')
                if self.store._path('sources',source+'.json').exists():
                    raise PinnedDeleteRejected('delete source sidecar before its image')
                source_image(self.store._read(leaf,CINEMATIC_MAX),m)
                self._uncorrelated_sources(source,m['officialRequestId'])
                if self._remaining_children(source):raise PinnedDeleteRejected('scene still has cinematic children')
            return mover.remove_locked(move)
