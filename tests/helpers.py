"""
Test helper functions for repo-workspace tests.

Provides assertion utilities and common operations for verifying
workspace structure, worktree state, and source integrity.
"""

import os
import subprocess
import time
from pathlib import Path


def _run(cmd, cwd=None, check=True):
    """Run a command, return CompletedProcess."""
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def _git(args, cwd):
    """Run a git command."""
    return _run(["git"] + args, cwd=cwd)


# ── Assertion helpers ──────────────────────────────────────────────


def assert_is_symlink(path: Path, target: Path = None):
    """Assert path is a symlink. If target given, verify it points there."""
    assert path.is_symlink(), f"Expected symlink: {path}"
    if target is not None:
        actual = path.resolve()
        expected = target.resolve()
        assert actual == expected, (
            f"Symlink {path} points to {actual}, expected {expected}"
        )


def assert_is_symlink_to(path: Path, target: Path):
    """Assert path is a symlink pointing to target (without resolving intermediate symlinks)."""
    assert path.is_symlink(), f"Expected symlink: {path}"
    actual_target = os.readlink(path)
    # Compare as resolved absolute paths
    if not os.path.isabs(actual_target):
        actual_target = str((path.parent / actual_target).resolve())
    assert Path(actual_target).resolve() == target.resolve(), (
        f"Symlink {path} -> {actual_target}, expected -> {target}"
    )


def assert_is_worktree(path: Path):
    """Assert path is a git worktree (has .git file pointing to worktree metadata)."""
    git_path = path / ".git"
    assert path.is_dir(), f"Expected directory: {path}"
    assert git_path.exists(), f"No .git entry at: {path}"
    assert git_path.is_file(), (
        f"Expected .git to be a file (worktree marker), got directory: {git_path}"
    )
    content = git_path.read_text().strip()
    assert content.startswith("gitdir:"), (
        f".git file does not start with 'gitdir:': {content}"
    )


def assert_is_real_dir(path: Path):
    """Assert path is a real directory (not a symlink)."""
    assert path.is_dir(), f"Expected directory: {path}"
    assert not path.is_symlink(), f"Expected real directory, got symlink: {path}"


def assert_workspace_clean(workspace_dir: Path, source_dir: Path):
    """Assert workspace has been fully cleaned up."""
    assert not workspace_dir.exists(), (
        f"Workspace directory still exists: {workspace_dir}"
    )
    # Verify no dangling worktree references in source repos
    for git_dir in source_dir.rglob(".git"):
        if git_dir.is_dir() and (git_dir / "worktrees").is_dir():
            for wt in (git_dir / "worktrees").iterdir():
                gitdir_file = wt / "gitdir"
                if gitdir_file.exists():
                    target = gitdir_file.read_text().strip()
                    assert not target.startswith(str(workspace_dir)), (
                        f"Dangling worktree reference in {wt}: {target}"
                    )


def assert_source_untouched(source_dir: Path, snapshot: dict):
    """Assert source directory matches a previously taken snapshot."""
    current = take_source_snapshot(source_dir)
    for repo_path, commit_hash in snapshot.items():
        assert repo_path in current, f"Repo {repo_path} missing from source"
        assert current[repo_path] == commit_hash, (
            f"Source repo {repo_path} HEAD changed: "
            f"was {commit_hash}, now {current[repo_path]}"
        )


# ── Snapshot helpers ───────────────────────────────────────────────


def take_source_snapshot(source_dir: Path) -> dict:
    """
    Take a snapshot of all sub-repo HEAD commits in source.
    Returns {repo_path: commit_hash}.
    """
    snapshot = {}
    project_list = source_dir / ".repo" / "project.list"
    if project_list.exists():
        for line in project_list.read_text().strip().splitlines():
            repo_path = line.strip()
            if repo_path:
                try:
                    result = _git(["rev-parse", "HEAD"], cwd=source_dir / repo_path)
                    snapshot[repo_path] = result.stdout.strip()
                except subprocess.CalledProcessError:
                    pass
    return snapshot


# ── Mutation helpers ───────────────────────────────────────────────


def make_dirty(repo_path: Path, filename: str = "dirty.txt"):
    """Create an uncommitted modified file in a repo."""
    fpath = repo_path / filename
    fpath.write_text(f"dirty at {time.time()}\n")


def make_staged(repo_path: Path, filename: str = "staged.txt"):
    """Create a staged but uncommitted file in a repo."""
    fpath = repo_path / filename
    fpath.write_text(f"staged at {time.time()}\n")
    _git(["add", str(fpath)], cwd=repo_path)


def make_commit(repo_path: Path, message: str = "test commit",
                filename: str = "committed.txt") -> str:
    """Create a commit in a repo. Returns the commit hash."""
    fpath = repo_path / filename
    fpath.write_text(f"committed at {time.time()}\n")
    _git(["add", str(fpath)], cwd=repo_path)
    _git(["-c", "user.email=test@test.com", "-c", "user.name=Test",
          "commit", "-m", message], cwd=repo_path)
    result = _git(["rev-parse", "HEAD"], cwd=repo_path)
    return result.stdout.strip()


def push_remote_update(remote_bare: Path, branch: str = "master",
                       filename: str = "remote_update.txt") -> str:
    """
    Push a new commit to a bare repo, simulating a remote update.
    Returns the new commit hash.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _run(["git", "clone", str(remote_bare), tmp])
        _git(["config", "user.email", "test@test.com"], cwd=tmp)
        _git(["config", "user.name", "Test"], cwd=tmp)
        fpath = Path(tmp) / filename
        fpath.write_text(f"remote update at {time.time()}\n")
        _git(["add", "-A"], cwd=tmp)
        _git(["commit", "-m", "remote update"], cwd=tmp)
        _git(["push", "origin", branch], cwd=tmp)
        result = _git(["rev-parse", "HEAD"], cwd=tmp)
        return result.stdout.strip()


def sync_source_repo(source_dir: Path, repo_path: str):
    """
    Update a sub-repo in source to the latest remote HEAD.
    Simulates what `repo sync` does for a single project.
    Auto-detects the remote name (repo uses 'local', normal git uses 'origin').
    """
    full_path = source_dir / repo_path
    # Get the first remote name
    result = _git(["remote"], cwd=full_path)
    remote = result.stdout.strip().splitlines()[0]
    _git(["fetch", remote], cwd=full_path)
    _git(["checkout", "FETCH_HEAD", "--detach"], cwd=full_path)


# ── Query helpers ──────────────────────────────────────────────────


def get_head_commit(repo_path: Path) -> str:
    """Get the HEAD commit hash of a repo."""
    result = _git(["rev-parse", "HEAD"], cwd=repo_path)
    return result.stdout.strip()


def has_local_changes(repo_path: Path) -> bool:
    """Check if a repo has uncommitted changes (staged or unstaged)."""
    result = _git(["status", "--porcelain"], cwd=repo_path)
    return bool(result.stdout.strip())


def is_on_branch(repo_path: Path, branch_name: str) -> bool:
    """Check if a repo is on a specific branch."""
    result = _git(["symbolic-ref", "--short", "HEAD"], cwd=repo_path)
    return result.stdout.strip() == branch_name


def is_detached(repo_path: Path) -> bool:
    """Check if a repo is in detached HEAD state."""
    result = _run(["git", "symbolic-ref", "HEAD"], cwd=repo_path, check=False)
    return result.returncode != 0


def count_commits_ahead(repo_path: Path, base_commit: str) -> int:
    """Count how many commits repo_path HEAD is ahead of base_commit."""
    result = _git(["rev-list", "--count", f"{base_commit}..HEAD"], cwd=repo_path)
    return int(result.stdout.strip())
