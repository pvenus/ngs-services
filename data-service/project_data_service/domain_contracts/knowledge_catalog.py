"""Normalized ProjectBS knowledge catalog backed by SQLite."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ALLOWED_ENTRY_STATUS = {"active", "draft", "legacy"}
ALLOWED_AUTHORITY = {"authoritative", "supporting", "reference"}


class KnowledgeError(ValueError):
    pass


class KnowledgeStore:
    def __init__(self, path: Path, seed_catalog: Path | None = None):
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()
        if self.connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0:
            if seed_catalog is None:
                raise KnowledgeError("knowledge catalog seed is required for an empty database")
            self._bootstrap(seed_catalog)
        elif seed_catalog is not None:
            self._backfill_capability_policies(seed_catalog)

    def _create_schema(self):
        self.connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS projects (
                project_key TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                overview TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                saved_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS categories (
                id TEXT PRIMARY KEY,
                project_key TEXT NOT NULL REFERENCES projects(project_key),
                title TEXT NOT NULL,
                purpose TEXT NOT NULL,
                overview TEXT NOT NULL,
                triggers_json TEXT NOT NULL,
                status TEXT NOT NULL,
                sort_order INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entries (
                id TEXT PRIMARY KEY,
                category_id TEXT NOT NULL REFERENCES categories(id),
                entry_type TEXT NOT NULL,
                title TEXT NOT NULL,
                purpose TEXT NOT NULL,
                content TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                triggers_json TEXT NOT NULL,
                authority TEXT NOT NULL,
                status TEXT NOT NULL,
                max_read_chars INTEGER NOT NULL,
                source_path TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mcp_capabilities (
                id TEXT PRIMARY KEY,
                project_key TEXT NOT NULL REFERENCES projects(project_key),
                title TEXT NOT NULL,
                purpose TEXT NOT NULL,
                server TEXT NOT NULL,
                capabilities_json TEXT NOT NULL,
                triggers_json TEXT NOT NULL,
                risk TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS legacy_references (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_key TEXT NOT NULL REFERENCES projects(project_key),
                source_path TEXT NOT NULL UNIQUE,
                migration_status TEXT NOT NULL DEFAULT 'unreviewed',
                replacement_entry_id TEXT REFERENCES entries(id),
                reviewed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS entry_revisions (
                entry_id TEXT NOT NULL REFERENCES entries(id),
                version INTEGER NOT NULL,
                saved_at TEXT NOT NULL,
                note TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                PRIMARY KEY(entry_id, version)
            );
            CREATE INDEX IF NOT EXISTS idx_entries_category_status ON entries(category_id, status);
            CREATE INDEX IF NOT EXISTS idx_categories_project_status ON categories(project_key, status);
            """
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(mcp_capabilities)")}
        if "execution_policy_json" not in columns:
            self.connection.execute(
                "ALTER TABLE mcp_capabilities ADD COLUMN execution_policy_json TEXT NOT NULL DEFAULT '{}'"
            )
        self.connection.commit()

    def _backfill_capability_policies(self, catalog_path: Path):
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        with self.connection:
            for item in catalog.get("mcps", []):
                policy = self._mcp_execution_payload(item)
                if policy:
                    self.connection.execute(
                        """UPDATE mcp_capabilities
                           SET title=?,purpose=?,server=?,capabilities_json=?,triggers_json=?,risk=?,status=?,
                               execution_policy_json=?
                           WHERE id=? AND execution_policy_json='{}'""",
                        (
                            item["title"], item["purpose"], item["server"],
                            json.dumps(item.get("capabilities", []), ensure_ascii=False),
                            json.dumps(item.get("triggers", []), ensure_ascii=False),
                            item.get("risk", "read_only"), item.get("status", "active"),
                            json.dumps(policy, ensure_ascii=False), item["id"],
                        ),
                    )

    @staticmethod
    def _mcp_execution_payload(item: dict) -> dict:
        payload = dict(item.get("execution_policy", {}))
        if item.get("fixed_context"):
            payload["fixedContext"] = item["fixed_context"]
        if item.get("fixed_document_ids"):
            payload["fixedDocumentIds"] = item["fixed_document_ids"]
        if item.get("actions"):
            payload["actions"] = item["actions"]
        return payload

    @staticmethod
    def _read(path: str, limit: int = 50000) -> str:
        return Path(path).read_text(encoding="utf-8")[:limit]

    @staticmethod
    def _initial_payload(document: dict) -> dict:
        if document["id"] == "seojin-character-brief":
            return {
                "characterKey": "seojin",
                "name": "서진",
                "known": {},
                "missing": [
                    "role", "personality", "genderAgeImpression", "faceHairBody", "defaultOutfit",
                    "props", "palette", "artStyle", "referenceAssets", "usage", "composition",
                    "expression", "background", "transparentBackground",
                ],
                "generationPolicy": {
                    "allowUnspecifiedDesign": False,
                    "requireExplicitReferences": True,
                },
            }
        if document["id"] == "character-image-generation-guide":
            return {
                "requiredInputs": [
                    "characterBrief", "usage", "composition", "expression", "outfit", "props",
                    "palette", "background", "transparentBackground", "referenceAssets",
                ],
                "steps": [
                    "read_character_brief", "check_missing_fields", "request_required_inputs",
                    "compose_generation_prompt", "call_image_gen", "visual_review", "report_artifact",
                ],
                "reviewChecks": ["identity", "outfit", "hands", "face", "background", "cropping"],
                "routes": {
                    "mainImage": {
                        "executionMode": "direct-image-generation",
                        "executor": "image-gen",
                        "allowedUsages": ["portrait", "profile", "character-card", "key-art"],
                        "excludedUsages": ["animation-frame", "motion-sequence", "sprite-sequence"],
                    },
                    "animation": {
                        "executionMode": "video-to-frames",
                        "executor": "service-gateway-video",
                        "postProcess": "frame-extraction",
                    },
                },
            }
        return {"domain": document.get("domain"), "sourceFormat": "markdown"}

    def _bootstrap(self, catalog_path: Path):
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        now = datetime.now(timezone.utc).isoformat()
        project_key = catalog["project_key"]
        overview = self._read(catalog["overview_path"], 12000)
        with self.connection:
            self.connection.execute(
                "INSERT INTO projects(project_key,schema_version,overview,revision,saved_at) VALUES(?,?,?,?,?)",
                (project_key, catalog.get("schema_version", 1), overview, 1, now),
            )
            for order, category in enumerate(catalog.get("categories", []), 1):
                category_catalog = json.loads(Path(category["catalog_path"]).read_text(encoding="utf-8"))
                self.connection.execute(
                    "INSERT INTO categories VALUES(?,?,?,?,?,?,?,?)",
                    (
                        category["id"], project_key, category["title"], category["purpose"],
                        self._read(category["overview_path"], 4000),
                        json.dumps(category.get("triggers", []), ensure_ascii=False),
                        category.get("status", "active"), order,
                    ),
                )
                for document in category_catalog.get("documents", []):
                    entry_type = "character_brief" if document["id"].endswith("character-brief") else "guide"
                    payload = self._initial_payload(document)
                    self.connection.execute(
                        """INSERT INTO entries(
                            id,category_id,entry_type,title,purpose,content,payload_json,triggers_json,
                            authority,status,max_read_chars,source_path,version,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            document["id"], category["id"], entry_type, document["title"], document["purpose"],
                            self._read(document["path"], document.get("max_read_chars", 12000)),
                            json.dumps(payload, ensure_ascii=False),
                            json.dumps(document.get("triggers", []), ensure_ascii=False),
                            document.get("authority", "supporting"), document.get("status", "active"),
                            document.get("max_read_chars", 12000), document["path"], 1, now,
                        ),
                    )
            for item in catalog.get("mcps", []):
                self.connection.execute(
                    """INSERT INTO mcp_capabilities(
                        id,project_key,title,purpose,server,capabilities_json,triggers_json,risk,status,
                        execution_policy_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        item["id"], project_key, item["title"], item["purpose"], item["server"],
                        json.dumps(item.get("capabilities", []), ensure_ascii=False),
                        json.dumps(item.get("triggers", []), ensure_ascii=False),
                        item.get("risk", "read_only"), item.get("status", "active"),
                        json.dumps(self._mcp_execution_payload(item), ensure_ascii=False),
                    ),
                )
            for source_path in catalog.get("policy", {}).get("legacy_roots", []):
                self.connection.execute(
                    "INSERT OR IGNORE INTO legacy_references(project_key,source_path) VALUES(?,?)",
                    (project_key, source_path),
                )

    @staticmethod
    def _json(value: str):
        return json.loads(value)

    def overview(self, project_key: str = "ProjectBS") -> dict | None:
        project = self.connection.execute("SELECT * FROM projects WHERE project_key=?", (project_key,)).fetchone()
        if project is None:
            return None
        rows = self.connection.execute(
            """SELECT c.id,c.title,c.purpose,c.overview,c.triggers_json,c.status,c.sort_order,
                      COUNT(CASE WHEN e.status='active' THEN 1 END) AS entry_count
               FROM categories c LEFT JOIN entries e ON e.category_id=c.id
               WHERE c.project_key=? AND c.status='active'
               GROUP BY c.id ORDER BY c.sort_order""",
            (project_key,),
        ).fetchall()
        return {
            "schemaVersion": project["schema_version"], "projectKey": project_key,
            "revision": project["revision"], "savedAt": project["saved_at"], "overview": project["overview"],
            "categories": [
                {"id": row["id"], "title": row["title"], "purpose": row["purpose"],
                 "overview": row["overview"], "triggers": self._json(row["triggers_json"]),
                 "status": row["status"], "entryCount": row["entry_count"]}
                for row in rows
            ],
        }

    def category(self, category_id: str) -> dict | None:
        category = self.connection.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone()
        if category is None:
            return None
        rows = self.connection.execute(
            """SELECT id,entry_type,title,purpose,triggers_json,authority,status,max_read_chars,version,updated_at
               FROM entries WHERE category_id=? AND status='active' ORDER BY id""",
            (category_id,),
        ).fetchall()
        return {
            "id": category["id"], "title": category["title"], "purpose": category["purpose"],
            "overview": category["overview"], "triggers": self._json(category["triggers_json"]),
            "entries": [
                {"id": row["id"], "type": row["entry_type"], "title": row["title"],
                 "purpose": row["purpose"], "triggers": self._json(row["triggers_json"]),
                 "authority": row["authority"], "status": row["status"],
                 "maxReadChars": row["max_read_chars"], "version": row["version"], "updatedAt": row["updated_at"]}
                for row in rows
            ],
        }

    def entry(self, entry_id: str) -> dict | None:
        row = self.connection.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"], "categoryId": row["category_id"], "type": row["entry_type"],
            "title": row["title"], "purpose": row["purpose"], "content": row["content"],
            "data": self._json(row["payload_json"]), "triggers": self._json(row["triggers_json"]),
            "authority": row["authority"], "status": row["status"], "maxReadChars": row["max_read_chars"],
            "sourcePath": row["source_path"], "version": row["version"], "updatedAt": row["updated_at"],
        }

    def capabilities(self, project_key: str = "ProjectBS") -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM mcp_capabilities WHERE project_key=? AND status='active' ORDER BY id", (project_key,)
        ).fetchall()
        return [
            {"id": row["id"], "title": row["title"], "purpose": row["purpose"], "server": row["server"],
             "capabilities": self._json(row["capabilities_json"]), "triggers": self._json(row["triggers_json"]),
             "risk": row["risk"], "status": row["status"],
             "executionPolicy": self._json(row["execution_policy_json"])}
            for row in rows
        ]

    def legacy_references(self, project_key: str = "ProjectBS") -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM legacy_references WHERE project_key=? ORDER BY source_path", (project_key,)
        ).fetchall()
        return [
            {"id": row["id"], "sourcePath": row["source_path"], "migrationStatus": row["migration_status"],
             "replacementEntryId": row["replacement_entry_id"], "reviewedAt": row["reviewed_at"]}
            for row in rows
        ]

    def revisions(self, entry_id: str) -> list[dict]:
        rows = self.connection.execute(
            "SELECT version,saved_at,note FROM entry_revisions WHERE entry_id=? ORDER BY version DESC", (entry_id,)
        ).fetchall()
        return [{"version": row["version"], "savedAt": row["saved_at"], "note": row["note"]} for row in rows]

    def create_entry(self, value: dict, note: str) -> dict:
        required = {"id", "categoryId", "type", "title", "purpose"}
        if not isinstance(value, dict) or not required <= set(value):
            raise KnowledgeError("id, categoryId, type, title and purpose are required")
        entry_id = value["id"]
        if not isinstance(entry_id, str) or not 2 <= len(entry_id) <= 128 or not all(c.isalnum() or c in "-_." for c in entry_id):
            raise KnowledgeError("invalid entry id")
        if self.entry(entry_id) is not None:
            raise KnowledgeError("knowledge entry already exists")
        category = self.connection.execute("SELECT id FROM categories WHERE id=?", (value["categoryId"],)).fetchone()
        if category is None:
            raise KnowledgeError("category not found")
        authority = value.get("authority", "supporting")
        status = value.get("status", "draft")
        title, purpose, content = value["title"], value["purpose"], value.get("content", "")
        data, triggers = value.get("data", {}), value.get("triggers", [])
        max_read_chars = value.get("maxReadChars", 12000)
        if authority not in ALLOWED_AUTHORITY or status not in ALLOWED_ENTRY_STATUS:
            raise KnowledgeError("invalid authority or status")
        if not all(isinstance(item, str) for item in (value["type"], title, purpose, content)):
            raise KnowledgeError("entry text fields must be strings")
        if not title.strip() or len(title) > 240 or not purpose.strip() or len(purpose) > 1000 or len(content) > 50000:
            raise KnowledgeError("invalid entry text length")
        if not isinstance(data, dict) or not isinstance(triggers, list) or any(not isinstance(item, str) for item in triggers):
            raise KnowledgeError("invalid structured data or triggers")
        if type(max_read_chars) is not int or not 500 <= max_read_chars <= 50000:
            raise KnowledgeError("invalid maxReadChars")
        if not isinstance(note, str) or not 3 <= len(note.strip()) <= 300:
            raise KnowledgeError("change note must be 3-300 characters")
        now = datetime.now(timezone.utc).isoformat()
        with __import__("contextlib").nullcontext():
            self.connection.execute(
                """INSERT INTO entries(
                    id,category_id,entry_type,title,purpose,content,payload_json,triggers_json,
                    authority,status,max_read_chars,source_path,version,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (entry_id, value["categoryId"], value["type"], title.strip(), purpose.strip(), content,
                 json.dumps(data, ensure_ascii=False), json.dumps(triggers, ensure_ascii=False), authority, status,
                 max_read_chars, None, 1, now),
            )
            created = self.entry(entry_id)
            self.connection.execute(
                "INSERT INTO entry_revisions VALUES(?,?,?,?,?)",
                (entry_id, 1, now, note.strip(), json.dumps(created, ensure_ascii=False)),
            )
            self.connection.execute(
                """UPDATE projects SET revision=revision+1,saved_at=?
                   WHERE project_key=(SELECT project_key FROM categories WHERE id=?)""",
                (now, value["categoryId"]),
            )
        return self.entry(entry_id)

    def update_entry(self, entry_id: str, expected_version: int, note: str, changes: dict) -> dict | None:
        allowed = {"title", "purpose", "content", "data", "triggers", "authority", "status", "maxReadChars"}
        if not isinstance(changes, dict) or not changes or set(changes) - allowed:
            raise KnowledgeError("unsupported entry changes")
        if not isinstance(note, str) or not 3 <= len(note.strip()) <= 300:
            raise KnowledgeError("change note must be 3-300 characters")
        current = self.entry(entry_id)
        if current is None:
            raise KnowledgeError("knowledge entry not found")
        if current["version"] != expected_version:
            return None
        updated = dict(current)
        updated.update(changes)
        if updated["authority"] not in ALLOWED_AUTHORITY or updated["status"] not in ALLOWED_ENTRY_STATUS:
            raise KnowledgeError("invalid authority or status")
        if not isinstance(updated["title"], str) or not updated["title"].strip() or len(updated["title"]) > 240:
            raise KnowledgeError("invalid title")
        if not isinstance(updated["purpose"], str) or not updated["purpose"].strip() or len(updated["purpose"]) > 1000:
            raise KnowledgeError("invalid purpose")
        if not isinstance(updated["content"], str) or len(updated["content"]) > 50000:
            raise KnowledgeError("invalid content")
        if not isinstance(updated["data"], dict) or not isinstance(updated["triggers"], list):
            raise KnowledgeError("invalid structured data or triggers")
        if type(updated["maxReadChars"]) is not int or not 500 <= updated["maxReadChars"] <= 50000:
            raise KnowledgeError("invalid maxReadChars")
        now = datetime.now(timezone.utc).isoformat()
        next_version = expected_version + 1
        with __import__("contextlib").nullcontext():
            self.connection.execute(
                "INSERT OR REPLACE INTO entry_revisions VALUES(?,?,?,?,?)",
                (entry_id, expected_version, now, note.strip(), json.dumps(current, ensure_ascii=False)),
            )
            cursor = self.connection.execute(
                """UPDATE entries SET title=?,purpose=?,content=?,payload_json=?,triggers_json=?,authority=?,status=?,
                   max_read_chars=?,version=?,updated_at=? WHERE id=? AND version=?""",
                (updated["title"], updated["purpose"], updated["content"], json.dumps(updated["data"], ensure_ascii=False),
                 json.dumps(updated["triggers"], ensure_ascii=False), updated["authority"], updated["status"],
                 updated["maxReadChars"], next_version, now, entry_id, expected_version),
            )
            if cursor.rowcount != 1:
                return None
            self.connection.execute(
                """UPDATE projects SET revision=revision+1,saved_at=?
                   WHERE project_key=(SELECT project_key FROM categories WHERE id=?)""",
                (now, current["categoryId"]),
            )
        return self.entry(entry_id)
