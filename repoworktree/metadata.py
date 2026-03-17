"""
Metadata — read/write .workspace.json and .workspaces.json.

.workspace.json lives inside each workspace directory.
.workspaces.json lives in the source repo root, indexing all workspaces.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


# ── Workspace metadata (.workspace.json) ──────────────────────────


class WorktreeEntry:
    """A single worktree sub-repo entry."""

    def __init__(self, path: str, branch: str | None = None, pinned: str | None = None):
        self.path = path
        self.branch = branch
        self.pinned = pinned

    def to_dict(self) -> dict:
        return {"path": self.path, "branch": self.branch, "pinned": self.pinned}

    @classmethod
    def from_dict(cls, d: dict) -> WorktreeEntry:
        return cls(path=d["path"], branch=d.get("branch"), pinned=d.get("pinned"))


class WorkspaceMetadata:
    """Metadata stored in .workspace.json inside a workspace."""

    VERSION = 1

    def __init__(
        self,
        source: str,
        name: str,
        created: str,
        worktrees: list[WorktreeEntry] | None = None,
    ):
        self.source = source
        self.name = name
        self.created = created
        self.worktrees = worktrees or []

    def to_dict(self) -> dict:
        return {
            "version": self.VERSION,
            "source": self.source,
            "name": self.name,
            "created": self.created,
            "worktrees": [w.to_dict() for w in self.worktrees],
        }

    @classmethod
    def from_dict(cls, d: dict) -> WorkspaceMetadata:
        return cls(
            source=d["source"],
            name=d["name"],
            created=d["created"],
            worktrees=[WorktreeEntry.from_dict(w) for w in d.get("worktrees", [])],
        )

    def find_worktree(self, path: str) -> WorktreeEntry | None:
        for w in self.worktrees:
            if w.path == path:
                return w
        return None

    def add_worktree(
        self, path: str, branch: str | None = None, pinned: str | None = None
    ):
        if self.find_worktree(path):
            raise ValueError(f"Worktree already exists: {path}")
        self.worktrees.append(WorktreeEntry(path, branch, pinned))

    def remove_worktree(self, path: str):
        entry = self.find_worktree(path)
        if not entry:
            raise ValueError(f"Worktree not found: {path}")
        self.worktrees.remove(entry)

    def pin_worktree(self, path: str, version: str):
        entry = self.find_worktree(path)
        if not entry:
            raise ValueError(f"Worktree not found: {path}")
        entry.pinned = version

    def unpin_worktree(self, path: str):
        entry = self.find_worktree(path)
        if not entry:
            raise ValueError(f"Worktree not found: {path}")
        entry.pinned = None


def create_workspace_metadata(
    source: str, name: str, worktrees: list[WorktreeEntry] | None = None
) -> WorkspaceMetadata:
    """Create a new WorkspaceMetadata with current timestamp."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return WorkspaceMetadata(source=source, name=name, created=now, worktrees=worktrees)


def save_workspace_metadata(workspace_dir: Path, meta: WorkspaceMetadata):
    """Write .workspace.json to workspace directory."""
    path = workspace_dir / ".workspace.json"
    path.write_text(json.dumps(meta.to_dict(), indent=2) + "\n")


def load_workspace_metadata(workspace_dir: Path) -> WorkspaceMetadata:
    """Read .workspace.json from workspace directory."""
    path = workspace_dir / ".workspace.json"
    if not path.exists():
        raise FileNotFoundError(f"No .workspace.json found in {workspace_dir}")
    data = json.loads(path.read_text())
    return WorkspaceMetadata.from_dict(data)


def detect_workspace(start: Path | None = None) -> Path | None:
    """Find workspace root by looking for .workspace.json in start or parents."""
    start = start or Path.cwd()
    for p in [start, *start.parents]:
        if (p / ".workspace.json").exists():
            return p
    return None


# ── Workspace index (.workspaces.json) ────────────────────────────


class WorkspaceIndex:
    """Index of all workspaces, stored in .workspaces.json in the source root."""

    def __init__(self, workspaces: list[dict[str, str]] | None = None):
        self.workspaces = workspaces or []

    def to_dict(self) -> dict:
        return {"workspaces": self.workspaces}

    @classmethod
    def from_dict(cls, d: dict) -> WorkspaceIndex:
        return cls(workspaces=d.get("workspaces", []))

    def register(self, name: str, path: str, created: str):
        # Remove existing entry with same path if any
        self.workspaces = [w for w in self.workspaces if w["path"] != path]
        self.workspaces.append({"name": name, "path": path, "created": created})

    def unregister(self, path: str):
        before = len(self.workspaces)
        self.workspaces = [w for w in self.workspaces if w["path"] != path]
        if len(self.workspaces) == before:
            raise ValueError(f"Workspace not found in index: {path}")

    def find_by_name(self, name: str) -> dict[str, str] | None:
        for w in self.workspaces:
            if w["name"] == name:
                return w
        return None

    def find_by_path(self, path: str) -> dict[str, str] | None:
        for w in self.workspaces:
            if w["path"] == path:
                return w
        return None

    def list_all(self) -> list[dict[str, str]]:
        return list(self.workspaces)


def load_workspace_index(source_dir: Path) -> WorkspaceIndex:
    """Load .workspaces.json from source directory. Returns empty index if not found."""
    path = source_dir / ".workspaces.json"
    if not path.exists():
        return WorkspaceIndex()
    data = json.loads(path.read_text())
    return WorkspaceIndex.from_dict(data)


def save_workspace_index(source_dir: Path, index: WorkspaceIndex):
    """Write .workspaces.json to source directory."""
    path = source_dir / ".workspaces.json"
    path.write_text(json.dumps(index.to_dict(), indent=2) + "\n")
