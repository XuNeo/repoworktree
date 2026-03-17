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
    ".patch",
    ".elf",
    ".zip",
    ".img",
    ".deb",
    ".png",
    ".jpg",
    ".json",
    ".html",
    ".log",
    ".txt",
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
    checkout: str | None = None,
) -> None:
    """
    Build the workspace directory tree.

    Args:
        source: Path to the main repo checkout.
        workspace: Path to the workspace being created.
        trie: RepoTrie with worktree repos marked.
        branch: Optional branch name for all worktrees.
        pin_map: Optional {repo_path: version} for pinned worktrees.
        checkout: Optional branch or tag to check out for all worktrees.
            Acts as a default pin_version; explicit pin_map entries take precedence.
            Unlike pin_map, this does not mark repos as pinned in metadata.
    """
    pin_map = pin_map or {}
    workspace.mkdir(parents=True, exist_ok=True)

    # Process sub-repo tree
    _build_level(
        source,
        workspace,
        trie.root,
        source,
        workspace,
        branch,
        pin_map,
        checkout=checkout,
    )

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
    checkout: str | None = None,
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
        checkout: Default ref to check out for all worktrees; explicit pin_map entries
            take precedence.
    """
    for name, child in trie_node.children.items():
        child_source = source / name
        child_workspace = workspace / name

        if child.is_repo and child.is_worktree:
            # Create git worktree for this repo
            rel_path = str(child_workspace.relative_to(workspace_root))
            if not child_source.exists():
                import sys

                print(
                    f"  Warning: skipping {rel_path} (not present in source checkout)",
                    file=sys.stderr,
                )
                continue
            pin = pin_map.get(rel_path) or checkout
            git_worktree_add(
                child_source, child_workspace, branch=branch, pin_version=pin
            )

            # If this repo has child repos in the trie, recurse to handle them.
            # Child repos that are worktrees get created on top;
            # child repos that aren't worktrees get symlinked on top.
            if child.children:
                _build_level(
                    child_source,
                    child_workspace,
                    child,
                    source_root,
                    workspace_root,
                    branch,
                    pin_map,
                    inside_worktree=True,
                    checkout=checkout,
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
                child_source,
                child_workspace,
                child,
                source_root,
                workspace_root,
                branch,
                pin_map,
                inside_worktree=inside_worktree,
                checkout=checkout,
            )

        elif inside_worktree:
            # We're inside a parent worktree checkout. Child repos in the trie
            # need to be symlinked on top, and intermediate dirs may need creation.
            if (
                child.is_repo
                and child_workspace.exists()
                and not child_workspace.is_symlink()
            ):
                # Child repo dir exists from parent worktree checkout —
                # replace with symlink to source so it has the correct content.
                import shutil

                shutil.rmtree(child_workspace)
                child_workspace.symlink_to(child_source)
            elif child.is_repo and not child_workspace.exists():
                # Child repo doesn't exist in parent worktree — symlink it.
                child_workspace.symlink_to(child_source)
            elif (
                child.children
                and child_workspace.is_dir()
                and not child_workspace.is_symlink()
            ):
                # Intermediate dir exists from parent worktree. Recurse.
                _build_level(
                    child_source,
                    child_workspace,
                    child,
                    source_root,
                    workspace_root,
                    branch,
                    pin_map,
                    inside_worktree=True,
                    checkout=checkout,
                )
            elif child.children and not child_workspace.exists():
                # Intermediate dir doesn't exist in parent worktree. Create and recurse.
                child_workspace.mkdir(parents=True, exist_ok=True)
                _build_level(
                    child_source,
                    child_workspace,
                    child,
                    source_root,
                    workspace_root,
                    branch,
                    pin_map,
                    inside_worktree=True,
                    checkout=checkout,
                )
            elif child_source.exists() and not child_workspace.exists():
                child_workspace.symlink_to(child_source)

        else:
            # Top-level (not inside a worktree): just symlink.
            if child_source.exists() and not child_workspace.exists():
                child_workspace.symlink_to(child_source)


def _symlink_non_trie_entries(
    source_dir: Path, workspace_dir: Path, trie_node: TrieNode
) -> None:
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
    child_repo_paths: list[str] = []
    intermediate_paths: list[str] = []
    _collect_non_worktree_repo_paths(
        trie_node, "", child_repo_paths, intermediate_paths
    )
    if not child_repo_paths and not intermediate_paths:
        return

    all_exclude_paths = child_repo_paths + intermediate_paths

    if child_repo_paths:
        result = subprocess.run(
            ["git", "ls-files", "--"] + child_repo_paths,
            cwd=worktree_path,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            subprocess.run(
                ["git", "update-index", "--skip-worktree", "--stdin"],
                cwd=worktree_path,
                check=False,
                capture_output=True,
                input=result.stdout,
                text=True,
            )

    gitignore = worktree_path / ".gitignore"
    lines = []
    if gitignore.exists():
        lines = gitignore.read_text().splitlines()
    lines.append("# Child repos managed by repoworktree")
    for path in all_exclude_paths:
        pattern = f"/{path}"
        if pattern not in lines:
            lines.append(pattern)
    if "/.gitignore" not in lines:
        lines.append("/.gitignore")
    gitignore.write_text("\n".join(lines) + "\n")

    subprocess.run(
        ["git", "update-index", "--skip-worktree", "--", ".gitignore"],
        cwd=worktree_path,
        check=False,
        capture_output=True,
    )


def _collect_non_worktree_repo_paths(
    node: TrieNode,
    prefix: str,
    child_repos: list[str],
    intermediates: list[str],
) -> None:
    """Collect child repo paths and intermediate directory paths separately."""
    for name, child in node.children.items():
        child_path = f"{prefix}/{name}" if prefix else name
        if child.is_repo and not child.is_worktree:
            child_repos.append(child_path)
            parts = child_path.split("/")
            for i in range(1, len(parts)):
                intermediate = "/".join(parts[:i])
                if intermediate not in intermediates:
                    intermediates.append(intermediate)
        if child.children:
            _collect_non_worktree_repo_paths(
                child, child_path, child_repos, intermediates
            )


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
    import subprocess
    from repoworktree.worktree import remove_worktree

    # Discover worktrees that belong to this workspace from the source side.
    # This is more reliable than scanning the trie: it works even when
    # .workspace.json is incomplete or corrupted (BUG-003).
    worktrees_to_remove: list[tuple[Path, Path]] = []
    seen_source_repos: set[Path] = set()

    # Walk trie to find all source repos that could have worktrees
    source_repos_in_trie: list[Path] = []
    _collect_source_repos(trie.root, source, source_repos_in_trie)

    workspace_str = str(workspace.resolve())

    for source_repo in source_repos_in_trie:
        if not source_repo.exists():
            continue
        if source_repo in seen_source_repos:
            continue
        seen_source_repos.add(source_repo)
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=source_repo,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            continue
        current_path: str | None = None
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                current_path = line[len("worktree ") :]
            elif line == "" and current_path:
                if (
                    current_path.startswith(workspace_str + "/")
                    or current_path == workspace_str
                ):
                    worktrees_to_remove.append((source_repo, Path(current_path)))
                current_path = None
        if current_path and (
            current_path.startswith(workspace_str + "/")
            or current_path == workspace_str
        ):
            worktrees_to_remove.append((source_repo, Path(current_path)))

    # Sort deepest first (handles parent-child worktree ordering)
    worktrees_to_remove.sort(key=lambda p: len(p[1].parts), reverse=True)

    failed: list[tuple[Path, Exception]] = []
    for source_repo, wt_path in worktrees_to_remove:
        try:
            remove_worktree(source_repo, wt_path, force=True)
        except Exception as e:
            failed.append((wt_path, e))

    if failed:
        msgs = "\n".join(f"  {p}: {e}" for p, e in failed)
        import warnings

        warnings.warn(
            f"teardown_workspace: failed to remove {len(failed)} worktree(s):\n{msgs}\n"
            f"Workspace directory will NOT be deleted to avoid data loss. "
            f"Run 'rwt destroy --force' to override.",
            stacklevel=2,
        )
        return

    if workspace.exists():
        shutil.rmtree(workspace)


def _collect_source_repos(
    trie_node: TrieNode,
    source_root: Path,
    result: list[Path],
    prefix: str = "",
) -> None:
    for name, child in trie_node.children.items():
        child_path = f"{prefix}/{name}" if prefix else name
        if child.is_repo:
            result.append(source_root / child_path)
        if child.children:
            _collect_source_repos(child, source_root, result, child_path)
