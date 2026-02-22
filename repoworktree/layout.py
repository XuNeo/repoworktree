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

import subprocess

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
    inside_worktree: bool = False,
) -> None:
    """
    Recursively build one level of the directory tree.

    For each child of trie_node:
    - If it's a worktree repo → git worktree add
      - If it also has worktree descendants → recurse into children for nested worktrees
    - If it has worktree descendants → mkdir + recurse
    - Otherwise → symlink

    Args:
        inside_worktree: True when recursing inside a parent worktree checkout.
            Controls whether intermediate dirs are created for child repo paths.
    """
    for name, child in trie_node.children.items():
        child_source = source / name
        child_workspace = workspace / name

        if child.is_repo and child.is_worktree:
            # Create git worktree for this repo
            rel_path = str(child_workspace.relative_to(workspace_root))
            pin = pin_map.get(rel_path)
            git_worktree_add(child_source, child_workspace, branch=branch, pin_version=pin)

            # If this repo has child repos in the trie, recurse to handle them.
            # Child repos that are worktrees get created on top;
            # child repos that aren't worktrees get symlinked on top.
            if child.children:
                _build_level(
                    child_source, child_workspace, child,
                    source_root, workspace_root, branch, pin_map,
                    inside_worktree=True,
                )
                # Exclude non-worktree child repo paths from git status
                # so symlinked child repos don't appear as dirty
                _exclude_child_repos(child_workspace, child)

        elif child.has_worktree_descendant:
            # Intermediate directory leading to a worktree: create real dir and recurse
            child_workspace.mkdir(parents=True, exist_ok=True)

            # Symlink all source entries that aren't handled by trie children
            _symlink_non_trie_entries(child_source, child_workspace, child)

            # Recurse into trie children
            _build_level(
                child_source, child_workspace, child,
                source_root, workspace_root, branch, pin_map,
                inside_worktree=inside_worktree,
            )

        elif inside_worktree:
            # We're inside a parent worktree checkout. Child repos in the trie
            # need to be symlinked on top, and intermediate dirs may need creation.
            if child.is_repo and child_workspace.exists() and not child_workspace.is_symlink():
                # Child repo dir exists from parent worktree checkout —
                # replace with symlink to source so it has the correct content.
                import shutil
                shutil.rmtree(child_workspace)
                child_workspace.symlink_to(child_source)
            elif child.is_repo and not child_workspace.exists():
                # Child repo doesn't exist in parent worktree — symlink it.
                child_workspace.symlink_to(child_source)
            elif child.children and child_workspace.is_dir() and not child_workspace.is_symlink():
                # Intermediate dir exists from parent worktree. Recurse.
                _build_level(
                    child_source, child_workspace, child,
                    source_root, workspace_root, branch, pin_map,
                    inside_worktree=True,
                )
            elif child.children and not child_workspace.exists():
                # Intermediate dir doesn't exist in parent worktree. Create and recurse.
                child_workspace.mkdir(parents=True, exist_ok=True)
                _build_level(
                    child_source, child_workspace, child,
                    source_root, workspace_root, branch, pin_map,
                    inside_worktree=True,
                )
            elif child_source.exists() and not child_workspace.exists():
                child_workspace.symlink_to(child_source)

        else:
            # Top-level (not inside a worktree): just symlink.
            if child_source.exists() and not child_workspace.exists():
                child_workspace.symlink_to(child_source)


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


def _exclude_child_repos(worktree_path: Path, trie_node: TrieNode) -> None:
    """
    Hide non-worktree child repo paths from git in a parent worktree.

    Uses skip-worktree flag for tracked files that get replaced by symlinks,
    and .gitignore for untracked intermediate dirs we create (info/exclude
    is not reliably read for worktrees by all git versions).
    """
    excludes = []
    _collect_non_worktree_repo_paths(trie_node, "", excludes)
    if not excludes:
        return

    # Mark tracked files as skip-worktree so git ignores content changes.
    # Batch all paths into a single git ls-files call, then a single update-index.
    all_tracked = []
    for path in excludes:
        result = subprocess.run(
            ["git", "ls-files", "--", path],
            cwd=worktree_path, check=False, capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            all_tracked.extend(result.stdout.strip().splitlines())

    if all_tracked:
        # Batch update-index via --stdin
        input_data = "\n".join(all_tracked) + "\n"
        subprocess.run(
            ["git", "update-index", "--skip-worktree", "--stdin"],
            cwd=worktree_path, check=False, capture_output=True,
            input=input_data, text=True,
        )

    # Write .gitignore for untracked intermediate dirs/symlinks
    gitignore = worktree_path / ".gitignore"
    lines = []
    if gitignore.exists():
        lines = gitignore.read_text().splitlines()
    lines.append("# Child repos managed by repoworktree")
    for path in excludes:
        pattern = f"/{path}"
        if pattern not in lines:
            lines.append(pattern)
    if "/.gitignore" not in lines:
        lines.append("/.gitignore")
    gitignore.write_text("\n".join(lines) + "\n")


def _collect_non_worktree_repo_paths(node: TrieNode, prefix: str, result: list[str]) -> None:
    """Recursively collect relative paths of non-worktree child repos and their intermediates."""
    for name, child in node.children.items():
        child_path = f"{prefix}/{name}" if prefix else name
        if child.is_repo and not child.is_worktree:
            # Add the repo path itself
            result.append(child_path)
            # Also add all intermediate path components that lead to it
            # e.g. for "fs/fatfs", also add "fs"
            parts = child_path.split("/")
            for i in range(1, len(parts)):
                intermediate = "/".join(parts[:i])
                if intermediate not in result:
                    result.append(intermediate)
        # Recurse into intermediate nodes
        if child.children:
            _collect_non_worktree_repo_paths(child, child_path, result)


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
