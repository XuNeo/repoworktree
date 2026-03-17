"""Unit tests for repoworktree/sync.py — Sync."""

import pytest
from repoworktree.scanner import scan_repos, build_trie
from repoworktree.layout import build_workspace, teardown_workspace
from repoworktree.metadata import (
    load_workspace_metadata,
    save_workspace_metadata,
    create_workspace_metadata,
    WorktreeEntry,
)
from repoworktree.sync import sync
from repoworktree.worktree import get_head
from tests.helpers import (
    assert_is_worktree,
    make_dirty,
    make_commit,
    push_remote_update,
    sync_source_repo,
    get_head_commit,
)
from tests.conftest import REPO_DEFS

# Map checkout_path → bare_repo_name
_CHECKOUT_TO_BARE = {path: bare for bare, path, _ in REPO_DEFS}


def _create_ws_with_worktrees(repo_env, workspace_dir, wt_set, pin_map=None):
    """Helper: create a workspace with specified worktrees."""
    pin_map = pin_map or {}
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths, worktree_paths=wt_set)
    build_workspace(repo_env.source_dir, workspace_dir, trie, pin_map=pin_map)
    meta = create_workspace_metadata(
        source=str(repo_env.source_dir),
        name="test",
        worktrees=[
            WorktreeEntry(
                p,
                pinned=pin_map.get(p),
            )
            for p in sorted(wt_set)
        ],
    )
    save_workspace_metadata(workspace_dir, meta)
    return paths


def _cleanup_worktrees(repo_env, workspace_dir, paths):
    """Helper: teardown workspace worktrees."""
    meta = load_workspace_metadata(workspace_dir)
    wt_set = {w.path for w in meta.worktrees}
    trie = build_trie(paths, worktree_paths=wt_set)
    teardown_workspace(repo_env.source_dir, workspace_dir, trie)


def _advance_source(repo_env, repo_path):
    """Push a remote update and sync source to it. Returns new HEAD."""
    bare_name = _CHECKOUT_TO_BARE[repo_path]
    bare_repo = repo_env.bare_repo_path(bare_name)
    push_remote_update(bare_repo)
    sync_source_repo(repo_env.source_dir, repo_path)
    return get_head_commit(repo_env.source_dir / repo_path)


def test_sync_clean_updated(repo_env, workspace_dir):
    """Sync a clean worktree after source advances → updated."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})

    old_head = get_head(workspace_dir / "nuttx")
    new_src_head = _advance_source(repo_env, "nuttx")
    assert old_head != new_src_head

    report = sync(workspace_dir, repo_env.source_dir)

    assert len(report.results) == 1
    r = report.results[0]
    assert r.path == "nuttx"
    assert r.action == "updated"
    assert r.new_head == new_src_head
    assert get_head(workspace_dir / "nuttx") == new_src_head

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_sync_already_up_to_date(repo_env, workspace_dir):
    """Sync when worktree is already at source HEAD → no-op."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})

    report = sync(workspace_dir, repo_env.source_dir)

    assert len(report.results) == 1
    r = report.results[0]
    assert r.action == "already_up_to_date"

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_sync_pinned_skipped(repo_env, workspace_dir):
    """Sync a pinned worktree → skipped."""
    pin_commit = get_head(repo_env.source_dir / "nuttx")
    paths = _create_ws_with_worktrees(
        repo_env,
        workspace_dir,
        {"nuttx"},
        pin_map={"nuttx": pin_commit},
    )

    _advance_source(repo_env, "nuttx")

    report = sync(workspace_dir, repo_env.source_dir)

    assert len(report.results) == 1
    r = report.results[0]
    assert r.action == "skipped"
    assert r.reason == "pinned"
    # HEAD should not have changed
    assert get_head(workspace_dir / "nuttx") == pin_commit

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_sync_dirty_skipped(repo_env, workspace_dir):
    """Sync a dirty worktree → skipped."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})

    _advance_source(repo_env, "nuttx")
    make_dirty(workspace_dir / "nuttx")

    report = sync(workspace_dir, repo_env.source_dir)

    assert len(report.results) == 1
    r = report.results[0]
    assert r.action == "skipped"
    assert "uncommitted" in r.reason

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_sync_local_commits_no_rebase(repo_env, workspace_dir):
    """Sync with local commits but no --rebase → skipped."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})

    _advance_source(repo_env, "nuttx")
    make_commit(workspace_dir / "nuttx", message="local work")

    report = sync(workspace_dir, repo_env.source_dir, rebase=False)

    assert len(report.results) == 1
    r = report.results[0]
    assert r.action == "skipped"
    assert "rebase" in r.reason

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_sync_local_commits_rebase(repo_env, workspace_dir):
    """Sync with local commits and --rebase → rebased."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})

    old_head = get_head(workspace_dir / "nuttx")
    new_src_head = _advance_source(repo_env, "nuttx")
    make_commit(workspace_dir / "nuttx", message="local work")

    report = sync(workspace_dir, repo_env.source_dir, rebase=True)

    assert len(report.results) == 1
    r = report.results[0]
    assert r.action == "rebased"
    # After rebase, HEAD should be different from both old and src
    rebased_head = get_head(workspace_dir / "nuttx")
    assert rebased_head != old_head
    assert rebased_head != new_src_head  # rebased commit has new hash

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_sync_multiple_worktrees(repo_env, workspace_dir):
    """Sync multiple worktrees with mixed states."""
    pin_commit = get_head(repo_env.source_dir / "build")
    paths = _create_ws_with_worktrees(
        repo_env,
        workspace_dir,
        {"nuttx", "apps", "build"},
        pin_map={"build": pin_commit},
    )

    # nuttx: advance source (will be updated)
    new_nuttx_head = _advance_source(repo_env, "nuttx")
    # apps: make dirty (will be skipped)
    _advance_source(repo_env, "apps")
    make_dirty(workspace_dir / "apps")
    # build: pinned (will be skipped)
    _advance_source(repo_env, "build")

    report = sync(workspace_dir, repo_env.source_dir)

    results = {r.path: r for r in report.results}
    assert results["nuttx"].action == "updated"
    assert results["nuttx"].new_head == new_nuttx_head
    assert results["apps"].action == "skipped"
    assert "uncommitted" in results["apps"].reason
    assert results["build"].action == "skipped"
    assert results["build"].reason == "pinned"

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_sync_preserves_named_branch(repo_env, workspace_dir):
    """BUG-014: sync must not detach HEAD if worktree is on a named branch."""
    import subprocess as _subprocess

    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})
    nuttx_ws = workspace_dir / "nuttx"

    _subprocess.run(
        ["git", "checkout", "-b", "feature/my-work"],
        cwd=nuttx_ws,
        check=True,
        capture_output=True,
    )

    branch_before = _subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=nuttx_ws,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branch_before == "feature/my-work"

    new_src_head = _advance_source(repo_env, "nuttx")
    sync(workspace_dir, repo_env.source_dir)

    branch_after = _subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=nuttx_ws,
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branch_after == "feature/my-work", (
        f"sync must not detach HEAD; expected branch feature/my-work, got {branch_after!r}"
    )
    assert get_head(nuttx_ws) == new_src_head, (
        "worktree must be at new source HEAD after sync"
    )

    _cleanup_worktrees(repo_env, workspace_dir, paths)
