# repoworktree

为 repo 管理的多仓库项目创建隔离工作空间。基于 git worktree + symlink，支持从全 symlink（零开销只读）到全 worktree（完全隔离）的连续光谱。

```
全 symlink (只读)                                    全 worktree (完全隔离)
│  rwt create /tmp/ws              rwt create /tmp/ws --all  │
│         ◄── rwt promote / demote 动态切换 ──►              │
└──────────────────────────────────────────────────────────┘
```

## 安装

Python 3.10+，无第三方依赖。

```bash
pip install repoworktree
```

## 快速开始

```bash
# 创建工作空间，指定需要修改的仓库
rwt create /tmp/ws-agent1 -w nuttx,apps -n "fix-serial-driver"

# 在工作空间中开发
cd /tmp/ws-agent1
source envsetup.sh && lunch && m   # 构建正常工作

# 开发中发现需要修改更多仓库
rwt promote frameworks/system/core

# 完成后不再需要修改的仓库降级回 symlink
rwt demote frameworks/system/core

# 销毁工作空间
rwt destroy /tmp/ws-agent1
```

## 命令参考

### `rwt create` — 创建工作空间

```bash
rwt create <path> [options]
```

| 选项 | 说明 |
|------|------|
| `<path>` | 工作空间目标路径 |
| `-n, --name` | 工作空间名称（默认：目录名） |
| `-w, --worktree` | 逗号分隔的子仓库路径，创建为 git worktree |
| `--all` | 所有子仓库都创建 git worktree |
| `-s, --source` | 主 repo checkout 路径（默认：自动检测 `.repo/`） |
| `--checkout` | 所有 worktree 检出此分支或 tag（默认：source HEAD） |
| `--pin` | 锁定版本，格式 `repo=version[,repo=version,...]` |
| `-b, --branch` | 为 worktree 创建命名分支而非 detached HEAD |

示例：

```bash
# 全 symlink 只读（极端 A）
rwt create /tmp/ws-readonly

# 典型用法：修改少量仓库
rwt create /tmp/ws-agent1 -w nuttx,apps -n "fix-serial-driver"

# 修改嵌套子仓库
rwt create /tmp/ws-bt -w nuttx,apps/system/adb

# 父子仓库同时修改
rwt create /tmp/ws-dev -w apps,apps/system/adb

# 全 worktree 完全隔离（极端 B）
rwt create /tmp/ws-full --all

# 锁定版本 + 命名分支
rwt create /tmp/ws-stable -w nuttx --pin nuttx=v12.0.0 -b feature/new-driver

# 所有 worktree 检出指定分支/tag（不锁定，sync 仍然有效）
rwt create /tmp/ws-release --all --checkout release/v2.0
```

### `rwt destroy` — 销毁工作空间

```bash
rwt destroy <path|name> [-s source] [-f]
```

| 选项 | 说明 |
|------|------|
| `<path\|name>` | 工作空间路径或名称 |
| `-s, --source` | 主 repo checkout 路径（按名称查找时使用，默认：自动检测） |
| `-f, --force` | 强制销毁，即使有未提交修改 |

```bash
rwt destroy /tmp/ws-agent1
rwt destroy fix-serial-driver     # 按名称
rwt destroy /tmp/ws-dirty -f      # 丢弃未提交修改
```

### `rwt promote` — 提升子仓库为可写 worktree

在工作空间内执行，将 symlink 的子仓库动态提升为 git worktree。

```bash
rwt promote <repo_path> [options]
```

| 选项 | 说明 |
|------|------|
| `<repo_path>` | 子仓库路径（如 `nuttx`、`frameworks/system/core`） |
| `--pin` | checkout 到指定版本 |
| `-b, --branch` | 创建命名分支 |

```bash
cd /tmp/ws-agent1
rwt promote vendor/xiaomi/miwear
rwt promote frameworks/system/core --pin abc1234
rwt promote nuttx -b fix/uart-bug
```

promote 自动处理嵌套情况：
- 顶层 symlink → 直接替换为 worktree
- 深层嵌套（如 `frameworks/system/core`）→ 自动拆解父级 symlink 为真实目录 + symlink 混合结构
- 父仓库已有子 worktree → 临时移除子 worktree，创建父 worktree，再恢复子 worktree

### `rwt demote` — 降级 worktree 为只读 symlink

```bash
rwt demote <repo_path> [-f]
```

| 选项 | 说明 |
|------|------|
| `<repo_path>` | 子仓库路径 |
| `-f, --force` | 强制降级，丢弃未提交修改 |

```bash
rwt demote apps
rwt demote apps -f    # 丢弃修改
```

demote 自动处理：
- 有子 worktree 时保留子 worktree，父目录重建为真实目录 + symlink 结构
- 无子 worktree 时向上合并，尽可能将父目录恢复为 symlink

### `rwt list` — 列出所有工作空间

```bash
rwt list [-s <source>] [--json]
```

### `rwt status` — 查看工作空间详情

```bash
rwt status [<path|name>] [-s source] [--json]
```

### `rwt sync` — 同步工作空间

```bash
rwt sync [-W workspace] [--rebase]
```

主仓库 `repo sync` 后，symlink 自动跟随更新。worktree 需要手动 sync：

| 子仓库状态 | 默认行为 | `--rebase` 行为 |
|-----------|----------|----------------|
| symlink | 自动跟随 | 同左 |
| worktree, pinned | 跳过 | 跳过 |
| worktree, 有本地修改 | 跳过，报告 | 跳过，报告 |
| worktree, 有本地 commit | 跳过，报告 | rebase 到最新 |
| worktree, clean | 更新到主仓库 HEAD | 同左 |

### `rwt pin` / `rwt unpin` — 版本锁定

```bash
rwt pin <repo_path> [<version>]
rwt unpin <repo_path>
```

### `rwt export` — 导出变更

```bash
rwt export [--format patch|bundle] [-o <dir>]
```

## 典型场景

### 多 agent 并行开发

```bash
# 两个 agent 同时修改 nuttx，互不影响
rwt create /tmp/ws-agent1 -w nuttx -n "agent1-serial-fix"
rwt create /tmp/ws-agent2 -w nuttx -n "agent2-spi-driver"
```

### 开发过程中动态调整

```bash
# 初始只修改 nuttx
rwt create /tmp/ws-dev -w nuttx

cd /tmp/ws-dev

# 发现需要修改 apps/system/adb
rwt promote apps/system/adb

# 又需要修改 apps 本身
rwt promote apps

# apps 改完了，降级回 symlink
rwt demote apps
```

### 导出变更到主目录

```bash
cd /tmp/ws-agent1/nuttx
git push origin HEAD:refs/for/main    # 直接推送到 Gerrit
```

## 工作空间结构

```
/tmp/ws-agent1/
├── .workspace.json     # 工作空间元数据
├── nuttx/              # git worktree（可写）
├── apps/               # 真实目录（内部有 worktree 后代）
│   ├── system/
│   │   ├── adb/        # git worktree（可写）
│   │   └── core/       # symlink → source（只读）
│   └── benchmarks/     # symlink → source（只读）
├── build/              # symlink → source（只读）
├── frameworks/         # symlink → source（只读）
├── build.sh            # symlink（保持原始相对链接）
└── CLAUDE.md           # symlink → source
```

## 原理

- **symlink 子仓库**：零开销，直接指向主 checkout 的对应目录，只读（修改会影响主目录）
- **worktree 子仓库**：通过 `git worktree add` 创建独立工作副本，有自己的 HEAD、index、工作树，完全隔离
- **嵌套仓库**：repo 管理的项目中存在父子仓库（如 `apps/` 和 `apps/system/adb/` 是独立 git 仓库）。当子仓库需要 worktree 时，父级 symlink 被拆解为真实目录 + symlink 混合结构，只在通往 worktree 的路径上创建真实目录
- **元数据**：`.workspace.json` 记录工作空间配置，主目录的 `.workspaces.json` 索引所有工作空间
