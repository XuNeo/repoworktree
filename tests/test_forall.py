"""
Tests for rwt forall command.

Covers: default scope (worktrees only), --all flag, env vars,
CWD, abort-on-errors, print-header, parallel execution, repo filter,
and exit-code propagation.
"""

import pytest
from pathlib import Path

from repoworktree.__main__ import cmd_forall
from repoworktree.scanner import scan_repos, build_trie
from repoworktree.layout import build_workspace
from repoworktree.metadata import (
    WorktreeEntry,
    create_workspace_metadata,
    save_workspace_metadata,
    load_workspace_index,
    save_workspace_index,
)
from tests.conftest import REPO_DEFS

ALL_CHECKOUT_PATHS = sorted(p for _, p, _ in REPO_DEFS)


def _create_workspace(repo_env, ws_dir, wt_paths=None, name="test"):
    """Create a workspace with the given repos as worktrees."""
    wt_set = set(wt_paths or [])
    paths = scan_repos(repo_env.source_dir)
    trie = build_trie(paths, worktree_paths=wt_set)
    build_workspace(repo_env.source_dir, ws_dir, trie)
    meta = create_workspace_metadata(
        source=str(repo_env.source_dir),
        name=name,
        worktrees=[WorktreeEntry(p) for p in sorted(wt_set)],
    )
    save_workspace_metadata(ws_dir, meta)
    index = load_workspace_index(repo_env.source_dir)
    index.register(name, str(ws_dir), meta.created)
    save_workspace_index(repo_env.source_dir, index)
    return paths


class _Args:
    """Minimal args object for cmd_forall."""
    workspace = None
    command = "true"
    all_repos = False
    repos = []
    jobs = 1
    print_header = False
    abort_on_errors = False


# ── Scope: worktrees only (default) ──────────────────────────────


def test_forall_runs_in_worktrees_only_by_default(repo_env, workspace_dir, tmp_path):
    """By default forall visits only worktree repos, not symlinked repos."""
    _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx", "apps"])
    result_file = tmp_path / "visited.txt"

    args = _Args()
    args.workspace = str(workspace_dir)
    args.command = f"echo $RWT_PROJECT >> {result_file}"

    ret = cmd_forall(args)

    assert ret == 0
    visited = set(result_file.read_text().strip().splitlines())
    assert visited == {"nuttx", "apps"}
    # symlinked repos must NOT appear
    assert "build" not in visited
    assert "frameworks" not in visited
    assert "external/lib-a" not in visited


def test_forall_default_no_worktrees_returns_zero(repo_env, workspace_dir):
    """forall with no worktrees (all-symlink workspace) returns 0 quietly."""
    _create_workspace(repo_env, workspace_dir, wt_paths=[])

    args = _Args()
    args.workspace = str(workspace_dir)
    args.command = "true"

    ret = cmd_forall(args)
    assert ret == 0


# ── Scope: --all includes symlinked repos ────────────────────────


def test_forall_all_includes_symlinked_repos(repo_env, workspace_dir, tmp_path):
    """With --all, forall visits every repo in the workspace including symlinks."""
    _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx"])
    result_file = tmp_path / "visited.txt"

    args = _Args()
    args.workspace = str(workspace_dir)
    args.all_repos = True
    args.command = f"echo $RWT_PROJECT >> {result_file}"

    ret = cmd_forall(args)

    assert ret == 0
    visited = set(result_file.read_text().strip().splitlines())
    for repo in ALL_CHECKOUT_PATHS:
        assert repo in visited, f"Expected repo {repo!r} in --all traversal"


# ── Environment variables ────────────────────────────────────────


def test_forall_env_vars_are_set(repo_env, workspace_dir, tmp_path):
    """forall sets RWT_PROJECT, RWT_PATH, RWT_TYPE, RWT_COUNT, RWT_I."""
    _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx"])
    result_file = tmp_path / "env.txt"

    args = _Args()
    args.workspace = str(workspace_dir)
    args.command = f'echo "$RWT_PROJECT|$RWT_PATH|$RWT_TYPE|$RWT_COUNT|$RWT_I" >> {result_file}'

    ret = cmd_forall(args)

    assert ret == 0
    line = result_file.read_text().strip()
    project, path, rtype, count, idx = line.split("|")
    assert project == "nuttx"
    assert path == str((workspace_dir / "nuttx").resolve())
    assert rtype == "worktree"
    assert count == "1"
    assert idx == "0"


def test_forall_env_var_rwt_type_symlink(repo_env, workspace_dir, tmp_path):
    """RWT_TYPE is 'symlink' for symlinked repos when using --all."""
    _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx"])
    result_file = tmp_path / "env.txt"

    args = _Args()
    args.workspace = str(workspace_dir)
    args.all_repos = True
    args.command = f'echo "$RWT_PROJECT|$RWT_TYPE" >> {result_file}'

    ret = cmd_forall(args)

    assert ret == 0
    lines = result_file.read_text().strip().splitlines()
    types_by_project = {line.split("|")[0]: line.split("|")[1] for line in lines}
    assert types_by_project["nuttx"] == "worktree"
    # any non-worktree repo should have type 'symlink'
    symlink_repos = [p for p in ALL_CHECKOUT_PATHS if p != "nuttx"]
    assert any(types_by_project.get(p) == "symlink" for p in symlink_repos)


def test_forall_env_var_rwt_count_and_index(repo_env, workspace_dir, tmp_path):
    """RWT_COUNT equals total repos iterated; RWT_I is 0-based per-repo index."""
    _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx", "apps"])
    result_file = tmp_path / "env.txt"

    args = _Args()
    args.workspace = str(workspace_dir)
    args.command = f'echo "$RWT_COUNT|$RWT_I" >> {result_file}'

    ret = cmd_forall(args)

    assert ret == 0
    lines = result_file.read_text().strip().splitlines()
    assert len(lines) == 2
    counts = {line.split("|")[0] for line in lines}
    assert counts == {"2"}  # both see count=2
    indices = {line.split("|")[1] for line in lines}
    assert indices == {"0", "1"}  # one gets 0, other gets 1


# ── CWD ─────────────────────────────────────────────────────────


def test_forall_cwd_is_repo_directory(repo_env, workspace_dir, tmp_path):
    """The command runs with CWD set to the repo's path in the workspace."""
    _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx"])
    result_file = tmp_path / "cwd.txt"

    args = _Args()
    args.workspace = str(workspace_dir)
    args.command = f"pwd >> {result_file}"

    ret = cmd_forall(args)

    assert ret == 0
    cwd = result_file.read_text().strip()
    assert cwd == str((workspace_dir / "nuttx").resolve())


# ── Exit code ────────────────────────────────────────────────────


def test_forall_returns_zero_on_success(repo_env, workspace_dir):
    """forall returns 0 when all commands exit 0."""
    _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx"])

    args = _Args()
    args.workspace = str(workspace_dir)
    args.command = "true"

    assert cmd_forall(args) == 0


def test_forall_returns_nonzero_on_failure(repo_env, workspace_dir):
    """forall returns non-zero when any command exits non-zero."""
    _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx"])

    args = _Args()
    args.workspace = str(workspace_dir)
    args.command = "exit 42"

    assert cmd_forall(args) != 0


# ── Abort on errors ──────────────────────────────────────────────


def test_forall_abort_on_errors_stops_after_first_failure(repo_env, workspace_dir, tmp_path):
    """With --abort-on-errors, forall stops iterating after the first non-zero exit."""
    _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx", "apps"])
    result_file = tmp_path / "visited.txt"

    args = _Args()
    args.workspace = str(workspace_dir)
    args.abort_on_errors = True
    args.command = f"echo $RWT_PROJECT >> {result_file}; exit 1"

    ret = cmd_forall(args)

    assert ret != 0
    visited = result_file.read_text().strip().splitlines()
    assert len(visited) == 1, f"Should stop after first failure, visited: {visited}"


def test_forall_without_abort_continues_on_failure(repo_env, workspace_dir, tmp_path):
    """Without --abort-on-errors, forall continues even when a command fails."""
    _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx", "apps"])
    result_file = tmp_path / "visited.txt"

    args = _Args()
    args.workspace = str(workspace_dir)
    args.abort_on_errors = False
    args.command = f"echo $RWT_PROJECT >> {result_file}; exit 1"

    ret = cmd_forall(args)

    assert ret != 0
    visited = result_file.read_text().strip().splitlines()
    assert len(visited) == 2, f"Should visit all repos despite failure, visited: {visited}"


# ── Print header ─────────────────────────────────────────────────


def test_forall_print_header_shows_project_name(repo_env, workspace_dir, capsys):
    """With -p, forall prints a header line containing the project name."""
    _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx"])

    args = _Args()
    args.workspace = str(workspace_dir)
    args.print_header = True
    args.command = "true"

    ret = cmd_forall(args)
    assert ret == 0

    captured = capsys.readouterr()
    assert "nuttx" in captured.out


def test_forall_no_header_by_default(repo_env, workspace_dir, capsys):
    """Without -p, forall does not print extra header lines."""
    _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx"])

    args = _Args()
    args.workspace = str(workspace_dir)
    args.print_header = False
    args.command = "true"

    ret = cmd_forall(args)
    assert ret == 0

    captured = capsys.readouterr()
    assert "nuttx" not in captured.out


# ── Repo filter (positional args) ───────────────────────────────


def test_forall_filter_repos_by_name(repo_env, workspace_dir, tmp_path):
    """Positional repo args limit iteration to only the specified repos."""
    _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx", "apps"])
    result_file = tmp_path / "visited.txt"

    args = _Args()
    args.workspace = str(workspace_dir)
    args.repos = ["nuttx"]
    args.command = f"echo $RWT_PROJECT >> {result_file}"

    ret = cmd_forall(args)

    assert ret == 0
    visited = result_file.read_text().strip().splitlines()
    assert visited == ["nuttx"]


def test_forall_filter_repos_respects_scope(repo_env, workspace_dir, tmp_path):
    """Filter repos must only select from the current scope (worktrees or --all)."""
    _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx"])
    result_file = tmp_path / "visited.txt"

    # "build" is symlinked — not in default scope → filter should return nothing
    args = _Args()
    args.workspace = str(workspace_dir)
    args.repos = ["build"]   # symlink, not in default worktree scope
    args.command = f"echo $RWT_PROJECT >> {result_file}"

    ret = cmd_forall(args)

    assert ret == 0
    assert not result_file.exists() or result_file.read_text().strip() == ""


# ── Parallel execution ───────────────────────────────────────────


def test_forall_parallel_visits_all_repos(repo_env, workspace_dir, tmp_path):
    """With -j N, all repos are still visited."""
    _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx", "apps"])
    marker_dir = tmp_path / "markers"
    marker_dir.mkdir()

    args = _Args()
    args.workspace = str(workspace_dir)
    args.jobs = 4
    # Write one marker file per repo (name: repo path with / replaced by _)
    args.command = (
        f"sh -c 'echo done > {marker_dir}/$(echo $RWT_PROJECT | tr / _).txt'"
    )

    ret = cmd_forall(args)

    assert ret == 0
    markers = list(marker_dir.glob("*.txt"))
    assert len(markers) == 2


def test_forall_parallel_print_header_shows_all_projects(repo_env, workspace_dir, capsys):
    """With -j N and -p, headers for all repos appear in output."""
    _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx", "apps"])

    args = _Args()
    args.workspace = str(workspace_dir)
    args.jobs = 2
    args.print_header = True
    args.command = "true"

    ret = cmd_forall(args)
    assert ret == 0

    captured = capsys.readouterr()
    assert "nuttx" in captured.out
    assert "apps" in captured.out


def test_forall_parallel_returns_nonzero_on_any_failure(repo_env, workspace_dir):
    """With -j N, forall returns non-zero if any command fails."""
    _create_workspace(repo_env, workspace_dir, wt_paths=["nuttx", "apps"])

    args = _Args()
    args.workspace = str(workspace_dir)
    args.jobs = 2
    args.command = "exit 1"

    assert cmd_forall(args) != 0
