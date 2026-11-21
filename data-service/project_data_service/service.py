from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .database import DataError, DataStore, RevisionConflict, utc_now
from .storage import StorageError, StorageRoot


ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{1,127}")


from .project_display import ProjectDisplayMixin
from .concept import ConceptModule
from .domains import DomainModule

class ProjectDataService(ProjectDisplayMixin):
    DATA_COLLECTIONS = {"services", "actions", "templates"}
    def __init__(self, storage_root: Path | str):
        self.storage = StorageRoot(storage_root)
        self.storage.initialize()
        self.store = DataStore(self.storage.root / "database" / "projectbs.sqlite3")
        self._display_import_pending()
        import os
        if os.environ.get('PDS_CONCEPT_SOURCE')=='filex':
            from .concept_filex import FilexConcept
            self.concept = FilexConcept(self.store)
        else:self.concept = ConceptModule(self.store)
        self.domains = DomainModule(self.storage.root,self.concept)

    def close(self) -> None:
        self.domains.close()
        self.store.close()

    @staticmethod
    def _id(value: Any, label: str = "id") -> str:
        if not isinstance(value, str) or not ID_RE.fullmatch(value):
            raise DataError(f"invalid {label}")
        return value

    @staticmethod
    def _request_hash(payload: dict) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def create_project(self, payload: dict) -> tuple[int, dict, bool]:
        request_id, actor, reason = payload["requestId"], payload["actor"], payload["reason"]
        project = payload["project"]
        project_id = self._id(project["id"], "project id")
        name = project["name"]
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 240:
            raise DataError("invalid project name")

        def work(connection):
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise DataError("project already exists")
            now = utc_now()
            connection.execute("INSERT INTO projects VALUES(?,?,?,?,?)", (project_id, name.strip(), 1, now, now))
            response = {"id": project_id, "name": name.strip(), "revision": 1, "createdAt": now, "updatedAt": now}
            self.store.audit(connection, request_id=request_id, actor=actor, operation="project.create",
                             resource_type="project", resource_id=project_id, before_revision=None,
                             after_revision=1, reason=reason)
            return 201, response

        return self.store.idempotent_write(request_id, "project.create", self._request_hash(payload), actor, reason, work)

    def get_project(self, project_id: str) -> dict | None:
        row = self.store.connection.execute("SELECT * FROM projects WHERE id=?", (self._id(project_id),)).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "name": row["name"], "revision": row["revision"],
                "createdAt": row["created_at"], "updatedAt": row["updated_at"]}

    def update_project(self, project_id: str, payload: dict) -> tuple[int, dict, bool]:
        project_id = self._id(project_id)
        request_id, actor, reason = payload["requestId"], payload["actor"], payload["reason"]
        expected, name = payload["expectedRevision"], payload["changes"]["name"]
        if type(expected) is not int or expected < 1:
            raise DataError("expectedRevision must be a positive integer")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 240:
            raise DataError("invalid project name")

        def work(connection):
            current = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if current is None:
                raise DataError("project not found")
            if current["revision"] != expected:
                raise RevisionConflict("project revision conflict")
            now, next_revision = utc_now(), expected + 1
            connection.execute("UPDATE projects SET name=?,revision=?,updated_at=? WHERE id=?",
                               (name.strip(), next_revision, now, project_id))
            response = {"id": project_id, "name": name.strip(), "revision": next_revision,
                        "createdAt": current["created_at"], "updatedAt": now}
            self.store.audit(connection, request_id=request_id, actor=actor, operation="project.update",
                             resource_type="project", resource_id=project_id, before_revision=expected,
                             after_revision=next_revision, reason=reason, details={"fields": ["name"]})
            return 200, response

        return self.store.idempotent_write(request_id, "project.update", self._request_hash(payload), actor, reason, work)

    def register_asset(self, payload: dict) -> tuple[int, dict, bool]:
        request_id, actor, reason = payload["requestId"], payload["actor"], payload["reason"]
        asset = payload["asset"]
        asset_id, project_id = self._id(asset["id"], "asset id"), self._id(asset["projectId"], "project id")
        inspection = self.storage.inspect_file(asset["relativePath"])
        mime_type, original_name = asset["mimeType"], asset["originalName"]
        if not isinstance(mime_type, str) or "/" not in mime_type or len(mime_type) > 200:
            raise DataError("invalid MIME type")
        if not isinstance(original_name, str) or not 1 <= len(original_name) <= 255 or "/" in original_name:
            raise DataError("invalid original file name")
        kind, origin = self._id(asset["kind"], "asset kind"), self._id(asset["origin"], "asset origin")

        def work(connection):
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DataError("project not found")
            if connection.execute("SELECT 1 FROM assets WHERE id=?", (asset_id,)).fetchone():
                raise DataError("asset already exists")
            now = utc_now()
            connection.execute(
                """INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (asset_id, project_id, kind, inspection["relativePath"], inspection["sha256"], inspection["size"],
                 mime_type, original_name, origin, 1, "available", now, now),
            )
            response = {"id": asset_id, "projectId": project_id, "kind": kind,
                        "relativePath": inspection["relativePath"], "sha256": inspection["sha256"],
                        "size": inspection["size"], "mimeType": mime_type, "originalName": original_name,
                        "origin": origin, "status": "available", "revision": 1, "createdAt": now, "updatedAt": now}
            self.store.audit(connection, request_id=request_id, actor=actor, operation="asset.register",
                             resource_type="asset", resource_id=asset_id, before_revision=None,
                             after_revision=1, reason=reason,
                             details={"relativePath": inspection["relativePath"], "sha256": inspection["sha256"]})
            return 201, response

        return self.store.idempotent_write(request_id, "asset.register", self._request_hash(payload), actor, reason, work)

    def get_asset(self, asset_id: str) -> dict | None:
        row = self.store.connection.execute("SELECT * FROM assets WHERE id=?", (self._id(asset_id),)).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "projectId": row["project_id"], "kind": row["kind"],
                "relativePath": row["relative_path"], "sha256": row["sha256"], "size": row["size"],
                "mimeType": row["mime_type"], "originalName": row["original_name"], "origin": row["origin"],
                "status": row["status"], "revision": row["revision"],
                "createdAt": row["created_at"], "updatedAt": row["updated_at"]}

    def audit_events(self, resource_type: str, resource_id: str) -> list[dict]:
        rows = self.store.connection.execute(
            "SELECT * FROM audit_events WHERE resource_type=? AND resource_id=? ORDER BY sequence",
            (resource_type, resource_id),
        ).fetchall()
        return [{**dict(row), "details": json.loads(row["details_json"])} for row in rows]

    @classmethod
    def _collection(cls, collection: str) -> str:
        if collection not in cls.DATA_COLLECTIONS:
            raise DataError("unsupported data collection")
        return collection

    @staticmethod
    def _resource_status(resource: dict) -> str:
        status = resource.get("status", "active")
        if status not in {"active", "draft", "disabled", "legacy"}:
            raise DataError("invalid resource status")
        return status

    def _data_row(self, collection: str, resource_id: str):
        collection = self._collection(collection)
        resource_id = self._id(resource_id, "resource id")
        if collection == "services":
            return self.store.connection.execute("SELECT * FROM service_records WHERE id=?", (resource_id,)).fetchone()
        if collection == "actions":
            return self.store.connection.execute("SELECT * FROM service_actions WHERE id=?", (resource_id,)).fetchone()
        if collection == "knowledge":
            return self.store.connection.execute("SELECT * FROM knowledge_entries WHERE id=?", (resource_id,)).fetchone()
        return self.store.connection.execute("SELECT * FROM codex_templates WHERE id=?", (resource_id,)).fetchone()

    @staticmethod
    def _data_response(collection: str, row) -> dict | None:
        if row is None:
            return None
        return {
            "collection": collection, "id": row["id"], "revision": row["revision"],
            "status": row["status"], "resource": json.loads(row["payload_json"]),
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }

    def get_data_resource(self, collection: str, resource_id: str) -> dict | None:
        return self._data_response(self._collection(collection), self._data_row(collection, resource_id))

    def list_data_resources(self, collection: str, status: str | None = None) -> list[dict]:
        collection = self._collection(collection)
        if status is not None and status not in {"active", "draft", "disabled", "legacy"}:
            raise DataError("invalid status filter")
        table = {"services": "service_records", "actions": "service_actions",
                 "knowledge": "knowledge_entries", "templates": "codex_templates"}[collection]
        query = f"SELECT * FROM {table}"  # table comes only from the fixed allowlist above.
        parameters = ()
        if status is not None:
            query += " WHERE status=?"
            parameters = (status,)
        query += " ORDER BY id"
        rows = self.store.connection.execute(query, parameters).fetchall()
        return [self._data_response(collection, row) for row in rows]

    def put_data_resource(self, collection: str, resource_id: str, payload: dict) -> tuple[int, dict, bool]:
        collection, resource_id = self._collection(collection), self._id(resource_id, "resource id")
        request_id, reason = payload["requestId"], payload["reason"]
        actor = payload.get("actor", "data-api")
        expected, resource = payload["expectedRevision"], payload["resource"]
        if type(expected) is not int or expected < 0:
            raise DataError("expectedRevision must be a non-negative integer")
        if not isinstance(resource, dict):
            raise DataError("resource must be an object")
        status = self._resource_status(resource)
        normalized = dict(resource)
        normalized["status"] = status
        project_id = normalized.get("projectId")
        if project_id is not None:
            project_id = self._id(project_id, "project id")
        if collection == "actions":
            service_id = self._id(normalized.get("serviceId"), "service id")
        elif collection == "knowledge":
            category = self._id(normalized.get("category"), "knowledge category")
            entry_type = self._id(normalized.get("entryType"), "knowledge entry type")
        elif collection == "templates":
            artifact_type = self._id(normalized.get("artifactType"), "artifact type")
        encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True)

        def work(connection):
            table = {"services": "service_records", "actions": "service_actions",
                     "knowledge": "knowledge_entries", "templates": "codex_templates"}[collection]
            current = connection.execute(f"SELECT * FROM {table} WHERE id=?", (resource_id,)).fetchone()
            current_revision = None if current is None else current["revision"]
            if current is None and expected != 0:
                raise RevisionConflict("resource does not exist; expectedRevision must be 0 to create")
            if current is not None and current_revision != expected:
                raise RevisionConflict("resource revision conflict")
            if project_id is not None and connection.execute(
                "SELECT 1 FROM projects WHERE id=?", (project_id,)
            ).fetchone() is None:
                raise DataError("project not found")
            if collection == "actions" and connection.execute(
                "SELECT 1 FROM service_records WHERE id=?", (service_id,)
            ).fetchone() is None:
                raise DataError("referenced service not found")
            now, next_revision = utc_now(), expected + 1
            created_at = now if current is None else current["created_at"]
            if collection == "services":
                values = (resource_id, project_id, encoded, next_revision, status, created_at, now)
                if current is None:
                    connection.execute("INSERT INTO service_records VALUES(?,?,?,?,?,?,?)", values)
                else:
                    connection.execute(
                        "UPDATE service_records SET project_id=?,payload_json=?,revision=?,status=?,updated_at=? WHERE id=?",
                        (project_id, encoded, next_revision, status, now, resource_id),
                    )
            elif collection == "actions":
                values = (resource_id, service_id, encoded, next_revision, status, created_at, now)
                if current is None:
                    connection.execute("INSERT INTO service_actions VALUES(?,?,?,?,?,?,?)", values)
                else:
                    connection.execute(
                        "UPDATE service_actions SET service_id=?,payload_json=?,revision=?,status=?,updated_at=? WHERE id=?",
                        (service_id, encoded, next_revision, status, now, resource_id),
                    )
            elif collection == "knowledge":
                values = (resource_id, category, entry_type, encoded, next_revision, status, created_at, now)
                if current is None:
                    connection.execute("INSERT INTO knowledge_entries VALUES(?,?,?,?,?,?,?,?)", values)
                else:
                    connection.execute(
                        """UPDATE knowledge_entries SET category=?,entry_type=?,payload_json=?,revision=?,status=?,updated_at=?
                           WHERE id=?""",
                        (category, entry_type, encoded, next_revision, status, now, resource_id),
                    )
            else:
                values = (resource_id, artifact_type, encoded, next_revision, status, created_at, now)
                if current is None:
                    connection.execute("INSERT INTO codex_templates VALUES(?,?,?,?,?,?,?)", values)
                else:
                    connection.execute(
                        """UPDATE codex_templates SET artifact_type=?,payload_json=?,revision=?,status=?,updated_at=?
                           WHERE id=?""",
                        (artifact_type, encoded, next_revision, status, now, resource_id),
                    )
            response = {"collection": collection, "id": resource_id, "revision": next_revision,
                        "status": status, "resource": normalized, "createdAt": created_at, "updatedAt": now}
            self.store.audit(
                connection, request_id=request_id, actor=actor, operation=f"{collection}.put",
                resource_type=collection, resource_id=resource_id, before_revision=current_revision,
                after_revision=next_revision, reason=reason,
            )
            return (201 if current is None else 200), response

        operation = f"data.{collection}.put.{resource_id}"
        hash_payload = {"collection": collection, "id": resource_id, **payload, "resource": normalized}
        return self.store.idempotent_write(
            request_id, operation, self._request_hash(hash_payload), actor, reason, work
        )

    @staticmethod
    def _reject_workflow_storage_fields(value: Any) -> None:
        forbidden = {"sql", "table", "database", "databasepath", "dbpath", "path",
                     "relativepath", "absolutepath"}
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise DataError("workflow object keys must be strings")
                if key.lower() in forbidden:
                    raise DataError(f"workflow resource cannot contain storage field: {key}")
                ProjectDataService._reject_workflow_storage_fields(child)
        elif isinstance(value, list):
            for child in value:
                ProjectDataService._reject_workflow_storage_fields(child)

    def _workflow_resource(self, session_id: str, resource: Any) -> dict:
        if not isinstance(resource, dict):
            raise DataError("resource must be an object")
        if resource.get("id") != session_id:
            raise DataError("resource id must match session id")
        for field in ("workflowType", "goal", "status"):
            if not isinstance(resource.get(field), str) or not resource[field].strip():
                raise DataError(f"workflow resource requires {field}")
        if not isinstance(resource.get("inputs"), dict):
            raise DataError("workflow inputs must be an object")
        steps = resource.get("steps")
        artifacts = resource.get("artifacts")
        if not isinstance(steps, list):
            raise DataError("workflow steps must be an array")
        if not isinstance(artifacts, dict):
            raise DataError("workflow artifacts must be an object")
        step_ids = []
        for step in steps:
            if not isinstance(step, dict):
                raise DataError("workflow step must be an object")
            step_ids.append(self._id(step.get("id"), "step id"))
        if len(step_ids) != len(set(step_ids)):
            raise DataError("workflow step ids must be unique")
        current_step = resource.get("currentStepId")
        if current_step is not None:
            current_step = self._id(current_step, "current step id")
            if current_step not in step_ids:
                raise DataError("currentStepId must reference a workflow step")
        for artifact_id, artifact in artifacts.items():
            self._id(artifact_id, "artifact id")
            if not isinstance(artifact, dict):
                raise DataError("workflow artifact must be an object")
            if "id" in artifact and artifact["id"] != artifact_id:
                raise DataError("artifact id must match its map key")
        self._reject_workflow_storage_fields(resource)
        return resource

    @staticmethod
    def _workflow_response(row) -> dict | None:
        if row is None:
            return None
        return {"id": row["id"], "revision": row["revision"],
                "resource": json.loads(row["resource_json"]),
                "createdAt": row["created_at"], "updatedAt": row["updated_at"]}

    def get_workflow_session(self, session_id: str) -> dict | None:
        session_id = self._id(session_id, "session id")
        row = self.store.connection.execute(
            "SELECT * FROM workflow_session_aggregates WHERE id=?", (session_id,)
        ).fetchone()
        return self._workflow_response(row)

    def list_workflow_sessions(self, *, status: str | None = None, limit: int = 100) -> list[dict]:
        if status is not None and status not in {"active", "completed", "failed", "running", "awaiting-confirmation"}:
            raise DataError("invalid workflow status filter")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise DataError("limit must be between 1 and 500")
        query = "SELECT * FROM workflow_session_aggregates"
        parameters: tuple = ()
        if status is not None:
            query += " WHERE status=?"
            parameters = (status,)
        query += " ORDER BY updated_at DESC LIMIT ?"
        rows = self.store.connection.execute(query, (*parameters, limit)).fetchall()
        result = []
        for row in rows:
            resource = json.loads(row["resource_json"])
            steps = resource.get("steps", [])
            completed = sum(1 for step in steps if step.get("status") == "completed")
            result.append({
                "id": row["id"], "revision": row["revision"], "status": row["status"],
                "workflowType": resource.get("workflowType"), "goal": resource.get("goal"),
                "projectId": resource.get("projectId") or resource.get("inputs", {}).get("projectId"),
                "currentStepId": resource.get("currentStepId"), "stepCount": len(steps),
                "completedStepCount": completed, "progress": resource.get("progress"),
                "message": resource.get("message"), "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            })
        return result

    def workflow_events(self, session_id: str) -> list[dict]:
        session_id = self._id(session_id, "session id")
        rows = self.store.connection.execute(
            """SELECT a.*,i.response_json FROM audit_events a
               LEFT JOIN idempotency_keys i ON i.request_id=a.request_id
               WHERE a.resource_type='workflow_session' AND a.resource_id=? ORDER BY a.sequence""",
            (session_id,),
        ).fetchall()
        events = []
        for row in rows:
            snapshot = None
            if row["response_json"]:
                try:
                    envelope = json.loads(row["response_json"])
                    snapshot = envelope.get("resource") if isinstance(envelope, dict) else None
                except json.JSONDecodeError:
                    snapshot = None
            events.append({
                "sequence": row["sequence"], "requestId": row["request_id"],
                "actor": row["actor"], "operation": row["operation"], "reason": row["reason"],
                "beforeRevision": row["before_revision"], "afterRevision": row["after_revision"],
                "createdAt": row["created_at"], "details": json.loads(row["details_json"]),
                "snapshot": snapshot,
            })
        return events

    def put_workflow_session(self, session_id: str, payload: dict) -> tuple[int, dict, bool]:
        session_id = self._id(session_id, "session id")
        request_id, actor, reason = payload["requestId"], payload["actor"], payload["reason"]
        expected = payload["expectedRevision"]
        if type(expected) is not int or expected < 0:
            raise DataError("expectedRevision must be a non-negative integer")
        resource = self._workflow_resource(session_id, payload["resource"])
        encoded = json.dumps(resource, ensure_ascii=False, sort_keys=True)

        def work(connection):
            current = connection.execute(
                "SELECT * FROM workflow_session_aggregates WHERE id=?", (session_id,)
            ).fetchone()
            current_revision = None if current is None else current["revision"]
            if current is None and expected != 0:
                raise RevisionConflict("workflow session does not exist; expectedRevision must be 0 to create")
            if current is not None and current_revision != expected:
                raise RevisionConflict("workflow session revision conflict")
            now, next_revision = utc_now(), expected + 1
            created_at = now if current is None else current["created_at"]
            if current is None:
                connection.execute(
                    "INSERT INTO workflow_session_aggregates VALUES(?,?,?,?,?,?)",
                    (session_id, encoded, next_revision, resource["status"], created_at, now),
                )
            else:
                connection.execute(
                    """UPDATE workflow_session_aggregates
                       SET resource_json=?,revision=?,status=?,updated_at=? WHERE id=?""",
                    (encoded, next_revision, resource["status"], now, session_id),
                )
            response = {"id": session_id, "revision": next_revision, "resource": resource,
                        "createdAt": created_at, "updatedAt": now}
            self.store.audit(
                connection, request_id=request_id, actor=actor, operation="workflow.session.put",
                resource_type="workflow_session", resource_id=session_id,
                before_revision=current_revision, after_revision=next_revision, reason=reason,
                details={"stepCount": len(resource["steps"]), "artifactCount": len(resource["artifacts"])},
            )
            return (201 if current is None else 200), response

        operation = f"workflow.session.put.{session_id}"
        hash_payload = {"sessionId": session_id, **payload, "resource": resource}
        return self.store.idempotent_write(
            request_id, operation, self._request_hash(hash_payload), actor, reason, work
        )

    def get_workflow_steps(self, session_id: str) -> dict | None:
        session = self.get_workflow_session(session_id)
        if session is None:
            return None
        return {"id": session["id"], "revision": session["revision"],
                "resource": session["resource"]["steps"],
                "createdAt": session["createdAt"], "updatedAt": session["updatedAt"]}

    def get_workflow_artifact(self, session_id: str, artifact_id: str) -> dict | None:
        artifact_id = self._id(artifact_id, "artifact id")
        session = self.get_workflow_session(session_id)
        if session is None or artifact_id not in session["resource"]["artifacts"]:
            return None
        return {"id": artifact_id, "revision": session["revision"],
                "resource": session["resource"]["artifacts"][artifact_id],
                "createdAt": session["createdAt"], "updatedAt": session["updatedAt"]}
