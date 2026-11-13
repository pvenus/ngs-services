from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


class AdapterError(RuntimeError):
    pass


class HttpJson:
    def __init__(self, base_url: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, body: Any = None, headers: Optional[Dict[str, str]] = None) -> Any:
        raw = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=raw, method=method,
                                         headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = response.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as exc:
            raise AdapterError("HTTP %s: %s" % (exc.code, exc.read().decode("utf-8", "replace"))) from exc


class DataJobRepository:
    """Persists the whole job as a workflow aggregate through A's public API."""
    def __init__(self, base_url: str):
        self.http = HttpJson(base_url)

    def get(self, job_id: str):
        try:
            value = self.http.request("GET", "/v1/workflows/sessions/" + urllib.parse.quote(job_id, safe=""))
        except AdapterError as exc:
            if "HTTP 404:" in str(exc):
                return None
            raise
        resource = dict(value["resource"])
        resource["revision"] = value["revision"]
        return resource

    def get_asset(self, asset_id: str) -> Dict[str, Any]:
        return self.http.request("GET", "/api/assets/" + urllib.parse.quote(asset_id, safe=""))

    def put(self, job: Dict[str, Any], expected_revision: int, request_id: str) -> Dict[str, Any]:
        resource = {k: v for k, v in job.items() if k != "revision"}
        body = {"requestId": request_id, "expectedRevision": expected_revision,
                "actor": "project-video-alpha-service", "reason": "video alpha job transition",
                "resource": resource}
        value = self.http.request("PUT", "/v1/workflows/sessions/" + urllib.parse.quote(job["id"], safe=""), body)
        result = dict(value["resource"])
        result["revision"] = value["revision"]
        return result


class FileServiceClient:
    def __init__(self, base_url: str):
        self.http = HttpJson(base_url, timeout=300)
        self.base_url = base_url.rstrip("/")

    def download(self, asset_id: str, destination: Path) -> None:
        link = self.http.request("POST", "/v1/assets/%s/download-links" % urllib.parse.quote(asset_id, safe=""),
                                 {"expiresInSeconds": 600})
        url = self.base_url + link["downloadPath"]
        with urllib.request.urlopen(url, timeout=300) as response, destination.open("wb") as output:
            shutil.copyfileobj(response, output)

    def upload(self, path: Path, project_id: str, kind: str, purpose: str, mime: str) -> Dict[str, Any]:
        query = urllib.parse.urlencode({"filename": path.name, "kind": kind, "purpose": purpose})
        request = urllib.request.Request(self.base_url + "/v1/uploads?" + query, data=path.read_bytes(), method="POST",
            headers={"Content-Type": "application/octet-stream", "X-Project-Id": project_id,
                     "X-Filename": path.name, "X-Asset-Kind": kind, "X-Purpose": purpose,
                     "X-File-Mime": mime})
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise AdapterError("file upload HTTP %s: %s" % (exc.code, exc.read().decode("utf-8", "replace"))) from exc


class FfmpegAdapter:
    def __init__(self, ffmpeg: Optional[str] = None, ffprobe: Optional[str] = None):
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg")
        self.ffprobe = ffprobe or shutil.which("ffprobe")

    @property
    def available(self) -> bool:
        return bool(self.ffmpeg and self.ffprobe)

    def probe(self, source: Path) -> Dict[str, Any]:
        if not self.available:
            raise AdapterError("ffmpeg and ffprobe are required")
        command = [self.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                   "stream=avg_frame_rate,nb_frames,duration", "-of", "json", str(source)]
        value = json.loads(subprocess.check_output(command, text=True))
        stream = value["streams"][0]
        numerator, denominator = stream.get("avg_frame_rate", "0/1").split("/")
        fps = float(numerator) / float(denominator or 1)
        return {"fps": fps, "reportedFrameCount": int(stream["nb_frames"]) if stream.get("nb_frames", "N/A").isdigit() else None}

    def process(self, source: Path, original_dir: Path, rgba_dir: Path, spec: Dict[str, Any]) -> Dict[str, Any]:
        probe = self.probe(source)
        original_dir.mkdir(parents=True)
        rgba_dir.mkdir(parents=True)
        subprocess.check_call([self.ffmpeg, "-v", "error", "-i", str(source), str(original_dir / "frame-%08d.png")])
        key = spec["chromaKey"]["color"]
        similarity = str(spec["chromaKey"].get("similarity", 0.18))
        blend = str(spec["chromaKey"].get("blend", 0.05))
        vf = "chromakey=%s:%s:%s,format=rgba" % (key, similarity, blend)
        subprocess.check_call([self.ffmpeg, "-v", "error", "-i", str(source), "-vf", vf,
                               str(rgba_dir / "frame-%08d.png")])
        originals = sorted(original_dir.glob("frame-*.png"))
        rgba = sorted(rgba_dir.glob("frame-*.png"))
        if not originals or len(originals) != len(rgba):
            raise AdapterError("frame output validation failed")
        return {"fps": probe["fps"], "frameCount": len(originals)}


class ModelMattingAdapter:
    """Runs the SAM2 + BiRefNet worker in its isolated model environment."""

    def __init__(self, python: Optional[str] = None, worker: Optional[str] = None):
        default_python = ""  # Explicit model environment required.
        self.python = python or os.environ.get("VIDEO_ALPHA_MODEL_PYTHON", default_python)
        self.worker = worker or str(Path(__file__).with_name("model_worker.py"))

    @property
    def available(self) -> bool:
        return Path(self.python).is_file() and Path(self.worker).is_file() and bool(shutil.which("ffmpeg"))

    def process(self, source: Path, original_dir: Path, rgba_dir: Path, spec: Dict[str, Any]) -> Dict[str, Any]:
        if not self.available:
            raise AdapterError("model matting runtime is unavailable")
        summary_path = original_dir.parent / "model-summary.json"
        command = [self.python, self.worker, str(source), str(original_dir), str(rgba_dir),
                   str(summary_path), json.dumps(spec, ensure_ascii=False)]
        env = dict(os.environ)
        env.setdefault("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
        try:
            subprocess.check_call(command, env=env)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (subprocess.CalledProcessError, OSError, ValueError) as exc:
            raise AdapterError("model matting worker failed: %s" % exc) from exc
        originals = sorted(original_dir.glob("frame-*.png"))
        rgba = sorted(rgba_dir.glob("frame-*.png"))
        if not originals or len(originals) != len(rgba) or len(rgba) != summary.get("frameCount"):
            raise AdapterError("model frame output validation failed")
        return summary


class VideoAlphaProcessor:
    def __init__(self, chroma=None, model=None):
        self.chroma = chroma or FfmpegAdapter()
        self.model = model or ModelMattingAdapter()

    @property
    def available(self) -> bool:
        return self.chroma.available or self.model.available

    def process(self, source: Path, original_dir: Path, rgba_dir: Path, spec: Dict[str, Any]) -> Dict[str, Any]:
        if spec.get("alphaMethod") == "model-matting":
            return self.model.process(source, original_dir, rgba_dir, spec)
        return self.chroma.process(source, original_dir, rgba_dir, spec)
