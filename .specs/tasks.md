# Tasks: Repo Workspace

## Task List

- [ ] **Task 1**: 测试基础设施
  - **Objective**: 创建测试环境搭建脚本：`conftest.py`（session 级 fixture，自动创建 12 个 bare repo + manifest + repo init + repo sync）和 `helpers.py`（验证辅助函数）
  - **Files**: `tests/conftest.py`, `tests/helpers.py`
  - **Acceptance**: `pytest --co` 能发现 fixture；`repo_env` fixture 成功创建包含 12 个子仓库的本地 repo 环境（含嵌套：apps/system/adb, frameworks/system/core 等）；`workspace_dir` fixture 提供独立目录并自动清理

- [ ] **Task 2**: 项目骨架与 CLI 入口
  - **Objective**: 创建 `rw/` 包结构，用 argparse 定义所有 10 个子命令及其参数（create, destroy, list, status, promote, demote, sync, pin, unpin, export），handler 暂为 stub
  - **Files**: `rw/__main__.py`
  - **Acceptance**: `python -m rw --help` 显示所有子命令；每个子命令的 `--help` 显示完整参数列表，与 design.md CLI 定义一致

- [ ] **Task 3**: Repo Scanner + 单元测试
  - **Depends on**: Task 1
  - **Objective**: 从 `.repo/project.list` 解析子仓库路径，构建路径前缀树（trie），支持 `has_worktree_descendant` 查询。编写 7 个单元测试
  - **Files**: `rw/scanner.py`, `tests/test_scanner.py`
  - **Acceptance**: 测试 repo 环境中正确解析 12 个子仓库；前缀树标记 worktree 后代正确；7 个测试全部通过

- [ ] **Task 4**: Metadata 读写 + 单元测试
  - **Depends on**: Task 1
  - **Objective**: 实现 `.workspace.json` 和 `.workspaces.json` 的 CRUD，包括注册/注销、按名查找、自动检测。编写 11 个单元测试
  - **Files**: `rw/metadata.py`, `tests/test_metadata.py`
  - **Acceptance**: JSON schema 与 design.md D5 一致；11 个测试全部通过

- [ ] **Task 5**: Git Worktree 封装 + 单元测试
  - **Depends on**: Task 1
  - **Objective**: 封装 `git worktree add/remove/list`，支持 detached HEAD、命名分支、指定版本、dirty 检测、local commit 检测。编写 10 个单元测试
  - **Files**: `rw/worktree.py`, `tests/test_worktree.py`
  - **Acceptance**: detached/branch/pinned 三种模式创建正确；dirty/force 检测正确；10 个测试全部通过

- [ ] **Task 6**: Layout Engine + 单元测试
  - **Depends on**: Task 3, Task 5
  - **Objective**: 实现 TreeBuilder 核心算法：递归构建 symlink + worktree + 真实目录的混合目录树，处理顶层文件，支持父子仓库同时 worktree。编写 10 个单元测试
  - **Files**: `rw/layout.py`, `tests/test_layout.py`
  - **Acceptance**: 全 symlink（极端 A）、全 worktree（极端 B）、嵌套拆解、父子共存、顶层文件处理均正确；10 个测试全部通过

- [ ] **Task 7**: `rw create` 与 `rw destroy`
  - **Depends on**: Task 2, Task 4, Task 6
  - **Objective**: 串联 scanner → layout → metadata，实现 create（含原子性：先 .tmp 再 rename）和 destroy（先 worktree remove 再删目录再更新索引）
  - **Files**: `rw/__main__.py`（补充 handler）
  - **Acceptance**: `rw create <path> -w nuttx,apps` 成功创建；`rw destroy <path>` 清理干净；创建失败时自动回滚；source 目录未受影响

- [ ] **Task 8**: Promote / Demote + 单元测试
  - **Depends on**: Task 4, Task 5, Task 6
  - **Objective**: 实现 promote（symlink 拆解 + worktree 创建，含父子仓库场景）和 demote（worktree 移除 + symlink 重建 + 向上合并）。编写 14 个单元测试
  - **Files**: `rw/promote.py`, `tests/test_promote.py`
  - **Acceptance**: 顶层 promote、嵌套拆解、父子共存 promote/demote、向上合并均正确；14 个测试全部通过

- [ ] **Task 9**: Sync + 单元测试
  - **Depends on**: Task 4, Task 5
  - **Objective**: 实现 sync 命令，处理 clean/dirty/pinned/local-commit 四种状态，支持 --rebase。编写 7 个单元测试
  - **Files**: `rw/sync.py`, `tests/test_sync.py`
  - **Acceptance**: clean 更新、dirty 跳过、pinned 跳过、rebase 正确；remote 更新后 sync 能同步；7 个测试全部通过

- [ ] **Task 10**: Pin/Unpin、Status/List
  - **Depends on**: Task 4, Task 5
  - **Objective**: 实现 pin/unpin（更新元数据 + checkout）、status（汇总各 worktree 的 git status）、list（列出所有工作空间，支持 --json）
  - **Files**: `rw/__main__.py`（补充 handler）
  - **Acceptance**: pin 后 sync 不更新；status 正确显示修改/commit/pinned 状态；list 显示所有工作空间

- [ ] **Task 11**: Export + 单元测试
  - **Depends on**: Task 4
  - **Objective**: 实现 export 命令，支持 patch（git format-patch）和 bundle（git bundle）两种格式。编写 5 个单元测试
  - **Files**: `rw/export.py`, `tests/test_export.py`
  - **Acceptance**: 有 commit 的 worktree 导出 patch/bundle；无 commit 时报告 nothing to export；patch 能在 source 中 git am 成功；5 个测试全部通过

- [ ] **Task 12**: 集成测试（13 个场景）
  - **Depends on**: Task 7, Task 8, Task 9, Task 10, Task 11
  - **Objective**: 编写 13 个端到端集成测试，对应 design.md 中的 13 个使用场景，每个测试走完 create → 操作 → 验证 → destroy 全流程
  - **Files**: `tests/test_integration.py`
  - **Acceptance**: 13 个场景测试全部通过；每个测试验证目录结构、symlink 目标、worktree 状态、metadata 正确性；source 目录在所有测试后未受影响
