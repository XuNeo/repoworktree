# Bug Tracker: repoworktree

已发现的 bug，按严重程度排序。每个 bug 包含：根因分析、复现步骤、修复计划、对应测试 case。

---

## BUG-001 [CRITICAL] teardown_workspace 静默吞异常导致 git 元数据孤儿

**状态**: ✅ 已修复  
**严重程度**: 数据丢失  
**已有测试**: `test_sibling_workspace_survives_corrupt_destroy`

### 根因

`layout.py:teardown_workspace` 在 `remove_worktree` 失败时 `except: pass` 静默吞掉，然后无论如何都执行 `shutil.rmtree(workspace)`：

```python
for source_repo, wt_path in worktree_paths:
    try:
        remove_worktree(source_repo, wt_path, force=True)
    except Exception:
        pass  # ← 静默失败

shutil.rmtree(workspace)  # ← 无论如何都删目录
```

结果：workspace 目录被删，但 source repo 的 `.git/worktrees/<name>/` 元数据还留着，里面的 `gitdir` 文件指向已删除的路径。

### 连锁反应

下次任何 `add_worktree` 碰到 "already registered" → 之前修复为 `git worktree remove --force <path>`，但如果 path 不存在，这个命令本身也会失败（check=False 所以会继续），然后 retry `git worktree add` 仍然报 "already registered"，最终抛出 `WorktreeError`。

更危险的情况：用户直接 `rm -rf workspace`（不通过 rwt），source repo 里留下大量孤儿引用，后续所有对这个 source repo 的 `add_worktree` 都会触发 "already registered" 错误。

### 复现步骤（待写测试）

```
1. rwt create /tmp/ws-A -w nuttx
2. 验证 source/.git/worktrees/ 里有 nuttx 的引用
3. rm -rf /tmp/ws-A  （不通过 rwt destroy）
4. rwt create /tmp/ws-A -w nuttx  （使用相同路径）
5. 预期：成功创建
6. 实际：可能报 WorktreeError "already registered worktree"
```

```
1. rwt create /tmp/ws-A -w nuttx
2. rwt create /tmp/ws-B -w nuttx
3. 在 ws-A/nuttx/.git 里故意写入错误内容，让 remove_worktree 失败
4. rwt destroy /tmp/ws-A
5. 验证 ws-B/nuttx git status 是否正常
6. 实际：ws-A 目录被删，source/.git/worktrees/nuttx_A 孤儿残留
```

### 修复计划

1. `teardown_workspace` 里 `remove_worktree` 失败时记录警告，**不继续 rmtree**（或提供 `--force` 选项）
2. 在 `add_worktree` 的 "already registered" 处理里，`git worktree remove --force` 失败后尝试手动清理 `.git/worktrees/<name>/` 目录

---

## BUG-002 [CRITICAL] 多 workspace 共享 source repo 时 destroy 破坏兄弟 workspace

**状态**: 部分修复（已删除 cmd_destroy fallback prune，已修复 add_worktree prune→targeted remove）  
**严重程度**: 数据丢失（用户工作区变成只读 source，无法 git status）  
**已有测试**: `test_add_worktree_prune_does_not_break_sibling`（覆盖 add_worktree 路径）

### 根因

`git worktree prune` 是全局操作，扫描整个 source repo 的 worktree 引用，凡是 gitdir 文件指向不存在目录的全部清除，不分 workspace。

之前代码两处调用：
1. `add_worktree` 遇到 "already registered" → **已修复**为 `git worktree remove --force <path>`
2. `cmd_destroy` fallback → **已删除**

### 残留问题

`teardown_workspace`（BUG-001）导致的孤儿引用，在下次 `add_worktree` 时仍然会触发 "already registered"，走到修复后的 `git worktree remove --force <path>`。但如果这个 `<path>` 已经不存在（rm -rf 删掉了），git 会报错，check=False 继续执行，retry add_worktree，**仍然报 "already registered"**，然后抛出 WorktreeError。

实际生产中的场景（已确认发生）：
- t113 workspace 的 `vendor/allwinnertech` worktree 引用被清掉
- `vendor/allwinnertech/.git` 文件指向不存在的 `worktrees/allwinnertech19`
- `git status` 报 `fatal: not a git repository`

### 复现步骤（待写测试）

```
1. ws1 = rwt create /tmp/ws1 -w nuttx
2. ws2 = rwt create /tmp/ws2 -w nuttx
3. rm -rf /tmp/ws1  （模拟意外删除，跳过 rwt destroy）
4. ws3 = rwt create /tmp/ws1 -w nuttx  （复用路径，触发 "already registered"）
5. 验证 ws2/nuttx git status 仍然正常
6. 实际：现有测试通过，但 step 4 可能 WorktreeError
```

---

## BUG-003 [HIGH] teardown_workspace 通过 trie 收集 worktree，而不是 metadata

**状态**: ✅ 已修复  
**严重程度**: 静默数据不一致  
**已有测试**: `test_destroy_with_corrupt_metadata_cleans_git_worktrees`

### 根因

`teardown_workspace` 接收 `trie` 参数，通过 `_collect_worktrees` 遍历 trie 来找 worktree。`cmd_destroy` 从 `.workspace.json` 重建 trie：

```python
# __main__.py cmd_destroy
trie = build_trie(all_repos, {w.path for w in meta.worktrees})
teardown_workspace(source_dir, ws_path, trie)
```

问题：`build_trie` 用的是 `scan_repos(source_dir)`（当前 source 状态）和 `meta.worktrees`（workspace 记录的）。如果 `.workspace.json` 损坏、或 source repo 发生了变化（repo sync 后路径改变），两者不一致，`teardown_workspace` 会漏掉部分 worktree，导致孤儿引用。

更安全的方式：直接用 `git worktree list` 从 source repo 侧枚举属于这个 workspace 的 worktree，通过路径前缀判断归属。

### 复现步骤（待写测试）

```
1. rwt create /tmp/ws -w nuttx
2. 手动修改 /tmp/ws/.workspace.json，删掉 nuttx 的 worktree 记录
3. rwt destroy /tmp/ws
4. 检查 source/.git/worktrees/ 里是否还有 nuttx 的孤儿引用
5. 实际：nuttx worktree 没有被 remove，孤儿引用残留
```

---

## BUG-004 [HIGH] add_worktree "already registered" 处理在 target_path 不存在时失效

**状态**: 未修复  
**严重程度**: WorktreeError，用户无法创建 workspace  
**已有测试**: 无

### 根因

修复后的重试逻辑：

```python
_git(["worktree", "remove", "--force", str(target_path)],
     cwd=source_repo, check=False)
try:
    _git(cmd, cwd=source_repo)  # retry add
    return
except subprocess.CalledProcessError as e2:
    raise WorktreeError(...)
```

当 target_path 不存在时（rm -rf 删掉的 workspace），`git worktree remove --force <path>` 报错（check=False 忽略），然后 retry `git worktree add` 仍然报 "already registered"，因为孤儿引用还没被清除。

正确做法：当 `git worktree remove --force` 失败且 target_path 不存在时，直接操作 source repo 的 `.git/worktrees/` 目录，找到指向 target_path 的引用并手动删除。

### 复现步骤（待写测试）

```
1. rwt create /tmp/ws -w nuttx  （创建成功）
2. rm -rf /tmp/ws               （模拟意外删除）
3. rwt create /tmp/ws -w nuttx  （同路径重新创建）
4. 预期：成功
5. 实际：WorktreeError "Failed to create worktree"
```

---

## BUG-005 [MEDIUM] promote() 中 rmtree 前无脏检查

**状态**: ✅ 已修复  
**严重程度**: 数据丢失  
**已有测试**: 已有 test_demote_dirty_rejected 模式，promote 侧待补充

### 根因

`promote.py:130`：

```python
elif target_ws.is_dir():
    if (target_ws / ".git").is_file():
        raise PromoteError(f"Already a worktree: {repo_path}")
    shutil.rmtree(target_ws)  # ← 没有检查目录里是否有未提交文件
```

当 `target_ws` 是从父 worktree checkout 出来的真实目录（inside_worktree 路径），如果用户在里面做了修改还没有提交，`promote` 会直接删掉整个目录。

### 复现步骤（待写测试）

```
1. rwt create /tmp/ws -w apps
2. echo "local change" >> /tmp/ws/apps/system/init.c
3. rwt promote apps/system/adb  （触发 _ensure_path_is_real → target_ws.is_dir()）
4. 验证 apps/system/init.c 是否还在
5. 实际：apps/system/ 可能被 rmtree
```

---

## 修复计划

按优先级排序：

### P0 — 已修复

| Bug | 修复方案 |
|-----|---------|
| BUG-001 | teardown 不再吞异常；remove 失败时拒绝 rmtree |
| BUG-002 | add_worktree 改用 targeted remove 替代 global prune |
| BUG-003 | teardown 改用 `git worktree list` 枚举，不依赖 metadata |
| BUG-004 | 实测已覆盖（targeted remove 处理了 path 不存在的情况） |
| BUG-005 | promote 在 rmtree 前检查 dirty；新增 `-f/--force` flag |
| BUG-006 | promote 临时删除 child worktree 前先检查 child 是否 dirty |
| BUG-007 | demote 临时删除 child worktree 前先检查 child 是否 dirty |
| BUG-008 | metadata load 加 try/except，损坏文件抛明确错误 |
| BUG-009 | metadata/index 写入改为原子操作（tmp + rename） |

### P2 — 后续

| Bug | 修复方案 |
|-----|---------|
| BUG-010 | add_worktree "already registered" 重试更健壮 |
| BUG-011 | sync 在 named branch worktree 上的行为 |

---

## 待补充的测试 Case

### 复现 BUG-001/004：rm -rf 后重建

```python
def test_create_after_accidental_rmrf(repo_env, tmp_path):
    """rm -rf workspace (bypassing rwt) then create again at same path must succeed."""
    ws = tmp_path / "workspace"
    paths = _create_workspace(repo_env, ws, wt_paths=["nuttx"])
    assert_is_worktree(ws / "nuttx")

    import shutil
    shutil.rmtree(ws)  # 模拟意外 rm -rf，不通过 rwt destroy

    # 重新创建同路径，必须成功
    paths2 = _create_workspace(repo_env, ws, wt_paths=["nuttx"])
    assert_is_worktree(ws / "nuttx")

    result = subprocess.run(["git", "status", "--porcelain"],
                            cwd=ws / "nuttx", capture_output=True, text=True)
    assert result.returncode == 0

    _destroy_workspace(repo_env, ws, paths2)
```

### 复现 BUG-001/002：destroy 失败后兄弟 workspace 仍正常

```python
def test_sibling_workspace_survives_corrupt_destroy(repo_env, tmp_path):
    """destroy with corrupted worktree must not break other workspaces."""
    ws1 = tmp_path / "ws1"
    ws2 = tmp_path / "ws2"

    paths = _create_workspace(repo_env, ws1, wt_paths=["nuttx"])
    _create_workspace(repo_env, ws2, wt_paths=["nuttx"])

    # 模拟 .git 文件损坏，导致 remove_worktree 失败
    (ws1 / "nuttx" / ".git").write_text("gitdir: /nonexistent/path")

    # destroy 应该报警告但不崩溃
    # 关键：ws2 的 worktree 必须还能正常工作
    try:
        _destroy_workspace(repo_env, ws1, paths)
    except Exception:
        pass

    result = subprocess.run(["git", "status", "--porcelain"],
                            cwd=ws2 / "nuttx", capture_output=True, text=True)
    assert result.returncode == 0, \
        f"ws2 git status broken after ws1 corrupt destroy: {result.stderr}"

    _destroy_workspace(repo_env, ws2, paths)
```

### 复现 BUG-003：metadata 损坏后 destroy 留下孤儿引用

```python
def test_destroy_with_corrupt_metadata_cleans_git_worktrees(repo_env, tmp_path):
    """destroy must clean git worktree refs even if .workspace.json is incomplete."""
    ws = tmp_path / "workspace"
    paths = _create_workspace(repo_env, ws, wt_paths=["nuttx", "apps"])

    # 从 metadata 中移除 apps 记录，模拟损坏
    import json
    meta_path = ws / ".workspace.json"
    data = json.loads(meta_path.read_text())
    data["worktrees"] = [w for w in data["worktrees"] if w["path"] != "apps"]
    meta_path.write_text(json.dumps(data))

    _destroy_workspace(repo_env, ws, paths)

    # source 里不应该留有 apps 的孤儿 worktree 引用
    source_apps = repo_env.source_dir / "apps"
    result = subprocess.run(["git", "worktree", "list", "--porcelain"],
                            cwd=source_apps, capture_output=True, text=True)
    worktree_paths = [l[len("worktree "):] for l in result.stdout.splitlines()
                      if l.startswith("worktree ")]
    orphans = [p for p in worktree_paths if str(ws) in p]
    assert not orphans, f"Orphan worktree refs remain after destroy: {orphans}"
```
