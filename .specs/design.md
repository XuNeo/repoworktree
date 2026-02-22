# Design: Repo Workspace

## Overview

`rw`（repo-workspace）是一个 Python CLI 工具，为 repo 管理的多仓库项目创建基于 git worktree + symlink 的隔离工作空间。

工作空间是一个**连续光谱**，两个极端分别是：

```
极端 A: 全 symlink                          极端 B: 全 worktree
(零磁盘开销, 完全只读)                       (最大隔离, 最大磁盘开销)
│                                                              │
│  rw create /tmp/ws                rw create /tmp/ws --all    │
│  (默认: 全 symlink)               (所有子仓库都是 worktree)   │
│                                                              │
│         ◄── rw promote/demote 在两端之间滑动 ──►             │
│                                                              │
│  常见用法: rw create /tmp/ws -w nuttx,apps                   │
│  (2 个 worktree + 487 个 symlink)                            │
└──────────────────────────────────────────────────────────────┘
```

## Architecture

```
┌──────────────────────────────────────────────────┐
│                   rw CLI (Python)                  │
│                                                    │
│  create │ destroy │ list │ status │ promote │      │
│  demote │ sync    │ pin  │ unpin  │ export  │      │
└────┬─────────────────────────────────┬─────────────┘
     │                                 │
     ▼                                 ▼
┌──────────────┐            ┌──────────────────────┐
│ Layout Engine │            │  Metadata (.json)    │
│              │            │                      │
│ - TreeBuilder │            │  workspace 内:       │
│ - Promoter   │            │    .workspace.json   │
│ - Demoter    │            │  source 内:          │
│              │            │    .workspaces.json   │
└──────┬───────┘            └──────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────┐
│         Main Repo Checkout (source of truth)      │
│  .repo/project.list  (489 sub-repos)              │
└──────────────────────────────────────────────────┘
```

## CLI 完整定义

### `rw create` — 创建工作空间

```
rw create <path> [options]
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `<path>` | positional | 必填 | 工作空间目标路径 |
| `--name`, `-n` | string | 目录名 | 工作空间名称，用于 list/status 显示 |
| `--worktree`, `-w` | string | 无 | 逗号分隔的子仓库路径，这些仓库创建 git worktree |
| `--all` | flag | false | 所有子仓库都创建 git worktree（极端 B） |
| `--source`, `-s` | path | 当前目录 | 主 repo checkout 路径（自动检测 `.repo/`） |
| `--pin` | string | 无 | 锁定版本，格式 `repo=version[,repo=version,...]` |
| `--branch`, `-b` | string | 无 | 为 worktree 创建命名分支而非 detached HEAD |

使用场景：

```bash
# 场景 1: 极端 A — 全 symlink，纯只读浏览/构建
rw create /tmp/ws-readonly

# 场景 2: 典型 LLM agent — 修改少量仓库
rw create /tmp/ws-agent1 -w nuttx,apps -n "fix-serial-driver"

# 场景 3: 修改深层嵌套仓库
rw create /tmp/ws-bt -w frameworks/connectivity/bluetooth/service

# 场景 4: 极端 B — 全 worktree，完全隔离
rw create /tmp/ws-full --all

# 场景 5: 锁定特定版本开发
rw create /tmp/ws-stable -w nuttx --pin nuttx=v12.0.0

# 场景 6: 创建命名分支
rw create /tmp/ws-feature -w nuttx,apps -b feature/new-driver
```

### `rw destroy` — 销毁工作空间

```
rw destroy <path|name> [options]
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `<path\|name>` | positional | 必填 | 工作空间路径或名称 |
| `--force`, `-f` | flag | false | 强制销毁，即使有未提交修改 |

```bash
rw destroy /tmp/ws-agent1
rw destroy fix-serial-driver        # 按名称
rw destroy /tmp/ws-dirty --force    # 丢弃未提交修改
```

### `rw list` — 列出所有工作空间

```
rw list [options]
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--source`, `-s` | path | 当前目录 | 主 repo checkout 路径 |
| `--json` | flag | false | JSON 格式输出（便于脚本/agent 解析） |

```bash
$ rw list
NAME                PATH                        WORKTREES  STATUS   CREATED
fix-serial-driver   /tmp/ws-agent1              2/489      dirty    2026-02-21 10:30
bt-refactor         /tmp/ws-bt                  1/489      clean    2026-02-21 11:00
full-isolation      /tmp/ws-full                489/489    dirty    2026-02-20 09:00
```

### `rw status` — 查看工作空间详情

```
rw status [<path|name>] [options]
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `<path\|name>` | positional | 当前目录 | 工作空间路径或名称 |
| `--json` | flag | false | JSON 格式输出 |

```bash
$ rw status fix-serial-driver
Workspace: fix-serial-driver
Path:      /tmp/ws-agent1
Source:    /home/neo/projects/vela
Created:   2026-02-21 10:30

Worktree repos (2/489):
  nuttx       2 modified, 1 commit ahead
  apps        clean

Symlinked repos: 487

$ rw status --json    # 在工作空间目录内执行，自动检测
```

### `rw promote` — 将只读子仓库提升为可写 worktree

```
rw promote <repo_path> [options]
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `<repo_path>` | positional | 必填 | 子仓库路径（如 `nuttx` 或 `frameworks/system/core`） |
| `--pin` | string | 无 | checkout 到指定版本 |
| `--branch`, `-b` | string | 无 | 创建命名分支 |

```bash
# 在工作空间内执行
cd /tmp/ws-agent1
rw promote vendor/xiaomi/miwear
rw promote frameworks/system/core --pin abc1234
rw promote nuttx -b fix/uart-bug
```

### `rw demote` — 将可写 worktree 降级为只读 symlink

```
rw demote <repo_path> [options]
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `<repo_path>` | positional | 必填 | 子仓库路径 |
| `--force`, `-f` | flag | false | 强制降级，丢弃未提交修改 |

```bash
rw demote apps                  # apps 必须是 clean 状态
rw demote apps --force          # 丢弃修改
```

### `rw sync` — 同步工作空间

```
rw sync [options]
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--rebase` | flag | false | 对有本地 commit 的 worktree 执行 rebase |

同步逻辑：

| 子仓库状态 | 默认行为 | `--rebase` 行为 |
|-----------|----------|----------------|
| symlink | 无需操作（自动跟随） | 同左 |
| worktree, pinned | 跳过 | 跳过 |
| worktree, 有本地修改 | 跳过，报告 | 跳过，报告 |
| worktree, 有本地 commit | 跳过，报告 | rebase 到最新 |
| worktree, clean 无 commit | 更新到主仓库 HEAD | 同左 |

```bash
$ rw sync
Syncing workspace fix-serial-driver...
  nuttx       skipped (1 local commit, use --rebase to update)
  apps        updated to abc1234

$ rw sync --rebase
  nuttx       rebased 1 commit onto def5678
  apps        already up to date
```

### `rw pin` / `rw unpin` — 版本锁定

```
rw pin <repo_path> [<version>]
rw unpin <repo_path>
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `<repo_path>` | positional | 必填 | 子仓库路径 |
| `<version>` | positional | 当前 HEAD | commit/tag/branch，不指定则锁定当前版本 |

```bash
rw pin nuttx v12.0.0       # 锁定到 tag
rw pin nuttx               # 锁定到当前 HEAD
rw unpin nuttx             # 解除锁定
```

### `rw export` — 导出变更

```
rw export [options]
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--format` | choice | `patch` | 导出格式：`patch`（git format-patch）或 `bundle`（git bundle） |
| `--output`, `-o` | path | `.` | 输出目录 |

```bash
$ rw export
Exported:
  nuttx.patch       (1 commit)
  apps.patch        (2 commits)

$ rw export --format bundle -o /tmp/patches/
```

## 使用场景全景

以下逐一列出所有具体使用场景，验证 design 的覆盖度。

### 场景 1: 全 symlink 只读工作空间（极端 A）

目的：快速创建一个只读副本用于浏览代码或执行构建，不修改任何仓库。

```bash
rw create /tmp/ws-readonly
```

结果：所有 489 个子仓库都是 symlink，零额外磁盘开销。可以 `source envsetup.sh && lunch && m` 构建（构建产物写入 workspace 内的 build output 目录）。

### 场景 2: 典型 LLM agent 开发（修改少量顶层仓库）

目的：agent 需要修改 nuttx 和 apps 两个顶层仓库。

```bash
rw create /tmp/ws-agent1 -w nuttx,apps -n "fix-serial-driver"
```

结果：
```
/tmp/ws-agent1/
├── nuttx/          # git worktree
├── apps/           # git worktree
├── build/          # symlink
├── frameworks/     # symlink
├── vendor/         # symlink
└── ...             # symlink
```

### 场景 3: 修改嵌套子仓库

目的：agent 需要修改 nuttx 和 apps/system/adb（apps 的子仓库）。

```bash
rw create /tmp/ws-agent1 -w nuttx,apps/system/adb
```

结果：
```
/tmp/ws-agent1/
├── nuttx/              # git worktree
├── apps/               # 真实目录（内部有 worktree 后代）
│   ├── system/         # 真实目录
│   │   ├── adb/        # git worktree
│   │   └── other/      # symlink → source
│   ├── benchmarks/     # symlink → source
│   └── ...             # symlink → source
├── build/              # symlink
└── ...                 # symlink
```

注意：`apps/` 本身也是一个 git 仓库，但这里它不在 `-w` 列表中，所以不创建 worktree。`apps/` 被创建为真实目录只是因为它的子树中有 worktree（`apps/system/adb`）。`apps/` 目录下除了通往 `system/adb` 路径上的目录外，其余子项都是 symlink 到 source。

### 场景 4: 父子仓库同时需要 worktree

目的：agent 需要同时修改 apps（父仓库）和 apps/system/adb（子仓库）。

```bash
rw create /tmp/ws-agent1 -w apps,apps/system/adb
```

结果：
```
/tmp/ws-agent1/
├── apps/               # git worktree（apps 仓库自身的文件可写）
│   ├── system/         # 真实目录（由 apps worktree checkout 产生）
│   │   ├── adb/        # git worktree（独立的 git 仓库）
│   │   └── other/      # 由 apps worktree 提供（可写）
│   ├── benchmarks/     # 由 apps worktree 提供（可写）
│   └── ...
└── ...
```

这是 repo 管理项目的天然结构：`apps/` 是一个 git 仓库，`apps/system/adb/` 是嵌套在其中的另一个独立 git 仓库。在主工作目录中两者本来就共存——`apps/.git` 和 `apps/system/adb/.git` 分别指向各自的 bare repo。

创建逻辑：
1. 先为 `apps/` 创建 git worktree → 这会 checkout apps 仓库的所有文件，包括 `system/adb/` 目录（但 adb 的内容由 apps 仓库管理的部分可能只是空目录或 .gitignore）
2. 再为 `apps/system/adb/` 创建 git worktree → 覆盖/填充 adb 目录的内容

这与 `repo sync` 的行为一致：repo 也是先 checkout 父仓库，再 checkout 子仓库覆盖对应目录。

### 场景 5: 开发过程中追加 worktree（promote）

目的：创建时只指定了 nuttx 和 apps/system/adb，开发过程中发现 apps 本身也需要修改。

```bash
# 初始创建
rw create /tmp/ws-agent1 -w nuttx,apps/system/adb

# 开发中发现需要修改 apps
cd /tmp/ws-agent1
rw promote apps
```

promote `apps` 的处理逻辑：

当前状态：`apps/` 是一个真实目录（因为之前为容纳 `apps/system/adb` 的 worktree 已被拆解），内部混合了 symlink 和子 worktree。

操作步骤：
1. 记录 `apps/` 下当前所有已存在的子 worktree（如 `apps/system/adb`）
2. 移除这些子 worktree 的 git worktree 引用（暂存）
3. 删除 `apps/` 目录（包括所有 symlink 和真实目录）
4. 为 `apps/` 创建 git worktree → checkout apps 仓库的完整内容
5. 恢复子 worktree：为 `apps/system/adb` 重新创建 git worktree
6. 更新 `.workspace.json`

结果：`apps/` 变成 git worktree（apps 仓库的文件可写），`apps/system/adb/` 仍然是独立的 git worktree。

### 场景 6: promote 深层嵌套仓库（需要拆解 symlink）

目的：工作空间中 `frameworks/` 当前是 symlink，需要修改 `frameworks/system/core`。

```bash
cd /tmp/ws-agent1
rw promote frameworks/system/core
```

当前状态：`frameworks/` 是一个 symlink → source/frameworks/。

操作步骤：
1. 删除 `frameworks/` symlink
2. `mkdir frameworks/`
3. 遍历 source/frameworks/ 的直接子项：
   - `connectivity/` → symlink
   - `graphics/` → symlink
   - `system/` → 需要继续拆解（因为目标在其子树中）
4. `mkdir frameworks/system/`
5. 遍历 source/frameworks/system/ 的直接子项：
   - `core/` → git worktree add
   - `kvdb/` → symlink
   - ...
6. 更新 `.workspace.json`

### 场景 7: demote 有子 worktree 的父仓库

目的：`apps` 和 `apps/system/adb` 都是 worktree，现在想 demote `apps`（但保留 `apps/system/adb`）。

```bash
rw demote apps
```

操作步骤：
1. 检查 `apps` worktree 是否有未提交修改（不含子仓库 adb 的修改）
2. 记录 `apps/` 下的子 worktree（`apps/system/adb`）
3. 暂存子 worktree 的 git worktree 引用
4. 移除 `apps` 的 git worktree
5. 重建 `apps/` 为真实目录 + symlink 结构（与场景 3 的结构一致）
6. 恢复 `apps/system/adb` 的 git worktree
7. 更新 `.workspace.json`

### 场景 8: demote 子仓库后父目录可合并为 symlink

目的：`frameworks/system/core` 是唯一的 worktree 在 `frameworks/` 子树下，demote 它。

```bash
rw demote frameworks/system/core
```

操作步骤：
1. 移除 `frameworks/system/core` 的 git worktree
2. 将 `core/` 替换为 symlink
3. 检查 `frameworks/system/` 下是否还有其他 worktree → 没有
4. 将 `frameworks/system/` 合并为 symlink（删除目录，创建 symlink）
5. 检查 `frameworks/` 下是否还有其他 worktree → 没有
6. 将 `frameworks/` 合并为 symlink（删除目录，创建 symlink）
7. 更新 `.workspace.json`

这个"向上合并"是可选优化，但能保持工作空间结构的简洁性。

### 场景 9: 全 worktree 完全隔离（极端 B）

目的：需要完全独立的工作副本，不依赖主目录的任何文件。

```bash
rw create /tmp/ws-full --all
```

结果：所有 489 个子仓库都创建 git worktree。没有任何 symlink（除了顶层的 build.sh 等项目 symlink 会被重建）。磁盘开销最大但隔离最彻底。

### 场景 10: 多 agent 并行

目的：两个 agent 同时修改 nuttx。

```bash
rw create /tmp/ws-agent1 -w nuttx -n "agent1-serial-fix"
rw create /tmp/ws-agent2 -w nuttx -n "agent2-spi-driver"
```

两个工作空间各自有独立的 nuttx worktree，互不影响。git worktree 机制保证同一仓库可以有多个 worktree。

### 场景 11: 锁定版本开发

目的：在 nuttx v12.0.0 的基础上开发，不受主线更新影响。

```bash
rw create /tmp/ws-stable -w nuttx --pin nuttx=v12.0.0
```

或者创建后锁定：

```bash
cd /tmp/ws-stable
rw pin nuttx v12.0.0      # checkout 到 v12.0.0 并标记为 pinned
rw sync                    # nuttx 不会被更新
rw unpin nuttx             # 解除锁定
```

### 场景 12: 导出变更给主工作目录

目的：agent 在工作空间中完成开发，需要将变更应用到主目录或上传 review。

```bash
cd /tmp/ws-agent1

# 查看所有变更
rw status

# 导出为 patch
rw export -o /tmp/patches/

# 在主目录中应用
cd /home/neo/projects/vela
git -C nuttx am /tmp/patches/nuttx.patch
git -C apps am /tmp/patches/apps.patch
```

或者直接在 worktree 中 push：

```bash
cd /tmp/ws-agent1/nuttx
git push origin HEAD:refs/for/main    # 直接推送到 Gerrit
```

### 场景 13: 主仓库 sync 后更新工作空间

目的：主仓库执行了 `repo sync`，需要更新工作空间。

```bash
# 主仓库更新
cd /home/neo/projects/vela
repo sync

# 更新工作空间
cd /tmp/ws-agent1
rw sync
```

symlink 的子仓库自动跟随（因为 symlink 指向 source）。worktree 的子仓库按规则处理（clean 的更新，dirty/pinned 的跳过）。

### 场景覆盖度检查

| 场景 | create | destroy | promote | demote | sync | pin | export | status/list |
|------|--------|---------|---------|--------|------|-----|--------|-------------|
| 1. 全 symlink | ✅ | ✅ | - | - | ✅ | - | - | ✅ |
| 2. 顶层 worktree | ✅ | ✅ | - | ✅ | ✅ | - | ✅ | ✅ |
| 3. 嵌套 worktree | ✅ | ✅ | - | ✅ | ✅ | - | ✅ | ✅ |
| 4. 父子同时 worktree | ✅ | ✅ | - | ✅ | ✅ | - | ✅ | ✅ |
| 5. 追加 worktree（有子 wt） | - | - | ✅ | - | - | - | - | - |
| 6. 拆解 symlink promote | - | - | ✅ | - | - | - | - | - |
| 7. demote 有子 wt 的父 | - | - | - | ✅ | - | - | - | - |
| 8. demote 后向上合并 | - | - | - | ✅ | - | - | - | - |
| 9. 全 worktree | ✅ | ✅ | - | - | ✅ | - | ✅ | ✅ |
| 10. 多 agent 并行 | ✅ | ✅ | - | - | ✅ | - | - | ✅ |
| 11. 版本锁定 | ✅ | - | - | - | ✅ | ✅ | - | ✅ |
| 12. 导出变更 | - | - | - | - | - | - | ✅ | ✅ |
| 13. sync 后更新 | - | - | - | - | ✅ | - | - | - |

所有命令在至少一个场景中被覆盖。

## 关键设计决策

### D1: 目录结构策略——递归拆解

子仓库存在大量嵌套（714 对），不能逐个 symlink。策略是**从顶层开始，只在通往 worktree 的路径上创建真实目录，其余全部 symlink**。

示例：`-w nuttx,frameworks/system/core` 时的结构：

```
workspace/
├── .workspace.json
├── nuttx/                       # git worktree
├── apps/                        # symlink → source/apps/
├── build/                       # symlink → source/build/
├── external/                    # symlink → source/external/
├── prebuilts/                   # symlink → source/prebuilts/
├── vendor/                      # symlink → source/vendor/
├── frameworks/                  # 真实目录（内部有 worktree）
│   ├── connectivity/            # symlink → source/frameworks/connectivity/
│   ├── graphics/                # symlink → source/frameworks/graphics/
│   └── system/                  # 真实目录（内部有 worktree）
│       ├── core/                # git worktree
│       └── kvdb/                # symlink → source/frameworks/system/kvdb/
├── build.sh                     # symlink（重建相对路径）
└── emulator.sh                  # symlink（重建相对路径）
```

`--all` 模式下，所有子仓库都是 git worktree，没有 symlink（极端 B）。
无 `-w` 也无 `--all` 时，所有子仓库都是 symlink（极端 A）。

### D2: 顶层文件处理

| 类型 | 处理 | 示例 |
|------|------|------|
| symlink 文件 | 重建相同的相对路径 symlink | `build.sh → nuttx/tools/build.sh` |
| 配置文件 | symlink 到 source | `CLAUDE.md` |
| 临时/用户文件 | 忽略 | `.patch`, `.elf`, `.zip` |

### D3: 构建兼容性

| 构建路径 | 兼容性 | 原因 |
|----------|--------|------|
| `source envsetup.sh && lunch && m` | ✅ | `gettop` 返回工作空间路径 |
| `./build.sh <board>:<config>` | ⚠️ | `readlink -f` 解析回 source |

推荐构建流程是 envsetup.sh，build.sh 的不兼容可接受。

### D4: Promote 的拆解算法

当 `frameworks/` 是 symlink，要 promote `frameworks/system/core`：

```
1. 删除 frameworks/ symlink
2. mkdir frameworks/
3. 遍历 source/frameworks/ 的直接子项:
   - connectivity/ → symlink
   - graphics/ → symlink
   - system/ → 需要继续拆解
4. mkdir frameworks/system/
5. 遍历 source/frameworks/system/ 的直接子项:
   - core/ → git worktree add
   - kvdb/ → symlink
6. 更新 .workspace.json
```

### D5: 元数据

工作空间内 `.workspace.json`：

```json
{
  "version": 1,
  "source": "/home/neo/projects/vela",
  "name": "fix-serial-driver",
  "created": "2026-02-21T10:30:00Z",
  "worktrees": [
    {"path": "nuttx", "branch": null, "pinned": null},
    {"path": "frameworks/system/core", "branch": null, "pinned": "abc1234"}
  ]
}
```

source 目录下 `.workspaces.json`：

```json
{
  "workspaces": [
    {"name": "fix-serial-driver", "path": "/tmp/ws-agent1", "created": "2026-02-21T10:30:00Z"}
  ]
}
```

## Implementation

### 语言与依赖

- Python 3.10+（项目环境已有）
- 仅使用标准库：`argparse`, `subprocess`, `json`, `pathlib`, `os`, `shutil`
- 无第三方依赖
- 单文件或小型 package，可直接放在项目中

### 模块结构

```
rw/
├── __main__.py          # CLI 入口, argparse 定义
├── scanner.py           # 解析 .repo/project.list，获取子仓库列表
├── layout.py            # TreeBuilder: 递归构建 symlink + worktree 目录树
├── worktree.py          # git worktree add/remove/list 封装
├── promote.py           # promote/demote 逻辑（含拆解算法）
├── sync.py              # sync 逻辑
├── metadata.py          # .workspace.json / .workspaces.json 读写
└── export.py            # patch/bundle 导出
```

### 核心算法: TreeBuilder

```python
def build_tree(source: Path, workspace: Path, all_repos: list[str], worktree_repos: set[str]):
    """
    构建工作空间目录树。

    对于 all_repos 中的每个子仓库路径:
    - 如果在 worktree_repos 中 → git worktree add
    - 否则 → symlink

    嵌套处理: 如果一个目录既不是子仓库也不是叶子目录,
    但其子树中包含 worktree_repos 的成员, 则创建真实目录并递归。
    """
    # 构建路径前缀树
    trie = build_prefix_trie(all_repos, worktree_repos)

    # 递归遍历前缀树
    for node in trie.children:
        if node.is_repo and node.path in worktree_repos:
            git_worktree_add(source / node.path, workspace / node.path)
        elif node.has_worktree_descendant:
            mkdir(workspace / node.path)
            build_subtree(node, source, workspace)  # 递归
        else:
            symlink(workspace / node.path, source / node.path)
```

## Error Handling

### 原子性

- create: 先在 `<path>.tmp` 构建，成功后 rename
- destroy: 先 remove 所有 git worktree，再删目录，最后更新索引
- promote/demote: 失败时回滚到操作前状态

### 错误场景

| 场景 | 处理 |
|------|------|
| 目标路径已存在 | 拒绝，提示 destroy |
| 子仓库路径无效 | 报错，列出相似路径（模糊匹配） |
| git worktree add 失败 | 回滚已创建的 worktree，清理临时目录 |
| demote 有未提交修改 | 拒绝，提示 commit/stash 或 --force |
| sync rebase 冲突 | 中止 rebase，报告冲突，保持原状态 |

## Testing Strategy

### 测试环境：本地 repo 仓库

所有测试基于一个由脚本自动创建的本地 repo 环境，使用真实的 `repo init` + `repo sync`，不使用 mock。

#### 测试 repo 结构

```
$TMPDIR/rw-test-{pid}/
├── remotes/                          # bare git repos（模拟 Gerrit 远端）
│   ├── manifest.git                  # manifest 仓库
│   ├── nuttx.git                     # 顶层仓库
│   ├── apps.git                      # 顶层仓库（有嵌套子仓库）
│   ├── apps-system-adb.git           # apps 的嵌套子仓库
│   ├── apps-system-core.git          # apps 的嵌套子仓库
│   ├── build.git                     # 构建脚本仓库
│   ├── frameworks.git                # 中间层仓库
│   ├── frameworks-system.git         # 中间层仓库
│   ├── frameworks-system-core.git    # 深层嵌套仓库
│   ├── frameworks-system-kvdb.git    # 深层嵌套仓库
│   ├── frameworks-connectivity.git   # 中间层仓库
│   ├── external-lib-a.git            # 外部库
│   └── external-lib-b.git            # 外部库
├── source/                           # repo init + sync 的主工作目录
│   ├── .repo/
│   ├── nuttx/
│   ├── apps/
│   │   └── system/
│   │       ├── adb/
│   │       └── core/
│   ├── build/
│   ├── frameworks/
│   │   ├── system/
│   │   │   ├── core/
│   │   │   └── kvdb/
│   │   └── connectivity/
│   └── external/
│       ├── lib-a/
│       └── lib-b/
└── workspaces/                       # 测试创建的工作空间放这里
```

manifest.xml:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<manifest>
  <remote name="local" fetch="{remotes_path}" />
  <default revision="master" remote="local" sync-j="4" />

  <project name="nuttx" path="nuttx" />
  <project name="apps" path="apps" />
  <project name="apps-system-adb" path="apps/system/adb" />
  <project name="apps-system-core" path="apps/system/core" />
  <project name="build" path="build" />
  <project name="frameworks" path="frameworks" />
  <project name="frameworks-system" path="frameworks/system" />
  <project name="frameworks-system-core" path="frameworks/system/core" />
  <project name="frameworks-system-kvdb" path="frameworks/system/kvdb" />
  <project name="frameworks-connectivity" path="frameworks/connectivity" />
  <project name="external-lib-a" path="external/lib-a" />
  <project name="external-lib-b" path="external/lib-b" />
</manifest>
```

12 个子仓库，覆盖：顶层仓库、2 层嵌套、3 层嵌套、父子仓库共存。

#### 测试环境搭建脚本

`tests/conftest.py` 中的 pytest fixture：

```python
@pytest.fixture(scope="session")
def repo_env(tmp_path_factory):
    """
    创建完整的 repo 测试环境。session 级别，所有测试共享。

    步骤:
    1. 为每个项目创建 bare git repo，添加初始 commit（含不同文件）
    2. 创建 manifest bare repo，包含 default.xml
    3. repo init -u <manifest.git> --no-repo-verify
    4. repo sync --no-repo-verify
    5. 返回 RepoTestEnv 对象（包含 remotes_dir, source_dir, workspaces_dir）
    """

@pytest.fixture
def workspace_dir(repo_env):
    """
    为每个测试用例提供独立的 workspace 目标目录。
    测试结束后自动清理（包括 git worktree remove）。
    """
```

### 单元测试

每个模块对应一个测试文件。

#### `tests/test_scanner.py` — Repo Scanner

| 测试 | 验证内容 |
|------|----------|
| `test_scan_project_list` | 从 `.repo/project.list` 正确解析出 12 个子仓库路径 |
| `test_scan_returns_sorted` | 返回的路径列表按字母序排列 |
| `test_scan_no_repo_dir` | 非 repo 目录下调用时抛出明确错误 |
| `test_trie_build` | 12 个路径构建的前缀树结构正确 |
| `test_trie_has_worktree_descendant` | 标记 `frameworks/system/core` 为 worktree 时，`frameworks` 和 `frameworks/system` 节点的 `has_worktree_descendant` 为 True |
| `test_trie_no_worktree_descendant` | `external` 子树无 worktree 时，`has_worktree_descendant` 为 False |
| `test_trie_root_repo_is_worktree` | `nuttx` 本身是 worktree 时，节点标记正确 |

#### `tests/test_worktree.py` — Git Worktree 封装

| 测试 | 验证内容 |
|------|----------|
| `test_add_detached` | 创建 detached HEAD worktree，验证 HEAD 与 source 一致 |
| `test_add_branch` | 创建命名分支 worktree，验证分支名和 HEAD |
| `test_add_pinned` | 创建指定 commit 的 worktree，验证 HEAD 是指定 commit |
| `test_remove_clean` | 移除 clean worktree 成功 |
| `test_remove_dirty_rejected` | 移除有未提交修改的 worktree 时拒绝 |
| `test_remove_dirty_force` | `force=True` 时强制移除 dirty worktree |
| `test_list` | 列出仓库的所有 worktree，返回路径列表 |
| `test_has_local_changes` | 检测未暂存/未提交修改 |
| `test_has_local_commits` | 检测相对于 source HEAD 的本地 commit |
| `test_multiple_worktrees_same_repo` | 同一仓库创建两个 worktree 互不影响 |

#### `tests/test_layout.py` — Layout Engine

| 测试 | 验证内容 |
|------|----------|
| `test_all_symlink` | 无 worktree 时，所有顶层目录都是 symlink |
| `test_all_worktree` | `--all` 时，所有子仓库都是 git worktree，无 symlink |
| `test_top_level_worktree` | `-w nuttx` 时，nuttx 是 worktree，其余是 symlink |
| `test_nested_worktree` | `-w apps/system/adb` 时，apps/ 是真实目录，apps/system/ 是真实目录，apps/system/adb/ 是 worktree，其余是 symlink |
| `test_parent_child_both_worktree` | `-w apps,apps/system/adb` 时，apps/ 是 worktree，apps/system/adb/ 也是 worktree |
| `test_deep_nested_worktree` | `-w frameworks/system/core` 时，frameworks/ 和 frameworks/system/ 是真实目录，core/ 是 worktree，connectivity/ 是 symlink |
| `test_multiple_worktrees_different_trees` | `-w nuttx,frameworks/system/core` 时，两棵子树分别正确处理 |
| `test_top_level_files_symlinks` | 顶层 symlink 文件（如 build.sh → nuttx/tools/build.sh）被正确重建 |
| `test_top_level_files_config` | 顶层配置文件被 symlink 到 source |
| `test_top_level_files_ignored` | 临时文件（.patch, .elf）不出现在工作空间中 |

#### `tests/test_metadata.py` — Metadata 读写

| 测试 | 验证内容 |
|------|----------|
| `test_create_workspace_json` | 创建 `.workspace.json`，字段完整 |
| `test_read_workspace_json` | 读取已有的 `.workspace.json` |
| `test_add_worktree_entry` | 添加 worktree 条目后 JSON 更新 |
| `test_remove_worktree_entry` | 移除 worktree 条目后 JSON 更新 |
| `test_pin_worktree` | 设置 pinned 字段 |
| `test_unpin_worktree` | 清除 pinned 字段 |
| `test_register_workspace` | 在 source 的 `.workspaces.json` 中注册 |
| `test_unregister_workspace` | 从 `.workspaces.json` 中移除 |
| `test_list_workspaces` | 列出所有已注册的工作空间 |
| `test_find_workspace_by_name` | 按名称查找工作空间 |
| `test_detect_workspace` | 在工作空间目录内自动检测 `.workspace.json` |

#### `tests/test_promote.py` — Promote / Demote

| 测试 | 验证内容 |
|------|----------|
| `test_promote_top_level` | promote `nuttx`（当前是 symlink）→ 变成 worktree |
| `test_promote_nested_split_symlink` | promote `frameworks/system/core`（frameworks/ 是 symlink）→ 拆解为目录 + symlink + worktree |
| `test_promote_under_existing_dir` | promote `apps/system/core`（apps/ 已是真实目录因为 apps/system/adb 是 worktree）→ 在已有目录结构中添加 worktree |
| `test_promote_parent_with_child_worktree` | promote `apps`（apps/system/adb 已是 worktree）→ apps 变成 worktree，adb 保持 worktree |
| `test_promote_already_worktree` | promote 已经是 worktree 的仓库 → 报错 |
| `test_promote_invalid_repo` | promote 不存在的仓库路径 → 报错 |
| `test_promote_with_pin` | promote 并指定版本 → worktree checkout 到指定版本 |
| `test_promote_with_branch` | promote 并创建分支 → worktree 在命名分支上 |
| `test_demote_top_level` | demote `nuttx` → 变回 symlink |
| `test_demote_nested` | demote `frameworks/system/core` → 变回 symlink，父目录向上合并为 symlink |
| `test_demote_parent_preserves_child` | demote `apps`（apps/system/adb 是 worktree）→ apps 变回目录+symlink 结构，adb 保持 worktree |
| `test_demote_dirty_rejected` | demote 有修改的 worktree → 拒绝 |
| `test_demote_dirty_force` | demote --force → 强制移除 |
| `test_demote_not_worktree` | demote 非 worktree 的仓库 → 报错 |

#### `tests/test_sync.py` — Sync

| 测试 | 验证内容 |
|------|----------|
| `test_sync_clean_worktree` | clean worktree 更新到 source 最新 HEAD |
| `test_sync_dirty_skipped` | 有未提交修改的 worktree 被跳过 |
| `test_sync_local_commits_skipped` | 有本地 commit 的 worktree 被跳过（无 --rebase） |
| `test_sync_local_commits_rebase` | --rebase 时本地 commit 被 rebase |
| `test_sync_pinned_skipped` | pinned worktree 不更新 |
| `test_sync_symlink_unchanged` | symlink 子仓库不受影响 |
| `test_sync_after_remote_update` | 向 remote bare repo push 新 commit → source 中 `git pull` → workspace sync 更新 |

为了测试 sync，需要在 remote bare repo 中添加新 commit 并在 source 中更新：

```python
def push_new_commit(remote_bare, branch="master"):
    """向 bare repo push 一个新 commit，模拟远端更新"""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "clone", str(remote_bare), tmp])
        (Path(tmp) / "update.txt").write_text(str(time.time()))
        subprocess.run(["git", "-C", tmp, "add", "-A"])
        subprocess.run(["git", "-C", tmp, "commit", "-m", "remote update"])
        subprocess.run(["git", "-C", tmp, "push", "origin", branch])

def sync_source(source_dir, repo_path):
    """在 source 中更新指定子仓库到最新"""
    remote_url = get_remote_url(source_dir / repo_path)
    subprocess.run(["git", "-C", str(source_dir / repo_path), "fetch", "origin"])
    subprocess.run(["git", "-C", str(source_dir / repo_path), "checkout",
                     "FETCH_HEAD", "--detach"])
```

#### `tests/test_export.py` — Export

| 测试 | 验证内容 |
|------|----------|
| `test_export_patch` | 有 commit 的 worktree 导出 .patch 文件 |
| `test_export_bundle` | 有 commit 的 worktree 导出 .bundle 文件 |
| `test_export_no_commits` | 无本地 commit 时不导出，报告 "nothing to export" |
| `test_export_multiple_repos` | 多个 worktree 有 commit 时，每个生成独立的 patch |
| `test_patch_apply` | 导出的 patch 能在 source 对应子仓库中 `git am` 成功 |

### 集成测试

`tests/test_integration.py` — 端到端全流程测试，每个测试对应一个使用场景。

| 测试 | 对应场景 | 验证内容 |
|------|----------|----------|
| `test_scenario_01_all_symlink` | 场景 1 | create 全 symlink → 所有目录是 symlink → status 显示 0 worktree → destroy 清理干净 |
| `test_scenario_02_top_level_worktree` | 场景 2 | create -w nuttx,apps → 验证目录结构 → 在 worktree 中修改文件 → status 显示修改 → destroy |
| `test_scenario_03_nested_worktree` | 场景 3 | create -w nuttx,apps/system/adb → 验证 apps/ 是目录、apps/system/adb 是 worktree → 在 adb 中修改 → status |
| `test_scenario_04_parent_child_worktree` | 场景 4 | create -w apps,apps/system/adb → 验证 apps 是 worktree、adb 也是 worktree → 分别修改 → status 分别显示 |
| `test_scenario_05_promote_parent` | 场景 5 | create -w nuttx,apps/system/adb → promote apps → 验证 apps 变成 worktree、adb 保持 worktree |
| `test_scenario_06_promote_deep_split` | 场景 6 | create 全 symlink → promote frameworks/system/core → 验证拆解正确 |
| `test_scenario_07_demote_parent_keep_child` | 场景 7 | create -w apps,apps/system/adb → demote apps → 验证 apps 变回目录+symlink、adb 保持 worktree |
| `test_scenario_08_demote_collapse` | 场景 8 | create → promote frameworks/system/core → demote frameworks/system/core → 验证 frameworks/ 合并回 symlink |
| `test_scenario_09_all_worktree` | 场景 9 | create --all → 所有 12 个子仓库都是 worktree → destroy 清理所有 worktree |
| `test_scenario_10_multi_workspace` | 场景 10 | create ws1 -w nuttx → create ws2 -w nuttx → 两个 worktree 独立 → list 显示 2 个 → destroy 各自 |
| `test_scenario_11_pin` | 场景 11 | create -w nuttx → pin nuttx → remote 更新 → sync → 验证 nuttx 未更新 → unpin → sync → 验证更新 |
| `test_scenario_12_export` | 场景 12 | create -w nuttx → 在 worktree 中 commit → export → 在 source 中 git am → 验证 patch 应用成功 |
| `test_scenario_13_sync_after_update` | 场景 13 | create -w nuttx,apps → remote 更新 nuttx → source 更新 → rw sync → 验证 clean worktree 更新、dirty 跳过 |

每个集成测试的结构：

```python
def test_scenario_XX(repo_env, workspace_dir):
    # 1. Setup: 创建工作空间
    # 2. Act: 执行操作
    # 3. Assert: 验证目录结构、symlink 目标、worktree 状态、metadata
    # 4. Teardown: destroy 工作空间，验证清理干净（fixture 自动处理）
```

### 验证辅助函数

```python
# tests/helpers.py

def assert_is_symlink(path: Path, target: Path):
    """断言 path 是 symlink 且指向 target"""

def assert_is_worktree(path: Path):
    """断言 path 是 git worktree（检查 .git 文件指向 worktree 元数据）"""

def assert_is_real_dir(path: Path):
    """断言 path 是真实目录（非 symlink）"""

def assert_workspace_clean(workspace_dir: Path):
    """断言工作空间已被完全清理（目录不存在，git worktree 引用已移除）"""

def assert_source_untouched(source_dir: Path, snapshot: dict):
    """断言 source 目录未被修改（对比创建前的快照）"""

def get_head_commit(repo_path: Path) -> str:
    """获取仓库的 HEAD commit hash"""

def make_dirty(repo_path: Path):
    """在仓库中创建未提交的修改"""

def make_commit(repo_path: Path, message: str) -> str:
    """在仓库中创建一个 commit，返回 hash"""

def push_remote_update(remote_bare: Path):
    """向 bare repo push 新 commit，模拟远端更新"""

def sync_source_repo(source_dir: Path, repo_path: str):
    """更新 source 中指定子仓库到远端最新"""
```

### 测试文件结构

```
tests/
├── conftest.py              # pytest fixtures: repo_env, workspace_dir
├── helpers.py               # 验证辅助函数
├── test_scanner.py          # 7 个测试
├── test_worktree.py         # 10 个测试
├── test_layout.py           # 10 个测试
├── test_metadata.py         # 11 个测试
├── test_promote.py          # 14 个测试
├── test_sync.py             # 7 个测试
├── test_export.py           # 5 个测试
└── test_integration.py      # 13 个测试（对应 13 个场景）
```

共计 **77 个测试**。

### 运行方式

```bash
# 运行全部测试
python -m pytest tests/ -v

# 只运行单元测试
python -m pytest tests/ -v --ignore=tests/test_integration.py

# 只运行集成测试
python -m pytest tests/test_integration.py -v

# 运行特定场景
python -m pytest tests/test_integration.py::test_scenario_05_promote_parent -v
```
