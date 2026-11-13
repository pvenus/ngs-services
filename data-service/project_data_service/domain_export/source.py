"""Read-only legacy source adapter; never constructs Store/KnowledgeStore databases."""
import importlib.util
import json
import os
import re
import sqlite3
import sys
import stat as stat_module
import threading
from contextlib import contextmanager, ExitStack
from pathlib import Path
from urllib.parse import quote
from .core import MirrorError, canonical, digest, sha


STATIC = {
    'npc-profile-catalog': 'npc_profile_catalog.json',
    'docs.MasterConceptAuthoringGuide': 'docs/MasterConceptAuthoringGuide.md',
    'docs.MasterConcept.schema': 'docs/MasterConcept.schema.json',
    'docs.MasterConcept.template': 'docs/MasterConcept.template.json',
    'docs.MasterConcept.ProjectBS.rev31': 'docs/examples/MasterConcept.ProjectBS.rev31.json',
    'docs.NpcNumericProfileGuide': 'docs/NpcNumericProfileGuide.md',
    'docs.NpcNumericProfile.schema': 'docs/NpcNumericProfile.schema.json',
    'asset.vinus-mark': 'assets/vinus-mark.png',
    'asset.vinus-logo-original': 'assets/vinus-logo-original.png',
    'relationship-icon-manifest': 'assets/relationships/manifest.json',
}
for _name in ('goodwill', 'trust', 'vigilance', 'fear', 'hostility', 'unknown'):
    for _ext in ('svg', 'png'):
        STATIC['asset.relationship.' + _name + '.' + _ext] = 'assets/relationships/' + _name + '.' + _ext


class LegacySource:
    def __init__(self, registered_root, domain_project='ProjectBS'):
        self.root = Path(registered_root).absolute()
        self.project = domain_project
        # Root is owner registry configuration, never Site/user payload.
        if self.root.is_symlink(): raise MirrorError('SOURCE_SYMLINK_DENIED')
        self._observations = None
        self._cohort_reads = None
        self._cohort_paths = set()
        self._cohort_gate = threading.Lock()
        self._cohort_owner = None

    def read_file(self, relative, limit=-1):
        parts = Path(relative).parts
        if Path(relative).is_absolute() or not parts or any(p in ('..', '.', '') for p in parts):
            raise MirrorError('SOURCE_PATH_DENIED')
        key = str(relative)
        retained = self._cohort_reads is not None and key in self._cohort_paths
        if retained and self._cohort_owner != threading.get_ident():
            raise MirrorError('CONCURRENT_READ_COHORT_DENIED')
        cached = self._cohort_reads.get(key) if retained else None
        fd, stream = cached if cached else (None, None)
        held = cached is not None
        try:
            if not cached:
                fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                for part in parts[:-1]:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.close(fd); fd = child
                leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                stream = os.fdopen(leaf, 'rb'); stream.__enter__()
                if retained:
                    self._cohort_reads[key] = (fd, stream); held = True
            stat = os.fstat(stream.fileno())
            fingerprint=lambda v:(v.st_dev,v.st_ino,v.st_size,v.st_mtime_ns,v.st_ctime_ns,v.st_mode,v.st_nlink)
            before=fingerprint(stat)
            previous=self._observations.get(key) if self._observations is not None else None
            if cached and previous is not None and previous != before: raise MirrorError('SOURCE_CHANGED_DURING_CAPTURE')
            if stat.st_nlink != 1: raise MirrorError('SOURCE_HARDLINK_DENIED')
            if not stat_module.S_ISREG(stat.st_mode): raise MirrorError('SOURCE_FILE_TYPE_DENIED')
            if self._observations is not None:
                previous=self._observations.get(key)
                if previous is not None and previous != before: raise MirrorError('SOURCE_CHANGED_DURING_CAPTURE')
                self._observations[key]=before
            if cached: stream.seek(0)
            raw=stream.read(limit)
            after=fingerprint(os.fstat(stream.fileno()))
            current=fingerprint(os.stat(parts[-1],dir_fd=fd,follow_symlinks=False))
            if before != after or before != current: raise MirrorError('SOURCE_CHANGED_DURING_READ')
            return raw
        finally:
            if not held:
                try:
                    if stream is not None: stream.__exit__(None, None, None)
                finally:
                    if fd is not None: os.close(fd)

    def _close_cohort_reads(self):
        entries = self._cohort_reads or {}
        self._cohort_reads = None; self._cohort_paths = set(); self._cohort_owner = None
        with ExitStack() as cleanup:
            for fd, stream in entries.values():
                cleanup.callback(os.close, fd); cleanup.callback(stream.__exit__, None, None, None)

    def connect(self, filename):
        header=self.read_file(filename,20)
        if len(header)>=20 and header[18:20] != bytes((1,1)): raise MirrorError('GLOBAL_READ_FENCE_REQUIRED_FOR_WAL')
        path = self.root / filename
        if path.is_symlink() or path.stat().st_nlink != 1: raise MirrorError('SOURCE_LINK_DENIED')
        connection = sqlite3.connect('file:' + quote(str(path), safe='/') + '?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON'); connection.execute('BEGIN')
        return connection

    @contextmanager
    def read_cohort(self):
        """Hold shared read locks for all rollback-journal source databases.

        Existing source uses SQLite rollback journals. WAL permits concurrent
        writers despite read locks, so refuse it without an external write fence.
        No BEGIN IMMEDIATE, schema creation or write transaction is performed.
        """
        if not self._cohort_gate.acquire(blocking=False):
            raise MirrorError('CONCURRENT_READ_COHORT_DENIED')
        filenames = []
        for filename in ('concept.sqlite3','story.sqlite3','npc.sqlite3','knowledge.sqlite3'):
            if filename=='concept.sqlite3':continue
            filenames.append(filename)
        self._cohort_reads = {}; self._cohort_paths = set(filenames); self._cohort_owner = threading.get_ident()
        with ExitStack() as cleanup:
            cleanup.callback(self._cohort_gate.release)
            # Raw DB descriptors are opened BEFORE SQLite shared locks and closed
            # AFTER every SQLite connection. Closing any raw FD for a locked inode
            # during capture can release process-owned POSIX locks.
            cleanup.callback(self._close_cohort_reads)
            for filename in filenames: self.read_file(filename, 0)
            for filename in filenames:
                db = self.connect(filename); cleanup.callback(db.close)
                if db.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal':
                    raise MirrorError('GLOBAL_READ_FENCE_REQUIRED_FOR_WAL')
                db.execute('SELECT count(*) FROM sqlite_schema').fetchone()
            yield

    def module(self, filename):
        from ..domain_contracts import legacy_http,npc_detail,combat_profile
        return {'server.py':legacy_http,'npc_detail.py':npc_detail,'combat_profile.py':combat_profile}[filename]

    def knowledge_entry_revision(self, entry_id, version):
        """Actual retained source body only; never synthesize a missing version.

        Legacy update stores the PREVIOUS body under its previous version. Row
        saved_at/note describe the history write, not that body's publication.
        """
        db=self.connect('knowledge.sqlite3')
        try:
            row=db.execute("SELECT r.version,r.saved_at,r.note,r.snapshot_json FROM entry_revisions r JOIN entries e ON e.id=r.entry_id JOIN categories c ON c.id=e.category_id WHERE r.entry_id=? AND r.version=? AND c.project_key=?",(entry_id,version,self.project)).fetchone()
            if row is None: return None
            entry=json.loads(row['snapshot_json'])
            if not isinstance(entry,dict) or entry.get('id')!=entry_id or entry.get('version')!=version:
                raise MirrorError('SOURCE_HISTORY_INVALID')
            return {'entry':entry,'version':version,'historySavedAt':row['saved_at'],'historyNote':row['note'],
                    'provenance':{'storage':'entry_revisions.snapshot_json','historyTimestampSemantics':'history row write time; not body publication time'}}
        finally: db.close()

    def _capture(self):
        resources, vector, connections = {}, {}, []
        def document(identity, value):
            resources[identity] = {'kind': 'document', 'mime': 'application/json', 'bytes': canonical(value)}
        try:
            for identity, filename in (('concept', 'concept.sqlite3'), ('story', 'story.sqlite3'), ('npcs', 'npc.sqlite3')):
                if identity=='concept':
                    api=self.concept_api;history=api('GET','/api/revisions',None)['revisions']
                    rows=[{'revision':e['revision'],'saved_at':e['savedAt'],'note':e['note'],'document':json.dumps(e['document'],ensure_ascii=False)} for e in [api('GET','/api/revisions/'+str(r['revision']),None) for r in history]]
                else:
                    db = self.connect(filename); connections.append(db)
                    rows = db.execute('SELECT revision,saved_at,note,document FROM revisions ORDER BY revision DESC').fetchall()
                if not rows: raise MirrorError('EMPTY_SOURCE')
                envelopes = [{'revision': r['revision'], 'savedAt': r['saved_at'], 'note': r['note'], 'document': json.loads(r['document'])} for r in rows]
                if envelopes[0]['document'].get('projectId') != self.project: raise MirrorError('SOURCE_PROJECT_MISMATCH')
                document(identity, envelopes[0]); vector[identity] = rows[0]['revision']
                document(identity + '.revisions', {'revisions': [{k: e[k] for k in ('revision', 'savedAt', 'note')} for e in envelopes[:100]]})
                # All historic source JSON remains available for revision inspection.
                for envelope in envelopes: document(identity + '.revision.' + str(envelope['revision']), envelope)
            db = self.connect('knowledge.sqlite3'); connections.append(db)
            # Import inspected source methods but bypass __init__ (schema/bootstrap/backfill writes).
            from ..domain_contracts import knowledge_catalog as module
            store = object.__new__(module.KnowledgeStore); store.connection = db
            overview = store.overview(self.project)
            if not overview: raise MirrorError('KNOWLEDGE_PROJECT_MISMATCH')
            document('knowledge.overview', overview); vector['knowledge'] = overview['revision']
            for category in db.execute('SELECT id FROM categories WHERE project_key=? ORDER BY sort_order,id', (self.project,)):
                document('knowledge.category.' + category['id'], store.category(category['id']))
                for entry in db.execute('SELECT id FROM entries WHERE category_id=? ORDER BY id', (category['id'],)):
                    identity = entry['id']; value = store.entry(identity)
                    document('knowledge.entry.' + identity, value)
                    history=store.revisions(identity)
                    retained={r['version'] for r in history}
                    document('knowledge.entry-revisions.' + identity, {'revisions':[{**r,'bodyAvailability':'source_retained','bodyResourceId':'knowledge.entry-revision.'+identity+'.'+str(r['version'])} for r in history],'currentVersion':value['version'],'currentBodyAvailable':True,'unavailableHistoricalVersions':[v for v in range(1,value['version']) if v not in retained],'bodyPolicy':'Only retained source snapshot_json bodies are available; absent versions are unavailable.'})
                    for revision in history:
                        body=self.knowledge_entry_revision(identity,revision['version'])
                        if body is None: raise MirrorError('SOURCE_HISTORY_CHANGED_DURING_CAPTURE')
                        document('knowledge.entry-revision.'+identity+'.'+str(revision['version']),body)
            document('knowledge.mcp-capabilities', {'capabilities': store.capabilities(self.project)})
            document('knowledge.legacy-references', {'references': store.legacy_references(self.project)})
        finally:
            for db in connections: db.close()
        npc_envelope = json.loads(resources['npcs']['bytes'])
        combat = self.module('combat_profile.py')
        detail = self.module('npc_detail.py')
        validation = self.module('server.py').validate_npcs
        for character in npc_envelope['document']['characters']:
            identity = character['id']
            document('npcs.combat-profile.' + identity, combat.combat_profile(npc_envelope, identity))
            document('npcs.detail.' + identity, detail.npc_detail(npc_envelope, identity, validation))
        seed = json.loads(self.read_file('story_seed.json'))
        for identity, value in seed.get('sources', {}).items(): document('story.source.' + identity, value)
        static = dict(STATIC)
        for character in json.loads(resources['npcs']['bytes'])['document']['characters']:
            for portrait in character.get('portraits', []):
                identity = portrait['assetId']
                if not re.fullmatch(r'[0-9a-f]{64}\.(png|jpg|jpeg|webp)', identity): raise MirrorError('PORTRAIT_ID_DENIED')
                static['asset.' + identity] = 'assets/portraits/' + identity
        for identity, relative in static.items():
            raw = self.read_file(relative)
            if identity.startswith('asset.'):
                ext = Path(relative).suffix
                mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.svg': 'image/svg+xml'}[ext]
                resources[identity] = {'kind': 'asset', 'assetId': identity.removeprefix('asset.'), 'mime': mime, 'bytes': raw}
            else:
                if relative.endswith('.json'): document(identity, json.loads(raw))
                else:
                    raw.decode('utf-8')
                    resources[identity] = {'kind': 'document', 'mime': 'text/markdown', 'bytes': raw}
        # Hash vector includes all source bytes, including static/catalog assets.
        vector['sourceContent'] = digest({k: sha(v['bytes']) for k, v in sorted(resources.items())})
        return {'resources': resources, 'sourceRevisionVector': vector, 'consistency':{'database':'rollback-journal-read-cohort','static':'file-identity-and-repeated-hashes','globalAtomic':False,'history':'retained-source-bodies-only'}, 'gaps': [
            'Browser rendering and HTTP endpoint roundtrip not verified (service stopped).',
            'Rollback-journal DB cohort only: per-file identity/mtime/ctime/size checks and repeated full hashes detect observed static drift; no common static writer fence or global atomicity guarantee.',
            'Knowledge historical bodies include only actual retained snapshot_json records; row timestamps are history-write times, not publication times.',
        ]}

    def capture(self):
        if self._observations is not None: raise MirrorError('CONCURRENT_CAPTURE_DENIED')
        # Observe DB path identities BEFORE cohort acquisition as well. A path
        # replaced while the old inode is locked must not enter a new connection
        # under a false cohort claim. Drift during acquisition is rejected.
        self._observations={}
        try:
            with self.read_cohort():
                first, second = self._capture(), self._capture()
                if first['sourceRevisionVector'] != second['sourceRevisionVector']: raise MirrorError('SOURCE_CHANGED_DURING_CAPTURE')
                for relative in list(self._observations): self.read_file(relative,0)
                return second
        finally: self._observations=None
