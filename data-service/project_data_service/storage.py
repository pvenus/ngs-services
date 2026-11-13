from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath


class StorageError(ValueError):
    pass


class StorageRoot:
    REQUIRED_DIRECTORIES = ("database", "assets", "jobs", "temporary", "backups")

    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser().resolve()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for name in self.REQUIRED_DIRECTORIES:
            (self.root / name).mkdir(exist_ok=True)
        marker = self.root / "storage.json"
        if not marker.exists():
            marker.write_text(
                json.dumps({"format": "projectbs-storage", "version": 1}, indent=2) + "\n",
                encoding="utf-8",
            )

    @staticmethod
    def normalize_relative(raw: str) -> str:
        if not isinstance(raw, str) or not raw.strip():
            raise StorageError("relative path is required")
        if "\\" in raw:
            raise StorageError("storage paths must use forward slashes")
        path = PurePosixPath(raw)
        if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
            raise StorageError("only normalized relative paths are allowed")
        return path.as_posix()

    def resolve(self, raw: str, *, must_exist: bool = False) -> Path:
        relative = self.normalize_relative(raw)
        candidate = self.root.joinpath(*PurePosixPath(relative).parts)
        resolved = candidate.resolve(strict=must_exist)
        if resolved != self.root and self.root not in resolved.parents:
            raise StorageError("path escapes the storage root")
        if must_exist and (not resolved.is_file() or resolved.is_symlink()):
            raise StorageError("asset path must be an existing regular file")
        return resolved

    def inspect_file(self, raw: str) -> dict:
        path = self.resolve(raw, must_exist=True)
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        return {"relativePath": self.normalize_relative(raw), "sha256": digest.hexdigest(), "size": size}

    def status(self) -> dict:
        marker = self.root / "storage.json"
        return {
            "format": "projectbs-storage",
            "rootAvailable": self.root.is_dir(),
            "writable": os.access(self.root, os.W_OK),
            "markerValid": marker.is_file(),
            "directories": {name: (self.root / name).is_dir() for name in self.REQUIRED_DIRECTORIES},
        }
