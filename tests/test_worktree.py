"""Unit tests for repoworktree/worktree.py — Git Worktree wrapper."""

import subprocess

import pytest
from repoworktree.worktree import (
    add_worktree,
    remove_worktree,
    list_worktrees,
    has_local_changes,
    has_local_commits,
    get_head,
    DirtyWorktreeError,
)


def _git(args, cwd):
    return subprocess.run(
        ["git"] + args, cwd=cwd, check=True, capture_output=True, text=True
    )


def test_add_detached(repo_env, tmp_path):
    """Create detached HEAD worktree, verify HEAD matches source."""
    source = repo_env.source_dir / "nuttx"
    target = tmp_path / "nuttx-wt"

    add_worktree(source, target)

    assert target.is_dir()
    assert (target / ".git").is_file()  # worktree marker
    source_head = get_head(source)
    wt_head = get_head(target)
    assert wt_head == source_head

    # Cleanup
    remove_worktree(source, target, force=True)


def test_add_branch(repo_env, tmp_path):
    """Create worktree with named branch, verify branch name and HEAD."""
    source = repo_env.source_dir / "nuttx"
    target = tmp_path / "nuttx-branch"

    add_worktree(source, target, branch="test-branch")

    assert target.is_dir()
    result = _git(["symbolic-ref", "--short", "HEAD"], cwd=target)
    assert result.stdout.strip() == "test-branch"

    source_head = get_head(source)
    wt_head = get_head(target)
    assert wt_head == source_head

    # Cleanup
    _git(["worktree", "remove", "--force", str(target)], cwd=source)
    _git(["branch", "-D", "test-branch"], cwd=source)


def test_add_pinned(repo_env, tmp_path):
    """Create worktree pinned to a specific commit."""
    source = repo_env.source_dir / "nuttx"
    source_head = get_head(source)
    target = tmp_path / "nuttx-pinned"

    add_worktree(source, target, pin_version=source_head)

    wt_head = get_head(target)
    assert wt_head == source_head

    # Cleanup
    remove_worktree(source, target, force=True)


def test_remove_clean(repo_env, tmp_path):
    """Remove a clean worktree succeeds."""
    source = repo_env.source_dir / "nuttx"
    target = tmp_path / "nuttx-clean"

    add_worktree(source, target)
    assert target.is_dir()

    remove_worktree(source, target)
    assert not target.exists()


def test_remove_dirty_rejected(repo_env, tmp_path):
    """Remove a dirty worktree without force raises DirtyWorktreeError."""
    source = repo_env.source_dir / "nuttx"
    target = tmp_path / "nuttx-dirty"

    add_worktree(source, target)
    (target / "dirty.txt").write_text("dirty")

    with pytest.raises(DirtyWorktreeError, match="uncommitted changes"):
        remove_worktree(source, target)

    # Worktree should still exist
    assert target.is_dir()

    # Cleanup
    remove_worktree(source, target, force=True)


def test_remove_dirty_force(repo_env, tmp_path):
    """Remove a dirty worktree with force=True succeeds."""
    source = repo_env.source_dir / "nuttx"
    target = tmp_path / "nuttx-dirty-force"

    add_worktree(source, target)
    (target / "dirty.txt").write_text("dirty")

    remove_worktree(source, target, force=True)
    assert not target.exists()


def test_list(repo_env, tmp_path):
    """List worktrees returns all worktrees including new ones."""
    source = repo_env.source_dir / "nuttx"
    target = tmp_path / "nuttx-list"

    before = list_worktrees(source)
    add_worktree(source, target)
    after = list_worktrees(source)

    assert len(after) == len(before) + 1
    paths = [w["path"] for w in after]
    assert str(target) in paths

    # Cleanup
    remove_worktree(source, target, force=True)


def test_has_local_changes(repo_env, tmp_path):
    """Detect uncommitted changes in a worktree."""
    source = repo_env.source_dir / "nuttx"
    target = tmp_path / "nuttx-changes"

    add_worktree(source, target)
    assert not has_local_changes(target)

    (target / "new_file.txt").write_text("new")
    assert has_local_changes(target)

    # Cleanup
    remove_worktree(source, target, force=True)


def test_has_local_commits(repo_env, tmp_path):
    """Detect local commits relative to a base commit."""
    source = repo_env.source_dir / "nuttx"
    target = tmp_path / "nuttx-commits"
    base = get_head(source)

    add_worktree(source, target)
    assert not has_local_commits(target, base)

    # Make a commit in the worktree
    (target / "committed.txt").write_text("committed")
    _git(["add", "committed.txt"], cwd=target)
    _git(
        [
            "-c",
            "user.email=test@test.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "local commit",
        ],
        cwd=target,
    )

    assert has_local_commits(target, base)

    # Cleanup
    remove_worktree(source, target, force=True)


def test_multiple_worktrees_same_repo(repo_env, tmp_path):
    """Two worktrees from the same repo are independent."""
    source = repo_env.source_dir / "nuttx"
    target1 = tmp_path / "nuttx-wt1"
    target2 = tmp_path / "nuttx-wt2"

    add_worktree(source, target1)
    add_worktree(source, target2)

    # Modify one, the other should be clean
    (target1 / "dirty.txt").write_text("dirty")
    assert has_local_changes(target1)
    assert not has_local_changes(target2)

    # Cleanup
    remove_worktree(source, target1, force=True)
    remove_worktree(source, target2, force=True)
