# Plan: 用 sparse-checkout 替换 skip-worktree

## 背景

### 问题

当父仓库（如 `apps`）是 worktree 且有子仓库（如 `apps/system/adb`）不是 worktree 时，子仓库目录被 symlink 替换。当前方案用 `skip-worktree` 压制这些"文件变更"的噪声，但同时也压制了用户对其他文件的真正修改，导致 `git status` / `git diff` 完全看不到变更。

用户不知道 `rwt` 的存在，也不应该需要知道。这是一个**透明性**问题。

### 方案

用 `git sparse-checkout` 替代手动 `skip-worktree`：

| | skip-worktree（当前） | sparse-checkout（新方案） |
|---|---|---|
| 原理 | 骗 git："这些文件没变" | 告诉 git："这些路径我不要" |
| 效果 | git 不检查标记文件的变更 | git 根本不检出被排除路径的文件 |
| 副作用 | 用户修改被一起压制 ❌ | 用户修改正常可见 ✅ |
| `git status` | 被压制文件不可见 | 正常工作 |
| `git diff` | 被压制文件不可见 | 正常工作 |
| 用户感知 | 需了解 rwt 机制 | **完全无感** |

### 前置条件

- Git ≥ 2.34（2021-11 发布，支持 per-worktree sparse-checkout）
- Python 3.10+（已有要求）

---

## 实现计划

### Phase 1: 核心替换

#### 1.1 新增 sparse-checkout 工具函数 (`layout.py`)

**文件**: `repoworktree/layout.py`

新增函数：

```python
def _setup_sparse_checkout(worktree_path: Path, exclude_paths: list[str]) -> None:
    """
    配置 sparse-checkout 排除指定路径。

    使用 non-cone 模式，写入排除规则：
      /*                    # 包含所有文件
      !/<path>/             # 排除每个子仓库路径

    然后 git read-tree -mu HEAD 应用配置。
    """

def _disable_sparse_checkout(worktree_path: Path) -> None:
    """禁用 sparse-checkout（当不再有需要排除的子仓库时）。"""

def _get_sparse_checkout_file(worktree_path: Path) -> Path:
    """获取 worktree 的 sparse-checkout 配置文件路径。
    
    对于 worktree，.git 是一个文件（内容为 gitdir: <path>），
    需要解析到实际的 git 目录。
    """
```

**关键实现细节**：

1. **获取 worktree 的 git 目录**：worktree 的 `.git` 是文件而非目录，内容为 `gitdir: /path/to/.git/worktrees/<name>`。需要解析这个路径来找到 `info/sparse-checkout` 文件的位置。

2. **sparse-checkout 规则格式**（non-cone 模式）：
   ```
   /*
   !/system/adb/
   !/system/core/
   ```
   - `/*` 包含所有顶层内容
   - `!/<path>/` 排除子仓库目录（尾部 `/` 确保只排除目录）

3. **中间路径处理**：如果子仓库是 `system/adb`，sparse-checkout 排除 `/system/adb/` 时仍然保留 `/system/` 目录及其直接文件——这正是我们需要的行为。

4. **应用配置**：写入规则文件后，运行 `git read-tree -mu HEAD` 让 git 移除被排除路径的文件。

#### 1.2 替换 `_exclude_child_repos` (`layout.py`)

**文件**: `repoworktree/layout.py`

**当前代码**（要替换）：
```python
def _exclude_child_repos(worktree_path: Path, trie_node: TrieNode) -> None:
    # 1. git ls-files -- <child_repo_paths>
    # 2. git update-index --skip-worktree --stdin
    # 3. 写 .gitignore
    # 4. git update-index --skip-worktree -- .gitignore
```

**新代码**：
```python
def _exclude_child_repos(worktree_path: Path, trie_node: TrieNode) -> None:
    """
    通过 sparse-checkout 排除非 worktree 的子仓库路径。

    子仓库文件不会出现在 working tree 中，之后用 symlink 覆盖。
    .gitignore 仅用于隐藏 symlink（untracked 内容）。
    不再使用 skip-worktree。
    """
    child_repo_paths, intermediate_paths = [], []
    _collect_non_worktree_repo_paths(trie_node, "", child_repo_paths, intermediate_paths)
    if not child_repo_paths and not intermediate_paths:
        return

    # 1. 设置 sparse-checkout 排除子仓库路径
    _setup_sparse_checkout(worktree_path, child_repo_paths)

    # 2. .gitignore 仍然需要——隐藏 symlink（untracked 内容）
    all_exclude_paths = child_repo_paths + intermediate_paths
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

    # 3. .gitignore 本身不再需要 skip-worktree
    #    因为它是我们新创建的文件（untracked），.gitignore 里已经有 /.gitignore 自我忽略
```

**关键变更**：
- 移除所有 `git update-index --skip-worktree` 调用
- 新增 `_setup_sparse_checkout()` 调用
- `.gitignore` 逻辑保留（仍需隐藏 symlink），但不再需要 skip-worktree .gitignore 本身
- `.gitignore` 是 untracked 文件（通过自身规则 `/.gitignore` 忽略自己）

#### 1.3 调整 `_build_level` 的 `inside_worktree` 分支 (`layout.py`)

**文件**: `repoworktree/layout.py`

**当前代码**（line 174-187）：
```python
elif inside_worktree:
    if child.is_repo and child_workspace.exists() and not child_workspace.is_symlink():
        import shutil
        shutil.rmtree(child_workspace)       # 删除 checkout 出来的目录
        child_workspace.symlink_to(child_source)
    elif child.is_repo and not child_workspace.exists():
        child_workspace.symlink_to(child_source)
```

**新代码**：
```python
elif inside_worktree:
    if child.is_repo and not child.is_worktree:
        # sparse-checkout 已经排除了这个路径，目录不存在
        # 直接创建 symlink
        if child_workspace.is_symlink():
            pass  # 已经是 symlink
        elif child_workspace.exists():
            # 不应该发生（sparse-checkout 应该已经移除了），但防御性处理
            import shutil
            shutil.rmtree(child_workspace)
            child_workspace.symlink_to(child_source)
        else:
            child_workspace.symlink_to(child_source)
    elif ...  # 其余分支不变
```

**关键变更**：
- 正常路径下不再需要 `shutil.rmtree`（sparse-checkout 已经排除了文件）
- 保留防御性 rmtree 以防 sparse-checkout 未生效
- `_exclude_child_repos` 的调用时序需要调整——先 sparse-checkout，再 symlink

**调用顺序调整**：

```python
# 之前（line 138-152）：
if child.children:
    _build_level(..., inside_worktree=True)  # 先递归创建 symlink
    _exclude_child_repos(child_workspace, child)  # 再设 skip-worktree

# 之后：
if child.children:
    _exclude_child_repos(child_workspace, child)  # 先 sparse-checkout（移除文件）
    _build_level(..., inside_worktree=True)  # 再递归创建 symlink（在空路径上）
```

#### 1.4 替换 `_rewrite_exclude` (`promote.py`)

**文件**: `repoworktree/promote.py`

**当前代码**（line 374-450）：
```python
def _rewrite_exclude(worktree_path, trie_node):
    # 1. git ls-files -v → 找 S 开头的行
    # 2. git update-index --no-skip-worktree --stdin  （清除旧标志）
    # 3. git ls-files -- <child_paths>
    # 4. git update-index --skip-worktree --stdin  （重新设置）
    # 5. 重写 .gitignore
    # 6. git update-index --skip-worktree -- .gitignore
```

**新代码**：
```python
def _rewrite_exclude(worktree_path, trie_node):
    """重建 sparse-checkout 和 .gitignore。"""
    child_repo_paths, intermediate_paths = [], []
    _collect_non_worktree_repo_paths(trie_node, "", child_repo_paths, intermediate_paths)

    if child_repo_paths:
        # 重新生成 sparse-checkout 规则
        _setup_sparse_checkout(worktree_path, child_repo_paths)
    else:
        # 没有需要排除的子仓库了，禁用 sparse-checkout
        _disable_sparse_checkout(worktree_path)

    # .gitignore 处理（与当前逻辑基本一致）
    all_exclude_paths = child_repo_paths + intermediate_paths
    gitignore = worktree_path / ".gitignore"
    if all_exclude_paths:
        lines = ["# Child repos managed by repoworktree"]
        for path in all_exclude_paths:
            lines.append(f"/{path}")
        lines.append("/.gitignore")
        gitignore.write_text("\n".join(lines) + "\n")
    elif gitignore.exists():
        gitignore.unlink()
```

**关键变更**：
- 移除所有 `git update-index --no-skip-worktree` / `--skip-worktree` 调用
- 改用 `_setup_sparse_checkout()` / `_disable_sparse_checkout()`
- `.gitignore` 不再需要 skip-worktree 管理
- `_refresh_ancestor_excludes()` 无需修改（它调用 `_rewrite_exclude`）

#### 1.5 更新 import 和清理 (`promote.py`)

**文件**: `repoworktree/promote.py`

- 在 `from repoworktree.layout import _exclude_child_repos` 之外，新增导入 `_setup_sparse_checkout`, `_disable_sparse_checkout`
- `_handle_non_worktree_child_repos()` 无需修改（它调用 `_exclude_child_repos`）

### Phase 2: 边界情况处理

#### 2.1 Git 版本检查

**文件**: `repoworktree/layout.py` 或 `repoworktree/worktree.py`

```python
def check_git_version():
    """确保 git 版本 ≥ 2.34（per-worktree sparse-checkout 支持）。"""
    result = subprocess.run(["git", "--version"], capture_output=True, text=True)
    # 解析版本号并比较
    # 低于 2.34 时抛出明确错误
```

**调用位置**: `build_workspace()` 和 `promote()` 入口。

#### 2.2 teardown 清理 sparse-checkout

**文件**: `repoworktree/layout.py`

`teardown_workspace()` 不需要额外修改——`git worktree remove` 会自动清理 worktree 的 sparse-checkout 配置（因为配置存储在 worktree 专用的 git 目录中）。

#### 2.3 `_handle_non_worktree_child_repos` 时序 (`promote.py`)

**文件**: `repoworktree/promote.py`

当前函数在 promote 后处理非 worktree 子仓库：
1. 遍历子仓库 → symlink 替换
2. 调用 `_exclude_child_repos`

新方案下时序变化：
1. 先调用 `_exclude_child_repos`（设置 sparse-checkout，移除文件）
2. 遍历子仓库 → 在空路径上创建 symlink

### Phase 3: 迁移兼容

#### 3.1 现有 workspace 的迁移

已创建的 workspace 仍然使用 skip-worktree。有两种策略：

**策略 A（推荐）：自动迁移**
- 在 `_rewrite_exclude` 入口检查是否有 skip-worktree 标志
- 如果有，先清除所有 skip-worktree，再设置 sparse-checkout
- promote/demote 操作时自然触发迁移

**策略 B：不兼容**
- 新版本不处理旧 workspace 的 skip-worktree
- 用户需要 `rwt destroy` + `rwt create` 重建

建议：先实现策略 B（简单），后续按需添加策略 A。

---

## 测试计划

### 新增测试

#### `tests/test_sparse_checkout.py`（新文件）

| 测试 | 验证内容 | 优先级 |
|------|----------|--------|
| `test_sparse_checkout_excludes_child_files` | 创建父 worktree 后，被排除子仓库路径下的文件不存在于 working tree | P0 |
| `test_sparse_checkout_preserves_parent_files` | 父仓库自身的文件（非子仓库路径下）正常存在 | P0 |
| `test_user_edit_visible_in_git_status` | 用户修改父仓库文件 → `git status` 正常显示 | P0 |
| `test_user_edit_visible_in_git_diff` | 用户修改父仓库文件 → `git diff` 正常显示 | P0 |
| `test_child_symlink_invisible_to_git` | 子仓库 symlink 不出现在 `git status`（被 .gitignore 隐藏） | P0 |
| `test_sparse_checkout_after_promote_child` | promote 子仓库后，父 worktree 的 sparse-checkout 规则更新（不再排除该子仓库） | P1 |
| `test_sparse_checkout_after_demote_child` | demote 子仓库后，父 worktree 的 sparse-checkout 规则更新（重新排除该子仓库） | P1 |
| `test_sparse_checkout_disable_when_no_children` | 所有子仓库都被 promote 后，sparse-checkout 被禁用 | P1 |
| `test_sparse_checkout_multiple_children` | 多个子仓库被排除时，sparse-checkout 规则正确 | P1 |
| `test_sparse_checkout_git_dir_resolution` | worktree 的 .git 文件正确解析到实际 git 目录 | P2 |
| `test_git_merge_with_sparse_checkout` | sparse-checkout 下 git merge 正常工作（被排除文件在 index 中正常合并） | P2 |

#### 修改现有测试

| 文件 | 变更 |
|------|------|
| `test_promote.py` | `test_create_worktree_sibling_files_visible_in_git_status` — 核心验证不变，但内部机制从 skip-worktree 改为 sparse-checkout |
| `test_promote.py` | `test_promote_worktree_sibling_files_visible_in_git_status` — 同上 |
| `test_promote.py` | `test_promote_child_updates_parent_exclude` — 验证 .gitignore 更新，但不再验证 skip-worktree 标志 |
| `test_promote.py` | 所有涉及 `skip-worktree` 关键字的测试注释/描述需要更新 |

### 测试场景验证矩阵

```
场景                              | git status | git diff | 编译  | 预期
---------------------------------|-----------|---------|------|--------
父 worktree, 改父仓库文件          | ✅ 可见    | ✅ 可见  | ✅    | 全部正常
父 worktree, 改子仓库路径下文件     | N/A       | N/A     | N/A  | 文件不存在（被 sparse-checkout 排除）
子仓库 symlink                    | 不可见     | 不可见   | ✅    | 被 .gitignore 隐藏
promote 子仓库后，改父仓库文件      | ✅ 可见    | ✅ 可见  | ✅    | sparse-checkout 规则更新
demote 子仓库后，子仓库文件状态     | 不可见     | 不可见   | ✅    | sparse-checkout 重新排除
```

---

## 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| Git 版本过低（< 2.34） | 低 | 功能不可用 | 入口检查版本，明确报错 |
| 用户手动 `git sparse-checkout disable` | 低 | 子仓库文件重现，symlink 冲突 | `rwt status` 检测并修复 |
| non-cone 模式性能 | 低 | 大量文件时 status 略慢 | 子仓库路径通常少于 20 个，影响可忽略 |
| merge/rebase 行为 | 低 | 排除路径的文件在 index 中正常合并 | Git 原生支持，无额外处理 |

---

## 代码变更清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `repoworktree/layout.py` | 修改 | 新增 sparse-checkout 函数；替换 `_exclude_child_repos`；调整 `_build_level` 时序 |
| `repoworktree/promote.py` | 修改 | 替换 `_rewrite_exclude`；更新 import |
| `repoworktree/worktree.py` | 修改 | 新增 `check_git_version()` |
| `tests/test_sparse_checkout.py` | 新增 | 11 个 sparse-checkout 专用测试 |
| `tests/test_promote.py` | 修改 | 更新已有测试的描述和预期 |
| `CLAUDE.md` | 修改 | 更新架构说明 |
| `README.md` | 修改 | 更新技术说明 |

**预计工作量**: 1-2 天

**不变更的文件**: `scanner.py`, `metadata.py`, `sync.py`, `export.py`, `__main__.py`, `conftest.py`, `helpers.py`
