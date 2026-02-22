"""Unit tests for repoworktree/scanner.py — Repo Scanner and Prefix Trie."""

import pytest
from repoworktree.scanner import scan_repos, build_trie, RepoTrie


def test_scan_project_list(repo_env):
    """From .repo/project.list, correctly parse all 12 sub-repo paths."""
    paths = scan_repos(repo_env.source_dir)
    assert len(paths) == 12
    assert paths == repo_env.all_repo_paths


def test_scan_returns_sorted(repo_env):
    """Returned paths are sorted alphabetically."""
    paths = scan_repos(repo_env.source_dir)
    assert paths == sorted(paths)


def test_scan_no_repo_dir(tmp_path):
    """Non-repo directory raises FileNotFoundError with clear message."""
    with pytest.raises(FileNotFoundError, match="Not a repo-managed directory"):
        scan_repos(tmp_path)


def test_trie_build(repo_env):
    """12 paths build a correct trie structure."""
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths)

    # Top-level children should be: apps, build, external, frameworks, nuttx
    top_names = sorted(n.name for n in trie.top_level_children)
    assert top_names == ["apps", "build", "external", "frameworks", "nuttx"]

    # nuttx should be a leaf repo
    nuttx = trie.lookup("nuttx")
    assert nuttx is not None
    assert nuttx.is_repo
    assert len(nuttx.children) == 0

    # apps should be a repo with children
    apps = trie.lookup("apps")
    assert apps is not None
    assert apps.is_repo
    assert "system" in apps.children

    # apps/system/adb should be a leaf repo
    adb = trie.lookup("apps/system/adb")
    assert adb is not None
    assert adb.is_repo

    # frameworks/system/core should be a leaf repo
    fw_core = trie.lookup("frameworks/system/core")
    assert fw_core is not None
    assert fw_core.is_repo


def test_trie_has_worktree_descendant(repo_env):
    """Marking frameworks/system/core as worktree sets has_worktree_descendant on ancestors."""
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths, worktree_paths={"frameworks/system/core"})

    # frameworks and frameworks/system should have worktree descendants
    fw = trie.lookup("frameworks")
    assert fw.has_worktree_descendant is True

    fw_sys = trie.lookup("frameworks/system")
    assert fw_sys.has_worktree_descendant is True

    # frameworks/system/core itself is a worktree
    fw_core = trie.lookup("frameworks/system/core")
    assert fw_core.is_worktree is True
    # It has no children, so no worktree descendants
    assert fw_core.has_worktree_descendant is False


def test_trie_no_worktree_descendant(repo_env):
    """Subtree without any worktree has has_worktree_descendant=False."""
    paths = scan_repos(repo_env.source_dir)
    # Only mark nuttx as worktree — external subtree should have no worktree descendants
    trie = build_trie(paths, worktree_paths={"nuttx"})

    ext = trie.lookup("external")
    assert ext is not None
    assert ext.has_worktree_descendant is False

    ext_a = trie.lookup("external/lib-a")
    assert ext_a.has_worktree_descendant is False


def test_trie_root_repo_is_worktree(repo_env):
    """Top-level repo marked as worktree: node.is_worktree=True, ancestors updated."""
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths, worktree_paths={"nuttx"})

    nuttx = trie.lookup("nuttx")
    assert nuttx.is_worktree is True

    # Root should have worktree descendant (nuttx is a child of root)
    assert trie.root.has_worktree_descendant is True

    # But apps subtree should not
    apps = trie.lookup("apps")
    assert apps.is_worktree is False
    assert apps.has_worktree_descendant is False
