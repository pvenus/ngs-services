from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


SCHEMA_VERSION = 2


class DataError(ValueError):
    pass


class RevisionConflict(DataError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DataStore:
    def __init__(self, database_path: Path):
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(database_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self._migrate()

    def close(self) -> None:
        self.connection.close()

    def _migrate(self) -> None:
        with self.connection:
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            current = self.connection.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
            if current < 1:
                self.connection.executescript(
                    """
                    CREATE TABLE projects(
                        id TEXT PRIMARY KEY, name TEXT NOT NULL, revision INTEGER NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE service_records(
                        id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), payload_json TEXT NOT NULL,
                        revision INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE service_actions(
                        id TEXT PRIMARY KEY, service_id TEXT NOT NULL REFERENCES service_records(id),
                        payload_json TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE knowledge_entries(
                        id TEXT PRIMARY KEY, category TEXT NOT NULL, entry_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE codex_templates(
                        id TEXT PRIMARY KEY, artifact_type TEXT NOT NULL, payload_json TEXT NOT NULL,
                        revision INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE assets(
                        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), kind TEXT NOT NULL,
                        relative_path TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
                        mime_type TEXT NOT NULL, original_name TEXT NOT NULL, origin TEXT NOT NULL,
                        revision INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE INDEX idx_assets_project_kind ON assets(project_id,kind,status);
                    CREATE TABLE workflow_sessions(
                        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), goal TEXT NOT NULL,
                        state_json TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE workflow_steps(
                        id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES workflow_sessions(id),
                        step_type TEXT NOT NULL, state_json TEXT NOT NULL, revision INTEGER NOT NULL,
                        status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE artifacts(
                        id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES workflow_sessions(id),
                        artifact_type TEXT NOT NULL, content_json TEXT NOT NULL, revision INTEGER NOT NULL,
                        status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE audit_events(
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL, actor TEXT NOT NULL,
                        operation TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
                        before_revision INTEGER, after_revision INTEGER, reason TEXT NOT NULL,
                        details_json TEXT NOT NULL, created_at TEXT NOT NULL
                    );
                    CREATE INDEX idx_audit_resource ON audit_events(resource_type,resource_id,sequence);
                    CREATE TABLE idempotency_keys(
                        request_id TEXT PRIMARY KEY, operation TEXT NOT NULL, request_hash TEXT NOT NULL,
                        status_code INTEGER NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL
                    );
                    """
                )
                self.connection.execute(
                    "INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)", (1, utc_now())
                )
            if current < 2:
                self.connection.executescript(
                    """
                    CREATE TABLE workflow_session_aggregates(
                        id TEXT PRIMARY KEY,
                        resource_json TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    """
                )
                self.connection.execute(
                    "INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)", (2, utc_now())
                )

    def schema_version(self) -> int:
        return self.connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]

    @staticmethod
    def _validate_common(request_id: str, actor: str, reason: str) -> None:
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 128:
            raise DataError("requestId must be 8-128 characters")
        if not isinstance(actor, str) or not 2 <= len(actor) <= 128:
            raise DataError("actor must be 2-128 characters")
        if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 300:
            raise DataError("reason must be 3-300 characters")

    def idempotent_write(
        self, request_id: str, operation: str, request_hash: str, actor: str, reason: str,
        work: Callable[[sqlite3.Connection], tuple[int, dict]],
    ) -> tuple[int, dict, bool]:
        self._validate_common(request_id, actor, reason)
        with self.lock, self.connection:
            existing = self.connection.execute(
                "SELECT operation,request_hash,status_code,response_json FROM idempotency_keys WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if existing:
                if existing["operation"] != operation or existing["request_hash"] != request_hash:
                    raise DataError("requestId was already used for a different request")
                return existing["status_code"], json.loads(existing["response_json"]), True
            status, response = work(self.connection)
            self.connection.execute(
                "INSERT INTO idempotency_keys VALUES(?,?,?,?,?,?)",
                (request_id, operation, request_hash, status, json.dumps(response, ensure_ascii=False), utc_now()),
            )
            return status, response, False

    def audit(
        self, connection: sqlite3.Connection, *, request_id: str, actor: str, operation: str,
        resource_type: str, resource_id: str, before_revision: int | None,
        after_revision: int | None, reason: str, details: dict | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO audit_events(
                request_id,actor,operation,resource_type,resource_id,before_revision,after_revision,
                reason,details_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (request_id, actor, operation, resource_type, resource_id, before_revision, after_revision,
             reason.strip(), json.dumps(details or {}, ensure_ascii=False), utc_now()),
        )

    @staticmethod
    def row_dict(row: sqlite3.Row | None) -> dict | None:
        return None if row is None else dict(row)
