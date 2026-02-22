"""Unit tests for repoworktree/promote.py — Promote / Demote."""

import pytest
from repoworktree.scanner import scan_repos, build_trie
from repoworktree.layout import build_workspace, teardown_workspace
from repoworktree.metadata import (
    load_workspace_metadata, save_workspace_metadata,
    create_workspace_metadata, WorktreeEntry,
)
from repoworktree.promote import promote, demote, PromoteError, DemoteError
from repoworktree.worktree import get_head, DirtyWorktreeError
from tests.helpers import (
    assert_is_symlink, assert_is_worktree, assert_is_real_dir, make_dirty,
)


def _create_all_symlink_ws(repo_env, workspace_dir):
    """Helper: create a workspace with all symlinks (extreme A)."""
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths)
    build_workspace(repo_env.source_dir, workspace_dir, trie)
    meta = create_workspace_metadata(source=str(repo_env.source_dir), name="test")
    save_workspace_metadata(workspace_dir, meta)
    return paths


def _create_ws_with_worktrees(repo_env, workspace_dir, wt_set):
    """Helper: create a workspace with specified worktrees."""
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths, worktree_paths=wt_set)
    build_workspace(repo_env.source_dir, workspace_dir, trie)
    meta = create_workspace_metadata(
        source=str(repo_env.source_dir), name="test",
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


def test_promote_top_level(repo_env, workspace_dir):
    """Promote nuttx (top-level symlink) → becomes worktree."""
    paths = _create_all_symlink_ws(repo_env, workspace_dir)

    assert_is_symlink(workspace_dir / "nuttx")
    promote(workspace_dir, repo_env.source_dir, "nuttx", paths)
    assert_is_worktree(workspace_dir / "nuttx")

    src_head = get_head(repo_env.source_dir / "nuttx")
    assert get_head(workspace_dir / "nuttx") == src_head

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_nested_split_symlink(repo_env, workspace_dir):
    """Promote frameworks/system/core (frameworks/ is symlink) → splits correctly."""
    paths = _create_all_symlink_ws(repo_env, workspace_dir)

    assert_is_symlink(workspace_dir / "frameworks")
    promote(workspace_dir, repo_env.source_dir, "frameworks/system/core", paths)

    assert_is_real_dir(workspace_dir / "frameworks")
    assert_is_real_dir(workspace_dir / "frameworks" / "system")
    assert_is_worktree(workspace_dir / "frameworks" / "system" / "core")
    assert_is_symlink(workspace_dir / "frameworks" / "system" / "kvdb")
    assert_is_symlink(workspace_dir / "frameworks" / "connectivity")

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_under_existing_dir(repo_env, workspace_dir):
    """Promote apps/system/core when apps/system/adb is already a worktree."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"apps/system/adb"})

    assert_is_real_dir(workspace_dir / "apps")
    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")

    promote(workspace_dir, repo_env.source_dir, "apps/system/core", paths)

    assert_is_worktree(workspace_dir / "apps" / "system" / "core")
    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_parent_with_child_worktree(repo_env, workspace_dir):
    """Promote apps when apps/system/adb is already a worktree."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"apps/system/adb"})

    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")

    promote(workspace_dir, repo_env.source_dir, "apps", paths)

    assert_is_worktree(workspace_dir / "apps")
    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_already_worktree(repo_env, workspace_dir):
    """Promote an already-worktree repo → raises error."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})

    with pytest.raises(PromoteError, match="Already a worktree"):
        promote(workspace_dir, repo_env.source_dir, "nuttx", paths)

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_invalid_repo(repo_env, workspace_dir):
    """Promote a non-existent repo path → raises error."""
    paths = _create_all_symlink_ws(repo_env, workspace_dir)

    with pytest.raises(PromoteError, match="Not a valid"):
        promote(workspace_dir, repo_env.source_dir, "nonexistent/repo", paths)

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_with_pin(repo_env, workspace_dir):
    """Promote with pinned version → worktree at specified commit."""
    paths = _create_all_symlink_ws(repo_env, workspace_dir)
    pin_commit = get_head(repo_env.source_dir / "nuttx")

    promote(workspace_dir, repo_env.source_dir, "nuttx", paths, pin_version=pin_commit)

    assert_is_worktree(workspace_dir / "nuttx")
    assert get_head(workspace_dir / "nuttx") == pin_commit

    meta = load_workspace_metadata(workspace_dir)
    assert meta.find_worktree("nuttx").pinned == pin_commit

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_with_branch(repo_env, workspace_dir):
    """Promote with named branch → worktree on that branch."""
    paths = _create_all_symlink_ws(repo_env, workspace_dir)

    promote(workspace_dir, repo_env.source_dir, "nuttx", paths, branch="feat-test")

    assert_is_worktree(workspace_dir / "nuttx")
    meta = load_workspace_metadata(workspace_dir)
    assert meta.find_worktree("nuttx").branch == "feat-test"

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_demote_top_level(repo_env, workspace_dir):
    """Demote nuttx  becomes symlink."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})

    assert_is_worktree(workspace_dir / "nuttx")
    demote(workspace_dir, repo_env.source_dir, "nuttx", paths)

    assert_is_symlink(workspace_dir / "nuttx")
    meta = load_workspace_metadata(workspace_dir)
    assert meta.find_worktree("nuttx") is None

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_demote_nested(repo_env, workspace_dir):
    """Demote frameworks/system/core → collapses back to frameworks/ symlink."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"frameworks/system/core"})

    assert_is_worktree(workspace_dir / "frameworks" / "system" / "core")
    demote(workspace_dir, repo_env.source_dir, "frameworks/system/core", paths)

    # Should collapse all the way up to frameworks/ symlink
    assert_is_symlink(workspace_dir / "frameworks")

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_demote_parent_preserves_child(repo_env, workspace_dir):
    """Demote apps (parent) while apps/system/adb (child) remains worktree."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"apps", "apps/system/adb"})

    assert_is_worktree(workspace_dir / "apps")
    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")

    demote(workspace_dir, repo_env.source_dir, "apps", paths)

    # apps should be a real dir now (not symlink, because child worktree exists)
    assert_is_real_dir(workspace_dir / "apps")
    # child worktree should still be there
    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")

    meta = load_workspace_metadata(workspace_dir)
    assert meta.find_worktree("apps") is None
    assert meta.find_worktree("apps/system/adb") is not None

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_demote_dirty_rejected(repo_env, workspace_dir):
    """Demote a dirty worktree without force → rejected."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})

    make_dirty(workspace_dir / "nuttx")

    with pytest.raises(DirtyWorktreeError, match="uncommitted changes"):
        demote(workspace_dir, repo_env.source_dir, "nuttx", paths)

    # Should still be a worktree
    assert_is_worktree(workspace_dir / "nuttx")

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_demote_dirty_force(repo_env, workspace_dir):
    """Demote a dirty worktree with force=True → succeeds."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})

    make_dirty(workspace_dir / "nuttx")
    demote(workspace_dir, repo_env.source_dir, "nuttx", paths, force=True)

    assert_is_symlink(workspace_dir / "nuttx")

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_demote_not_worktree(repo_env, workspace_dir):
    """Demote a non-worktree repo → raises error."""
    paths = _create_all_symlink_ws(repo_env, workspace_dir)

    with pytest.raises(DemoteError, match="Not a worktree"):
        demote(workspace_dir, repo_env.source_dir, "nuttx", paths)

    _cleanup_worktrees(repo_env, workspace_dir, paths)
