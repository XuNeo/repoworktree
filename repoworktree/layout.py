"""
Layout Engine — build workspace directory tree with symlink + worktree mix.

Core algorithm: recursively traverse the repo prefix trie, creating:
- git worktree for repos marked as worktree
- real directories for intermediate paths leading to worktrees
- symlinks for everything else
"""

from __future__ import annotations

import os
from pathlib import Path

from repoworktree.scanner import RepoTrie, TrieNode
from repoworktree.worktree import add_worktree as git_worktree_add


# File extensions to ignore when symlinking top-level files
IGNORED_EXTENSIONS = {
    ".patch", ".elf", ".zip", ".img", ".deb", ".png", ".jpg", ".json",
    ".html", ".log", ".txt",
}

# Specific filenames to always ignore
IGNORED_FILES = {
    ".workspaces.json",
    ".workspace.json",
}

# Known project config files to symlink
CONFIG_FILES = {
    "CLAUDE.md",
    ".vela_makefile_fixed_config",
}


def build_workspace(
    source: Path,
    workspace: Path,
    trie: RepoTrie,
    branch: str | None = None,
    pin_map: dict[str, str] | None = None,
) -> None:
    """
    Build the workspace directory tree.

    Args:
        source: Path to the main repo checkout.
        workspace: Path to the workspace being created.
        trie: RepoTrie with worktree repos marked.
        branch: Optional branch name for all worktrees.
        pin_map: Optional {repo_path: version} for pinned worktrees.
    """
    pin_map = pin_map or {}
    workspace.mkdir(parents=True, exist_ok=True)

    # Process sub-repo tree
    _build_level(source, workspace, trie.root, source, workspace, branch, pin_map)

    # Process top-level files
    _process_top_level_files(source, workspace)


def _build_level(
    source: Path,
    workspace: Path,
    trie_node: TrieNode,
    source_root: Path,
    workspace_root: Path,
    branch: str | None,
    pin_map: dict[str, str],
) -> None:
    """
    Recursively build one level of the directory tree.

    For each child of trie_node:
    - If it's a worktree repo → git worktree add
      - If it also has worktree descendants → recurse into children for nested worktrees
    - If it has worktree descendants → mkdir + recurse
    - Otherwise → symlink
    """
    for name, child in trie_node.children.items():
        child_source = source / name
        child_workspace = workspace / name

        if child.is_repo and child.is_worktree:
            # Create git worktree for this repo
            rel_path = str(child_workspace.relative_to(workspace_root))
            pin = pin_map.get(rel_path)
            git_worktree_add(child_source, child_workspace, branch=branch, pin_version=pin)

            # If this repo also has worktree descendants (parent-child case),
            # recurse to create child worktrees on top
            if child.has_worktree_descendant:
                _build_level(
                    child_source, child_workspace, child,
                    source_root, workspace_root, branch, pin_map,
                )

        elif child.has_worktree_descendant or (child.is_worktree):
            # Intermediate directory: create real dir and recurse
            child_workspace.mkdir(parents=True, exist_ok=True)

            # Symlink all source entries that aren't handled by trie children
            _symlink_non_trie_entries(child_source, child_workspace, child)

            # Recurse into trie children
            _build_level(
                child_source, child_workspace, child,
                source_root, workspace_root, branch, pin_map,
            )

        else:
            # No worktree in this subtree: symlink the whole thing
            if child_source.exists():
                child_workspace.symlink_to(child_source)
            # If it's a repo node without worktree and no descendants,
            # and the source doesn't exist as a standalone dir (shouldn't happen),
            # just skip


def _symlink_non_trie_entries(source_dir: Path, workspace_dir: Path, trie_node: TrieNode) -> None:
    """
    For a real directory in the workspace, symlink all source entries
    that are NOT represented as children in the trie.

    This handles files and directories that exist in the source but
    aren't sub-repos tracked by the trie.
    """
    if not source_dir.is_dir():
        return

    trie_child_names = set(trie_node.children.keys())

    for entry in sorted(source_dir.iterdir()):
        if entry.name in trie_child_names:
            continue  # Will be handled by trie traversal
        if entry.name == ".git":
            continue  # Never symlink .git

        target = workspace_dir / entry.name
        if not target.exists() and not target.is_symlink():
            target.symlink_to(entry)


def _process_top_level_files(source: Path, workspace: Path) -> None:
    """
    Handle top-level files in the source directory.

    - Symlink files that are themselves symlinks (rebuild relative symlink)
    - Symlink known config files
    - Ignore temporary/user files
    """
    for entry in sorted(source.iterdir()):
        if not entry.is_file() and not entry.is_symlink():
            continue  # Skip directories (handled by trie)
        if entry.is_dir():
            continue  # Skip directories even if they're also symlinks

        name = entry.name
        target = workspace / name

        if target.exists() or target.is_symlink():
            continue  # Already handled

        if name in IGNORED_FILES:
            continue

        if entry.is_symlink():
            # Rebuild the same relative symlink
            link_target = os.readlink(entry)
            target.symlink_to(link_target)
        elif name in CONFIG_FILES:
            # Symlink config files to source
            target.symlink_to(entry)
        elif entry.suffix not in IGNORED_EXTENSIONS:
            # Unknown file type — symlink to be safe
            target.symlink_to(entry)


def teardown_workspace(source: Path, workspace: Path, trie: RepoTrie) -> None:
    """
    Remove all git worktrees created for a workspace, then delete the workspace dir.

    Must remove worktrees via git before deleting files, otherwise
    git's worktree tracking gets corrupted.
    """
    import shutil
    from repoworktree.worktree import remove_worktree

    # Collect worktree paths (deepest first to handle parent-child)
    worktree_paths = []
    _collect_worktrees(workspace, trie.root, workspace, worktree_paths)
    worktree_paths.sort(key=lambda p: len(p[1].parts), reverse=True)

    for source_repo, wt_path in worktree_paths:
        try:
            remove_worktree(source_repo, wt_path, force=True)
        except Exception:
            pass  # Best effort

    if workspace.exists():
        shutil.rmtree(workspace)


def _collect_worktrees(
    workspace: Path,
    trie_node: TrieNode,
    workspace_root: Path,
    result: list[tuple[Path, Path]],
) -> None:
    """Recursively collect (source_repo, worktree_path) pairs."""
    # We need to find the source from workspace metadata, but for teardown
    # we can read .workspace.json. For now, find worktrees by checking .git files.
    for name, child in trie_node.children.items():
        child_ws = workspace / name
        if child.is_repo and child.is_worktree and child_ws.exists():
            # Find the source repo from the .git file
            git_file = child_ws / ".git"
            if git_file.is_file():
                content = git_file.read_text().strip()
                if content.startswith("gitdir:"):
                    gitdir = content[len("gitdir:"):].strip()
                    # The gitdir points to .git/worktrees/<name>/
                    # The source repo is the parent of .git/
                    gitdir_path = Path(gitdir)
                    if not gitdir_path.is_absolute():
                        gitdir_path = (child_ws / gitdir_path).resolve()
                    # Go up from worktrees/<name>/ to .git/ to repo/
                    source_repo = gitdir_path.parent.parent.parent
                    result.append((source_repo, child_ws))

        if child.has_worktree_descendant:
            _collect_worktrees(child_ws, child, workspace_root, result)
