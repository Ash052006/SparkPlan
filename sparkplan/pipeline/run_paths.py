"""Safe filesystem layout for Phase 6 run artifacts.

A run is claimed with one atomic directory creation. Every artifact path is
resolved under that claimed directory, symlinks are rejected, and JSON files
are published atomically without overwriting an existing file by default.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Final

from sparkplan.pipeline.run_models import (
    MAX_RUN_ID_LENGTH,
    RUN_ID_PATTERN,
    RunResult,
)

RUNS_DIR_ENV: Final = "SPARKPLAN_RUNS_DIR"
DEFAULT_RUNS_DIR: Final = Path("runs")

ARTIFACT_PATHS: Final[dict[str, str]] = {
    "spec": "spec.json",
    "report": "report.json",
    "base_stl": "base.stl",
    "lid_stl": "lid.stl",
    "assembled_stl": "assembled.stl",
    "sketch": "sketch/sketch.ino",
    "guide": "GUIDE.md",
    "trace": "trace.json",
}

_RUN_ID_RE = re.compile(RUN_ID_PATTERN)


class RunPathError(ValueError):
    """Base error for invalid or unsafe run paths."""


class InvalidRunId(RunPathError):
    """The run ID is empty, too long, or contains unsafe characters."""


class UnsafeRunPath(RunPathError):
    """A path or symlink would escape the claimed run directory."""


class RunExistsError(FileExistsError):
    """The requested run directory already exists."""


def validate_run_id(run_id: str) -> str:
    """Return a bounded, safe run ID or raise :class:`InvalidRunId`."""
    if not isinstance(run_id, str):
        raise InvalidRunId("run_id must be a string")
    if not run_id or len(run_id) > MAX_RUN_ID_LENGTH or not _RUN_ID_RE.fullmatch(run_id):
        raise InvalidRunId(
            f"run_id must match {RUN_ID_PATTERN} and be at most " f"{MAX_RUN_ID_LENGTH} characters"
        )
    return run_id


def resolve_runs_root(runs_root: str | Path | None = None) -> Path:
    """Resolve the configured runs root without creating it."""
    if runs_root is None:
        configured = os.environ.get(RUNS_DIR_ENV)
        runs_root = configured if configured else DEFAULT_RUNS_DIR
    root = Path(runs_root).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    resolved = root.resolve(strict=False)
    if resolved.exists() and not resolved.is_dir():
        raise UnsafeRunPath(f"runs root is not a directory: {resolved}")
    return resolved


class RunPaths:
    """Paths and exclusive persistence operations for one Phase 6 run."""

    artifacts = ARTIFACT_PATHS

    def __init__(self, run_id: str, runs_root: str | Path | None = None) -> None:
        self.run_id = validate_run_id(run_id)
        self.runs_root = resolve_runs_root(runs_root)
        self.run_dir = self.runs_root / self.run_id
        self._claimed = False
        self._assert_safe(self.run_dir)

    @property
    def claimed(self) -> bool:
        return self._claimed

    def claim(self) -> RunPaths:
        """Create the run directory exclusively or raise ``RunExistsError``."""
        if self._claimed:
            self._require_claimed()
            return self

        try:
            self.runs_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RunPathError(f"could not create runs root: {self.runs_root}") from exc
        if not self.runs_root.is_dir():
            raise UnsafeRunPath(f"runs root is not a directory: {self.runs_root}")

        try:
            os.mkdir(self.run_dir, 0o755)
        except FileExistsError as exc:
            self._assert_safe(self.run_dir)
            raise RunExistsError(f"run directory already exists: {self.run_dir}") from exc
        except OSError as exc:
            raise RunPathError(f"could not claim run directory: {self.run_dir}") from exc

        self._claimed = True
        try:
            self._assert_safe(self.run_dir)
            self._assert_safe(self.run_dir.resolve(strict=True))
        except Exception:
            self._claimed = False
            with contextlib.suppress(OSError):
                os.rmdir(self.run_dir)
            raise
        return self

    def artifact(self, name: str) -> Path:
        """Return the contained path for a planned artifact name."""
        try:
            relative = ARTIFACT_PATHS[name]
        except KeyError as exc:
            raise RunPathError(f"unknown artifact: {name!r}") from exc
        return self._assert_safe(self.run_dir / relative)

    def artifact_inventory(self) -> dict[str, str]:
        """Return logical artifact names mapped to safe relative POSIX paths."""
        return dict(
            sorted(
                (name, PurePosixPath(relative).as_posix())
                for name, relative in ARTIFACT_PATHS.items()
            )
        )

    def write_json(
        self,
        artifact_name: str,
        payload: RunResult | Mapping[str, Any],
    ) -> Path:
        """Atomically write a JSON artifact, refusing to overwrite."""
        path = self.artifact(artifact_name)
        if path.suffix != ".json":
            raise RunPathError(f"artifact is not JSON: {artifact_name!r}")
        self._require_claimed()
        if path.exists():
            raise FileExistsError(f"artifact already exists: {path}")

        if isinstance(payload, RunResult):
            serializable: Mapping[str, Any] = payload.to_dict()
        elif isinstance(payload, Mapping):
            serializable = payload
        else:
            raise TypeError("payload must be a RunResult or a mapping")
        text = (
            json.dumps(
                dict(serializable),
                sort_keys=True,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        )
        self._ensure_parent(path)
        self._atomic_write_text(path, text)
        return path

    def write_text(self, artifact_name: str, text: str) -> Path:
        """Write a text artifact exclusively; never overwrite an existing file."""
        return self.write_bytes(artifact_name, text.encode("utf-8"))

    def write_bytes(self, artifact_name: str, data: bytes) -> Path:
        """Write a binary artifact exclusively; never overwrite an existing file."""
        path = self._non_json_artifact(artifact_name)
        self._require_claimed()
        self._ensure_parent(path)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError as exc:
            raise FileExistsError(f"artifact already exists: {path}") from exc
        except OSError as exc:
            raise RunPathError(f"could not create artifact: {path}") from exc

        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
            raise
        return path

    def _non_json_artifact(self, name: str) -> Path:
        path = self.artifact(name)
        if path.suffix == ".json":
            raise RunPathError(f"use write_json for JSON artifact: {name!r}")
        return path

    def _require_claimed(self) -> None:
        if not self._claimed:
            raise RunPathError("run directory has not been claimed")
        if self.run_dir.is_symlink() or not self.run_dir.is_dir():
            raise RunPathError(f"claimed run directory is missing or unsafe: {self.run_dir}")

    def _ensure_parent(self, path: Path) -> None:
        parent = path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RunPathError(f"could not create artifact directory: {parent}") from exc
        self._assert_safe(parent)
        self._assert_safe(path)

    def _atomic_write_text(self, path: Path, text: str) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o644)
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise FileExistsError(f"artifact already exists: {path}") from exc
            finally:
                with contextlib.suppress(OSError):
                    temporary.unlink(missing_ok=True)
        except Exception:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
            raise

    def _assert_safe(self, path: Path) -> Path:
        try:
            path.relative_to(self.run_dir)
        except ValueError as exc:
            raise UnsafeRunPath(f"path escapes run directory: {path}") from exc

        if path.is_symlink():
            raise UnsafeRunPath(f"symlink paths are not allowed: {path}")
        for parent in path.parents:
            if parent == self.runs_root:
                break
            if parent.is_symlink():
                raise UnsafeRunPath(f"symlink path component is not allowed: {parent}")

        resolved = path.resolve(strict=False)
        run_dir_resolved = self.run_dir.resolve(strict=False)
        if not resolved.is_relative_to(run_dir_resolved):
            raise UnsafeRunPath(f"resolved path escapes run directory: {path}")
        if not resolved.is_relative_to(self.runs_root):
            raise UnsafeRunPath(f"resolved path escapes runs root: {path}")
        return path
