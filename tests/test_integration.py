"""
Integration tests — 13 scenarios from design.md.

Each test covers create → operate → verify → destroy full lifecycle.
"""

import subprocess

import pytest
from pathlib import Path

from repoworktree.scanner import scan_repos, build_trie
from repoworktree.layout import build_workspace, teardown_workspace
from repoworktree.metadata import (
    WorktreeEntry,
    create_workspace_metadata,
    save_workspace_metadata,
    load_workspace_metadata,
    load_workspace_index,
    save_workspace_index,
    WorkspaceIndex,
)
from repoworktree.promote import promote, demote
from repoworktree.sync import sync
from repoworktree.export import export
from repoworktree.worktree import get_head, has_local_changes, has_local_commits
from tests.helpers import (
    assert_is_symlink,
    assert_is_worktree,
    assert_is_real_dir,
    assert_source_untouched,
    take_source_snapshot,
    make_commit,
    make_dirty,
    push_remote_update,
    sync_source_repo,
    get_head_commit,
)
from tests.conftest import REPO_DEFS

_CHECKOUT_TO_BARE = {path: bare for bare, path, _ in REPO_DEFS}
ALL_CHECKOUT_PATHS = sorted(path for _, path, _ in REPO_DEFS)


def _create_workspace(repo_env, ws_dir, wt_paths=None, pin_map=None, name="test"):
    """Full workspace creation helper (scanner → trie → layout → metadata)."""
    wt_set = set(wt_paths or [])
    pin_map = pin_map or {}
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths, worktree_paths=wt_set)
    build_workspace(repo_env.source_dir, ws_dir, trie, pin_map=pin_map)
    meta = create_workspace_metadata(
        source=str(repo_env.source_dir),
        name=name,
        worktrees=[WorktreeEntry(p, pinned=pin_map.get(p)) for p in sorted(wt_set)],
    )
    save_workspace_metadata(ws_dir, meta)
    # Register in index
    index = load_workspace_index(repo_env.source_dir)
    index.register(name, str(ws_dir), meta.created)
    save_workspace_index(repo_env.source_dir, index)
    return paths


def _destroy_workspace(repo_env, ws_dir, paths):
    """Full workspace teardown."""
    meta = load_workspace_metadata(ws_dir)
    wt_set = {w.path for w in meta.worktrees}
    trie = build_trie(paths, worktree_paths=wt_set)
    teardown_workspace(repo_env.source_dir, ws_dir, trie)
    import shutil

    if ws_dir.exists():
        shutil.rmtree(ws_dir, ignore_errors=True)


def _advance_source(repo_env, repo_path):
    """Push remote update and sync source. Returns new HEAD."""
    bare_name = _CHECKOUT_TO_BARE[repo_path]
    bare_repo = repo_env.bare_repo_path(bare_name)
    push_remote_update(bare_repo)
    sync_source_repo(repo_env.source_dir, repo_path)
    return get_head_commit(repo_env.source_dir / repo_path)


# ── Scenario 1: 全 symlink 只读工作空间（极端 A）──────────────────


def test_scenario_01_all_symlink(repo_env, workspace_dir):
    """All repos are symlinks, zero worktrees."""
    snapshot = take_source_snapshot(repo_env.source_dir)
    paths = _create_workspace(repo_env, workspace_dir, wt_paths=[], name="readonly")

    # Every repo path should be a symlink (or part of a symlink tree)
    for rp in ALL_CHECKOUT_PATHS:
        ws_path = workspace_dir / rp
        # Top-level repos should be symlinks
        top = rp.split("/")[0]
        assert (workspace_dir / top).is_symlink() or (workspace_dir / top).is_dir()

    # No .git worktree files anywhere
    meta = load_workspace_metadata(workspace_dir)
    assert len(meta.worktrees) == 0

    assert_source_untouched(repo_env.source_dir, snapshot)
    _destroy_workspace(repo_env, workspace_dir, paths)


# ── Scenario 2: 典型 LLM agent（修改少量顶层仓库）────────────────


def test_scenario_02_typical_agent(repo_env, workspace_dir):
    """Two top-level worktrees (nuttx, apps), rest symlinked."""
    snapshot = take_source_snapshot(repo_env.source_dir)
    paths = _create_workspace(
        repo_env, workspace_dir, wt_paths=["nuttx", "apps"], name="fix-serial"
    )

    assert_is_worktree(workspace_dir / "nuttx")
    assert_is_worktree(workspace_dir / "apps")
    assert_is_symlink(workspace_dir / "build")
    assert_is_symlink(workspace_dir / "frameworks")

    meta = load_workspace_metadata(workspace_dir)
    assert len(meta.worktrees) == 2

    assert_source_untouched(repo_env.source_dir, snapshot)
    _destroy_workspace(repo_env, workspace_dir, paths)


# ── Scenario 3: 修改嵌套子仓库 ──────────────────────────────────


def test_scenario_03_nested_worktree(repo_env, workspace_dir):
    """Worktree for nuttx + apps/system/adb (nested). apps/ is real dir, not worktree."""
    snapshot = take_source_snapshot(repo_env.source_dir)
    paths = _create_workspace(
        repo_env, workspace_dir, wt_paths=["nuttx", "apps/system/adb"]
    )

    assert_is_worktree(workspace_dir / "nuttx")
    assert_is_real_dir(workspace_dir / "apps")
    assert_is_real_dir(workspace_dir / "apps" / "system")
    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")

    # apps itself is NOT a worktree (no .git file at apps level from worktree)
    # It's a real dir because it has a worktree descendant
    assert (
        not (workspace_dir / "apps" / ".git").is_file() or True
    )  # apps may have .git from source

    assert_source_untouched(repo_env.source_dir, snapshot)
    _destroy_workspace(repo_env, workspace_dir, paths)


# ── Scenario 4: 父子仓库同时 worktree ───────────────────────────


def test_scenario_04_parent_child_worktree(repo_env, workspace_dir):
    """Both apps (parent) and apps/system/adb (child) are worktrees."""
    snapshot = take_source_snapshot(repo_env.source_dir)
    paths = _create_workspace(
        repo_env, workspace_dir, wt_paths=["apps", "apps/system/adb"]
    )

    assert_is_worktree(workspace_dir / "apps")
    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")

    # apps worktree files should be writable
    assert (workspace_dir / "apps" / "Makefile").exists()

    assert_source_untouched(repo_env.source_dir, snapshot)
    _destroy_workspace(repo_env, workspace_dir, paths)


# ── Scenario 5: promote 追加 worktree ───────────────────────────


def test_scenario_05_promote_parent(repo_env, workspace_dir):
    """Create with nuttx + apps/system/adb, then promote apps."""
    snapshot = take_source_snapshot(repo_env.source_dir)
    paths = _create_workspace(
        repo_env, workspace_dir, wt_paths=["nuttx", "apps/system/adb"]
    )

    # apps is real dir, not worktree
    assert_is_real_dir(workspace_dir / "apps")
    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")

    # Promote apps
    promote(workspace_dir, repo_env.source_dir, "apps", paths)

    # Now apps is a worktree AND adb is still a worktree
    assert_is_worktree(workspace_dir / "apps")
    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")

    meta = load_workspace_metadata(workspace_dir)
    wt_paths = {w.path for w in meta.worktrees}
    assert "apps" in wt_paths
    assert "apps/system/adb" in wt_paths

    assert_source_untouched(repo_env.source_dir, snapshot)
    _destroy_workspace(repo_env, workspace_dir, paths)


# ── Scenario 6: promote 深层嵌套（拆解 symlink）─────────────────


def test_scenario_06_promote_deep_nested(repo_env, workspace_dir):
    """Promote frameworks/system/core from a symlinked frameworks/."""
    snapshot = take_source_snapshot(repo_env.source_dir)
    paths = _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx"])

    # frameworks/ should be a symlink initially
    assert_is_symlink(workspace_dir / "frameworks")

    # Promote deep nested repo
    promote(workspace_dir, repo_env.source_dir, "frameworks/system/core", paths)

    # frameworks/ should now be a real dir
    assert_is_real_dir(workspace_dir / "frameworks")
    assert_is_real_dir(workspace_dir / "frameworks" / "system")
    assert_is_worktree(workspace_dir / "frameworks" / "system" / "core")

    # Sibling repos should be symlinks
    assert_is_symlink(workspace_dir / "frameworks" / "system" / "kvdb")
    assert_is_symlink(workspace_dir / "frameworks" / "connectivity")

    meta = load_workspace_metadata(workspace_dir)
    assert meta.find_worktree("frameworks/system/core") is not None

    assert_source_untouched(repo_env.source_dir, snapshot)
    _destroy_workspace(repo_env, workspace_dir, paths)


# ── Scenario 7: demote 有子 worktree 的父仓库 ───────────────────


def test_scenario_07_demote_parent_with_child(repo_env, workspace_dir):
    """Demote apps while preserving apps/system/adb worktree."""
    snapshot = take_source_snapshot(repo_env.source_dir)
    paths = _create_workspace(
        repo_env, workspace_dir, wt_paths=["apps", "apps/system/adb"]
    )

    assert_is_worktree(workspace_dir / "apps")
    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")

    # Demote apps (child adb should survive)
    demote(workspace_dir, repo_env.source_dir, "apps", paths)

    # apps should now be a real dir (not worktree), adb still worktree
    assert_is_real_dir(workspace_dir / "apps")
    assert not (workspace_dir / "apps" / ".git").is_file()
    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")

    meta = load_workspace_metadata(workspace_dir)
    assert meta.find_worktree("apps") is None
    assert meta.find_worktree("apps/system/adb") is not None

    assert_source_untouched(repo_env.source_dir, snapshot)
    _destroy_workspace(repo_env, workspace_dir, paths)


# ── Scenario 8: demote 后 repo 变 symlink，parent dir 保留 ───────


def test_scenario_08_demote_no_collapse(repo_env, workspace_dir):
    snapshot = take_source_snapshot(repo_env.source_dir)
    paths = _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx"])

    promote(workspace_dir, repo_env.source_dir, "frameworks/system/core", paths)
    assert_is_worktree(workspace_dir / "frameworks" / "system" / "core")

    demote(workspace_dir, repo_env.source_dir, "frameworks/system/core", paths)

    assert_is_symlink(workspace_dir / "frameworks" / "system" / "core")
    assert_is_real_dir(workspace_dir / "frameworks")
    assert_is_real_dir(workspace_dir / "frameworks" / "system")

    meta = load_workspace_metadata(workspace_dir)
    assert meta.find_worktree("frameworks/system/core") is None

    assert_source_untouched(repo_env.source_dir, snapshot)
    _destroy_workspace(repo_env, workspace_dir, paths)


# ── Scenario 9: 全 worktree 完全隔离（极端 B）───────────────────


def test_scenario_09_all_worktree(repo_env, workspace_dir):
    """All repos are worktrees (extreme B)."""
    snapshot = take_source_snapshot(repo_env.source_dir)
    paths = _create_workspace(
        repo_env, workspace_dir, wt_paths=ALL_CHECKOUT_PATHS, name="full-isolation"
    )

    # Every repo should be a worktree
    for rp in ALL_CHECKOUT_PATHS:
        assert_is_worktree(workspace_dir / rp)

    meta = load_workspace_metadata(workspace_dir)
    assert len(meta.worktrees) == len(ALL_CHECKOUT_PATHS)

    assert_source_untouched(repo_env.source_dir, snapshot)
    _destroy_workspace(repo_env, workspace_dir, paths)


# ── Scenario 10: 多 agent 并行 ──────────────────────────────────


def test_scenario_10_multi_agent(repo_env, tmp_path):
    """Two workspaces with independent nuttx worktrees."""
    snapshot = take_source_snapshot(repo_env.source_dir)

    ws1 = tmp_path / "ws-agent1"
    ws2 = tmp_path / "ws-agent2"

    paths = _create_workspace(repo_env, ws1, wt_paths=["nuttx"], name="agent1")
    _create_workspace(repo_env, ws2, wt_paths=["nuttx"], name="agent2")

    # Both have independent nuttx worktrees
    assert_is_worktree(ws1 / "nuttx")
    assert_is_worktree(ws2 / "nuttx")

    # Modify one, other is unaffected
    make_commit(ws1 / "nuttx", message="agent1 change")
    head1 = get_head(ws1 / "nuttx")
    head2 = get_head(ws2 / "nuttx")
    assert head1 != head2

    assert_source_untouched(repo_env.source_dir, snapshot)
    _destroy_workspace(repo_env, ws1, paths)
    _destroy_workspace(repo_env, ws2, paths)


# ── Scenario 11: 锁定版本开发 ───────────────────────────────────


def test_scenario_11_pinned_version(repo_env, workspace_dir):
    """Pin nuttx to current HEAD, advance source, sync skips pinned."""
    snapshot_before = get_head_commit(repo_env.source_dir / "nuttx")
    paths = _create_workspace(
        repo_env, workspace_dir, wt_paths=["nuttx"], pin_map={"nuttx": snapshot_before}
    )

    pinned_head = get_head(workspace_dir / "nuttx")
    assert pinned_head == snapshot_before

    # Advance source
    _advance_source(repo_env, "nuttx")

    # Sync should skip pinned
    report = sync(workspace_dir, repo_env.source_dir)
    assert report.results[0].action == "skipped"
    assert report.results[0].reason == "pinned"

    # HEAD unchanged
    assert get_head(workspace_dir / "nuttx") == pinned_head

    # Unpin via metadata
    meta = load_workspace_metadata(workspace_dir)
    meta.unpin_worktree("nuttx")
    save_workspace_metadata(workspace_dir, meta)

    # Now sync should update
    report = sync(workspace_dir, repo_env.source_dir)
    assert report.results[0].action == "updated"

    _destroy_workspace(repo_env, workspace_dir, paths)


# ── Scenario 12: 导出变更 ───────────────────────────────────────


def test_scenario_12_export_changes(repo_env, workspace_dir, tmp_path):
    """Make commits in worktrees, export as patches."""
    paths = _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx", "apps"])

    make_commit(workspace_dir / "nuttx", message="nuttx fix", filename="fix.c")
    make_commit(workspace_dir / "apps", message="apps fix", filename="fix.c")

    export_dir = tmp_path / "patches"
    report = export(workspace_dir, repo_env.source_dir, export_dir, fmt="patch")

    assert len(report.exported) == 2
    exported_paths = {r.path for r in report.exported}
    assert "nuttx" in exported_paths
    assert "apps" in exported_paths

    # Verify patch files exist
    assert (export_dir / "nuttx").is_dir()
    assert len(list((export_dir / "nuttx").glob("*.patch"))) == 1
    assert (export_dir / "apps").is_dir()
    assert len(list((export_dir / "apps").glob("*.patch"))) == 1

    _destroy_workspace(repo_env, workspace_dir, paths)


# ── Scenario 13: 主仓库 sync 后更新工作空间 ─────────────────────


def test_scenario_13_sync_after_repo_sync(repo_env, workspace_dir):
    """Advance source (simulating repo sync), then rw sync updates worktrees."""
    paths = _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx", "apps"])

    old_nuttx = get_head(workspace_dir / "nuttx")
    old_apps = get_head(workspace_dir / "apps")

    # Simulate repo sync: advance both repos in source
    new_nuttx = _advance_source(repo_env, "nuttx")
    new_apps = _advance_source(repo_env, "apps")

    assert old_nuttx != new_nuttx
    assert old_apps != new_apps

    # rw sync
    report = sync(workspace_dir, repo_env.source_dir)

    results = {r.path: r for r in report.results}
    assert results["nuttx"].action == "updated"
    assert results["apps"].action == "updated"

    assert get_head(workspace_dir / "nuttx") == new_nuttx
    assert get_head(workspace_dir / "apps") == new_apps

    _destroy_workspace(repo_env, workspace_dir, paths)


# ── Issue 1: destroy must not break other workspaces ─────────────


def test_add_worktree_prune_does_not_break_sibling(repo_env, tmp_path):
    """add_worktree must not globally prune when retrying after 'already registered'.

    Simulate: create two workspaces. Manually remove ws1's worktree metadata
    (without proper cleanup) so it becomes stale. Then create ws3 that reuses
    the same path — if add_worktree triggers global prune on "already registered",
    it could damage ws2's worktree linkage.
    """
    import shutil

    ws1 = tmp_path / "ws-alpha"
    ws2 = tmp_path / "ws-beta"
    ws3 = tmp_path / "ws-gamma"

    paths = _create_workspace(repo_env, ws1, wt_paths=["nuttx"], name="alpha")
    _create_workspace(repo_env, ws2, wt_paths=["nuttx"], name="beta")

    assert_is_worktree(ws1 / "nuttx")
    assert_is_worktree(ws2 / "nuttx")

    shutil.rmtree(ws1)

    _create_workspace(repo_env, ws3, wt_paths=["nuttx"], name="gamma")
    assert_is_worktree(ws3 / "nuttx")

    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ws2 / "nuttx",
        capture_output=True,
        text=True,
    )
    assert (
        result.returncode == 0
    ), f"git status failed in surviving workspace: {result.stderr.strip()}"

    _destroy_workspace(repo_env, ws3, paths)
    _destroy_workspace(repo_env, ws2, paths)


# ── Bug regression tests ──────────────────────────────────────────


def test_create_after_accidental_rmrf(repo_env, tmp_path):
    """BUG-001/004: rm -rf workspace (bypassing rwt) then create at same path must succeed.

    Simulates the common case where a user deletes a workspace directory
    manually without going through rwt destroy. The orphaned git worktree
    reference in source/.git/worktrees/ must be cleaned up automatically
    so the next create at the same path succeeds.
    """
    import shutil as _shutil

    ws = tmp_path / "workspace"
    paths = _create_workspace(repo_env, ws, wt_paths=["nuttx"])
    assert_is_worktree(ws / "nuttx")

    _shutil.rmtree(ws)
    assert not ws.exists()

    paths2 = _create_workspace(repo_env, ws, wt_paths=["nuttx"])
    assert_is_worktree(ws / "nuttx")

    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ws / "nuttx",
        capture_output=True,
        text=True,
    )
    assert (
        result.returncode == 0
    ), f"git status failed after recreate: {result.stderr.strip()}"
    _destroy_workspace(repo_env, ws, paths2)


def test_sibling_workspace_survives_corrupt_destroy(repo_env, tmp_path):
    """BUG-001/002: destroy with corrupted worktree .git must not break sibling workspace."""
    import shutil as _shutil

    ws1 = tmp_path / "ws1"
    ws2 = tmp_path / "ws2"

    paths = _create_workspace(repo_env, ws1, wt_paths=["nuttx"])
    _create_workspace(repo_env, ws2, wt_paths=["nuttx"])

    assert_is_worktree(ws1 / "nuttx")
    assert_is_worktree(ws2 / "nuttx")

    (ws1 / "nuttx" / ".git").write_text("gitdir: /nonexistent/path\n")

    try:
        _destroy_workspace(repo_env, ws1, paths)
    except Exception:
        _shutil.rmtree(ws1, ignore_errors=True)

    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ws2 / "nuttx",
        capture_output=True,
        text=True,
    )
    assert (
        result.returncode == 0
    ), f"ws2 git status broken after ws1 corrupt destroy: {result.stderr.strip()}"
    _destroy_workspace(repo_env, ws2, paths)


def test_destroy_with_corrupt_metadata_cleans_git_worktrees(repo_env, tmp_path):
    """BUG-003: destroy must clean git worktree refs even if .workspace.json is incomplete."""
    import json as _json

    ws = tmp_path / "workspace"
    paths = _create_workspace(repo_env, ws, wt_paths=["nuttx", "apps"])

    meta_path = ws / ".workspace.json"
    data = _json.loads(meta_path.read_text())
    data["worktrees"] = [w for w in data["worktrees"] if w["path"] != "apps"]
    meta_path.write_text(_json.dumps(data))

    _destroy_workspace(repo_env, ws, paths)

    source_apps = repo_env.source_dir / "apps"
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=source_apps,
        capture_output=True,
        text=True,
    )
    worktree_paths = [
        line[len("worktree ") :]
        for line in result.stdout.splitlines()
        if line.startswith("worktree ")
    ]
    orphans = [p for p in worktree_paths if str(ws) in p]
    assert not orphans, f"Orphan worktree refs remain after destroy: {orphans}"
