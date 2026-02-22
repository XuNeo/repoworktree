"""
Export — export changes from workspace worktrees as patches or bundles.

Supports two formats:
- patch: git format-patch (one .patch file per worktree with commits)
- bundle: git bundle (one .bundle file per worktree with commits)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from repoworktree.metadata import load_workspace_metadata
from repoworktree.worktree import get_head, has_local_commits, _git


@dataclass
class ExportResult:
    """Result of exporting a single worktree."""
    path: str
    action: str       # "exported", "skipped"
    reason: str = ""
    output_file: str = ""
    commit_count: int = 0


@dataclass
class ExportReport:
    """Aggregate result of exporting all worktrees."""
    results: list[ExportResult] = field(default_factory=list)

    @property
    def exported(self) -> list[ExportResult]:
        return [r for r in self.results if r.action == "exported"]

    @property
    def skipped(self) -> list[ExportResult]:
        return [r for r in self.results if r.action == "skipped"]


def export(
    workspace: Path,
    source: Path,
    output_dir: Path,
    fmt: str = "patch",
) -> ExportReport:
    """
    Export changes from all worktrees with local commits.

    Args:
        workspace: Path to the workspace directory.
        source: Path to the source repo checkout.
        output_dir: Directory to write exported files.
        fmt: Export format — "patch" or "bundle".

    Returns:
        ExportReport with per-worktree results.
    """
    meta = load_workspace_metadata(workspace)
    output_dir.mkdir(parents=True, exist_ok=True)
    report = ExportReport()

    for wt in meta.worktrees:
        wt_path = workspace / wt.path
        src_path = source / wt.path

        if not wt_path.is_dir() or not (wt_path / ".git").is_file():
            report.results.append(ExportResult(
                path=wt.path, action="skipped", reason="worktree not found",
            ))
            continue

        src_head = get_head(src_path)

        if not has_local_commits(wt_path, src_head):
            report.results.append(ExportResult(
                path=wt.path, action="skipped", reason="no local commits",
            ))
            continue

        # Count commits
        count_result = _git(
            ["rev-list", "--count", f"{src_head}..HEAD"], cwd=wt_path,
        )
        commit_count = int(count_result.stdout.strip())

        # Safe filename: replace / with -
        safe_name = wt.path.replace("/", "-")

        if fmt == "patch":
            result = _export_patch(wt_path, wt.path, safe_name, src_head, output_dir, commit_count)
        elif fmt == "bundle":
            result = _export_bundle(wt_path, wt.path, safe_name, src_head, output_dir, commit_count)
        else:
            result = ExportResult(
                path=wt.path, action="skipped", reason=f"unknown format: {fmt}",
            )

        report.results.append(result)

    return report


def _export_patch(
    wt_path: Path, repo_path: str, safe_name: str,
    base: str, output_dir: Path, commit_count: int,
) -> ExportResult:
    """Export commits as patch files using git format-patch."""
    patch_dir = output_dir / safe_name
    patch_dir.mkdir(parents=True, exist_ok=True)

    try:
        _git(["format-patch", f"{base}..HEAD", "-o", str(patch_dir)], cwd=wt_path)
        return ExportResult(
            path=repo_path, action="exported",
            output_file=str(patch_dir), commit_count=commit_count,
        )
    except subprocess.CalledProcessError as e:
        return ExportResult(
            path=repo_path, action="skipped",
            reason=f"format-patch failed: {e.stderr.strip()}",
        )


def _export_bundle(
    wt_path: Path, repo_path: str, safe_name: str,
    base: str, output_dir: Path, commit_count: int,
) -> ExportResult:
    """Export commits as a git bundle."""
    bundle_file = output_dir / f"{safe_name}.bundle"

    try:
        _git(["bundle", "create", str(bundle_file), f"{base}..HEAD"], cwd=wt_path)
        return ExportResult(
            path=repo_path, action="exported",
            output_file=str(bundle_file), commit_count=commit_count,
        )
    except subprocess.CalledProcessError as e:
        return ExportResult(
            path=repo_path, action="skipped",
            reason=f"bundle failed: {e.stderr.strip()}",
        )
