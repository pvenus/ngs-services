"""Local master concept editor for NPC production. Python standard library only."""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote
from .profile_model import TRAIT_GROUPS, validate_profile

from .npc_detail import npc_detail, NpcDetailError
from .combat_profile import combat_profile, encode_profile, etag_matches, CombatProfileError

ROOT = Path(__file__).resolve().parent
SEED = ROOT / "seed.json"
STORY_SEED = ROOT / "story_seed.json"
NPC_SEED = ROOT / "npc_seed.json"
MAX_BODY = 512 * 1024
MAX_STORY_BODY = 2 * 1024 * 1024
MAX_PORTRAIT_BODY = 16 * 1024 * 1024
PORTRAIT_ROOT = ROOT / "assets" / "portraits"
LOCK = threading.Lock()
ALLOWED_STATUS = {"source", "proposal", "open", "confirmed"}
ASSETS = {
    "/assets/relationships/goodwill.svg": ("assets/relationships/goodwill.svg", "image/svg+xml"),
    "/assets/relationships/goodwill.png": ("assets/relationships/goodwill.png", "image/png"),
    "/assets/relationships/trust.svg": ("assets/relationships/trust.svg", "image/svg+xml"),
    "/assets/relationships/trust.png": ("assets/relationships/trust.png", "image/png"),
    "/assets/relationships/vigilance.svg": ("assets/relationships/vigilance.svg", "image/svg+xml"),
    "/assets/relationships/vigilance.png": ("assets/relationships/vigilance.png", "image/png"),
    "/assets/relationships/fear.svg": ("assets/relationships/fear.svg", "image/svg+xml"),
    "/assets/relationships/fear.png": ("assets/relationships/fear.png", "image/png"),
    "/assets/relationships/hostility.svg": ("assets/relationships/hostility.svg", "image/svg+xml"),
    "/assets/relationships/hostility.png": ("assets/relationships/hostility.png", "image/png"),
    "/assets/relationships/unknown.svg": ("assets/relationships/unknown.svg", "image/svg+xml"),
    "/assets/relationships/unknown.png": ("assets/relationships/unknown.png", "image/png"),
    "/assets/relationships/manifest.json": ("assets/relationships/manifest.json", "application/json"),
    "/episode_flow.js": ("episode_flow.js", "text/javascript"),
    "/npc_profile_catalog.json": ("npc_profile_catalog.json", "application/json"),
    "/npc_profiles.js": ("npc_profiles.js", "text/javascript"),
    "/docs/NpcNumericProfileGuide.md": ("docs/NpcNumericProfileGuide.md", "text/plain"),
    "/docs/NpcNumericProfile.schema.json": ("docs/NpcNumericProfile.schema.json", "application/json"),
    "/": ("index.html", "text/html"),
    "/app.js": ("app.js", "text/javascript"),
    "/style.css": ("style.css", "text/css"),
    "/docs.html": ("docs.html", "text/html"),
    "/docs.js": ("docs.js", "text/javascript"),
    "/story.html": ("story.html", "text/html"),
    "/story.js": ("story.js", "text/javascript"),
    "/story.css": ("story.css", "text/css"),
    "/npcs.html": ("npcs.html", "text/html"),
    "/npcs.js": ("npcs.js", "text/javascript"),
    "/npcs.css": ("npcs.css", "text/css"),
    "/assets/vinus-mark.png": ("assets/vinus-mark.png", "image/png"),
    "/assets/vinus-logo-original.png": ("assets/vinus-logo-original.png", "image/png"),
    "/docs/MasterConceptAuthoringGuide.md": ("docs/MasterConceptAuthoringGuide.md", "text/plain"),
    "/docs/MasterConcept.schema.json": ("docs/MasterConcept.schema.json", "application/json"),
    "/docs/MasterConcept.template.json": ("docs/MasterConcept.template.json", "application/json"),
    "/docs/examples/MasterConcept.ProjectBS.rev31.json": ("docs/examples/MasterConcept.ProjectBS.rev31.json", "application/json"),
}


def validate_summary(value):
    if isinstance(value, str):
        return 1 <= len(value.strip()) <= 600
    return (isinstance(value, dict) and set(value) == {"schemaVersion", "keywords", "sentence"}
            and value["schemaVersion"] == "npc-concept-summary.v1"
            and isinstance(value["keywords"], list) and 1 <= len(value["keywords"]) <= 12
            and all(isinstance(x, str) and x == x.strip() and 1 <= len(x) <= 100 for x in value["keywords"])
            and len(set(value["keywords"])) == len(value["keywords"])
            and isinstance(value["sentence"], str) and value["sentence"] == value["sentence"].strip()
            and 1 <= len(value["sentence"]) <= 600)


def validate_legacy(document: object) -> dict:
    if not isinstance(document, dict) or document.get("schemaVersion") != 1 or document.get("projectId") != "ProjectBS":
        raise ValueError("지원하지 않는 문서 형식입니다.")
    if not isinstance(document.get("title"), str) or not document["title"].strip() or len(document["title"]) > 120:
        raise ValueError("제목을 확인하세요.")
    sections = document.get("sections")
    if not isinstance(sections, list) or len(sections) != 8:
        raise ValueError("필수 영역 8개가 필요합니다.")
    section_priorities = [section.get("priority") for section in sections if isinstance(section, dict)]
    if len(section_priorities) != 8 or any(type(p) is not int for p in section_priorities) or sorted(section_priorities) != list(range(1, 9)):
        raise ValueError("영역 우선순위는 1부터 8까지 중복 없이 지정하세요.")
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    for section, expected in zip(sections, seed["sections"]):
        if not isinstance(section, dict) or section.get("id") != expected["id"]:
            raise ValueError("영역 ID 또는 순서를 변경할 수 없습니다.")
        fields = section.get("fields")
        if not isinstance(fields, list) or len(fields) != len(expected["fields"]):
            raise ValueError("필수 항목을 삭제할 수 없습니다.")
        field_priorities = [field.get("priority") for field in fields if isinstance(field, dict)]
        if len(field_priorities) != len(fields) or any(type(p) is not int for p in field_priorities) or sorted(field_priorities) != list(range(1, len(fields) + 1)):
            raise ValueError("항목 우선순위는 영역 안에서 중복 없이 지정하세요.")
        for field, original in zip(fields, expected["fields"]):
            if not isinstance(field, dict) or field.get("id") != original["id"]:
                raise ValueError("항목 ID 또는 순서를 변경할 수 없습니다.")
            if not isinstance(field.get("value"), str) or len(field["value"]) > 12000:
                raise ValueError("항목 내용은 12,000자 이내여야 합니다.")
            if field.get("status") not in ALLOWED_STATUS:
                raise ValueError("항목 상태가 올바르지 않습니다.")
            sources = field.get("sources")
            if not isinstance(sources, list) or len(sources) > 20 or any(not isinstance(s, str) or len(s) > 300 for s in sources):
                raise ValueError("출처 목록을 확인하세요.")
            if not isinstance(field.get("label"), str) or not field["label"].strip() or len(field["label"]) > 100:
                raise ValueError("항목 이름을 확인하세요.")
        for key in ("title", "description"):
            if not isinstance(section.get(key), str) or len(section[key]) > 200:
                raise ValueError("영역 설명을 확인하세요.")
    for section in sections:
        for item in [section] + section["fields"]:
            if "summary" in item and not validate_summary(item["summary"]):
                raise ValueError("요약 데이터 형식을 확인하세요.")
    if 'hierarchy' in document:
        from .concept_groups import validate_hierarchy
        validate_hierarchy(document['hierarchy'], [section['id'] for section in sections])
    return document


def validate(document: object) -> dict:
    from .concept_content_v2 import validate as validate_v2
    return validate_v2(document, json.loads(SEED.read_text(encoding='utf-8')), validate_legacy)


def story_seed() -> dict:
    return json.loads(STORY_SEED.read_text(encoding="utf-8"))


def validate_story(document: object) -> dict:
    from .story_groups import validate_library, upgrade
    if not isinstance(document, dict) or document.get('schemaVersion') != 1 or document.get('projectId') != 'ProjectBS' or document.get('actId') != 'act01':
        raise ValueError('지원하지 않는 스토리 문서 형식입니다.')
    with sqlite3.connect(ROOT / 'npc.sqlite3') as connection:
        row = connection.execute('SELECT document FROM revisions ORDER BY revision DESC LIMIT 1').fetchone()
    roster = json.loads(row[0]) if row else json.loads(NPC_SEED.read_text())
    return validate_library(upgrade(document), {c['id'] for c in roster['characters']})


NPC_TEXT_FIELDS = ("name", "kind", "role", "summary", "publicFace", "actualTruth", "desire", "fear", "values", "belief", "behavior", "voice", "arc", "relationships", "stateKeys", "openQuestions")
NPC_TRAIT_GROUPS = TRAIT_GROUPS


def validate_npcs(document: object, asset_reader=None) -> dict:
    if not isinstance(document, dict) or document.get("schemaVersion") != 1 or document.get("projectId") != "ProjectBS" or document.get("actId") != "act01":
        raise ValueError("지원하지 않는 NPC 문서 형식입니다.")
    for key in ("masterRevision", "storyRevision"):
        if type(document.get(key)) is not int or document[key] < 1:
            raise ValueError("상위 기획 개정을 확인하세요.")
    characters = document.get("characters")
    if not isinstance(characters, list) or len(characters) > 100:
        raise ValueError("NPC 목록은 최대 100명입니다.")
    seen = set()
    for character in characters:
        if not isinstance(character, dict) or not isinstance(character.get("id"), str) or not re.fullmatch(r"(?:character|npc|entity|enemy)\.[a-z0-9._-]{2,80}", character["id"]):
            raise ValueError("인물 ID는 character.*, npc.*, entity.*, enemy.* 형식이어야 합니다.")
        if character["id"] in seen:
            raise ValueError("인물 ID가 중복되었습니다.")
        seen.add(character["id"])
        if character.get("status") not in ("source", "proposal", "review", "confirmed"):
            raise ValueError("NPC 상태를 확인하세요.")
        for key in NPC_TEXT_FIELDS:
            if not isinstance(character.get(key), str) or len(character[key]) > 4000:
                raise ValueError(f"{character['id']}의 {key} 항목을 확인하세요.")
        if not character["name"].strip() or (not character["role"].strip() and character.get("creationBasis",{}).get("profileState")!="uninitialized"):
            raise ValueError("NPC 이름과 서사 역할은 필수입니다.")
        chapters = character.get("chapters")
        if not isinstance(chapters, list) or len(chapters) > 5 or any(ch not in ("chapter01", "chapter02", "chapter03", "chapter04", "chapter05") for ch in chapters) or len(set(chapters)) != len(chapters):
            raise ValueError("Chapter 연결을 확인하세요.")
        refs = character.get("sourceRefs")
        if not isinstance(refs, list) or len(refs) > 20 or any(not isinstance(ref, str) or len(ref) > 300 for ref in refs):
            raise ValueError("근거 문서 목록을 확인하세요.")
        traits = character.get("traits")
        if not isinstance(traits, dict) or set(traits) != set(NPC_TRAIT_GROUPS):
            raise ValueError("성향 대분류 4개가 필요합니다.")
        for group, keys in NPC_TRAIT_GROUPS.items():
            values = traits[group]
            if not isinstance(values, dict) or set(values) != set(keys) or any(value is not None and (type(value) is not int or not 0 <= value <= 100) for value in values.values()):
                raise ValueError(f"{group} 성향은 모든 축 각각 0~100 정수 또는 미설정이어야 합니다.")
        validate_profile(character)
        portraits = character.get("portraits")
        active = character.get("activePortraitId")
        if not isinstance(portraits, list):
            raise ValueError("포트레이트 목록을 확인하세요.")
        ids = set()
        for portrait in portraits:
            if not isinstance(portrait, dict) or set(portrait) != {"assetId", "label", "sourceRef"}:
                raise ValueError("포트레이트 정보를 확인하세요.")
            asset_id = portrait["assetId"]
            if not isinstance(asset_id, str) or not re.fullmatch(r"[0-9a-f]{64}\.(?:png|jpg|webp)", asset_id) or asset_id in ids:
                raise ValueError("포트레이트 파일 ID를 확인하세요.")
            if asset_reader is not None:
                asset_reader(asset_id)
            elif not (PORTRAIT_ROOT / asset_id).is_file():
                raise ValueError("등록된 포트레이트 파일을 찾을 수 없습니다.")
            ids.add(asset_id)
            if any(not isinstance(portrait[key], str) or len(portrait[key]) > 300 for key in ("label", "sourceRef")) or not portrait["label"].strip():
                raise ValueError("포트레이트 이름과 출처를 확인하세요.")
        from project_data_service.npc_animations import validate_slots
        validate_slots(character,asset_reader)
        from project_data_service.npc_animation_experiments import validate_experiments
        validate_experiments(character,asset_reader)
        slots = character.get('portraitSets', {})
        if not isinstance(slots, dict) or set(slots) - {'representative', 'combat', 'idle'}:
            raise ValueError('NPC_PORTRAIT_SLOTS_INVALID')
        for slot, pair in slots.items():
            # A workflow-owned original may be published before its paired pixel task.
            # Existing paired results retain their full contract.
            if slot in ('representative','idle','combat') and isinstance(pair,dict) and set(pair)=={'portraitAssetId','requestId'}:
                if (not isinstance(pair['portraitAssetId'],str) or not re.fullmatch(r'[a-f0-9]{64}\.png',pair['portraitAssetId'])
                    or pair['portraitAssetId'] not in ids or (slot=='representative' and active!=pair['portraitAssetId'])
                    or not isinstance(pair['requestId'],str) or not re.fullmatch(r'workflow\.task\.[a-f0-9]{64}',pair['requestId'])):
                    raise ValueError('NPC_PORTRAIT_REPRESENTATIVE_INVALID')
                continue
            if not isinstance(pair, dict) or set(pair) - {'pixelProvenance', 'pixelSourceAssetId'} != {'portraitAssetId', 'pixelAssetId', 'referencePortraitAssetId', 'requestId'}:
                raise ValueError('NPC_PORTRAIT_PAIR_INVALID')
            if pair['portraitAssetId'] not in ids or pair['pixelAssetId'] not in ids or pair['portraitAssetId'] == pair['pixelAssetId']:
                raise ValueError('NPC_PORTRAIT_PAIR_ASSETS_REQUIRED')
            provenance=pair.get('pixelProvenance')
            if provenance is not None:
                if not isinstance(provenance,dict) or set(provenance)!={'pipeline','postprocess'} or not isinstance(provenance['pipeline'],dict) or not isinstance(provenance['postprocess'],dict):
                    raise ValueError('NPC_PIXEL_PROVENANCE_INVALID')
                proof=provenance['pipeline']
                if proof.get('postprocessCompleted')is not True or proof.get('portraitSha256')!=pair['portraitAssetId'][:-4] or proof.get('referencePortraitSha256')!=pair['portraitAssetId'][:-4] or proof.get('pixelSha256')!=pair['pixelAssetId'][:-4] or proof.get('pixelSourceSha256')!=proof.get('postprocessInputSha256') or not isinstance(proof.get('pixelSourceSha256'),str) or not re.fullmatch('[a-f0-9]{64}',proof['pixelSourceSha256']):
                    raise ValueError('NPC_PIXEL_PROVENANCE_HASH')
            if 'pixelSourceAssetId' in pair:
                source = pair['pixelSourceAssetId']
                if not isinstance(source, str) or not re.fullmatch(r'[0-9a-f]{64}\.png', source):
                    raise ValueError('NPC_PIXEL_SOURCE_ASSET_INVALID')
                if provenance is not None and provenance['pipeline']['pixelSourceSha256'] != source[:-4]:
                    raise ValueError('NPC_PIXEL_SOURCE_PROVENANCE_MISMATCH')
                if asset_reader is not None:
                    asset_reader(source)
                elif not (PORTRAIT_ROOT / source).is_file():
                    raise ValueError('NPC_PIXEL_SOURCE_ASSET_MISSING')
            reference = pair['referencePortraitAssetId']
            if slot == 'representative' and (reference is not None or active != pair['portraitAssetId']):
                raise ValueError('NPC_PORTRAIT_REPRESENTATIVE_INVALID')
            if slot != 'representative' and (not isinstance(reference, str) or not re.fullmatch(r'[0-9a-f]{64}\.png', reference)):
                raise ValueError('NPC_PORTRAIT_REFERENCE_REQUIRED')
            if not isinstance(pair['requestId'], str) or not pair['requestId']:
                raise ValueError('NPC_PORTRAIT_PAIR_REQUEST_REQUIRED')
        if active is not None and active not in ids:
            raise ValueError("대표 포트레이트를 확인하세요.")
    return document


class Store:
    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.execute("CREATE TABLE IF NOT EXISTS revisions (revision INTEGER PRIMARY KEY, saved_at TEXT NOT NULL, note TEXT NOT NULL, document TEXT NOT NULL)")
        if self.connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 0:
            seed = validate(json.loads(SEED.read_text(encoding="utf-8")))
            self.connection.execute("INSERT INTO revisions VALUES (?, ?, ?, ?)", (1, datetime.now(timezone.utc).isoformat(), "기존 ProjectBS 자료로 초기화", json.dumps(seed, ensure_ascii=False)))
            self.connection.commit()

    def current(self):
        row = self.connection.execute("SELECT revision, saved_at, note, document FROM revisions ORDER BY revision DESC LIMIT 1").fetchone()
        return {"revision": row[0], "savedAt": row[1], "note": row[2], "document": json.loads(row[3])}

    def history(self):
        rows = self.connection.execute("SELECT revision, saved_at, note FROM revisions ORDER BY revision DESC LIMIT 100").fetchall()
        return [{"revision": row[0], "savedAt": row[1], "note": row[2]} for row in rows]

    def revision(self, number: int):
        row = self.connection.execute("SELECT revision, saved_at, note, document FROM revisions WHERE revision = ?", (number,)).fetchone()
        return None if row is None else {"revision": row[0], "savedAt": row[1], "note": row[2], "document": json.loads(row[3])}

    def save(self, expected: int, note: str, document: dict):
        with LOCK:
            latest = self.current()["revision"]
            if expected != latest:
                return None
            now = datetime.now(timezone.utc).isoformat()
            self.connection.execute("INSERT INTO revisions VALUES (?, ?, ?, ?)", (latest + 1, now, note, json.dumps(document, ensure_ascii=False)))
            self.connection.commit()
            return {"revision": latest + 1, "savedAt": now, "note": note, "document": document}


class StoryStore:
    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.execute("CREATE TABLE IF NOT EXISTS revisions (revision INTEGER PRIMARY KEY, saved_at TEXT NOT NULL, note TEXT NOT NULL, document TEXT NOT NULL)")
        if self.connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 0:
            seed = story_seed()
            document = {key: value for key, value in seed.items() if key != "sources"}
            validate_story(document)
            self.connection.execute("INSERT INTO revisions VALUES (?, ?, ?, ?)", (1, datetime.now(timezone.utc).isoformat(), "기존 Act 1 에피소드 42개로 검토용 초안 생성", json.dumps(document, ensure_ascii=False)))
            self.connection.commit()

    def current(self):
        row = self.connection.execute("SELECT revision, saved_at, note, document FROM revisions ORDER BY revision DESC LIMIT 1").fetchone()
        return {"revision": row[0], "savedAt": row[1], "note": row[2], "document": json.loads(row[3])}

    def history(self):
        rows = self.connection.execute("SELECT revision, saved_at, note FROM revisions ORDER BY revision DESC LIMIT 100").fetchall()
        return [{"revision": row[0], "savedAt": row[1], "note": row[2]} for row in rows]

    def revision(self, number: int):
        row = self.connection.execute("SELECT revision, saved_at, note, document FROM revisions WHERE revision = ?", (number,)).fetchone()
        return None if row is None else {"revision": row[0], "savedAt": row[1], "note": row[2], "document": json.loads(row[3])}

    def save(self, expected: int, note: str, document: dict):
        with LOCK:
            latest = self.current()["revision"]
            if expected != latest:
                return None
            now = datetime.now(timezone.utc).isoformat()
            self.connection.execute("INSERT INTO revisions VALUES (?, ?, ?, ?)", (latest + 1, now, note, json.dumps(document, ensure_ascii=False)))
            self.connection.commit()
            return {"revision": latest + 1, "savedAt": now, "note": note, "document": document}


class NpcStore(StoryStore):
    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.execute("CREATE TABLE IF NOT EXISTS revisions (revision INTEGER PRIMARY KEY, saved_at TEXT NOT NULL, note TEXT NOT NULL, document TEXT NOT NULL)")
        if self.connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 0:
            document = validate_npcs(json.loads(NPC_SEED.read_text(encoding="utf-8")))
            self.connection.execute("INSERT INTO revisions VALUES (?, ?, ?, ?)", (1, datetime.now(timezone.utc).isoformat(), "Act 1 기존 인물 자료로 검토용 NPC 초안 생성", json.dumps(document, ensure_ascii=False)))
            self.connection.commit()


class Handler(BaseHTTPRequestHandler):
    server_version = "NpcConceptStudio/1.0"

    def respond(self, code: int, value: object):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def serve_combat_profile(self, npc_id, full=False):
        try:
            with LOCK:
                snapshot = self.server.npc_store.current()
            value = npc_detail(snapshot, npc_id, validate_npcs) if full else combat_profile(snapshot, npc_id)
            body, etag = encode_profile(value, "npc-detail-v1" if full else "combat-profile-v1")
        except (CombatProfileError, NpcDetailError) as exc:
            return self.respond(exc.status, {"error": {"code": exc.code, "message": exc.message, "npcId": npc_id}})
        except (ValueError, TypeError, sqlite3.Error) as exc:
            return self.respond(500, {"error": {"code": "npc_store_error", "message": "최신 NPC 개정을 읽을 수 없습니다.", "npcId": npc_id}})
        unchanged = etag_matches(self.headers.get("If-None-Match"), etag)
        self.send_response(304 if unchanged else 200)
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "private, no-cache")
        self.send_header("X-Source-Revision", str(value["sourceRevision"]))
        self.send_header("X-Content-Type-Options", "nosniff")
        if not unchanged:
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not unchanged and self.command != "HEAD":
            self.wfile.write(body)

    def route(self):
        host = self.headers.get("Host", "")
        port = self.server.server_port
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return self.respond(403, {"error": "invalid_host"})
        if self.command in ("PUT", "POST") and self.headers.get("Origin") not in (None, f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            return self.respond(403, {"error": "invalid_origin"})
        path = urlsplit(self.path).path
        if self.command in ("GET", "HEAD") and path.startswith("/assets/portraits/"):
            asset_id = path.removeprefix("/assets/portraits/")
            if not re.fullmatch(r"[0-9a-f]{64}\.(?:png|jpg|webp)", asset_id):
                return self.respond(404, {"error": "not_found"})
            if hasattr(self.server.npc_store, 'asset_get'):
                data = self.server.npc_store.asset_get(asset_id)
            else:
                file = PORTRAIT_ROOT / asset_id
                if not file.is_file():
                    return self.respond(404, {"error": "not_found"})
                data = file.read_bytes()
            mime = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[asset_id.rsplit(".", 1)[1]]
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD": self.wfile.write(data)
            return
        if self.command in ("GET", "HEAD") and path in ASSETS:
            name, mime = ASSETS[path]
            data = (ROOT / name).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
            return
        if path == "/health" and self.command in ("GET", "HEAD"):
            return self.respond(200, {"ok": True, "service": "npc-concept-studio"})
        if path in ('/api/knowledge','/v1/knowledge','/knowledge.html','/knowledge.js','/knowledge.css') or path.startswith(('/api/knowledge/','/v1/knowledge/')):
            return self.respond(410, {'error':'knowledge_feature_removed'})
        if path == "/api/concept" and self.command in ("GET", "HEAD"):
            with LOCK:
                return self.respond(200, self.server.store.current())
        if path == "/api/story" and self.command in ("GET", "HEAD"):
            with LOCK:
                return self.respond(200, self.server.story_store.current())
        combat_match = re.fullmatch(r"/api/npcs/([^/]+)/combat-profile", path)
        if combat_match and self.command in ("GET", "HEAD"):
            return self.serve_combat_profile(unquote(combat_match.group(1)))
        if path == "/api/npcs" and self.command in ("GET", "HEAD"):
            with LOCK:
                return self.respond(200, self.server.npc_store.current())
        if path == "/api/portraits" and self.command == "POST":
            length = self.headers.get("Content-Length", "")
            if not length.isdecimal() or not 0 < int(length) <= MAX_PORTRAIT_BODY:
                return self.respond(413, {"error": "이미지는 10MB 이하여야 합니다."})
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                return self.respond(415, {"error": "json_required"})
            try:
                payload = json.loads(self.rfile.read(int(length)))
                kind = payload["mimeType"]
                ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(kind)
                if ext is None: raise ValueError("PNG, JPG, WebP 이미지만 등록할 수 있습니다.")
                data = base64.b64decode(payload["data"], validate=True)
                if not 0 < len(data) <= 10 * 1024 * 1024: raise ValueError("이미지는 10MB 이하여야 합니다.")
                if not ((ext == "png" and data.startswith(b"\x89PNG\r\n\x1a\n")) or (ext == "jpg" and data.startswith(b"\xff\xd8\xff")) or (ext == "webp" and data.startswith(b"RIFF") and data[8:12] == b"WEBP")):
                    raise ValueError("이미지 파일 형식이 올바르지 않습니다.")
            except (ValueError, TypeError, KeyError, UnicodeDecodeError, binascii.Error) as exc:
                return self.respond(422, {"error": str(exc)})
            asset_id = hashlib.sha256(data).hexdigest() + "." + ext
            if hasattr(self.server.npc_store, 'asset_put'):
                self.server.npc_store.asset_put(asset_id, data)
            else:
                PORTRAIT_ROOT.mkdir(parents=True, exist_ok=True)
                destination = PORTRAIT_ROOT / asset_id
                if not destination.exists(): destination.write_bytes(data)
            return self.respond(201, {"assetId": asset_id, "url": f"/assets/portraits/{asset_id}"})
        if path == "/api/npcs/revisions" and self.command in ("GET", "HEAD"):
            with LOCK:
                return self.respond(200, {"revisions": self.server.npc_store.history()})
        if path.startswith("/api/npcs/revisions/") and self.command in ("GET", "HEAD"):
            tail = path.removeprefix("/api/npcs/revisions/")
            if not tail.isdecimal():
                return self.respond(404, {"error": "not_found"})
            with LOCK:
                item = self.server.npc_store.revision(int(tail))
            return self.respond(200, item) if item else self.respond(404, {"error": "not_found"})
        detail_match = re.fullmatch(r"/api/npcs/([^/]+)", path)
        if detail_match and self.command in ("GET", "HEAD"):
            return self.serve_combat_profile(unquote(detail_match.group(1)), full=True)
        if path == "/api/story/revisions" and self.command in ("GET", "HEAD"):
            with LOCK:
                return self.respond(200, {"revisions": self.server.story_store.history()})
        if path.startswith("/api/story/revisions/") and self.command in ("GET", "HEAD"):
            tail = path.removeprefix("/api/story/revisions/")
            if not tail.isdecimal():
                return self.respond(404, {"error": "not_found"})
            with LOCK:
                item = self.server.story_store.revision(int(tail))
            return self.respond(200, item) if item else self.respond(404, {"error": "not_found"})
        if path.startswith("/api/story/sources/") and self.command in ("GET", "HEAD"):
            source_id = path.removeprefix("/api/story/sources/")
            source = story_seed().get("sources", {}).get(source_id)
            return self.respond(200, {"id": source_id, "text": source}) if source is not None else self.respond(404, {"error": "not_found"})
        if path == "/api/revisions" and self.command in ("GET", "HEAD"):
            with LOCK:
                return self.respond(200, {"revisions": self.server.store.history()})
        if path.startswith("/api/revisions/") and self.command in ("GET", "HEAD"):
            tail = path.removeprefix("/api/revisions/")
            if not tail.isdecimal():
                return self.respond(404, {"error": "not_found"})
            with LOCK:
                item = self.server.store.revision(int(tail))
            return self.respond(200, item) if item else self.respond(404, {"error": "not_found"})
        if path == "/api/concept" and self.command == "PUT":
            length = self.headers.get("Content-Length", "")
            if not length.isdecimal() or not 0 < int(length) <= MAX_BODY:
                return self.respond(413, {"error": "invalid_body_size"})
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                return self.respond(415, {"error": "json_required"})
            try:
                payload = json.loads(self.rfile.read(int(length)))
                expected = payload["expectedRevision"]
                note = payload["note"]
                if type(expected) is not int or not isinstance(note, str) or not 3 <= len(note.strip()) <= 300:
                    raise ValueError("개정 번호와 변경 사유를 확인하세요.")
                document = validate(payload["document"])
            except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
                return self.respond(422, {"error": str(exc)})
            saved = self.server.store.save(expected, note.strip(), document)
            return self.respond(200, saved) if saved else self.respond(409, {"error": "다른 곳에서 먼저 수정했습니다. 새로고침 후 다시 편집하세요."})
        if path == "/api/story" and self.command == "PUT":
            length = self.headers.get("Content-Length", "")
            if not length.isdecimal() or not 0 < int(length) <= MAX_STORY_BODY:
                return self.respond(413, {"error": "invalid_body_size"})
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                return self.respond(415, {"error": "json_required"})
            try:
                payload = json.loads(self.rfile.read(int(length)))
                expected, note = payload["expectedRevision"], payload["note"]
                if type(expected) is not int or not isinstance(note, str) or not 3 <= len(note.strip()) <= 300:
                    raise ValueError("개정 번호와 변경 사유를 확인하세요.")
                document = validate_story(payload["document"])
            except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
                return self.respond(422, {"error": str(exc)})
            saved = self.server.story_store.save(expected, note.strip(), document)
            return self.respond(200, saved) if saved else self.respond(409, {"error": "다른 곳에서 먼저 수정했습니다. 새로고침 후 다시 편집하세요."})
        if path == "/api/npcs/remove-category" and self.command == "POST":
            if self.headers.get('Origin') is not None or self.client_address[0] != '127.0.0.1':
                return self.respond(403, {'error': 'formal_server_only'})
            from ..filex_npc_authority import FilexCurrentNPC
            if not isinstance(self.server.npc_store, FilexCurrentNPC):
                return self.respond(409, {'error':'npc_filex_authority_required'})
            try:
                from ..npc_category_removal import remove
                length = int(self.headers['Content-Length'])
                if not 0 < length <= 65536: raise ValueError('category removal size')
                payload = json.loads(self.rfile.read(length))
                if set(payload) != {'requestId','inputHash','characterId','categoryId','expectedRevision'}:
                    raise ValueError('category removal contract')
                if any(not isinstance(payload[k],str) or not payload[k] for k in ('requestId','inputHash','characterId','categoryId')):
                    raise ValueError('category removal binding')
                saved = remove(self.server.npc_store,payload['requestId'],payload['inputHash'],payload['characterId'],payload['categoryId'],payload['expectedRevision'])
                return self.respond(200,saved) if saved else self.respond(409,{'error':'npc_source_changed'})
            except (ValueError, KeyError, TypeError) as exc:
                return self.respond(422,{'error':str(exc)})
        if path == "/api/npcs/publish-portrait" and self.command == "POST":
            if self.headers.get('Origin') is not None or self.client_address[0] != '127.0.0.1':
                return self.respond(403, {'error':'formal_server_only'})
            from ..filex_npc_authority import FilexCurrentNPC
            if not isinstance(self.server.npc_store,FilexCurrentNPC):return self.respond(409, {'error':'npc_filex_authority_required'})
            if self.headers.get('Content-Type','').split(';',1)[0]!='application/json':return self.respond(415, {'error':'json_required'})
            try:
                from ..npc_scoped_portrait import publish
                length=self.headers.get('Content-Length','')
                if not length.isdecimal()or not 0<int(length)<=MAX_STORY_BODY:return self.respond(413, {'error':'invalid_body_size'})
                raw=self.rfile.read(int(length))
                if len(raw)!=int(length):raise ValueError('NPC_PORTRAIT_BODY_INCOMPLETE')
                return self.respond(200,publish(self.server.npc_store,json.loads(raw)))
            except (ValueError,KeyError,TypeError,UnicodeDecodeError)as exc:
                return self.respond(409 if str(exc)=='NPC_PORTRAIT_SOURCE_CHANGED'else 422, {'error':str(exc)})
            except OSError:return self.respond(503, {'error':'NPC_PORTRAIT_OUTCOME_UNCERTAIN'})
        if path == "/api/npcs/create-character" and self.command == "POST":
            if self.headers.get('Origin') is not None or self.client_address[0] != '127.0.0.1':
                return self.respond(403, {'error':'formal_server_only'})
            from ..filex_npc_authority import FilexCurrentNPC
            if not isinstance(self.server.npc_store,FilexCurrentNPC):
                return self.respond(409, {'error':'npc_filex_authority_required'})
            if self.headers.get('Content-Type','').split(';',1)[0]!='application/json':
                return self.respond(415, {'error':'json_required'})
            try:
                from ..npc_scoped_create import create
                length=self.headers.get('Content-Length','')
                if not length.isdecimal() or not 0<int(length)<=MAX_STORY_BODY:
                    return self.respond(413, {'error':'invalid_body_size'})
                raw=self.rfile.read(int(length))
                if len(raw)!=int(length):raise ValueError('NPC_CREATE_BODY_INCOMPLETE')
                return self.respond(200,create(self.server.npc_store,json.loads(raw)))
            except (ValueError,KeyError,TypeError,UnicodeDecodeError) as exc:
                return self.respond(409 if str(exc)=='NPC_CREATE_SOURCE_CHANGED' else 422, {'error':str(exc)})
            except OSError:
                return self.respond(503, {'error':'NPC_CREATE_OUTCOME_UNCERTAIN'})
        if path == "/api/npcs/delete-character" and self.command == "POST":
            if self.headers.get('Origin') is not None or self.client_address[0] != '127.0.0.1':
                return self.respond(403, {'error': 'formal_server_only'})
            if not hasattr(self.server.npc_store, 'delete_character'):
                return self.respond(409, {'error': 'npc_filex_authority_required'})
            try:
                length = int(self.headers['Content-Length'])
                if length <= 0 or length > 65536: raise ValueError('delete contract size')
                payload = json.loads(self.rfile.read(length))
                if set(payload) != {'requestId','inputHash','characterId','expectedRevision'}:
                    raise ValueError('delete contract')
                if type(payload['expectedRevision']) is not int or any(not isinstance(payload[k],str) or not payload[k] for k in ('requestId','inputHash','characterId')):
                    raise ValueError('delete binding')
                saved = self.server.npc_store.delete_character(payload['requestId'],payload['inputHash'],payload['characterId'],payload['expectedRevision'])
                return self.respond(200,saved) if saved else self.respond(409,{'error':'npc_source_changed'})
            except (ValueError, KeyError, TypeError) as exc:
                return self.respond(422, {'error': str(exc)})
        if path == "/api/npcs" and self.command == "PUT":
            length = self.headers.get("Content-Length", "")
            if not length.isdecimal() or not 0 < int(length) <= MAX_STORY_BODY:
                return self.respond(413, {"error": "invalid_body_size"})
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                return self.respond(415, {"error": "json_required"})
            try:
                payload = json.loads(self.rfile.read(int(length)))
                expected, note = payload["expectedRevision"], payload["note"]
                if type(expected) is not int or not isinstance(note, str) or not 3 <= len(note.strip()) <= 300:
                    raise ValueError("개정 번호와 변경 사유를 확인하세요.")
                document = validate_npcs(payload["document"], getattr(self.server.npc_store, "asset_get", None))
            except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
                return self.respond(422, {"error": str(exc)})
            saved = self.server.npc_store.save(expected, note.strip(), document)
            return self.respond(200, saved) if saved else self.respond(409, {"error": "다른 곳에서 먼저 수정했습니다. 새로고침 후 다시 편집하세요."})
        return self.respond(404, {"error": "not_found"})

    def do_GET(self): self.route()
    def do_HEAD(self): self.route()
    def do_PUT(self): self.route()
    def do_POST(self): self.route()

