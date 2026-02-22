"""Unit tests for repoworktree/layout.py — Layout Engine."""

import os
from pathlib import Path

import pytest
from repoworktree.scanner import scan_repos, build_trie
from repoworktree.layout import build_workspace, teardown_workspace
from repoworktree.worktree import get_head
from tests.helpers import assert_is_symlink, assert_is_worktree, assert_is_real_dir


def test_all_symlink(repo_env, workspace_dir):
    """No worktree: all top-level directories are symlinks (extreme A)."""
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths)  # no worktrees marked

    build_workspace(repo_env.source_dir, workspace_dir, trie)

    # All top-level repo dirs should be symlinks
    assert_is_symlink(workspace_dir / "nuttx")
    assert_is_symlink(workspace_dir / "apps")
    assert_is_symlink(workspace_dir / "build")
    assert_is_symlink(workspace_dir / "external")
    assert_is_symlink(workspace_dir / "frameworks")

    # Symlinks should point to source
    assert (workspace_dir / "nuttx").resolve() == (repo_env.source_dir / "nuttx").resolve()

    teardown_workspace(repo_env.source_dir, workspace_dir, trie)


def test_all_worktree(repo_env, workspace_dir):
    """All repos as worktree (extreme B)."""
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths, worktree_paths=set(paths))

    build_workspace(repo_env.source_dir, workspace_dir, trie)

    # All repos should be worktrees
    for repo_path in paths:
        assert_is_worktree(workspace_dir / repo_path)

    teardown_workspace(repo_env.source_dir, workspace_dir, trie)


def test_top_level_worktree(repo_env, workspace_dir):
    """Single top-level worktree, rest symlinked."""
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths, worktree_paths={"nuttx"})

    build_workspace(repo_env.source_dir, workspace_dir, trie)

    assert_is_worktree(workspace_dir / "nuttx")
    assert_is_symlink(workspace_dir / "apps")
    assert_is_symlink(workspace_dir / "build")
    assert_is_symlink(workspace_dir / "external")
    assert_is_symlink(workspace_dir / "frameworks")

    # Worktree HEAD should match source
    src_head = get_head(repo_env.source_dir / "nuttx")
    wt_head = get_head(workspace_dir / "nuttx")
    assert wt_head == src_head

    teardown_workspace(repo_env.source_dir, workspace_dir, trie)


def test_nested_worktree(repo_env, workspace_dir):
    """Nested worktree: apps/system/adb is worktree, apps/ is real dir with symlinks."""
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths, worktree_paths={"apps/system/adb"})

    build_workspace(repo_env.source_dir, workspace_dir, trie)

    # apps/ should be a real directory (not symlink)
    assert_is_real_dir(workspace_dir / "apps")
    # apps/system/ should be a real directory
    assert_is_real_dir(workspace_dir / "apps" / "system")
    # apps/system/adb should be a worktree
    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")
    # apps/system/core should be a symlink
    assert_is_symlink(workspace_dir / "apps" / "system" / "core")
    # Other top-level dirs should be symlinks
    assert_is_symlink(workspace_dir / "nuttx")
    assert_is_symlink(workspace_dir / "frameworks")

    teardown_workspace(repo_env.source_dir, workspace_dir, trie)


def test_parent_child_both_worktree(repo_env, workspace_dir):
    """Parent and child repos both as worktree."""
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths, worktree_paths={"apps", "apps/system/adb"})

    build_workspace(repo_env.source_dir, workspace_dir, trie)

    # apps/ should be a worktree (parent repo)
    assert_is_worktree(workspace_dir / "apps")
    # apps/system/adb should also be a worktree (child repo)
    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")

    # Both should have correct HEAD
    src_apps_head = get_head(repo_env.source_dir / "apps")
    src_adb_head = get_head(repo_env.source_dir / "apps" / "system" / "adb")
    assert get_head(workspace_dir / "apps") == src_apps_head
    assert get_head(workspace_dir / "apps" / "system" / "adb") == src_adb_head

    teardown_workspace(repo_env.source_dir, workspace_dir, trie)


def test_deep_nested_worktree(repo_env, workspace_dir):
    """Deep nested worktree: frameworks/system/core."""
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths, worktree_paths={"frameworks/system/core"})

    build_workspace(repo_env.source_dir, workspace_dir, trie)

    # frameworks/ should be real dir
    assert_is_real_dir(workspace_dir / "frameworks")
    # frameworks/system/ should be real dir
    assert_is_real_dir(workspace_dir / "frameworks" / "system")
    # frameworks/system/core should be worktree
    assert_is_worktree(workspace_dir / "frameworks" / "system" / "core")
    # frameworks/system/kvdb should be symlink
    assert_is_symlink(workspace_dir / "frameworks" / "system" / "kvdb")
    # frameworks/connectivity should be symlink
    assert_is_symlink(workspace_dir / "frameworks" / "connectivity")

    teardown_workspace(repo_env.source_dir, workspace_dir, trie)


def test_multiple_worktrees_different_trees(repo_env, workspace_dir):
    """Worktrees in different subtrees: nuttx + frameworks/system/core."""
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths, worktree_paths={"nuttx", "frameworks/system/core"})

    build_workspace(repo_env.source_dir, workspace_dir, trie)

    assert_is_worktree(workspace_dir / "nuttx")
    assert_is_real_dir(workspace_dir / "frameworks")
    assert_is_worktree(workspace_dir / "frameworks" / "system" / "core")
    assert_is_symlink(workspace_dir / "apps")
    assert_is_symlink(workspace_dir / "external")

    teardown_workspace(repo_env.source_dir, workspace_dir, trie)


def test_parent_worktree_child_repo_symlinked(repo_env, workspace_dir):
    """When nuttx is worktree, child repo nuttx/fs/fatfs must be symlinked on top."""
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths, worktree_paths={"nuttx"})

    build_workspace(repo_env.source_dir, workspace_dir, trie)

    assert_is_worktree(workspace_dir / "nuttx")
    # nuttx/fs/fatfs is a separate repo — it must exist as a symlink
    fatfs_ws = workspace_dir / "nuttx" / "fs" / "fatfs"
    assert fatfs_ws.exists(), f"Child repo nuttx/fs/fatfs missing from workspace"
    assert_is_symlink(fatfs_ws)
    # Should point to source
    assert fatfs_ws.resolve() == (repo_env.source_dir / "nuttx" / "fs" / "fatfs").resolve()

    teardown_workspace(repo_env.source_dir, workspace_dir, trie)
    """Top-level symlink files (like build.sh) are rebuilt in workspace."""
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths, worktree_paths={"nuttx"})

    build_workspace(repo_env.source_dir, workspace_dir, trie)

    build_sh = workspace_dir / "build.sh"
    assert build_sh.is_symlink()
    # Should be the same relative symlink as in source
    source_target = os.readlink(repo_env.source_dir / "build.sh")
    ws_target = os.readlink(build_sh)
    assert ws_target == source_target

    teardown_workspace(repo_env.source_dir, workspace_dir, trie)


def test_top_level_files_config(repo_env, workspace_dir):
    """Top-level config files are symlinked to source."""
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths)

    build_workspace(repo_env.source_dir, workspace_dir, trie)

    claude_md = workspace_dir / "CLAUDE.md"
    assert claude_md.is_symlink()
    assert claude_md.resolve() == (repo_env.source_dir / "CLAUDE.md").resolve()

    teardown_workspace(repo_env.source_dir, workspace_dir, trie)


def test_top_level_files_ignored(repo_env, workspace_dir):
    """Temporary files (.patch, .elf) are not in workspace."""
    # Create some temp files in source
    (repo_env.source_dir / "test.patch").write_text("patch")
    (repo_env.source_dir / "test.elf").write_text("elf")

    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths)

    build_workspace(repo_env.source_dir, workspace_dir, trie)

    assert not (workspace_dir / "test.patch").exists()
    assert not (workspace_dir / "test.elf").exists()

    # Cleanup source
    (repo_env.source_dir / "test.patch").unlink()
    (repo_env.source_dir / "test.elf").unlink()

    teardown_workspace(repo_env.source_dir, workspace_dir, trie)
