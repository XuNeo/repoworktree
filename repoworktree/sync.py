"""
Sync — update workspace worktrees to match source repo HEAD.

Handles five worktree states:
- pinned: skip (user explicitly locked version)
- dirty (uncommitted changes): skip, report
- local commits (no --rebase): skip, report
- local commits (--rebase): rebase onto source HEAD
- clean, no local commits: fast-forward to source HEAD
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from repoworktree.metadata import load_workspace_metadata, save_workspace_metadata
from repoworktree.worktree import get_head, has_local_changes, has_local_commits, _git


@dataclass
class SyncResult:
    """Result of syncing a single worktree."""
    path: str
    action: str          # "updated", "rebased", "skipped", "already_up_to_date"
    reason: str = ""     # why skipped
    old_head: str = ""
    new_head: str = ""


@dataclass
class SyncReport:
    """Aggregate result of syncing all worktrees."""
    results: list[SyncResult] = field(default_factory=list)

    @property
    def updated(self) -> list[SyncResult]:
        return [r for r in self.results if r.action in ("updated", "rebased")]

    @property
    def skipped(self) -> list[SyncResult]:
        return [r for r in self.results if r.action == "skipped"]


def sync(
    workspace: Path,
    source: Path,
    rebase: bool = False,
) -> SyncReport:
    """
    Sync all worktrees in a workspace to their source repo HEAD.

    Args:
        workspace: Path to the workspace directory.
        source: Path to the source repo checkout.
        rebase: If True, rebase local commits onto source HEAD.

    Returns:
        SyncReport with per-worktree results.
    """
    meta = load_workspace_metadata(workspace)
    report = SyncReport()

    for wt in meta.worktrees:
        wt_path = workspace / wt.path
        src_path = source / wt.path

        # Skip if worktree directory doesn't exist or isn't a worktree
        if not wt_path.is_dir() or not (wt_path / ".git").is_file():
            report.results.append(SyncResult(
                path=wt.path, action="skipped", reason="worktree not found",
            ))
            continue

        # 1. Pinned: skip
        if wt.pinned:
            report.results.append(SyncResult(
                path=wt.path, action="skipped", reason="pinned",
            ))
            continue

        wt_head = get_head(wt_path)
        src_head = get_head(src_path)

        # 2. Already up to date
        if wt_head == src_head:
            report.results.append(SyncResult(
                path=wt.path, action="already_up_to_date",
                old_head=wt_head, new_head=src_head,
            ))
            continue

        # 3. Dirty: skip
        if has_local_changes(wt_path):
            report.results.append(SyncResult(
                path=wt.path, action="skipped", reason="uncommitted changes",
                old_head=wt_head,
            ))
            continue

        # 4. Local commits
        if has_local_commits(wt_path, src_head):
            if rebase:
                result = _rebase_onto(wt_path, wt.path, src_head)
                report.results.append(result)
            else:
                report.results.append(SyncResult(
                    path=wt.path, action="skipped",
                    reason="local commits (use --rebase)",
                    old_head=wt_head,
                ))
            continue

        # 5. Clean, no local commits: update to source HEAD
        result = _update_to(wt_path, wt.path, src_head, wt_head)
        report.results.append(result)

    return report


def _update_to(wt_path: Path, repo_path: str, target: str, old_head: str) -> SyncResult:
    """Update a clean worktree to a target commit."""
    try:
        _git(["checkout", "--detach", target], cwd=wt_path)
        return SyncResult(
            path=repo_path, action="updated",
            old_head=old_head, new_head=target,
        )
    except Exception as e:
        return SyncResult(
            path=repo_path, action="skipped",
            reason=f"update failed: {e}", old_head=old_head,
        )


def _rebase_onto(wt_path: Path, repo_path: str, upstream: str) -> SyncResult:
    """Rebase local commits onto upstream."""
    old_head = get_head(wt_path)
    try:
        _git(["rebase", upstream], cwd=wt_path)
        new_head = get_head(wt_path)
        return SyncResult(
            path=repo_path, action="rebased",
            old_head=old_head, new_head=new_head,
        )
    except Exception:
        # Abort failed rebase
        _git(["rebase", "--abort"], cwd=wt_path)
        return SyncResult(
            path=repo_path, action="skipped",
            reason="rebase conflict", old_head=old_head,
        )
