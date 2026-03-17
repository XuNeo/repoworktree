"""Unit tests for repoworktree/metadata.py — Metadata read/write."""

import json
from pathlib import Path

import pytest
from repoworktree.metadata import (
    WorktreeEntry,
    WorkspaceMetadata,
    WorkspaceIndex,
    create_workspace_metadata,
    save_workspace_metadata,
    load_workspace_metadata,
    detect_workspace,
    load_workspace_index,
    save_workspace_index,
)


def test_create_workspace_json(tmp_path):
    """Create .workspace.json with all fields populated."""
    meta = create_workspace_metadata(
        source="/home/neo/projects/vela",
        name="test-ws",
        worktrees=[WorktreeEntry("nuttx"), WorktreeEntry("apps", pinned="abc123")],
    )
    save_workspace_metadata(tmp_path, meta)

    data = json.loads((tmp_path / ".workspace.json").read_text())
    assert data["version"] == 1
    assert data["source"] == "/home/neo/projects/vela"
    assert data["name"] == "test-ws"
    assert "created" in data
    assert len(data["worktrees"]) == 2
    assert data["worktrees"][0]["path"] == "nuttx"
    assert data["worktrees"][1]["pinned"] == "abc123"


def test_read_workspace_json(tmp_path):
    """Read back a previously written .workspace.json."""
    meta = create_workspace_metadata(
        source="/src", name="ws1", worktrees=[WorktreeEntry("nuttx", branch="fix")]
    )
    save_workspace_metadata(tmp_path, meta)

    loaded = load_workspace_metadata(tmp_path)
    assert loaded.source == "/src"
    assert loaded.name == "ws1"
    assert len(loaded.worktrees) == 1
    assert loaded.worktrees[0].path == "nuttx"
    assert loaded.worktrees[0].branch == "fix"


def test_add_worktree_entry(tmp_path):
    """Adding a worktree entry updates the metadata."""
    meta = create_workspace_metadata(source="/src", name="ws1")
    assert len(meta.worktrees) == 0

    meta.add_worktree("nuttx", branch="dev")
    assert len(meta.worktrees) == 1
    assert meta.find_worktree("nuttx").branch == "dev"

    # Duplicate should raise
    with pytest.raises(ValueError, match="already exists"):
        meta.add_worktree("nuttx")


def test_remove_worktree_entry(tmp_path):
    """Removing a worktree entry updates the metadata."""
    meta = create_workspace_metadata(
        source="/src",
        name="ws1",
        worktrees=[WorktreeEntry("nuttx"), WorktreeEntry("apps")],
    )
    meta.remove_worktree("nuttx")
    assert len(meta.worktrees) == 1
    assert meta.find_worktree("nuttx") is None
    assert meta.find_worktree("apps") is not None

    # Removing non-existent should raise
    with pytest.raises(ValueError, match="not found"):
        meta.remove_worktree("nuttx")


def test_pin_worktree(tmp_path):
    """Pin sets the pinned field on a worktree entry."""
    meta = create_workspace_metadata(
        source="/src",
        name="ws1",
        worktrees=[WorktreeEntry("nuttx")],
    )
    assert meta.find_worktree("nuttx").pinned is None

    meta.pin_worktree("nuttx", "v12.0.0")
    assert meta.find_worktree("nuttx").pinned == "v12.0.0"


def test_unpin_worktree(tmp_path):
    """Unpin clears the pinned field."""
    meta = create_workspace_metadata(
        source="/src",
        name="ws1",
        worktrees=[WorktreeEntry("nuttx", pinned="v12.0.0")],
    )
    meta.unpin_worktree("nuttx")
    assert meta.find_worktree("nuttx").pinned is None


def test_register_workspace(tmp_path):
    """Register adds an entry to .workspaces.json."""
    index = WorkspaceIndex()
    index.register("ws1", "/tmp/ws1", "2026-02-21T10:00:00+00:00")
    save_workspace_index(tmp_path, index)

    loaded = load_workspace_index(tmp_path)
    assert len(loaded.list_all()) == 1
    assert loaded.list_all()[0]["name"] == "ws1"
    assert loaded.list_all()[0]["path"] == "/tmp/ws1"


def test_unregister_workspace(tmp_path):
    """Unregister removes an entry from .workspaces.json."""
    index = WorkspaceIndex()
    index.register("ws1", "/tmp/ws1", "2026-02-21T10:00:00+00:00")
    index.register("ws2", "/tmp/ws2", "2026-02-21T11:00:00+00:00")
    index.unregister("/tmp/ws1")
    assert len(index.list_all()) == 1
    assert index.list_all()[0]["name"] == "ws2"

    with pytest.raises(ValueError, match="not found"):
        index.unregister("/tmp/ws1")


def test_list_workspaces(tmp_path):
    """List returns all registered workspaces."""
    index = WorkspaceIndex()
    index.register("ws1", "/tmp/ws1", "2026-02-21T10:00:00+00:00")
    index.register("ws2", "/tmp/ws2", "2026-02-21T11:00:00+00:00")
    save_workspace_index(tmp_path, index)

    loaded = load_workspace_index(tmp_path)
    all_ws = loaded.list_all()
    assert len(all_ws) == 2
    names = {w["name"] for w in all_ws}
    assert names == {"ws1", "ws2"}


def test_find_workspace_by_name(tmp_path):
    """Find by name returns the matching workspace or None."""
    index = WorkspaceIndex()
    index.register("ws1", "/tmp/ws1", "2026-02-21T10:00:00+00:00")
    index.register("ws2", "/tmp/ws2", "2026-02-21T11:00:00+00:00")

    found = index.find_by_name("ws1")
    assert found is not None
    assert found["path"] == "/tmp/ws1"

    assert index.find_by_name("nonexistent") is None


def test_detect_workspace(tmp_path):
    """Detect workspace by walking up from a subdirectory."""
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    (ws_root / ".workspace.json").write_text("{}")

    # From workspace root
    assert detect_workspace(ws_root) == ws_root

    # From a subdirectory
    sub = ws_root / "nuttx" / "arch"
    sub.mkdir(parents=True)
    assert detect_workspace(sub) == ws_root

    # From unrelated directory
    assert detect_workspace(tmp_path / "other") is None
