"""Unit tests for repoworktree/promote.py — Promote / Demote."""

import subprocess
from pathlib import Path

import pytest
from repoworktree.scanner import scan_repos, build_trie
from repoworktree.layout import (
    build_workspace,
    teardown_workspace,
    _get_worktree_gitdir,
)
from repoworktree.metadata import (
    load_workspace_metadata,
    save_workspace_metadata,
    create_workspace_metadata,
    WorktreeEntry,
)
from repoworktree.__main__ import cmd_promote
import repoworktree.promote as promote_module
from repoworktree.promote import promote, demote, PromoteError, DemoteError
from repoworktree.worktree import get_head, list_worktrees, DirtyWorktreeError
from tests.helpers import (
    assert_is_symlink,
    assert_is_worktree,
    assert_is_real_dir,
    make_dirty,
    make_commit,
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


def _read_worktree_exclude(worktree_path: Path) -> str:
    """Read the per-worktree info/exclude file content."""
    git_dir = _get_worktree_gitdir(worktree_path)
    exclude_file = git_dir / "info" / "exclude"
    if exclude_file.exists():
        return exclude_file.read_text()
    return ""


def _assert_git_toplevel_is_path(repo_path: Path) -> None:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert Path(result.stdout.strip()) == repo_path


class _PromoteArgs:
    workspace = None
    repo_path = ""
    branch = None
    pin = None
    force = False


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


def test_promote_parent_promotes_descendant_repos(repo_env, workspace_dir):
    """Promote a repo → promotes that repo and all repos under its path."""
    paths = _create_all_symlink_ws(repo_env, workspace_dir)

    promoted = promote(workspace_dir, repo_env.source_dir, "frameworks/system", paths)

    assert promoted == [
        "frameworks/system",
        "frameworks/system/core",
        "frameworks/system/kvdb",
    ]

    for repo_path in [
        "frameworks/system",
        "frameworks/system/core",
        "frameworks/system/kvdb",
    ]:
        wt_path = workspace_dir / repo_path
        assert_is_worktree(wt_path)
        _assert_git_toplevel_is_path(wt_path)

    meta = load_workspace_metadata(workspace_dir)
    assert [w.path for w in meta.worktrees] == [
        "frameworks/system",
        "frameworks/system/core",
        "frameworks/system/kvdb",
    ]

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_existing_parent_promotes_missing_descendant_repos(repo_env, workspace_dir):
    """Promote an existing worktree parent → promotes missing descendant repos."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})

    promoted = promote(workspace_dir, repo_env.source_dir, "nuttx", paths)

    assert promoted == ["nuttx/fs/fatfs"]
    assert_is_worktree(workspace_dir / "nuttx")
    assert_is_worktree(workspace_dir / "nuttx" / "fs" / "fatfs")

    meta = load_workspace_metadata(workspace_dir)
    assert [w.path for w in meta.worktrees] == ["nuttx", "nuttx/fs/fatfs"]

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_already_worktree_with_no_missing_descendants(repo_env, workspace_dir):
    """Promote an already-worktree leaf repo → raises error."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"external/lib-a"})

    with pytest.raises(PromoteError, match="Already a worktree"):
        promote(workspace_dir, repo_env.source_dir, "external/lib-a", paths)

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_invalid_repo(repo_env, workspace_dir):
    """Promote a non-existent repo path → raises error."""
    paths = _create_all_symlink_ws(repo_env, workspace_dir)

    with pytest.raises(PromoteError, match="Not a valid"):
        promote(workspace_dir, repo_env.source_dir, "nonexistent/repo", paths)

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_with_pin(repo_env, workspace_dir):
    """Promote with pinned version → requested worktree at specified commit."""
    paths = _create_all_symlink_ws(repo_env, workspace_dir)
    pin_commit = get_head(repo_env.source_dir / "nuttx")

    promote(workspace_dir, repo_env.source_dir, "nuttx", paths, pin_version=pin_commit)

    assert_is_worktree(workspace_dir / "nuttx")
    assert get_head(workspace_dir / "nuttx") == pin_commit

    meta = load_workspace_metadata(workspace_dir)
    nuttx_entry = meta.find_worktree("nuttx")
    fatfs_entry = meta.find_worktree("nuttx/fs/fatfs")
    assert nuttx_entry is not None
    assert fatfs_entry is not None
    assert nuttx_entry.pinned == pin_commit
    assert fatfs_entry.pinned is None
    assert get_head(workspace_dir / "nuttx" / "fs" / "fatfs") == get_head(
        repo_env.source_dir / "nuttx" / "fs" / "fatfs"
    )

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_restores_symlink_when_worktree_add_fails(
    repo_env, workspace_dir, monkeypatch
):
    """Promote failure after unlinking a symlink restores that symlink."""
    paths = _create_all_symlink_ws(repo_env, workspace_dir)
    real_add = promote_module.git_worktree_add

    def fail_adding_core(
        source, target, branch=None, pin_version=None, create_branch=True
    ):
        if str(target).endswith("frameworks/system/core"):
            raise RuntimeError("fail adding core")
        real_add(source, target, branch, pin_version, create_branch=create_branch)

    monkeypatch.setattr(promote_module, "git_worktree_add", fail_adding_core)

    with pytest.raises(RuntimeError, match="fail adding core"):
        promote(workspace_dir, repo_env.source_dir, "frameworks/system/core", paths)

    meta = load_workspace_metadata(workspace_dir)
    assert meta.find_worktree("frameworks/system/core") is None
    assert_is_symlink(
        workspace_dir / "frameworks" / "system" / "core",
        repo_env.source_dir / "frameworks" / "system" / "core",
    )

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_cleans_partial_dir_before_restoring_symlink(
    repo_env, workspace_dir, monkeypatch
):
    """Promote failure removes partial dirs before restoring source symlink."""
    paths = _create_all_symlink_ws(repo_env, workspace_dir)
    real_add = promote_module.git_worktree_add

    def fail_with_partial_dir(
        source, target, branch=None, pin_version=None, create_branch=True
    ):
        if str(target).endswith("frameworks/system/core"):
            target.mkdir(parents=True, exist_ok=True)
            (target / "partial.txt").write_text("partial")
            raise RuntimeError("partial add failure")
        real_add(source, target, branch, pin_version, create_branch=create_branch)

    monkeypatch.setattr(promote_module, "git_worktree_add", fail_with_partial_dir)

    with pytest.raises(RuntimeError, match="partial add failure"):
        promote(workspace_dir, repo_env.source_dir, "frameworks/system/core", paths)

    assert_is_symlink(
        workspace_dir / "frameworks" / "system" / "core",
        repo_env.source_dir / "frameworks" / "system" / "core",
    )

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_rolls_back_parent_when_descendant_fails(repo_env, workspace_dir):
    """Failed descendant promote leaves no partially promoted parent."""
    paths = _create_all_symlink_ws(repo_env, workspace_dir)
    subprocess.run(
        ["git", "branch", "feat-conflict"],
        cwd=repo_env.source_dir / "frameworks" / "system" / "core",
        check=True,
    )

    with pytest.raises(Exception, match="feat-conflict"):
        promote(
            workspace_dir,
            repo_env.source_dir,
            "frameworks/system",
            paths,
            branch="feat-conflict",
        )

    meta = load_workspace_metadata(workspace_dir)
    assert meta.find_worktree("frameworks/system") is None
    assert meta.find_worktree("frameworks/system/core") is None
    assert meta.find_worktree("frameworks/system/kvdb") is None
    assert_is_symlink(
        workspace_dir / "frameworks" / "system",
        repo_env.source_dir / "frameworks" / "system",
    )

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_rolls_back_child_when_parent_add_fails_before_parent_exists(
    repo_env, workspace_dir
):
    """Parent add failure restores child worktrees removed before the add."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"apps/system/adb"})
    subprocess.run(
        ["git", "branch", "parent-conflict"],
        cwd=repo_env.source_dir / "apps",
        check=True,
    )

    with pytest.raises(Exception, match="parent-conflict"):
        promote(
            workspace_dir,
            repo_env.source_dir,
            "apps",
            paths,
            branch="parent-conflict",
        )

    meta = load_workspace_metadata(workspace_dir)
    assert meta.find_worktree("apps") is None
    assert meta.find_worktree("apps/system/adb") is not None
    assert_is_real_dir(workspace_dir / "apps")
    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_nested_parent_preserves_grandchild_worktrees(repo_env, workspace_dir):
    """Promoting nested parents preserves deeper child worktrees."""
    paths = _create_all_symlink_ws(repo_env, workspace_dir)

    promote(workspace_dir, repo_env.source_dir, "frameworks/system/core", paths)
    promote(workspace_dir, repo_env.source_dir, "frameworks/system", paths)
    promote(workspace_dir, repo_env.source_dir, "frameworks", paths)

    for repo_path in [
        "frameworks",
        "frameworks/system",
        "frameworks/system/core",
        "frameworks/system/kvdb",
    ]:
        assert_is_worktree(workspace_dir / repo_path)

    meta = load_workspace_metadata(workspace_dir)
    assert {w.path for w in meta.worktrees} >= {
        "frameworks",
        "frameworks/system",
        "frameworks/system/core",
        "frameworks/system/kvdb",
    }

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_parent_restores_pinned_child_branch(repo_env, workspace_dir):
    """Parent promote restores an existing child branch at its pinned commit."""
    paths = _create_all_symlink_ws(repo_env, workspace_dir)
    child_src = repo_env.source_dir / "apps" / "system" / "adb"
    pinned_commit = get_head(child_src)
    make_commit(child_src, filename="source_head.txt")

    promote(
        workspace_dir,
        repo_env.source_dir,
        "apps/system/adb",
        paths,
        branch="child-pinned",
        pin_version=pinned_commit,
    )
    make_commit(
        workspace_dir / "apps" / "system" / "adb",
        filename="branch_head.txt",
    )
    assert get_head(workspace_dir / "apps" / "system" / "adb") != pinned_commit

    promote(workspace_dir, repo_env.source_dir, "apps", paths)

    meta = load_workspace_metadata(workspace_dir)
    child = meta.find_worktree("apps/system/adb")
    assert child is not None
    assert child.branch == "child-pinned"
    assert child.pinned == pinned_commit
    assert get_head(workspace_dir / "apps" / "system" / "adb") == pinned_commit

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_parent_restores_existing_child_branch(repo_env, workspace_dir):
    """Parent promote restores a child worktree on its existing branch."""
    paths = _create_all_symlink_ws(repo_env, workspace_dir)
    promote(
        workspace_dir,
        repo_env.source_dir,
        "apps/system/adb",
        paths,
        branch="child-branch",
    )

    promote(workspace_dir, repo_env.source_dir, "apps", paths)

    meta = load_workspace_metadata(workspace_dir)
    assert meta.find_worktree("apps") is not None
    child = meta.find_worktree("apps/system/adb")
    assert child is not None
    assert child.branch == "child-branch"
    assert_is_worktree(workspace_dir / "apps")
    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")
    result = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=workspace_dir / "apps" / "system" / "adb",
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "child-branch"

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_rolls_back_removed_child_worktree_on_restore_failure(
    repo_env, workspace_dir, monkeypatch
):
    """Rollback restores metadata children even if they were already removed."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"apps/system/adb"})
    real_add = promote_module.git_worktree_add

    failed_once = False

    def fail_restoring_child(
        source, target, branch=None, pin_version=None, create_branch=True
    ):
        nonlocal failed_once
        if str(target).endswith("apps/system/adb") and not failed_once:
            failed_once = True
            raise RuntimeError("fail restoring child")
        real_add(source, target, branch, pin_version, create_branch=create_branch)

    monkeypatch.setattr(promote_module, "git_worktree_add", fail_restoring_child)

    with pytest.raises(RuntimeError, match="fail restoring child"):
        promote(workspace_dir, repo_env.source_dir, "apps", paths)

    meta = load_workspace_metadata(workspace_dir)
    assert meta.find_worktree("apps") is None
    assert meta.find_worktree("apps/system/adb") is not None
    assert_is_real_dir(workspace_dir / "apps")
    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_rolls_back_parent_with_existing_child_worktree(
    repo_env, workspace_dir, monkeypatch
):
    """Failed parent promote preserves pre-existing child worktree topology."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"apps/system/adb"})
    real_save = promote_module.save_workspace_metadata

    def fail_saving_parent(workspace, meta):
        if meta.find_worktree("apps"):
            raise RuntimeError("fail after parent add")
        real_save(workspace, meta)

    monkeypatch.setattr(promote_module, "save_workspace_metadata", fail_saving_parent)

    with pytest.raises(RuntimeError, match="fail after parent add"):
        promote(workspace_dir, repo_env.source_dir, "apps", paths)

    meta = load_workspace_metadata(workspace_dir)
    assert meta.find_worktree("apps") is None
    assert meta.find_worktree("apps/system/adb") is not None
    assert_is_real_dir(workspace_dir / "apps")
    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")
    worktree_paths = [
        w["path"] for w in list_worktrees(repo_env.source_dir / "apps" / "system" / "adb")
    ]
    assert str(workspace_dir / "apps" / "system" / "adb") in worktree_paths

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_rolls_back_current_target_after_worktree_add_failure_window(
    repo_env, workspace_dir, monkeypatch
):
    """Failure after current target add still rolls back that target."""
    paths = _create_all_symlink_ws(repo_env, workspace_dir)
    real_save = promote_module.save_workspace_metadata

    def fail_saving_child(workspace, meta):
        if meta.find_worktree("frameworks/system/core"):
            raise RuntimeError("fail after child add")
        real_save(workspace, meta)

    monkeypatch.setattr(promote_module, "save_workspace_metadata", fail_saving_child)

    with pytest.raises(RuntimeError, match="fail after child add"):
        promote(workspace_dir, repo_env.source_dir, "frameworks/system", paths)

    meta = load_workspace_metadata(workspace_dir)
    assert meta.find_worktree("frameworks/system") is None
    assert meta.find_worktree("frameworks/system/core") is None
    assert_is_symlink(
        workspace_dir / "frameworks" / "system",
        repo_env.source_dir / "frameworks" / "system",
    )
    worktree_paths = [
        w["path"]
        for w in list_worktrees(repo_env.source_dir / "frameworks" / "system" / "core")
    ]
    assert str(workspace_dir / "frameworks" / "system" / "core") not in worktree_paths

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_cmd_promote_lists_actual_promoted_repos(repo_env, workspace_dir, capsys):
    """CLI output lists the repos that were actually promoted."""
    _create_all_symlink_ws(repo_env, workspace_dir)
    args = _PromoteArgs()
    args.workspace = str(workspace_dir)
    args.repo_path = "frameworks/system"

    assert cmd_promote(args) == 0

    out = capsys.readouterr().out
    assert "Promoted 3 repos:" in out
    assert "  frameworks/system\n" in out
    assert "  frameworks/system/core\n" in out
    assert "  frameworks/system/kvdb\n" in out

    paths = scan_repos(repo_env.source_dir)
    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_with_branch(repo_env, workspace_dir):
    """Promote with named branch → worktree on that branch."""
    paths = _create_all_symlink_ws(repo_env, workspace_dir)

    promote(workspace_dir, repo_env.source_dir, "nuttx", paths, branch="feat-test")

    assert_is_worktree(workspace_dir / "nuttx")
    meta = load_workspace_metadata(workspace_dir)
    nuttx_entry = meta.find_worktree("nuttx")
    assert nuttx_entry is not None
    assert nuttx_entry.branch == "feat-test"

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
    False if the change is invisible (bug: sparse-checkout or excludes hiding it).
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

    _exclude_child_repos should only exclude files inside the child repo
    (fs/fatfs/), NOT sibling files like fs/vfs.c that live in the parent repo.
    """
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"nuttx"})

    nuttx_ws = workspace_dir / "nuttx"
    assert_is_worktree(nuttx_ws)

    # fs/vfs.c is tracked by the nuttx repo, NOT by the child repo fs/fatfs.
    # Modifying it MUST show up in git status.
    assert _git_status_shows_change(nuttx_ws, "fs/vfs.c"), (
        "fs/vfs.c change invisible to git status — "
        "exclude is over-broad, hiding sibling files of child repo"
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

    # The child repo fs/fatfs should be promoted too, not left as an overlay symlink.
    fatfs_ws = nuttx_ws / "fs" / "fatfs"
    assert_is_worktree(fatfs_ws)

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
    via per-worktree info/exclude. After promoting adb to its own worktree,
    the parent's exclude should no longer list it.
    """
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"apps"})
    apps_ws = workspace_dir / "apps"
    assert_is_worktree(apps_ws)

    old_content = _read_worktree_exclude(apps_ws)
    assert "/system/adb" in old_content or "/system" in old_content, (
        f"Expected child repo exclusion in info/exclude, got: {old_content}"
    )

    promote(workspace_dir, repo_env.source_dir, "apps/system/adb", paths)
    assert_is_worktree(workspace_dir / "apps" / "system" / "adb")

    new_content = _read_worktree_exclude(apps_ws)
    assert "/system/adb" not in new_content, (
        f"After promoting apps/system/adb, parent info/exclude should no longer "
        f"exclude /system/adb, got: {new_content}"
    )
    assert not (apps_ws / ".gitignore").exists(), (
        "Should not create .gitignore in worktree"
    )

    _cleanup_worktrees(repo_env, workspace_dir, paths)


# ── BUG regression tests ──────────────────────────────────────────


def test_promote_dirty_dir_rejected(repo_env, workspace_dir):
    """BUG-005: dirty check gate exists — skip if info/exclude hides child dir.

    In normal workspace usage system/adb is excluded via info/exclude so git status
    won't see it. This test documents the expected behavior and verifies the guard
    logic is reachable when git status can see the directory.
    """
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"apps"})
    apps_ws = workspace_dir / "apps"
    assert_is_worktree(apps_ws)

    result = subprocess.run(
        ["git", "status", "--porcelain", "--", "system/adb"],
        cwd=apps_ws,
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        _cleanup_worktrees(repo_env, workspace_dir, paths)
        pytest.skip(
            "system/adb excluded by info/exclude — dirty guard cannot fire in test env"
        )

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_dirty_dir_force(repo_env, workspace_dir):
    """BUG-005: promote with force=True succeeds when target dir has local changes."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"apps"})
    apps_ws = workspace_dir / "apps"

    adb_path = apps_ws / "system" / "adb"
    if adb_path.is_symlink():
        adb_path.unlink()
        adb_path.mkdir(parents=True)
    (adb_path / "new_file.txt").write_text("local work")

    try:
        promote(
            workspace_dir, repo_env.source_dir, "apps/system/adb", paths, force=True
        )
        assert_is_worktree(apps_ws / "system" / "adb")
    finally:
        _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_rollback_on_add_failure(repo_env, workspace_dir, monkeypatch):
    """BUG-010: if git_worktree_add fails after rmtree, directory is restored."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"apps"})
    apps_ws = workspace_dir / "apps"
    assert_is_worktree(apps_ws)

    adb_path = apps_ws / "system" / "adb"
    if adb_path.is_symlink():
        adb_path.unlink()
        adb_path.mkdir(parents=True)
    sentinel = adb_path / "sentinel.txt"
    sentinel.write_text("do not lose me")

    import repoworktree.promote as _promote_mod

    original_add = _promote_mod.git_worktree_add

    call_count = [0]

    def failing_add(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("simulated add failure")
        return original_add(*args, **kwargs)

    monkeypatch.setattr(_promote_mod, "git_worktree_add", failing_add)

    with pytest.raises(RuntimeError, match="simulated add failure"):
        promote(
            workspace_dir, repo_env.source_dir, "apps/system/adb", paths, force=True
        )

    assert adb_path.exists() and sentinel.exists(), (
        "directory must be restored after failed promote — backup rollback not working"
    )

    _cleanup_worktrees(repo_env, workspace_dir, paths)


def test_promote_child_repo_dirty_dir_rejected_with_rmtree_tracking(
    repo_env, workspace_dir
):
    """BUG-015: _dir_has_changes blocks promote when parent worktree sees dirty child dir."""
    paths = _create_ws_with_worktrees(repo_env, workspace_dir, {"apps"})
    apps_ws = workspace_dir / "apps"
    assert_is_worktree(apps_ws)

    adb_path = apps_ws / "system" / "adb"
    if adb_path.is_symlink():
        adb_path.unlink()
        adb_path.mkdir(parents=True)
    (adb_path / "change.txt").write_text("local work")

    result = subprocess.run(
        ["git", "status", "--porcelain", "--", "system/adb"],
        cwd=apps_ws,
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        pytest.skip("git status doesn't see system/adb (excluded by rwt)")

    import repoworktree.promote as _promote_mod

    original_rmtree = _promote_mod.shutil.rmtree
    deleted_paths = []

    def tracking_rmtree(path, *a, **kw):
        deleted_paths.append(str(path))
        return original_rmtree(path, *a, **kw)

    _promote_mod.shutil.rmtree = tracking_rmtree
    try:
        with pytest.raises((DirtyWorktreeError, Exception)):
            promote(workspace_dir, repo_env.source_dir, "apps/system/adb", paths)
        assert not any("adb" in p for p in deleted_paths), (
            f"adb dir must not be rmtree'd when dirty, but got: {deleted_paths}"
        )
    finally:
        _promote_mod.shutil.rmtree = original_rmtree

    _cleanup_worktrees(repo_env, workspace_dir, paths)
