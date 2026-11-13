#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import imageio.v3 as iio
import numpy as np

from videoforge_mcp.alpha_models import BiRefNetMatting, SAM2VideoTracker
from videoforge_mcp.alpha_pipeline import AlphaConfig, run_alpha_pipeline


def model_initial_mask(frame: np.ndarray, model: BiRefNetMatting) -> np.ndarray:
    raw = np.round(np.clip(model.predict(frame), 0.0, 1.0) * 255).astype(np.uint8)
    binary = np.where(raw >= 24, 255, 0).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise RuntimeError("model_found_no_initial_subject")
    mask = np.zeros_like(binary)
    cv2.drawContours(mask, [max(contours, key=cv2.contourArea)], -1, 255, thickness=cv2.FILLED)
    return mask


def main() -> int:
    source, original_dir, rgba_dir, summary_path, spec_json = sys.argv[1:6]
    spec = json.loads(spec_json)
    model_spec = spec["modelMatting"]
    fps = int(model_spec["fps"])
    width = int(model_spec["width"])
    height = int(model_spec["height"])
    temporal = float(model_spec["temporalStrength"])
    max_frames = int(model_spec["maxFrames"])
    source_path = Path(source)
    original_path, rgba_path = Path(original_dir), Path(rgba_dir)
    with tempfile.TemporaryDirectory(prefix="projectbs-model-worker-") as temporary:
        root = Path(temporary)
        normalized = root / "normalized.mp4"
        subprocess.check_call(["ffmpeg", "-y", "-v", "error", "-i", str(source_path), "-vf",
                               f"fps={fps},scale={width}:{height}:flags=lanczos", "-an", "-c:v", "libx264",
                               "-pix_fmt", "yuv420p", str(normalized)])
        first = np.asarray(next(iio.imiter(normalized)), dtype=np.uint8)[..., :3]
        matting = BiRefNetMatting(model_spec["mattingModel"])
        initial_mask = model_initial_mask(first, matting)
        result_dir = root / "result"
        result = run_alpha_pipeline(
            normalized,
            result_dir,
            AlphaConfig(mode="subject", temporal_strength=temporal, max_frames=max_frames),
            initial_mask=initial_mask,
            tracker=SAM2VideoTracker(model_spec["trackingModel"]),
            matting=matting,
        )
        original_path.mkdir(parents=True)
        rgba_path.mkdir(parents=True)
        alpha_source = result_dir / ("alpha_raw" if temporal == 0 else "alpha_stabilized")
        for index in range(result["frame_count"]):
            rgb = iio.imread(result_dir / "frames_rgb" / f"{index:06d}.png")
            alpha = iio.imread(alpha_source / f"{index:06d}.png")
            iio.imwrite(original_path / f"frame-{index + 1:08d}.png", rgb)
            iio.imwrite(rgba_path / f"frame-{index + 1:08d}.png", np.dstack((rgb[..., :3], alpha)))
        summary = {"fps": result["fps"] or fps, "frameCount": result["frame_count"],
                   "trackingModel": model_spec["trackingModel"], "mattingModel": model_spec["mattingModel"],
                   "temporalStrength": temporal, "initialMaskMethod": "birefnet"}
        Path(summary_path).write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
