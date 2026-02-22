# Requirements: Repo Workspace

## Overview

为 repo（Google repo/Gerrit）管理的多仓库项目提供隔离的并行工作空间能力，核心目标是让多个 LLM agent 能够同时在同一代码库上独立开发，互不干扰，也不影响开发者的主工作目录。

### 背景与约束

- 当前项目（Vela）由 repo 管理，包含 **489 个子仓库**，工作目录 95GB，`.repo` 目录 69GB
- 构建系统依赖 `source build/envsetup.sh` + `lunch` + `m`，假设特定的目录结构
- 典型的 LLM 开发任务只涉及 2~5 个子仓库（如 nuttx、apps、applications、某个 framework）
- 为所有 489 个子仓库创建完整 worktree 既浪费磁盘又浪费时间
- `repo` 原生不支持创建并行工作目录，`repo init --worktree` 仅是内部存储优化
- workspace 中需要能执行构建、测试等操作，不能只是代码的只读副本

---

## User Stories

### US-1: 创建隔离工作空间

**As a** LLM agent 的使用者，**I want** 基于当前 repo checkout 快速创建一个隔离的工作空间，**So that** agent 可以在其中自由修改代码而不影响主工作目录或其他 agent 的工作空间。

#### Acceptance Criteria

- [ ] **AC-1.1**: Given 一个 repo 管理的项目目录，when 执行创建工作空间命令，then 在指定路径生成一个包含完整目录结构的工作空间，其中需要修改的子仓库使用 git worktree，其余子仓库使用轻量级引用（symlink/bind mount/共享）
- [ ] **AC-1.2**: Given 创建命令指定了需要修改的子仓库列表（如 `nuttx,apps,applications`），when 工作空间创建完成，then 只有这些子仓库是真正的 git worktree（可写、可 commit），其余子仓库为只读引用
- [ ] **AC-1.3**: Given 创建命令未指定子仓库列表，when 工作空间创建完成，then 所有子仓库默认使用只读引用，用户可以后续按需将特定子仓库"提升"为可写 worktree
- [ ] **AC-1.4**: Given 工作空间创建完成，when 在工作空间中执行 `source build/envsetup.sh && lunch <target> && m`，then 构建系统能正常工作
- [ ] **AC-1.5**: Given 创建 489 个子仓库的工作空间，when 只有 3 个子仓库需要 worktree，then 创建时间应在 **60 秒以内**，额外磁盘占用应远小于完整 checkout 的大小

### US-2: 动态提升/降级子仓库

**As a** LLM agent，**I want** 在工作过程中将某个只读引用的子仓库动态提升为可写的 git worktree，**So that** 我可以根据实际需要灵活扩展修改范围，而不必在创建时就预知所有需要修改的仓库。

#### Acceptance Criteria

- [ ] **AC-2.1**: Given 工作空间中某个子仓库当前是只读引用，when 执行提升命令，then 该子仓库被替换为 git worktree，之前的只读引用被移除
- [ ] **AC-2.2**: Given 工作空间中某个子仓库是 git worktree 且没有未提交的修改，when 执行降级命令，then 该子仓库被替换回只读引用，worktree 被清理
- [ ] **AC-2.3**: Given 工作空间中某个子仓库是 git worktree 且有未提交的修改，when 执行降级命令，then 操作被拒绝并提示用户先处理修改

### US-3: 同步更新工作空间

**As a** 开发者，**I want** 在主仓库 `repo sync` 之后，能够选择性地将工作空间中未修改的子仓库更新到最新版本，**So that** 工作空间不会因为长期不更新而与主线产生过大偏差。

#### Acceptance Criteria

- [ ] **AC-3.1**: Given 主仓库已执行 `repo sync`，when 对工作空间执行同步命令，then 所有只读引用的子仓库自动指向最新版本（因为它们本身就是对主仓库的引用）
- [ ] **AC-3.2**: Given 工作空间中某个 worktree 子仓库没有本地修改也没有本地 commit，when 执行同步命令，then 该子仓库自动更新到主仓库对应的最新 commit
- [ ] **AC-3.3**: Given 工作空间中某个 worktree 子仓库有本地 commit 或本地修改，when 执行同步命令，then 该子仓库保持不变，并在同步报告中标记为"已跳过（有本地变更）"
- [ ] **AC-3.4**: Given 同步命令带有 `--rebase` 选项，when 某个 worktree 子仓库有本地 commit，then 尝试将本地 commit rebase 到最新版本上，冲突时中止并报告
- [ ] **AC-3.5**: Given 用户通过 pin 机制锁定了某个子仓库的版本，when 执行同步命令，then 该子仓库无论是否有修改都保持不变

### US-4: 多 Agent 并行工作

**As a** 需要同时运行多个 LLM agent 的开发者，**I want** 能够创建多个独立的工作空间，每个 agent 使用各自的工作空间，**So that** 多个 agent 可以同时修改同一个子仓库的不同分支而互不干扰。

#### Acceptance Criteria

- [ ] **AC-4.1**: Given 已存在工作空间 A，when 创建工作空间 B 且两者都需要修改同一个子仓库（如 nuttx），then 两个工作空间各自拥有独立的 git worktree，互不影响
- [ ] **AC-4.2**: Given 多个工作空间同时存在，when 执行列表命令，then 显示所有工作空间的状态概览（路径、可写子仓库数、修改状态、创建时间）
- [ ] **AC-4.3**: Given 某个工作空间不再需要，when 执行销毁命令，then 该工作空间的所有 worktree 被清理，只读引用被移除，不影响其他工作空间和主工作目录

### US-5: 工作空间中的变更管理

**As a** LLM agent 或开发者，**I want** 能够方便地查看、提交、导出工作空间中的变更，**So that** 工作空间中的开发成果可以被 review 和合并到主线。

#### Acceptance Criteria

- [ ] **AC-5.1**: Given 工作空间中有多个子仓库被修改，when 执行状态命令，then 汇总显示所有子仓库的 git status（哪些有未暂存修改、未提交修改、本地 commit）
- [ ] **AC-5.2**: Given 工作空间中某些子仓库有本地 commit，when 执行导出命令，then 生成可以在主工作目录中应用的 patch set（每个子仓库一个 patch 文件）或提供 cherry-pick 指引
- [ ] **AC-5.3**: Given 工作空间中的修改已经完成并提交，when 需要上传到 Gerrit review，then 能够在工作空间中直接执行 `repo upload` 或等效操作，或者提供明确的操作指引

### US-6: 版本锁定与分支管理

**As a** 开发者**I want** 能够将工作空间中的特定子仓库锁定到指定的 commit/tag/branch，**So that** 我可以在稳定的基线上开发，不受主线更新的影响。

#### Acceptance Criteria

- [ ] **AC-6.1**: Given 创建工作空间时指定了某个子仓库的目标版本（commit hash/tag/branch），when 工作空间创建完成，then 该子仓库的 worktree checkout 到指定版本
- [ ] **AC-6.2**: Given 工作空间中某个子仓库已被 pin 到特定版本，when 执行同步命令，then 该子仓库不会被更新
- [ ] **AC-6.3**: Given 工作空间中某个子仓库已被 pin，when 执行 unpin 命令，then 该子仓库恢复为跟随主仓库版本

### US-7: 构建环境兼容

**As a** 在工作空间中工作的 LLM agent，**I want** 工作空间的目录结构与主工作目录完全一致，**So that** 所有构建脚本、路径引用、相对路径都能正常工作，无需任何适配。

#### Acceptance Criteria

- [ ] **AC-7.1**: Given 工作空间已创建，when 查看工作空间的目录结构，then 与主工作目录的目录树完全一致（相同的子目录名和层级）
- [ ] **AC-7.2**: Given 工作空间中混合了 worktree 和只读引用，when 构建系统遍历目录树，then 无法区分两者的差异（对构建系统透明）
- [ ] **AC-7.3**: Given 工作空间已创建，when 执行 `source build/envsetup.sh`，then envsetup.sh 能正确识别工作空间根目录为项目根目录

---

## Non-Functional Requirements

### NFR-1: 性能
- 创建只含 3 个 worktree 子仓库的工作空间应在 60 秒内完成
- 同步操作应在 30 秒内完成（不含网络操作）
- 列表/状态命令应在 5 秒内返回

### NFR-2: 磁盘效率
- 只读引用的子仓库不应产生额外的磁盘占用（使用 symlink 或类似机制）
- git worktree 共享 object store，不重复存储 git 对象

### NFR-3: 可靠性
- 创建/销毁操作应是原子的——失败时自动回滚，不留下半成品
- 不应破坏主工作目录或 `.repo/` 的任何状态

### NFR-4: 兼容性
- 支持 Linux（主要平台）
- 兼容 git >= 2.20
- 兼容 repo v2.x
- 不依赖 root 权限

### NFR-5: 可观测性
- 所有操作应有清晰的进度输出
- 错误信息应明确指出问题原因和修复建议

---

## Decisions (Resolved Open Questions)

1. **只读引用的实现方式**：使用 symlink。需在设计阶段验证 `build/envsetup.sh` 是否用 `realpath`/`pwd -P` 解析路径，如有问题则对 `build/` 等少量关键目录做特殊处理（拷贝或 worktree）
2. **`.repo/` 目录的处理**：工作空间中不需要 `.repo/` 目录。各子仓库单独 push，不依赖 `repo upload`
3. **prebuilts 目录的处理**：始终使用只读引用（symlink），不创建 worktree
4. **并发安全**：不考虑。实际使用中不会对同一子仓库同时创建 worktree
