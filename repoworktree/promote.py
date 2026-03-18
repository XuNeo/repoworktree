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
from repoworktree.layout import (
    _exclude_child_repos,
    _setup_sparse_checkout,
    _disable_sparse_checkout,
)


def _has_own_changes(worktree_path: Path, repo_path: str, child_wts: list) -> bool:
    """
    Check if a worktree has its own uncommitted changes,
    excluding paths that belong to child worktrees.
    """
    from repoworktree.worktree import _git

    result = _git(["status", "--porcelain"], cwd=worktree_path)
    if not result.stdout.strip():
        return False

    child_prefixes = []
    for cw in child_wts:
        if cw.path.startswith(repo_path + "/"):
            child_prefixes.append(cw.path[len(repo_path) + 1 :])
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
            filepath.startswith(prefix + "/")
            or filepath == prefix
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


def _find_parent_worktree(workspace: Path, repo_path: str, meta) -> Path | None:
    parts = repo_path.split("/")
    for i in range(len(parts) - 1, 0, -1):
        ancestor = "/".join(parts[:i])
        if meta.find_worktree(ancestor):
            return workspace / ancestor
    return None


def _dir_has_changes(worktree_root: Path, rel_path: str) -> bool:
    from repoworktree.worktree import _git

    result = _git(["status", "--porcelain", "--", rel_path], cwd=worktree_root)
    return bool(result.stdout.strip())


def promote(
    workspace: Path,
    source: Path,
    repo_path: str,
    all_repos: list[str],
    branch: str | None = None,
    pin_version: str | None = None,
    force: bool = False,
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
    child_wts = [w for w in meta.worktrees if w.path.startswith(repo_path + "/")]
    child_info = []
    for cw in child_wts:
        child_ws_path = workspace / cw.path
        child_src_path = source / cw.path
        if child_ws_path.exists() and (child_ws_path / ".git").is_file():
            child_info.append(cw)
            try:
                git_worktree_remove(child_src_path, child_ws_path, force=True)
            except Exception:
                pass

    # Split symlinks along the path to the target
    _ensure_path_is_real(workspace, source, repo_path, all_repos)

    # Now target_ws should be either a symlink or a directory
    # If it's a symlink, remove it
    backup = None
    if target_ws.is_symlink():
        target_ws.unlink()
    elif target_ws.is_dir():
        if (target_ws / ".git").is_file():
            raise PromoteError(f"Already a worktree: {repo_path}")
        if not force:
            parent_wt = _find_parent_worktree(workspace, repo_path, meta)
            if parent_wt is not None:
                rel = str(target_ws.relative_to(parent_wt))
                if _dir_has_changes(parent_wt, rel):
                    raise DirtyWorktreeError(
                        f"Directory has uncommitted changes: {repo_path}\n"
                        f"Use force=True or commit/stash changes first."
                    )
        backup = target_ws.parent / f"{target_ws.name}.rwt-backup"
        if backup.exists():
            shutil.rmtree(backup)
        shutil.copytree(target_ws, backup, symlinks=True)
        shutil.rmtree(target_ws)

    try:
        git_worktree_add(target_src, target_ws, branch=branch, pin_version=pin_version)
    except Exception:
        if backup is not None and backup.exists():
            shutil.move(str(backup), str(target_ws))
        raise

    if backup is not None and backup.exists():
        shutil.rmtree(backup)

    # Restore child worktrees on top
    for cw in child_info:
        child_ws_path = workspace / cw.path
        child_src_path = source / cw.path
        # The parent worktree may have created the directory already; remove it
        if child_ws_path.exists() and not (child_ws_path / ".git").is_file():
            shutil.rmtree(child_ws_path)
        elif child_ws_path.is_symlink():
            child_ws_path.unlink()
        git_worktree_add(
            child_src_path, child_ws_path, branch=cw.branch, pin_version=cw.pinned
        )

    # Handle non-worktree child repos: symlink on top and exclude from git
    _handle_non_worktree_child_repos(
        workspace,
        source,
        repo_path,
        all_repos,
        meta,
        target_ws,
        force=force,
    )

    # Update metadata
    meta.add_worktree(repo_path, branch=branch, pinned=pin_version)
    save_workspace_metadata(workspace, meta)

    # If this repo lives inside a parent worktree, refresh parent's exclude list
    _refresh_ancestor_excludes(workspace, source, repo_path, all_repos, meta)


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
    child_wts = [
        w
        for w in meta.worktrees
        if w.path != repo_path and w.path.startswith(repo_path + "/")
    ]

    # Check for dirty state: parent own changes + child worktrees
    if not force:
        if _has_own_changes(target_ws, repo_path, child_wts):
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
            git_worktree_add(
                child_src_path, child_ws_path, branch=cw.branch, pin_version=cw.pinned
            )
    else:
        target_ws.symlink_to(target_src)

    # Update metadata
    meta.remove_worktree(repo_path)
    save_workspace_metadata(workspace, meta)

    # If this repo lived inside a parent worktree, refresh parent's exclude list
    # (the demoted repo is no longer a worktree so must be added back to excludes)
    _refresh_ancestor_excludes(workspace, source, repo_path, all_repos, meta)


def _handle_non_worktree_child_repos(
    workspace: Path,
    source: Path,
    repo_path: str,
    all_repos: list[str],
    meta,
    worktree_path: Path,
    force: bool = False,
) -> None:
    """
    After creating a parent worktree, handle child repos that are NOT worktrees:
    replace their checkout dirs with symlinks to source and exclude from git.
    """
    worktree_set = {w.path for w in meta.worktrees}
    child_repos = [
        r for r in all_repos if r.startswith(repo_path + "/") and r not in worktree_set
    ]
    if not child_repos:
        return

    trie = build_trie(all_repos)
    parent_node = trie.lookup(repo_path)
    if parent_node and parent_node.children:
        for w in meta.worktrees:
            if w.path.startswith(repo_path + "/"):
                wt_node = trie.lookup(w.path)
                if wt_node:
                    wt_node.is_worktree = True
        _exclude_child_repos(worktree_path, parent_node)

    for child_repo in child_repos:
        rel = child_repo[len(repo_path) + 1 :]
        child_ws = worktree_path / rel
        child_src = source / child_repo

        child_ws.parent.mkdir(parents=True, exist_ok=True)

        if child_ws.is_symlink():
            pass
        elif child_ws.is_dir():
            parent_wt = _find_parent_worktree(workspace, repo_path, meta)
            if parent_wt is not None and not force:
                rel = str(child_ws.relative_to(parent_wt))
                if _dir_has_changes(parent_wt, rel):
                    raise DirtyWorktreeError(
                        f"Directory has uncommitted changes: {child_repo}\n"
                        f"Use force=True or commit/stash changes first."
                    )
            shutil.rmtree(child_ws)
            child_ws.symlink_to(child_src)
        elif not child_ws.exists():
            child_ws.symlink_to(child_src)


def _refresh_ancestor_excludes(
    workspace: Path,
    source: Path,
    repo_path: str,
    all_repos: list[str],
    meta,
) -> None:
    """Re-generate sparse-checkout and .gitignore for any ancestor worktree of repo_path."""
    worktree_set = {w.path for w in meta.worktrees}
    parts = repo_path.split("/")
    for i in range(len(parts) - 1, 0, -1):
        ancestor = "/".join(parts[:i])
        if ancestor in worktree_set:
            ancestor_ws = workspace / ancestor
            if not ancestor_ws.is_dir() or not (ancestor_ws / ".git").is_file():
                continue
            trie = build_trie(all_repos)
            for w_path in worktree_set:
                if w_path.startswith(ancestor + "/"):
                    node = trie.lookup(w_path)
                    if node:
                        node.is_worktree = True
            parent_node = trie.lookup(ancestor)
            if parent_node and parent_node.children:
                _rewrite_exclude(ancestor_ws, parent_node)
            break


def _rewrite_exclude(worktree_path: Path, trie_node) -> None:
    """Rebuild sparse-checkout rules and .gitignore for a worktree."""
    child_repo_paths: list[str] = []
    intermediate_paths: list[str] = []
    from repoworktree.layout import _collect_non_worktree_repo_paths

    _collect_non_worktree_repo_paths(
        trie_node, "", child_repo_paths, intermediate_paths
    )

    if child_repo_paths:
        _setup_sparse_checkout(worktree_path, child_repo_paths)
    else:
        _disable_sparse_checkout(worktree_path)

    all_exclude_paths = child_repo_paths + intermediate_paths

    gitignore = worktree_path / ".gitignore"
    if all_exclude_paths:
        lines = []
        for path in all_exclude_paths:
            lines.append(f"/{path}")
        lines.append("/.gitignore")
        gitignore.write_text("\n".join(lines) + "\n")
    elif gitignore.exists():
        gitignore.unlink()


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
        partial = "/".join(parts[: i + 1])
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
    child_wt_paths = {
        w.path for w in meta.worktrees if w.path.startswith(repo_path + "/")
    }

    # Get the immediate next path components needed for child worktrees
    needed_subdirs = set()
    for cwp in child_wt_paths:
        relative = cwp[len(repo_path) + 1 :]  # strip "repo_path/"
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
        direct_child = any(
            w.path == sub_repo_path for w in meta.worktrees if w.path in child_wt_paths
        )
        # Check if deeper children exist
        deeper_children = any(
            w.path.startswith(sub_repo_path + "/")
            for w in meta.worktrees
            if w.path in child_wt_paths
        )

        if direct_child:
            # Will be restored as worktree by caller, leave space
            pass
        elif deeper_children:
            _rebuild_as_split_dir(workspace, source, sub_repo_path, all_repos, meta)
        else:
            sub_ws.symlink_to(sub_src)
