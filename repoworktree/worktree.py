"""
Git Worktree — wrapper around git worktree add/remove/list operations.

Provides a clean Python interface for managing git worktrees,
with support for detached HEAD, named branches, pinned versions,
and dirty state detection.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class WorktreeError(Exception):
    """Error during worktree operation."""
    pass


class DirtyWorktreeError(WorktreeError):
    """Attempted to remove a worktree with uncommitted changes."""
    pass


def _git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command."""
    return subprocess.run(
        ["git"] + args, cwd=cwd, check=check, capture_output=True, text=True,
    )


def add_worktree(
    source_repo: Path,
    target_path: Path,
    branch: str | None = None,
    pin_version: str | None = None,
) -> None:
    """
    Create a git worktree.

    Args:
        source_repo: Path to the source git repo (must have .git).
        target_path: Where to create the worktree.
        branch: If set, create a named branch. Otherwise detached HEAD.
        pin_version: If set, checkout this commit/tag/branch.
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["worktree", "add"]

    if branch:
        cmd += ["-b", branch]
        cmd.append(str(target_path))
        if pin_version:
            cmd.append(pin_version)
    elif pin_version:
        cmd += ["--detach", str(target_path), pin_version]
    else:
        cmd += ["--detach", str(target_path)]

    try:
        _git(cmd, cwd=source_repo)
    except subprocess.CalledProcessError as e:
        raise WorktreeError(
            f"Failed to create worktree at {target_path}: {e.stderr.strip()}"
        ) from e


def remove_worktree(source_repo: Path, target_path: Path, force: bool = False) -> None:
    """
    Remove a git worktree.

    Args:
        source_repo: Path to the source git repo.
        target_path: Path of the worktree to remove.
        force: If True, remove even with uncommitted changes.

    Raises:
        DirtyWorktreeError: If worktree has changes and force=False.
    """
    if not force and has_local_changes(target_path):
        raise DirtyWorktreeError(
            f"Worktree has uncommitted changes: {target_path}\n"
            f"Use force=True or commit/stash changes first."
        )

    cmd = ["worktree", "remove"]
    if force:
        cmd.append("--force")
    cmd.append(str(target_path))

    try:
        _git(cmd, cwd=source_repo)
    except subprocess.CalledProcessError as e:
        raise WorktreeError(
            f"Failed to remove worktree {target_path}: {e.stderr.strip()}"
        ) from e


def list_worktrees(source_repo: Path) -> list[dict[str, str]]:
    """
    List all worktrees for a repo.

    Returns list of dicts with keys: path, head, branch.
    """
    result = _git(["worktree", "list", "--porcelain"], cwd=source_repo)
    worktrees = []
    current: dict[str, str] = {}

    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            if current:
                worktrees.append(current)
            current = {"path": line[len("worktree "):]}
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):]
        elif line == "detached":
            current["branch"] = None

    if current:
        worktrees.append(current)

    return worktrees


def has_local_changes(worktree_path: Path) -> bool:
    """Check if a worktree has uncommitted changes (staged or unstaged)."""
    result = _git(["status", "--porcelain"], cwd=worktree_path)
    return bool(result.stdout.strip())


def has_local_commits(worktree_path: Path, base_commit: str) -> bool:
    """
    Check if a worktree has local commits beyond base_commit.

    Args:
        worktree_path: Path to the worktree.
        base_commit: The commit to compare against (typically source repo HEAD).
    """
    result = _git(
        ["rev-list", "--count", f"{base_commit}..HEAD"],
        cwd=worktree_path, check=False,
    )
    if result.returncode != 0:
        return False
    return int(result.stdout.strip()) > 0


def get_head(repo_path: Path) -> str:
    """Get the HEAD commit hash of a repo or worktree."""
    result = _git(["rev-parse", "HEAD"], cwd=repo_path)
    return result.stdout.strip()


def checkout_detached(worktree_path: Path, commit: str) -> None:
    """Checkout a specific commit in detached HEAD mode."""
    try:
        _git(["checkout", "--detach", commit], cwd=worktree_path)
    except subprocess.CalledProcessError as e:
        raise WorktreeError(
            f"Failed to checkout {commit} in {worktree_path}: {e.stderr.strip()}"
        ) from e
