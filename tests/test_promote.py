"""Unit tests for repoworktree/promote.py — Promote / Demote."""

import subprocess
from pathlib import Path

import pytest
from repoworktree.scanner import scan_repos, build_trie
from repoworktree.layout import build_workspace, teardown_workspace
from repoworktree.metadata import (
    load_workspace_metadata,
    save_workspace_metadata,
    create_workspace_metadata,
    WorktreeEntry,
)
from repoworktree.promote import promote, demote, PromoteError, DemoteError
from repoworktree.worktree import get_head, DirtyWorktreeError
from tests.helpers import (
    assert_is_symlink,
    assert_is_worktree,
    assert_is_real_dir,
    make_dirty,
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
    paths = _create_ws_with_worktrees(
        repo_env, workspace_dir, {"frameworks/system/core"}
    )

    assert_is_worktree(workspace_dir / "frameworks" / "system" / "core")
    demote(workspace_dir, repo_env.source_dir, "frameworks/system/core", paths)

    assert_is_symlink(workspace_dir / "frameworks" / "system" / "core")
    assert_is_real_dir(workspace_dir / "frameworks")
    assert_is_real_dir(workspace_dir / "frameworks" / "system")

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_demote_parent_preserves_child(repo_env, workspace_dir):
    """Demote apps (parent) while apps/system/adb (child) remains worktree."""
    paths = _create_ws_with_worktrees(
        repo_env, workspace_dir, {"apps", "apps/system/adb"}
    )

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


# ── Bug reproduction tests ─────────────────────────────────────────


def _git_status_shows_change(worktree_path: Path, rel_file: str) -> bool:
    """
    Modify a file in a worktree and check if git status detects it.

    Returns True if git status shows the change (correct behavior),
    False if the change is invisible (bug: skip-worktree or gitignore hiding it).
    """
    fpath = worktree_path / rel_file
    assert fpath.exists(), f"File does not exist: {fpath}"
    original = fpath.read_text()
    fpath.write_text(original + "\n// modified\n")

    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    visible = any(rel_file in line for line in result.stdout.splitlines())

    # Restore original content
    fpath.write_text(original)
    return visible


def test_create_worktree_sibling_files_visible_in_git_status(repo_env, workspace_dir):
    """Bug 1: rwt create -w nuttx — files in fs/ (sibling to child repo fs/fatfs)
    must be visible to git status.

    _exclude_child_repos should only skip-worktree files inside the child repo
    (fs/fatfs/), NOT sibling files like fs/vfs.c that live in the parent repo.
    """
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})

    nuttx_ws = workspace_dir / "nuttx"
    assert_is_worktree(nuttx_ws)

    # fs/vfs.c is tracked by the nuttx repo, NOT by the child repo fs/fatfs.
    # Modifying it MUST show up in git status.
    assert _git_status_shows_change(nuttx_ws, "fs/vfs.c"), (
        "fs/vfs.c change invisible to git status — "
        "skip-worktree is over-broad, marking sibling files of child repo"
    )

    # Also verify a top-level file still works
    assert _git_status_shows_change(nuttx_ws, "README.md"), (
        "README.md change invisible to git status"
    )

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_worktree_sibling_files_visible_in_git_status(repo_env, workspace_dir):
    """Bug 2: rwt create + rwt promote nuttx — files in fs/ must be visible
    to git status after promote.

    promote() must handle non-worktree child repos: symlink them on top of the
    parent worktree and exclude them from git status, WITHOUT hiding sibling files.
    """
    # Step 1: create all-symlink workspace
    paths = _create_all_symlink_ws(repo_env, workspace_dir)
    assert_is_symlink(workspace_dir / "nuttx")

    # Step 2: promote nuttx
    promote(workspace_dir, repo_env.source_dir, "nuttx", paths)
    nuttx_ws = workspace_dir / "nuttx"
    assert_is_worktree(nuttx_ws)

    # fs/vfs.c must be visible to git status after promote
    assert _git_status_shows_change(nuttx_ws, "fs/vfs.c"), (
        "fs/vfs.c change invisible to git status after promote — "
        "child repo exclusion missing or over-broad in promote path"
    )

    # The child repo fs/fatfs should be a symlink (not a plain checkout dir)
    fatfs_ws = nuttx_ws / "fs" / "fatfs"
    assert fatfs_ws.exists(), (
        f"Child repo fs/fatfs should exist after promote (as symlink to source)"
    )
    assert fatfs_ws.is_symlink(), (
        f"Child repo fs/fatfs should be symlinked after promote, "
        f"got: symlink={fatfs_ws.is_symlink()}, dir={fatfs_ws.is_dir()}"
    )

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_demote_nested_worktree_not_falsely_dirty(repo_env, workspace_dir):
    """Issue 2: _has_own_changes uses worktree_path.name which fails for nested repos.

    For nested worktrees, worktree_path.name returns only the last component
    (e.g. "system") but cw.path contains the full repo path (e.g.
    "frameworks/system/core") — the prefix strip fails silently, so child dirt
    is misattributed to the parent.

    Reproduce: frameworks/system + frameworks/system/core both worktrees,
    dirty child, demote parent without --force.
    """
    paths = _create_ws_with_worktrees(
        repo_env, workspace_dir, {"frameworks/system", "frameworks/system/core"}
    )

    assert_is_worktree(workspace_dir / "frameworks" / "system")
    assert_is_worktree(workspace_dir / "frameworks" / "system" / "core")

    make_dirty(
        workspace_dir / "frameworks" / "system" / "core", filename="child_dirty.txt"
    )

    demote(workspace_dir, repo_env.source_dir, "frameworks/system", paths)

    assert_is_worktree(workspace_dir / "frameworks" / "system" / "core")
    meta = load_workspace_metadata(workspace_dir)
    assert meta.find_worktree("frameworks/system") is None

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_child_updates_parent_exclude(repo_env, workspace_dir):
    """Issue 3: promoting a child repo should update parent worktree's exclusions.

    When apps is a worktree, child repos like apps/system/adb are excluded
    via skip-worktree + .gitignore. After promoting adb to its own worktree,
    the parent's .gitignore should no longer list it.
    """
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"apps"})
    apps_ws = workspace_dir / "apps"
    assert_is_worktree(apps_ws)

    gitignore = apps_ws / ".gitignore"
    assert gitignore.exists()
    old_content = gitignore.read_text()
    assert "/system/adb" in old_content or "/system" in old_content, (
        f"Expected child repo exclusion in .gitignore, got: {old_content}"
    )

    promote(workspace_dir, repo_env.source_dir, "apps/system/adb", paths)
    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")

    new_content = gitignore.read_text()
    assert "/system/adb" not in new_content, (
        f"After promoting apps/system/adb, parent .gitignore should no longer "
        f"exclude /system/adb, got: {new_content}"
    )

    _cleanup_worktrees(repo_env, workspace_dir, paths)
