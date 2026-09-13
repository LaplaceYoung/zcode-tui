"""Read-only access to ZCode checkpoints (~/.zcode/v2/checkpoints).

Local format (verified 2026-09-13): per workspace the directory
`<sha256(workspacePath)[:12]>/` holds:
  state.json                 workspace metadata + lastAccepted hashes
  manifests/<sha256>.json    repo_snapshot_manifest/v2 — [{path, sizeBytes}]
  extra-manifests/<sha256>.json  app-memory config inventory (contentHash)

The manifests are pure inventory: they contain paths and byte sizes, NEVER
file contents (encrypted contents are synced to zcode's cloud). ztui can
therefore fully implement browse + drift analysis, but content restore is
impossible locally and must be reported as such.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

CP_ROOT = Path.home() / ".zcode" / "v2" / "checkpoints"

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".tox",
    "dist", "build", ".next", ".cache", ".idea", ".vscode",
}


def project_hash(workspace: Path) -> str:
    return hashlib.sha256(str(workspace).encode()).hexdigest()[:12]


@dataclass
class Checkpoint:
    full_hash: str
    created_ms: int
    file_count: int
    total_bytes: int
    accepted: bool
    failure_count: int
    workspace_path: str
    project: str  # 12-char hash dir
    extra_hash: str | None


@dataclass
class ExtraConfig:
    path: str
    size_bytes: int
    content_hash: str
    source: str
    group: str


@dataclass
class Drift:
    missing: list[str] = field(default_factory=list)       # in checkpoint, not in workspace
    changed_size: list[tuple[str, int, int]] = field(default_factory=list)  # path, old, new
    added: list[str] = field(default_factory=list)          # in workspace, not in checkpoint
    truncated: bool = False


def _load_state(cp_dir: Path) -> dict:
    try:
        return json.loads((cp_dir / "state.json").read_text(errors="replace"))
    except (OSError, ValueError):
        return {}


def list_checkpoints(workspace: Path | None = None) -> list[tuple[str, str, list[Checkpoint]]]:
    """Return [(project_hash, workspace_path, [Checkpoint...])], newest first."""
    if not CP_ROOT.exists():
        return []
    projects: list[tuple[str, Path]] = []
    if workspace is not None:
        wanted = project_hash(workspace)
        d = CP_ROOT / wanted
        if d.exists():
            projects = [(wanted, d)]
    else:
        projects = [(d.name, d) for d in sorted(CP_ROOT.iterdir()) if d.is_dir()]
    out = []
    for phash, cp_dir in projects:
        state = _load_state(cp_dir)
        accepted_path = state.get("lastAcceptedManifestPath", "")
        cps: list[Checkpoint] = []
        mdir = cp_dir / "manifests"
        if mdir.exists():
            for mf in mdir.glob("*.json"):
                try:
                    data = json.loads(mf.read_text(errors="replace"))
                except (OSError, ValueError):
                    continue
                files = data.get("files", [])
                cps.append(
                    Checkpoint(
                        full_hash=mf.stem,
                        created_ms=data.get("createdAt", 0),
                        file_count=len(files),
                        total_bytes=sum(f.get("sizeBytes", 0) for f in files),
                        accepted=(accepted_path.endswith(mf.name)),
                        failure_count=state.get("failureCount", 0),
                        workspace_path=state.get("workspacePath", ""),
                        project=phash,
                        extra_hash=state.get("lastAcceptedExtraManifestHash"),
                    )
                )
        cps.sort(key=lambda c: c.created_ms, reverse=True)
        if cps or state:
            out.append((phash, state.get("workspacePath", phash), cps))
    out.sort(key=lambda x: (x[2][0].created_ms if x[2] else 0), reverse=True)
    return out


def load_manifest(project: str, full_hash: str) -> dict:
    mf = CP_ROOT / project / "manifests" / f"{full_hash}.json"
    return json.loads(mf.read_text(errors="replace"))


def load_extra_configs(project: str) -> list[ExtraConfig]:
    out: list[ExtraConfig] = []
    edir = CP_ROOT / project / "extra-manifests"
    if not edir.exists():
        return out
    jsons = sorted(edir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not jsons:
        return out
    try:
        data = json.loads(jsons[0].read_text(errors="replace"))
    except (OSError, ValueError):
        return out
    for group in data.get("groups", []):
        for f in group.get("files", []):
            out.append(
                ExtraConfig(
                    path=f.get("path", ""),
                    size_bytes=f.get("sizeBytes", 0),
                    content_hash=f.get("contentHash", ""),
                    source=f.get("source", ""),
                    group=group.get("groupId", ""),
                )
            )
    return out


def _workspace_sizes(workspace: Path) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for dirpath, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            try:
                rel = str(p.relative_to(workspace))
                sizes[rel] = p.stat().st_size
            except OSError:
                continue
    return sizes


def analyze_drift(workspace: Path, manifest_files: list[dict], cap: int = 200) -> Drift:
    current = _workspace_sizes(workspace)
    drift = Drift()
    checkpoint_paths = set()
    for entry in manifest_files:
        path = entry.get("path", "")
        size = entry.get("sizeBytes", 0)
        checkpoint_paths.add(path)
        if path not in current:
            drift.missing.append(path)
        elif current[path] != size:
            drift.changed_size.append((path, size, current[path]))
    drift.added = sorted(p for p in current if p not in checkpoint_paths)
    if len(drift.missing) + len(drift.changed_size) > cap:
        drift.truncated = True
        drift.missing = drift.missing[: cap // 2]
        drift.changed_size = drift.changed_size[: cap - cap // 2]
    drift.missing.sort()
    drift.changed_size.sort(key=lambda x: x[0])
    return drift


def fmt_bytes(n: int | float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if n >= 10 else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
