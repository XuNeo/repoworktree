# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

`repoworktree` (CLI: `rwt`) creates isolated workspaces for Google `repo`-managed multi-repository projects using git worktree + symlink. Each workspace is a mix of symlinks (read-only, zero overhead) and git worktrees (writable, isolated). The ratio is adjustable per-repo via `promote`/`demote`.

## Build & Test

```bash
# Install in development mode
pip install -e ".[test]"

# Run all tests (requires `repo` tool)
pytest tests/ -v

# Single test file
pytest tests/test_promote.py -v

# Single test with output
pytest tests/test_integration.py::test_scenario_05_promote_parent -xvs
```

Tests use a session-scoped `repo_env` fixture (creates 13 bare repos + `repo init` + `repo sync`) and per-test `workspace_dir` fixture, both in `tests/conftest.py`. Test helpers in `tests/helpers.py`.

## Architecture

Data flow: `scanner.py` (parse `.repo/project.list` → prefix trie) → `layout.py` (trie → directory tree) → `metadata.py` (persist to `.workspace.json`)

CLI entry point: `__main__.py` defines all `cmd_*` handlers and the argparse tree. The `rwt` console script maps to `__main__:main`.

Key design patterns:
- **Prefix trie** (`scanner.py`): Each node has `is_repo`, `is_worktree`, `has_worktree_descendant` — drives whether a path becomes symlink, real dir, or worktree. `has_worktree_descendant` is lazily cached and must be invalidated via `invalidate_cache()` when marking worktrees.
- **Nested repo handling** (`promote.py`): Parent-child repo pairs (e.g. `apps/` and `apps/system/adb/`) require temporarily removing child worktrees before operating on parent, then restoring. This remove-operate-restore pattern is used in both `promote()` and `demote()`.
- **Child repo under parent worktree** (`layout.py`): When a parent repo is a worktree, child repos (separate git repos) are NOT included in the git checkout. `_build_level` uses `inside_worktree` flag to create intermediate dirs and symlink child repos on top. `_exclude_child_repos` hides these from git via `skip-worktree` (tracked files) and `.gitignore` (untracked dirs). Note: `info/exclude` doesn't work reliably for worktrees.
- **Symlink splitting** (`promote.py:_ensure_path_is_real`): Promoting a deep repo like `frameworks/system/core` recursively splits parent symlinks into real directories with symlinked siblings.
- **Upward collapse** (`promote.py:_try_collapse_upward`): Demoting merges empty parent directories back into symlinks, walking from deepest parent up to top.
- **Dual metadata files**: `.workspace.json` (per-workspace config) and `.workspaces.json` (source-root index of all workspaces). Both in `metadata.py`.
- **Atomic create**: `cmd_create` builds into a `.tmp` directory then renames, with rollback on failure.

## Code Conventions

- Python 3.10+, stdlib only (no third-party dependencies)
- Type hints: `X | None` (not `Optional[X]`)
- All git operations go through `worktree.py:_git()` helper
- Metadata mutations must call `save_workspace_metadata()` after modification
- Worktree teardown order: deepest paths first (sorted by path depth descending) to handle parent-child correctly

## Git Safety

- NEVER use `git push --force` or `git push -f`. If a push is rejected, inform the user and let them decide how to proceed.
