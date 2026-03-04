# repoworktree

[![PyPI](https://img.shields.io/pypi/v/repoworktree)](https://pypi.org/project/repoworktree/)
[![Python](https://img.shields.io/pypi/pyversions/repoworktree)](https://pypi.org/project/repoworktree/)

Create isolated workspaces for Google `repo`-managed multi-repository projects using git worktree + symlink. Supports a continuous spectrum from all-symlink (zero overhead, read-only) to all-worktree (fully isolated).

[中文文档](README_zh.md)

```
All symlink (read-only)                              All worktree (fully isolated)
│  rwt create /tmp/ws              rwt create /tmp/ws --all  │
│         ◄── rwt promote / demote dynamically ──►           │
└────────────────────────────────────────────────────────────┘
```

## Install

Python 3.10+, no third-party dependencies.

```bash
pip install repoworktree
```

## Quick Start

```bash
# Create workspace, specify repos to modify
rwt create /tmp/ws-agent1 -w nuttx,apps -n "fix-serial-driver"

# Develop in the workspace
cd /tmp/ws-agent1
source envsetup.sh && lunch && m   # build works normally

# Need to modify more repos during development
rwt promote frameworks/system/core

# Done with a repo, demote back to symlink
rwt demote frameworks/system/core

# Destroy workspace
rwt destroy /tmp/ws-agent1
```

## Command Reference

### `rwt create` — Create workspace

```bash
rwt create <path> [options]
```

| Option | Description |
|--------|-------------|
| `<path>` | Workspace target path |
| `-n, --name` | Workspace name (default: directory name) |
| `-w, --worktree` | Comma-separated sub-repo paths to create as git worktrees |
| `--all` | Create git worktrees for all sub-repos |
| `-s, --source` | Main repo checkout path (default: auto-detect `.repo/`) |
| `--checkout` | Check out this branch or tag for all worktrees (default: source HEAD) |
| `--pin` | Pin version, format `repo=version[,repo=version,...]` |
| `-b, --branch` | Create named branch instead of detached HEAD |

Examples:

```bash
# All symlink read-only (extreme A)
rwt create /tmp/ws-readonly

# Typical: modify a few repos
rwt create /tmp/ws-agent1 -w nuttx,apps -n "fix-serial-driver"

# Modify nested sub-repo
rwt create /tmp/ws-bt -w nuttx,apps/system/adb

# Parent and child repos both writable
rwt create /tmp/ws-dev -w apps,apps/system/adb

# All worktree fully isolated (extreme B)
rwt create /tmp/ws-full --all

# Pin version + named branch
rwt create /tmp/ws-stable -w nuttx --pin nuttx=v12.0.0 -b feature/new-driver

# Check out a specific branch/tag for all worktrees (without pinning — sync still works)
rwt create /tmp/ws-release --all --checkout release/v2.0
```

### `rwt destroy` — Destroy workspace

```bash
rwt destroy <path|name> [-s source] [-f]
```

| Option | Description |
|--------|-------------|
| `<path\|name>` | Workspace path or name |
| `-s, --source` | Source repo checkout path (for name lookup, default: auto-detect) |
| `-f, --force` | Force destroy even with uncommitted changes or local commits |

```bash
rwt destroy /tmp/ws-agent1
rwt destroy fix-serial-driver     # by name
rwt destroy /tmp/ws-dirty -f      # discard uncommitted changes
```

### `rwt promote` — Promote sub-repo to writable worktree

Run inside a workspace to dynamically promote a symlinked sub-repo to a git worktree.

```bash
rwt promote <repo_path> [options]
```

| Option | Description |
|--------|-------------|
| `<repo_path>` | Sub-repo path (e.g. `nuttx`, `frameworks/system/core`) |
| `--pin` | Checkout specific version |
| `-b, --branch` | Create named branch |

```bash
cd /tmp/ws-agent1
rwt promote vendor/xiaomi/miwear
rwt promote frameworks/system/core --pin abc1234
rwt promote nuttx -b fix/uart-bug
```

Promote handles nesting automatically:
- Top-level symlink → directly replaced with worktree
- Deep nested (e.g. `frameworks/system/core`) → parent symlinks split into real dir + symlink mix
- Parent repo already has child worktrees → temporarily removes children, creates parent worktree, restores children

### `rwt demote` — Demote worktree to read-only symlink

```bash
rwt demote <repo_path> [-f]
```

| Option | Description |
|--------|-------------|
| `<repo_path>` | Sub-repo path |
| `-f, --force` | Force demote, discard uncommitted changes |

```bash
rwt demote apps
rwt demote apps -f    # discard changes
```

Demote handles nesting automatically:
- With child worktrees → preserves children, rebuilds parent as real dir + symlink structure
- Without child worktrees → collapses upward, restoring parent directories to symlinks

### `rwt list` — List all workspaces

```bash
rwt list [-s <source>] [--json]
```

### `rwt status` — Show workspace details

```bash
rwt status [<path|name>] [-s source] [--json]
```

### `rwt sync` — Sync workspace

```bash
rwt sync [-W workspace] [--rebase]
```

After `repo sync` on the main checkout, symlinks follow automatically. Worktrees need manual sync:

| Worktree state | Default | `--rebase` |
|----------------|---------|------------|
| symlink | auto follows | same |
| worktree, pinned | skip | skip |
| worktree, uncommitted changes | skip, report | skip, report |
| worktree, local commits | skip, report | rebase onto latest |
| worktree, clean | update to source HEAD | same |

### `rwt pin` / `rwt unpin` — Version pinning

```bash
rwt pin <repo_path> [<version>]
rwt unpin <repo_path>
```

### `rwt export` — Export changes

```bash
rwt export [--format patch|bundle] [-o <dir>]
```

## Use Cases

### Parallel multi-agent development

```bash
# Two agents modify nuttx simultaneously, fully isolated
rwt create /tmp/ws-agent1 -w nuttx -n "agent1-serial-fix"
rwt create /tmp/ws-agent2 -w nuttx -n "agent2-spi-driver"
```

### Dynamic adjustment during development

```bash
# Start with only nuttx
rwt create /tmp/ws-dev -w nuttx

cd /tmp/ws-dev

# Need to modify apps/system/adb
rwt promote apps/system/adb

# Also need to modify apps itself
rwt promote apps

# Done with apps, demote back
rwt demote apps
```

### Push changes to Gerrit

```bash
cd /tmp/ws-agent1/nuttx
git push origin HEAD:refs/for/main
```

## Workspace Structure

```
/tmp/ws-agent1/
├── .workspace.json     # workspace metadata
├── nuttx/              # git worktree (writable)
├── apps/               # real directory (has worktree descendants)
│   ├── system/
│   │   ├── adb/        # git worktree (writable)
│   │   └── core/       # symlink → source (read-only)
│   └── benchmarks/     # symlink → source (read-only)
├── build/              # symlink → source (read-only)
├── frameworks/         # symlink → source (read-only)
├── build.sh            # symlink (preserves original relative link)
└── CLAUDE.md           # symlink → source
```

## How It Works

- **Symlinked repos**: Zero overhead, point directly to the main checkout directory. Read-only — modifications affect the main checkout.
- **Worktree repos**: Created via `git worktree add` with their own HEAD, index, and working tree. Fully isolated.
- **Nested repos**: `repo`-managed projects have parent-child repos (e.g. `apps/` and `apps/system/adb/` are independent git repos). When a child needs a worktree, parent symlinks are split into real directories with symlinked siblings — real directories are only created along the path to the worktree.
- **Metadata**: `.workspace.json` stores per-workspace config. `.workspaces.json` in the source root indexes all workspaces.

## License

MIT
