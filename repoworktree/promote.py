"""
Promote / Demote — dynamically switch sub-repos between symlink and worktree.

Promote: symlink → worktree (with symlink splitting for nested paths)
Demote: worktree → symlink (with upward merging when possible)
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from repoworktree.scanner import scan_repos, build_trie
from repoworktree.metadata import load_workspace_metadata, save_workspace_metadata
from repoworktree.worktree import (
    add_worktree as git_worktree_add,
    remove_worktree as git_worktree_remove,
    has_local_changes,
    DirtyWorktreeError,
)


def _has_own_changes(worktree_path: Path, child_wts: list) -> bool:
    """
    Check if a worktree has its own uncommitted changes,
    excluding paths that belong to child worktrees.
    """
    from repoworktree.worktree import _git
    result = _git(["status", "--porcelain"], cwd=worktree_path)
    if not result.stdout.strip():
        return False

    # Get child worktree relative paths
    child_prefixes = []
    for cw in child_wts:
        # cw.path is absolute like "apps/system/adb", we need relative to worktree
        # e.g. if worktree is "apps", child is "apps/system/adb" → relative is "system/adb"
        wt_rel = str(worktree_path.name)
        if cw.path.startswith(wt_rel + "/"):
            child_prefixes.append(cw.path[len(wt_rel) + 1:])
        else:
            child_prefixes.append(cw.path)

    for line in result.stdout.strip().splitlines():
        # git status --porcelain format: "XY filename" or "XY filename -> newname"
        if len(line) < 4:
            continue
        filepath = line[3:].split(" -> ")[0].rstrip("/")
        # Check if this file belongs to a child worktree:
        # - filepath is inside a child prefix (e.g. "system/adb/foo")
        # - filepath IS a child prefix (e.g. "system/adb")
        # - filepath is a PARENT of a child prefix (e.g. "system" when child is "system/adb")
        is_child = any(
            filepath.startswith(prefix + "/") or filepath == prefix
            or prefix.startswith(filepath + "/")
            for prefix in child_prefixes
        )
        if not is_child:
            return True

    return False


class PromoteError(Exception):
    pass


class DemoteError(Exception):
    pass


def promote(
    workspace: Path,
    source: Path,
    repo_path: str,
    all_repos: list[str],
    branch: str | None = None,
    pin_version: str | None = None,
) -> None:
    """
    Promote a sub-repo from symlink/directory to git worktree.

    Handles three cases:
    1. Target is directly a symlink (top-level repo) → replace with worktree
    2. Target is inside a symlinked parent → split parent symlink, then create worktree
    3. Target is inside a real directory (already split) → create worktree in place

    For parent-child case (parent is already a worktree):
    4. Target is inside an existing worktree parent → just add child worktree
    """
    meta = load_workspace_metadata(workspace)
    target_ws = workspace / repo_path
    target_src = source / repo_path

    # Validate
    if repo_path not in all_repos:
        raise PromoteError(f"Not a valid sub-repo path: {repo_path}")
    if meta.find_worktree(repo_path):
        raise PromoteError(f"Already a worktree: {repo_path}")
    if not target_src.is_dir():
        raise PromoteError(f"Source repo does not exist: {target_src}")

    # Find existing child worktrees inside this repo
    child_wts = [w for w in meta.worktrees
                 if w.path.startswith(repo_path + "/")]
    child_info = []
    for cw in child_wts:
        child_ws_path = workspace / cw.path
        child_src_path = source / cw.path
        if child_ws_path.exists() and (child_ws_path / ".git").is_file():
            child_info.append(cw)
            # Temporarily remove child worktree
            try:
                git_worktree_remove(child_src_path, child_ws_path, force=True)
            except Exception:
                pass

    # Split symlinks along the path to the target
    _ensure_path_is_real(workspace, source, repo_path, all_repos)

    # Now target_ws should be either a symlink or a directory
    # If it's a symlink, remove it
    if target_ws.is_symlink():
        target_ws.unlink()
    elif target_ws.is_dir():
        # It's a real directory (from a parent worktree or previous split)
        # If it has a .git file, it's already a worktree — shouldn't happen
        if (target_ws / ".git").is_file():
            raise PromoteError(f"Already a worktree: {repo_path}")
        # Remove the directory contents (they're from parent worktree or symlinks)
        shutil.rmtree(target_ws)

    # Create git worktree
    git_worktree_add(target_src, target_ws, branch=branch, pin_version=pin_version)

    # Restore child worktrees on top
    for cw in child_info:
        child_ws_path = workspace / cw.path
        child_src_path = source / cw.path
        # The parent worktree may have created the directory already; remove it
        if child_ws_path.exists() and not (child_ws_path / ".git").is_file():
            shutil.rmtree(child_ws_path)
        elif child_ws_path.is_symlink():
            child_ws_path.unlink()
        git_worktree_add(child_src_path, child_ws_path,
                         branch=cw.branch, pin_version=cw.pinned)

    # Update metadata
    meta.add_worktree(repo_path, branch=branch, pinned=pin_version)
    save_workspace_metadata(workspace, meta)


def demote(
    workspace: Path,
    source: Path,
    repo_path: str,
    all_repos: list[str],
    force: bool = False,
) -> None:
    """
    Demote a sub-repo from worktree back to symlink.

    Handles:
    1. Simple top-level demote → remove worktree, create symlink
    2. Nested demote with child worktrees → rebuild directory structure
    3. After demote, try to merge parent directories back into symlinks (upward collapse)
    """
    meta = load_workspace_metadata(workspace)
    target_ws = workspace / repo_path
    target_src = source / repo_path

    # Validate
    entry = meta.find_worktree(repo_path)
    if not entry:
        raise DemoteError(f"Not a worktree: {repo_path}")
    if not (target_ws / ".git").is_file():
        raise DemoteError(f"Not a worktree directory: {target_ws}")

    # Find child worktrees that live inside this repo
    child_wts = [w for w in meta.worktrees
                 if w.path != repo_path and w.path.startswith(repo_path + "/")]

    # Check for dirty state (ignore child worktree paths in the check)
    if not force:
        if _has_own_changes(target_ws, child_wts):
            raise DirtyWorktreeError(
                f"Worktree has uncommitted changes: {repo_path}\n"
                f"Use force=True or commit/stash changes first."
            )

    # Save child worktree info for restoration
    child_info = []
    for cw in child_wts:
        child_ws_path = workspace / cw.path
        if child_ws_path.exists() and (child_ws_path / ".git").is_file():
            child_info.append(cw)

    # Remove child worktrees temporarily
    for cw in child_info:
        child_ws_path = workspace / cw.path
        child_src_path = source / cw.path
        try:
            git_worktree_remove(child_src_path, child_ws_path, force=True)
        except Exception:
            pass

    # Remove the main worktree
    # When child worktrees exist, has_local_changes() gives false positives
    # (child .git files appear as untracked). We already did a child-aware
    # dirty check above, so force removal is safe here.
    git_worktree_remove(target_src, target_ws, force=(force or bool(child_wts)))

    # Rebuild: if there are child worktrees, create directory structure
    if child_info:
        _rebuild_as_split_dir(workspace, source, repo_path, all_repos, meta)
        # Restore child worktrees
        for cw in child_info:
            child_ws_path = workspace / cw.path
            child_src_path = source / cw.path
            if child_ws_path.is_symlink():
                child_ws_path.unlink()
            elif child_ws_path.is_dir():
                shutil.rmtree(child_ws_path)
            git_worktree_add(child_src_path, child_ws_path,
                             branch=cw.branch, pin_version=cw.pinned)
    else:
        # Simple case: just create symlink
        target_ws.symlink_to(target_src)
        # Try upward collapse
        _try_collapse_upward(workspace, source, repo_path, all_repos, meta)

    # Update metadata
    meta.remove_worktree(repo_path)
    save_workspace_metadata(workspace, meta)


def _ensure_path_is_real(
    workspace: Path,
    source: Path,
    repo_path: str,
    all_repos: list[str],
) -> None:
    """
    Ensure all directories along the path to repo_path are real directories
    (not symlinks). Split symlinks as needed.

    For example, if repo_path is "frameworks/system/core" and "frameworks/"
    is a symlink, this will:
    1. Remove frameworks/ symlink
    2. mkdir frameworks/
    3. Symlink all entries in source/frameworks/ except "system"
    4. mkdir frameworks/system/
    5. Symlink all entries in source/frameworks/system/ except "core"
    """
    parts = repo_path.split("/")

    for i in range(len(parts) - 1):  # Don't process the last part (the target itself)
        partial = "/".join(parts[:i + 1])
        ws_dir = workspace / partial
        src_dir = source / partial

        if ws_dir.is_symlink():
            # Need to split this symlink into a real directory
            ws_dir.unlink()
            ws_dir.mkdir(parents=True, exist_ok=True)

            # Symlink all entries except the next part in our path
            next_part = parts[i + 1]
            _symlink_dir_contents(src_dir, ws_dir, exclude={next_part, ".git"})

        elif not ws_dir.exists():
            ws_dir.mkdir(parents=True, exist_ok=True)
            next_part = parts[i + 1]
            _symlink_dir_contents(src_dir, ws_dir, exclude={next_part, ".git"})


def _symlink_dir_contents(
    src_dir: Path,
    ws_dir: Path,
    exclude: set[str] | None = None,
) -> None:
    """Symlink all entries in src_dir into ws_dir, except those in exclude."""
    exclude = exclude or set()
    if not src_dir.is_dir():
        return
    for entry in sorted(src_dir.iterdir()):
        if entry.name in exclude:
            continue
        target = ws_dir / entry.name
        if not target.exists() and not target.is_symlink():
            target.symlink_to(entry)


def _rebuild_as_split_dir(
    workspace: Path,
    source: Path,
    repo_path: str,
    all_repos: list[str],
    meta,
) -> None:
    """
    Rebuild a demoted repo path as a real directory with symlinks,
    preserving paths needed for child worktrees.
    """
    ws_dir = workspace / repo_path
    src_dir = source / repo_path

    ws_dir.mkdir(parents=True, exist_ok=True)

    # Find which child paths need to remain as real directories
    child_wt_paths = {w.path for w in meta.worktrees
                      if w.path.startswith(repo_path + "/")}

    # Get the immediate next path components needed for child worktrees
    needed_subdirs = set()
    for cwp in child_wt_paths:
        relative = cwp[len(repo_path) + 1:]  # strip "repo_path/"
        first_part = relative.split("/")[0]
        needed_subdirs.add(first_part)

    # Symlink everything except needed subdirs
    _symlink_dir_contents(src_dir, ws_dir, exclude=needed_subdirs | {".git"})

    # Recursively handle needed subdirs
    for subdir in needed_subdirs:
        sub_repo_path = f"{repo_path}/{subdir}"
        sub_ws = ws_dir / subdir
        sub_src = src_dir / subdir

        # Check if any child worktree is directly this subdir
        direct_child = any(w.path == sub_repo_path for w in meta.worktrees
                           if w.path in child_wt_paths)
        # Check if deeper children exist
        deeper_children = any(w.path.startswith(sub_repo_path + "/")
                              for w in meta.worktrees if w.path in child_wt_paths)

        if direct_child:
            # Will be restored as worktree by caller, leave space
            pass
        elif deeper_children:
            _rebuild_as_split_dir(workspace, source, sub_repo_path, all_repos, meta)
        else:
            sub_ws.symlink_to(sub_src)


def _try_collapse_upward(
    workspace: Path,
    source: Path,
    repo_path: str,
    all_repos: list[str],
    meta,
) -> None:
    """
    After demoting a repo, check if parent directories can be collapsed
    back into symlinks (when no worktrees remain in the subtree).
    """
    remaining_wt_paths = {w.path for w in meta.worktrees if w.path != repo_path}

    parts = repo_path.split("/")
    # Walk from deepest parent up to top
    for i in range(len(parts) - 1, 0, -1):
        parent_path = "/".join(parts[:i])
        parent_ws = workspace / parent_path
        parent_src = source / parent_path

        # Check if any remaining worktree is under this parent
        has_wt_below = any(wt.startswith(parent_path + "/") for wt in remaining_wt_paths)

        if has_wt_below:
            break  # Can't collapse this or any higher parent

        if parent_ws.is_dir() and not parent_ws.is_symlink():
            # Safe to collapse: remove dir and replace with symlink
            shutil.rmtree(parent_ws)
            parent_ws.symlink_to(parent_src)
