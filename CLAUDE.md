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

Tests use a session-scoped `repo_env` fixture (creates 12 bare repos + `repo init` + `repo sync`) and per-test `workspace_dir` fixture, both in `tests/conftest.py`. Test helpers in `tests/helpers.py`.

## Architecture

Data flow: `scanner.py` (parse `.repo/project.list` → prefix trie) → `layout.py` (trie → directory tree) → `metadata.py` (persist to `.workspace.json`)

Key design patterns:
- **Prefix trie** (`scanner.py`): Each node has `is_repo`, `is_worktree`, `has_worktree_descendant` — drives whether a path becomes symlink, real dir, or worktree
- **Nested repo handling** (`promote.py`): Parent-child repo pairs (e.g. `apps/` and `apps/system/adb/`) require temporarily removing child worktrees before operating on parent, then restoring
- **Symlink splitting**: Promoting a deep repo like `frameworks/system/core` recursively splits parent symlinks into real directories with symlinked siblings
- **Upward collapse**: Demoting merges empty parent directories back into symlinks

## Code Conventions

- Python 3.10+, stdlib only (no third-party dependencies)
- Type hints: `X | None` (not `Optional[X]`)
- All git operations go through `worktree.py:_git()` helper
- Metadata mutations must call `save_workspace_metadata()` after modification

## Git Safety

- NEVER use `git push --force` or `git push -f`. If a push is rejected, inform the user and let them decide how to proceed.
