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
    _setup_worktree_excludes,
    _disable_worktree_excludes,
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
) -> list[str]:
    """Promote a sub-repo and all descendant sub-repos to git worktrees."""
    if repo_path not in all_repos:
        raise PromoteError(f"Not a valid sub-repo path: {repo_path}")

    targets = [r for r in all_repos if r == repo_path or r.startswith(repo_path + "/")]
    targets.sort(key=lambda p: (p.count("/"), p))

    promoted = []
    current = None
    try:
        for target in targets:
            meta = load_workspace_metadata(workspace)
            if meta.find_worktree(target):
                continue
            current = target
            _promote_one(
                workspace,
                source,
                target,
                all_repos,
                branch=branch,
                pin_version=pin_version if target == repo_path else None,
                force=force,
            )
            promoted.append(target)
            current = None
    except Exception:
        rollback_targets = promoted[:]
        if current is not None:
            rollback_targets.append(current)
        for target in sorted(rollback_targets, key=lambda p: (p.count("/"), p), reverse=True):
            _rollback_promote_target(workspace, source, target, all_repos)
        raise

    if not promoted:
        raise PromoteError(f"Already a worktree: {repo_path}")

    return promoted


def _worktree_depth_key(entry) -> tuple[int, str]:
    return (entry.path.count("/"), entry.path)


def _child_worktrees(meta, repo_path: str) -> list:
    return [w for w in meta.worktrees if w.path.startswith(repo_path + "/")]


def _existing_worktrees(workspace: Path, entries: list) -> list:
    return [
        entry
        for entry in entries
        if (workspace / entry.path).exists()
        and ((workspace / entry.path) / ".git").is_file()
    ]


def _remove_worktrees(workspace: Path, source: Path, entries: list) -> None:
    for entry in sorted(entries, key=_worktree_depth_key, reverse=True):
        entry_ws = workspace / entry.path
        if entry_ws.exists() and (entry_ws / ".git").is_file():
            try:
                git_worktree_remove(source / entry.path, entry_ws, force=True)
            except Exception:
                pass


def _restore_worktrees(workspace: Path, source: Path, entries: list) -> None:
    for entry in sorted(entries, key=_worktree_depth_key):
        entry_ws = workspace / entry.path
        if entry_ws.is_symlink():
            entry_ws.unlink()
        elif entry_ws.is_dir():
            shutil.rmtree(entry_ws)
        git_worktree_add(
            source / entry.path,
            entry_ws,
            branch=entry.branch,
            pin_version=entry.pinned,
            create_branch=False,
        )


def _restore_child_worktrees(
    workspace: Path,
    source: Path,
    repo_path: str,
    all_repos: list[str],
    meta,
) -> None:
    child_wts = _child_worktrees(meta, repo_path)
    if not child_wts:
        return

    _rebuild_as_split_dir(workspace, source, repo_path, all_repos, meta)
    _restore_worktrees(workspace, source, child_wts)


def _rollback_promote_target(
    workspace: Path,
    source: Path,
    repo_path: str,
    all_repos: list[str],
) -> None:
    meta = load_workspace_metadata(workspace)
    target_ws = workspace / repo_path
    target_src = source / repo_path
    child_wts = _child_worktrees(meta, repo_path)

    try:
        if meta.find_worktree(repo_path):
            demote(workspace, source, repo_path, all_repos, force=True)
        elif (target_ws / ".git").is_file():
            _remove_worktrees(workspace, source, child_wts)
            git_worktree_remove(target_src, target_ws, force=True)
            if child_wts:
                _restore_child_worktrees(workspace, source, repo_path, all_repos, meta)
            else:
                target_ws.symlink_to(target_src)
        elif child_wts:
            _restore_child_worktrees(workspace, source, repo_path, all_repos, meta)
    except Exception:
        pass


def _promote_one(
    workspace: Path,
    source: Path,
    repo_path: str,
    all_repos: list[str],
    branch: str | None = None,
    pin_version: str | None = None,
    force: bool = False,
) -> None:
    """
    Promote a single sub-repo from symlink/directory to git worktree.

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
    child_info = _existing_worktrees(workspace, _child_worktrees(meta, repo_path))
    _remove_worktrees(workspace, source, child_info)

    # Split symlinks along the path to the target
    _ensure_path_is_real(workspace, source, repo_path, all_repos)

    # Now target_ws should be either a symlink or a directory
    # If it's a symlink, remove it
    backup = None
    restore_symlink_on_add_failure = False
    symlink_target = target_src
    if target_ws.is_symlink():
        symlink_target = os.readlink(target_ws)
        restore_symlink_on_add_failure = True
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
    else:
        restore_symlink_on_add_failure = True

    try:
        git_worktree_add(target_src, target_ws, branch=branch, pin_version=pin_version)
    except Exception:
        if backup is not None and backup.exists():
            shutil.move(str(backup), str(target_ws))
        elif restore_symlink_on_add_failure:
            if (target_ws / ".git").is_file():
                git_worktree_remove(target_src, target_ws, force=True)
            elif target_ws.is_dir():
                shutil.rmtree(target_ws)
            elif target_ws.exists() or target_ws.is_symlink():
                target_ws.unlink()
            target_ws.symlink_to(symlink_target)
        raise

    if backup is not None and backup.exists():
        shutil.rmtree(backup)

    # Restore child worktrees on top
    _restore_worktrees(workspace, source, child_info)

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
    child_info = _existing_worktrees(workspace, child_wts)

    # Remove child worktrees temporarily
    _remove_worktrees(workspace, source, child_info)

    # Remove the main worktree
    # When child worktrees exist, has_local_changes() gives false positives
    # (child .git files appear as untracked). We already did a child-aware
    # dirty check above, so force removal is safe here.
    git_worktree_remove(target_src, target_ws, force=(force or bool(child_wts)))

    # Rebuild: if there are child worktrees, create directory structure
    if child_info:
        _rebuild_as_split_dir(workspace, source, repo_path, all_repos, meta)
        # Restore child worktrees
        _restore_worktrees(workspace, source, child_info)
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
    exclude from git first, then replace their checkout dirs with symlinks to source.
    """
    worktree_set = {w.path for w in meta.worktrees}
    descendants = [
        r
        for r in all_repos
        if r.startswith(repo_path + "/")
        and r not in worktree_set
        and not any(worktree.startswith(r + "/") for worktree in worktree_set)
    ]
    child_repos = []
    for descendant in sorted(descendants, key=lambda path: (path.count("/"), path)):
        if not any(descendant.startswith(child + "/") for child in child_repos):
            child_repos.append(descendant)
    if not child_repos:
        return

    # Exclude first (sparse-checkout removes files), then overlay symlinks
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
    """Re-generate excludes for any ancestor worktree of repo_path."""
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
    from repoworktree.layout import _collect_non_worktree_repo_paths

    child_repo_paths: list[str] = []
    intermediate_paths: list[str] = []
    _collect_non_worktree_repo_paths(
        trie_node, "", child_repo_paths, intermediate_paths
    )

    all_exclude_paths = child_repo_paths + intermediate_paths

    if child_repo_paths:
        _setup_sparse_checkout(worktree_path, child_repo_paths)
    else:
        _disable_sparse_checkout(worktree_path)

    if all_exclude_paths:
        _setup_worktree_excludes(worktree_path, all_exclude_paths)
    else:
        _disable_worktree_excludes(worktree_path)


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
