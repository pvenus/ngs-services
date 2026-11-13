from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4


class ValidationError(ValueError): pass
class ConflictError(RuntimeError): pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


class VideoAlphaService:
    def __init__(self, repository, files, processor):
        self.repository, self.files, self.processor = repository, files, processor

    def create(self, payload: Dict[str, Any], request_id: str) -> Dict[str, Any]:
        if set(payload) - {"id", "projectId", "sourceAssetId", "processingSpec"}:
            raise ValidationError("unsupported create fields")
        spec = self._validate_spec(payload.get("processingSpec"))
        source = payload.get("sourceAssetId")
        project = payload.get("projectId")
        if not isinstance(source, str) or not source or not isinstance(project, str) or not project:
            raise ValidationError("sourceAssetId and projectId are required")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", source) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", project):
            raise ValidationError("invalid asset or project id")
        metadata = self.repository.get_asset(source)
        if metadata.get("projectId") != project or metadata.get("status") != "available" or not str(metadata.get("mimeType", "")).startswith("video/"):
            raise ValidationError("source asset must be an available video in the same project")
        job_id = payload.get("id") or "video-alpha-" + uuid4().hex
        if self.repository.get(job_id):
            raise ConflictError("job already exists")
        job = {"id": job_id, "workflowType": "video-alpha", "goal": "extract frames and apply alpha",
               "status": "awaiting-confirmation",
               "projectId": project, "sourceAssetId": source, "processingSpec": spec,
               "inputs": {"sourceAssetId": source, "processingSpec": spec},
               "attempt": 0, "createdAt": now(), "updatedAt": now(),
               "progress": {"percent": 0, "stage": "queued", "message": "작업 생성됨"},
               "message": "실행 승인을 기다리는 중",
               "steps": [{"id": step_id, "status": "pending", "message": message} for step_id, message in (
                   ("download-source", "원본 파일 가져오기"), ("process-frames", "프레임 분리 및 알파 처리"),
                   ("package-source", "원본 프레임 패키징"), ("package-result", "RGBA 프레임 패키징"),
                   ("upload-assets", "결과 자산 저장"), ("verify-result", "결과 검증"),
               )], "artifacts": {}}
        return self.repository.put(job, 0, request_id)

    def get(self, job_id: str):
        return self.repository.get(job_id)

    def run(self, job_id: str, *, expected_revision: int, confirmed: bool, request_id: str,
            processing_spec=None) -> Dict[str, Any]:
        job = self.repository.get(job_id)
        if not job: raise ValidationError("job not found")
        if job["revision"] != expected_revision: raise ConflictError("revision conflict")
        if not confirmed: raise ValidationError("confirmed=true is required")
        if processing_spec is not None and self._validate_spec(processing_spec) != job["processingSpec"]:
            raise ConflictError("processingSpec differs from the confirmed job")
        if job["status"] not in ("awaiting-confirmation", "failed"):
            raise ConflictError("job is not runnable")
        running = dict(job); running.update(status="running", attempt=job.get("attempt", 0) + 1,
            progress={"percent": 1, "stage": "starting", "message": "작업 실행 시작"},
            message="작업 실행 시작", updatedAt=now())
        running = self.repository.put(running, expected_revision, request_id + ":running")
        try:
            completed = self._execute(running)
            completed["status"] = "completed"; completed["updatedAt"] = now()
            completed["progress"] = {"percent": 100, "stage": "completed", "message": "모든 작업 완료"}
            completed["message"] = "모든 작업 완료"
            return self.repository.put(completed, completed["revision"], request_id + ":completed")
        except Exception as exc:
            latest = self.repository.get(job_id) or running
            failed = dict(latest); failed.update(status="failed", lastError={"type": type(exc).__name__, "message": str(exc)},
                progress={"percent": latest.get("progress", {}).get("percent", 0), "stage": "failed", "message": str(exc)},
                message="작업 실패: " + str(exc), updatedAt=now())
            self.repository.put(failed, latest["revision"], request_id + ":failed")
            raise

    def _progress(self, job: Dict[str, Any], request_id: str, step_id: str,
                  percent: int, message: str, *, completed: bool = False) -> None:
        updated = dict(job)
        steps = [dict(step) for step in job.get("steps", [])]
        for step in steps:
            if step["id"] == step_id:
                step["status"] = "completed" if completed else "running"
                step["message"] = message
            elif step["status"] == "running":
                step["status"] = "completed"
        updated.update(steps=steps, progress={"percent": percent, "stage": step_id, "message": message},
                       message=message, updatedAt=now())
        stored = self.repository.put(updated, job["revision"], request_id + ":progress:" + step_id + ":" + str(percent))
        job.clear(); job.update(stored)

    def _execute(self, job: Dict[str, Any]) -> Dict[str, Any]:
        request_id = "progress-" + uuid4().hex
        with tempfile.TemporaryDirectory(prefix="projectbs-video-alpha-") as temporary:
            root = Path(temporary); source = root / "source-video"
            original = root / "original-frames"; rgba = root / "rgba-frames"
            self._progress(job, request_id, "download-source", 4, "원본 영상 다운로드 중")
            self.files.download(job["sourceAssetId"], source)
            self._progress(job, request_id, "download-source", 10, "원본 영상 다운로드 완료", completed=True)
            self._progress(job, request_id, "process-frames", 15, "모델 로딩 및 프레임 처리 중")
            summary = self.processor.process(source, original, rgba, job["processingSpec"])
            self._progress(job, request_id, "process-frames", 76, f"{summary['frameCount']}개 프레임 알파 처리 완료", completed=True)
            original_zip = root / "original-frames.zip"; rgba_zip = root / "rgba-frames.zip"
            self._progress(job, request_id, "package-source", 80, "원본 프레임 패키징 중")
            self._zip(original, original_zip); self._zip(rgba, rgba_zip)
            self._progress(job, request_id, "package-source", 84, "원본 프레임 패키징 완료", completed=True)
            self._progress(job, request_id, "package-result", 88, "RGBA 프레임 패키징 완료", completed=True)
            self._progress(job, request_id, "upload-assets", 90, "결과 자산 업로드 중")
            original_asset = self.files.upload(original_zip, job["projectId"], "video-frame-source", "immutable extracted source frames", "application/zip")
            rgba_asset = self.files.upload(rgba_zip, job["projectId"], "video-frame-alpha", "RGBA alpha frames", "application/zip")
            if not original_asset.get("id") or not rgba_asset.get("id") or original_asset["id"] == rgba_asset["id"]:
                raise RuntimeError("File Service must return distinct original/result asset IDs")
            manifest = {"version": 1, "jobId": job["id"], "alphaMethod": job["processingSpec"]["alphaMethod"],
                "frameCount": summary["frameCount"], "fps": summary["fps"], "sourceAssetId": job["sourceAssetId"],
                "originalFramesAssetId": original_asset["id"], "resultFramesAssetId": rgba_asset["id"],
                "processing": summary,
                "verification": {"frameCountsMatch": True, "sourceArchiveSha256": digest(original_zip),
                                 "resultArchiveSha256": digest(rgba_zip)}}
            manifest_path = root / "manifest.json"; manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            manifest_asset = self.files.upload(manifest_path, job["projectId"], "video-alpha-manifest", "processing manifest", "application/json")
            if not manifest_asset.get("id") or manifest_asset["id"] in (original_asset["id"], rgba_asset["id"]):
                raise RuntimeError("File Service must return a distinct manifest asset ID")
            result = dict(job); result["result"] = {**manifest, "manifestAssetId": manifest_asset["id"]}
            result["artifacts"] = {original_asset["id"]: {"id": original_asset["id"], "role": "source-frames"},
                                   rgba_asset["id"]: {"id": rgba_asset["id"], "role": "rgba-frames"},
                                   manifest_asset["id"]: {"id": manifest_asset["id"], "role": "manifest"}}
            self._progress(result, request_id, "upload-assets", 97, "결과 자산 저장 완료", completed=True)
            self._progress(result, request_id, "verify-result", 99, "프레임 수와 결과 자산 검증 완료", completed=True)
            return result

    @staticmethod
    def _zip(folder: Path, output: Path) -> None:
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(folder.iterdir()): archive.write(path, path.name)

    @staticmethod
    def _validate_spec(spec: Any) -> Dict[str, Any]:
        if not isinstance(spec, dict) or spec.get("alphaMethod") not in ("chroma-key", "model-matting"):
            raise ValidationError("processingSpec.alphaMethod must be chroma-key or model-matting")
        if spec.get("alphaMethod") == "model-matting":
            if set(spec) - {"alphaMethod", "outputFormat", "modelMatting"}:
                raise ValidationError("processingSpec contains unsupported fields")
            if spec.get("outputFormat", "rgba-png") != "rgba-png":
                raise ValidationError("outputFormat must be rgba-png")
            model = spec.get("modelMatting", {})
            if not isinstance(model, dict) or set(model) - {"fps", "width", "height", "temporalStrength", "maxFrames", "trackingModel", "mattingModel"}:
                raise ValidationError("invalid modelMatting settings")
            values = {
                "fps": model.get("fps", 6), "width": model.get("width", 640), "height": model.get("height", 480),
                "temporalStrength": model.get("temporalStrength", 0), "maxFrames": model.get("maxFrames", 1200),
                "trackingModel": model.get("trackingModel", "facebook/sam2.1-hiera-small"),
                "mattingModel": model.get("mattingModel", "ZhengPeng7/BiRefNet-matting"),
            }
            if not isinstance(values["fps"], int) or not 1 <= values["fps"] <= 60:
                raise ValidationError("modelMatting.fps must be between 1 and 60")
            if any(not isinstance(values[key], int) or values[key] < 256 or values[key] > 4096 or values[key] % 2 for key in ("width", "height")):
                raise ValidationError("modelMatting dimensions must be even and between 256 and 4096")
            if not isinstance(values["temporalStrength"], (int, float)) or not 0 <= values["temporalStrength"] <= .8:
                raise ValidationError("modelMatting.temporalStrength must be between 0 and 0.8")
            if not isinstance(values["maxFrames"], int) or not 1 <= values["maxFrames"] <= 12000:
                raise ValidationError("modelMatting.maxFrames must be between 1 and 12000")
            if not all(isinstance(values[key], str) and values[key] for key in ("trackingModel", "mattingModel")):
                raise ValidationError("model identifiers are required")
            return {"alphaMethod": "model-matting", "outputFormat": "rgba-png", "modelMatting": values}
        if set(spec) - {"alphaMethod", "outputFormat", "chromaKey"}:
            raise ValidationError("processingSpec contains unsupported fields")
        if spec.get("outputFormat", "rgba-png") != "rgba-png": raise ValidationError("outputFormat must be rgba-png")
        chroma = spec.get("chromaKey", {"color": "green"})
        if not isinstance(chroma, dict) or not isinstance(chroma.get("color"), str): raise ValidationError("invalid chromaKey")
        if set(chroma) - {"color", "similarity", "blend"}:
            raise ValidationError("processingSpec.chromaKey contains unsupported fields")
        if chroma["color"] not in ("green", "blue") and not chroma["color"].startswith("0x"):
            raise ValidationError("chroma key color must be green, blue, or 0xRRGGBB")
        similarity = chroma.get("similarity", .18); blend = chroma.get("blend", .05)
        if not (isinstance(similarity, (int, float)) and 0 <= similarity <= 1 and isinstance(blend, (int, float)) and 0 <= blend <= 1):
            raise ValidationError("similarity and blend must be between 0 and 1")
        return {"alphaMethod": "chroma-key", "outputFormat": "rgba-png",
                "chromaKey": {"color": chroma["color"], "similarity": similarity, "blend": blend}}
