"""
Repo Scanner — parse sub-repo list from .repo/project.list and build a prefix trie.

The trie supports efficient queries like "does this path's subtree contain
any worktree repos?" which is needed by the Layout Engine to decide whether
to symlink a directory or recursively expand it.
"""

from __future__ import annotations

from pathlib import Path


class TrieNode:
    """A node in the repo path prefix trie."""

    __slots__ = (
        "name",
        "children",
        "is_repo",
        "is_worktree",
        "_has_worktree_descendant",
    )

    def __init__(self, name: str):
        self.name = name
        self.children: dict[str, TrieNode] = {}
        self.is_repo = False
        self.is_worktree = False
        self._has_worktree_descendant: bool | None = None  # cached

    @property
    def has_worktree_descendant(self) -> bool:
        """Check if any descendant (not self) is a worktree."""
        if self._has_worktree_descendant is None:
            self._has_worktree_descendant = any(
                child.is_worktree or child.has_worktree_descendant
                for child in self.children.values()
            )
        return self._has_worktree_descendant

    def invalidate_cache(self):
        """Invalidate the cached has_worktree_descendant value (and ancestors)."""
        self._has_worktree_descendant = None

    def get_child(self, name: str) -> TrieNode | None:
        return self.children.get(name)

    def __repr__(self):
        flags = []
        if self.is_repo:
            flags.append("repo")
        if self.is_worktree:
            flags.append("wt")
        if self.has_worktree_descendant:
            flags.append("wt_desc")
        return f"TrieNode({self.name!r}, {','.join(flags)})"


class RepoTrie:
    """Prefix trie of all repo paths, with worktree marking."""

    def __init__(self):
        self.root = TrieNode("")

    def add_repo(self, path: str):
        """Add a repo path to the trie."""
        node = self.root
        for part in path.split("/"):
            if part not in node.children:
                node.children[part] = TrieNode(part)
            node = node.children[part]
        node.is_repo = True

    def mark_worktree(self, path: str):
        """Mark a repo path as a worktree. Invalidates ancestor caches."""
        node = self.root
        ancestors = [node]
        for part in path.split("/"):
            node = node.children.get(part)
            if node is None:
                raise ValueError(f"Path not in trie: {path}")
            ancestors.append(node)
        if not node.is_repo:
            raise ValueError(f"Path is not a repo: {path}")
        node.is_worktree = True
        # Invalidate ancestor caches
        for ancestor in ancestors:
            ancestor.invalidate_cache()

    def lookup(self, path: str) -> TrieNode | None:
        """Look up a node by path. Returns None if not found."""
        node = self.root
        for part in path.split("/"):
            node = node.children.get(part)
            if node is None:
                return None
        return node

    @property
    def top_level_children(self) -> list[TrieNode]:
        """Get the root's direct children (top-level directories)."""
        return list(self.root.children.values())


def scan_repos(source_dir: Path) -> list[str]:
    """
    Parse .repo/project.list to get all sub-repo checkout paths.

    Returns sorted list of paths.
    Raises FileNotFoundError if not a repo-managed directory.
    """
    project_list = source_dir / ".repo" / "project.list"
    if not project_list.exists():
        raise FileNotFoundError(
            f"Not a repo-managed directory: {source_dir}\n"
            f"Expected {project_list} to exist."
        )
    lines = project_list.read_text().strip().splitlines()
    paths = sorted(line.strip() for line in lines if line.strip())
    return paths


def build_trie(
    repo_paths: list[str], worktree_paths: set[str] | None = None
) -> RepoTrie:
    """
    Build a prefix trie from repo paths, optionally marking some as worktrees.

    Args:
        repo_paths: All sub-repo checkout paths.
        worktree_paths: Subset of repo_paths to mark as worktrees.

    Returns:
        RepoTrie with all paths added and worktrees marked.
    """
    trie = RepoTrie()
    for path in repo_paths:
        trie.add_repo(path)
    if worktree_paths:
        for path in worktree_paths:
            trie.mark_worktree(path)
    return trie
