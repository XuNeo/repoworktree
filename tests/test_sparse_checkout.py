"""Tests for sparse-checkout based child repo isolation."""

import subprocess
from pathlib import Path

import pytest
from repoworktree.scanner import scan_repos, build_trie
from repoworktree.layout import (
    build_workspace,
    teardown_workspace,
    _get_sparse_checkout_file,
)
from repoworktree.metadata import (
    load_workspace_metadata,
    save_workspace_metadata,
    create_workspace_metadata,
    WorktreeEntry,
)
from repoworktree.promote import promote, demote
from tests.helpers import (
    assert_is_symlink,
    assert_is_worktree,
)


def _create_ws_with_worktrees(repo_env, workspace_dir, wt_set):
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


def _create_all_symlink_ws(repo_env, workspace_dir):
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths)
    build_workspace(repo_env.source_dir, workspace_dir, trie)
    meta = create_workspace_metadata(source=str(repo_env.source_dir), name="test")
    save_workspace_metadata(workspace_dir, meta)
    return paths


def _cleanup_worktrees(repo_env, workspace_dir, paths):
    meta = load_workspace_metadata(workspace_dir)
    wt_set = {w.path for w in meta.worktrees}
    trie = build_trie(paths, worktree_paths=wt_set)
    teardown_workspace(repo_env.source_dir, workspace_dir, trie)


def _git_status(worktree_path: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _git_diff(worktree_path: Path) -> str:
    result = subprocess.run(
        ["git", "diff"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _modify_and_check_visible(worktree_path: Path, rel_file: str) -> bool:
    fpath = worktree_path / rel_file
    assert fpath.exists(), f"File does not exist: {fpath}"
    original = fpath.read_text()
    fpath.write_text(original + "\n// modified\n")
    status = _git_status(worktree_path)
    visible = any(rel_file in line for line in status.splitlines())
    fpath.write_text(original)
    return visible


def _modify_and_check_diff(worktree_path: Path, rel_file: str) -> bool:
    fpath = worktree_path / rel_file
    assert fpath.exists(), f"File does not exist: {fpath}"
    original = fpath.read_text()
    fpath.write_text(original + "\n// modified\n")
    diff = _git_diff(worktree_path)
    visible = rel_file in diff
    fpath.write_text(original)
    return visible


# ── P0: Core sparse-checkout behavior ─────────────────────────────


def test_sparse_checkout_excludes_child_files(repo_env, workspace_dir):
    """Excluded child repo files must not exist in the worktree working tree."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})
    nuttx_ws = workspace_dir / "nuttx"
    assert_is_worktree(nuttx_ws)

    fatfs_readme = nuttx_ws / "fs" / "fatfs" / "README.md"
    fatfs_c = nuttx_ws / "fs" / "fatfs" / "fatfs.c"
    assert not fatfs_readme.exists() or (nuttx_ws / "fs" / "fatfs").is_symlink(), (
        "Child repo file should not exist as a tracked file — "
        "sparse-checkout should have excluded it"
    )
    assert not fatfs_c.exists() or (nuttx_ws / "fs" / "fatfs").is_symlink()

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_sparse_checkout_preserves_parent_files(repo_env, workspace_dir):
    """Parent repo's own files (not under child repo paths) must exist."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})
    nuttx_ws = workspace_dir / "nuttx"
    assert_is_worktree(nuttx_ws)

    assert (nuttx_ws / "README.md").exists()
    assert (nuttx_ws / "fs" / "vfs.c").exists()
    assert (nuttx_ws / "fs" / "inode.c").exists()
    assert (nuttx_ws / "tools" / "Unix.mk").exists()

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_user_edit_visible_in_git_status(repo_env, workspace_dir):
    """Edits to parent repo files must appear in git status."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})
    nuttx_ws = workspace_dir / "nuttx"

    assert _modify_and_check_visible(nuttx_ws, "fs/vfs.c"), (
        "fs/vfs.c edit must be visible in git status"
    )
    assert _modify_and_check_visible(nuttx_ws, "README.md"), (
        "README.md edit must be visible in git status"
    )

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_user_edit_visible_in_git_diff(repo_env, workspace_dir):
    """Edits to parent repo files must appear in git diff."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})
    nuttx_ws = workspace_dir / "nuttx"

    assert _modify_and_check_diff(nuttx_ws, "fs/vfs.c"), (
        "fs/vfs.c edit must be visible in git diff"
    )
    assert _modify_and_check_diff(nuttx_ws, "README.md"), (
        "README.md edit must be visible in git diff"
    )

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_child_symlink_invisible_to_git(repo_env, workspace_dir):
    """Child repo symlinks must not appear in git status (hidden by .gitignore)."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})
    nuttx_ws = workspace_dir / "nuttx"
    assert_is_worktree(nuttx_ws)

    fatfs_ws = nuttx_ws / "fs" / "fatfs"
    assert_is_symlink(fatfs_ws)

    status = _git_status(nuttx_ws)
    assert "fs/fatfs" not in status, (
        f"Child repo symlink should be hidden from git status, got: {status}"
    )

    _cleanup_worktrees(repo_env, workspace_dir, paths)


# ── P1: Promote/demote sparse-checkout updates ────────────────────


def test_sparse_checkout_after_promote_child(repo_env, workspace_dir):
    """After promoting a child repo, parent's sparse-checkout must stop excluding it."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"apps"})
    apps_ws = workspace_dir / "apps"
    assert_is_worktree(apps_ws)

    sparse_file = _get_sparse_checkout_file(apps_ws)
    assert sparse_file.exists()
    rules_before = sparse_file.read_text()
    assert "!/system/adb/" in rules_before

    promote(workspace_dir, repo_env.source_dir, "apps/system/adb", paths)

    if sparse_file.exists():
        rules_after = sparse_file.read_text()
        assert "!/system/adb/" not in rules_after, (
            f"After promoting child, sparse-checkout should not exclude it: {rules_after}"
        )

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_sparse_checkout_after_demote_child(repo_env, workspace_dir):
    """After demoting a child repo, parent's sparse-checkout must re-exclude it."""
    paths = _create_ws_with_worktrees(
        repo_env, workspace_dir, {"apps", "apps/system/adb"}
    )
    apps_ws = workspace_dir / "apps"

    sparse_file = _get_sparse_checkout_file(apps_ws)
    if sparse_file.exists():
        rules_with_child = sparse_file.read_text()
        assert "!/system/adb/" not in rules_with_child

    demote(workspace_dir, repo_env.source_dir, "apps/system/adb", paths)

    rules_after = sparse_file.read_text()
    assert "!/system/adb/" in rules_after, (
        f"After demoting child, sparse-checkout must re-exclude it: {rules_after}"
    )

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_sparse_checkout_disable_when_no_children(repo_env, workspace_dir):
    """When all child repos are promoted, sparse-checkout should be disabled."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"apps"})
    apps_ws = workspace_dir / "apps"

    child_repos = [r for r in paths if r.startswith("apps/") and r != "apps"]
    for child in child_repos:
        promote(workspace_dir, repo_env.source_dir, child, paths)

    sparse_file = _get_sparse_checkout_file(apps_ws)
    if sparse_file.exists():
        content = sparse_file.read_text().strip()
        assert not any(line.startswith("!/") for line in content.splitlines()), (
            f"No exclusion rules should remain: {content}"
        )

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_sparse_checkout_multiple_children(repo_env, workspace_dir):
    """Multiple child repos must all be excluded in sparse-checkout rules."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"apps"})
    apps_ws = workspace_dir / "apps"

    sparse_file = _get_sparse_checkout_file(apps_ws)
    assert sparse_file.exists()
    rules = sparse_file.read_text()

    assert "!/system/adb/" in rules
    assert "!/system/core/" in rules

    _cleanup_worktrees(repo_env, workspace_dir, paths)


# ── P2: Edge cases ────────────────────────────────────────────────


def test_sparse_checkout_git_dir_resolution(repo_env, workspace_dir):
    """Worktree .git file must resolve to actual git dir for sparse-checkout."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})
    nuttx_ws = workspace_dir / "nuttx"

    git_entry = nuttx_ws / ".git"
    assert git_entry.is_file(), ".git should be a file for worktrees"

    sparse_file = _get_sparse_checkout_file(nuttx_ws)
    assert "worktrees" in str(sparse_file), (
        f"Sparse-checkout file should be inside worktrees dir, got: {sparse_file}"
    )
    assert sparse_file.parent.name == "info"

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_apps_worktree_git_status_clean_after_create(repo_env, workspace_dir):
    """After creating apps as worktree, git status must be clean (no noise)."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"apps"})
    apps_ws = workspace_dir / "apps"
    assert_is_worktree(apps_ws)

    status = _git_status(apps_ws)
    assert not status.strip(), (
        f"git status should be clean after workspace creation, got:\n{status}"
    )

    _cleanup_worktrees(repo_env, workspace_dir, paths)
