"""Unit tests for repoworktree/export.py — Export."""

import pytest
from repoworktree.scanner import scan_repos, build_trie
from repoworktree.layout import build_workspace, teardown_workspace
from repoworktree.metadata import (
    load_workspace_metadata,
    save_workspace_metadata,
    create_workspace_metadata,
    WorktreeEntry,
)
from repoworktree.export import export
from repoworktree.worktree import get_head
from tests.helpers import make_commit, get_head_commit


def _create_ws_with_worktrees(repo_env, workspace_dir, wt_set):
    """Helper: create a workspace with specified worktrees."""
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths, worktree_paths=wt_set)
    build_workspace(repo_env.source_dir, workspace_dir, trie)
    meta = create_workspace_metadata(
        source=str(repo_env.source_dir),
        name="test",
        worktrees=[WorktreeEntry(p) for p in sorted(wt_set)],
    )
    save_workspace_metadata(workspace_dir, meta)
    return paths


def _cleanup_worktrees(repo_env, workspace_dir, paths):
    """Helper: teardown workspace worktrees."""
    meta = load_workspace_metadata(workspace_dir)
    wt_set = {w.path for w in meta.worktrees}
    trie = build_trie(paths, worktree_paths=wt_set)
    teardown_workspace(repo_env.source_dir, workspace_dir, trie)


def test_export_patch(repo_env, workspace_dir, tmp_path):
    """Export a worktree with local commits as patch."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})

    make_commit(workspace_dir / "nuttx", message="local fix")

    output_dir = tmp_path / "export_out"
    report = export(workspace_dir, repo_env.source_dir, output_dir, fmt="patch")

    assert len(report.exported) == 1
    r = report.exported[0]
    assert r.path == "nuttx"
    assert r.commit_count == 1
    # Patch dir should contain .patch file(s)
    patch_dir = output_dir / "nuttx"
    assert patch_dir.is_dir()
    patches = list(patch_dir.glob("*.patch"))
    assert len(patches) == 1

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_export_bundle(repo_env, workspace_dir, tmp_path):
    """Export a worktree with local commits as bundle."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})

    make_commit(workspace_dir / "nuttx", message="local fix")

    output_dir = tmp_path / "export_out"
    report = export(workspace_dir, repo_env.source_dir, output_dir, fmt="bundle")

    assert len(report.exported) == 1
    r = report.exported[0]
    assert r.path == "nuttx"
    assert r.commit_count == 1
    bundle_file = output_dir / "nuttx.bundle"
    assert bundle_file.is_file()

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_export_no_commits(repo_env, workspace_dir, tmp_path):
    """Export a clean worktree with no local commits → skipped."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})

    output_dir = tmp_path / "export_out"
    report = export(workspace_dir, repo_env.source_dir, output_dir, fmt="patch")

    assert len(report.skipped) == 1
    assert report.skipped[0].reason == "no local commits"
    assert len(report.exported) == 0

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_export_multiple_commits(repo_env, workspace_dir, tmp_path):
    """Export a worktree with multiple local commits."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})

    make_commit(workspace_dir / "nuttx", message="fix 1", filename="fix1.txt")
    make_commit(workspace_dir / "nuttx", message="fix 2", filename="fix2.txt")

    output_dir = tmp_path / "export_out"
    report = export(workspace_dir, repo_env.source_dir, output_dir, fmt="patch")

    assert len(report.exported) == 1
    r = report.exported[0]
    assert r.commit_count == 2
    patch_dir = output_dir / "nuttx"
    patches = list(patch_dir.glob("*.patch"))
    assert len(patches) == 2

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_export_mixed_worktrees(repo_env, workspace_dir, tmp_path):
    """Export multiple worktrees: one with commits, one without."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx", "apps"})

    make_commit(workspace_dir / "nuttx", message="nuttx fix")
    # apps has no commits

    output_dir = tmp_path / "export_out"
    report = export(workspace_dir, repo_env.source_dir, output_dir, fmt="patch")

    results = {r.path: r for r in report.results}
    assert results["nuttx"].action == "exported"
    assert results["apps"].action == "skipped"
    assert results["apps"].reason == "no local commits"

    _cleanup_worktrees(repo_env, workspace_dir, paths)
