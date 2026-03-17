"""
Pytest fixtures for repo-workspace tests.

Creates a real repo-managed test environment with 13 sub-repos,
including nested repos (apps/system/adb, frameworks/system/core, etc.).
Uses local bare git repos as remotes.
"""

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


# Sub-repo definitions: (bare_repo_name, checkout_path, initial_files)
REPO_DEFS = [
    (
        "nuttx",
        "nuttx",
        {
            "README.md": "# NuttX RTOS",
            "tools/Unix.mk": "# makefile",
            "fs/vfs.c": "// vfs",
            "fs/inode.c": "// inode",
            "drivers/note/note_driver.c": "// note driver",
        },
    ),
    (
        "nuttx-fs-fatfs",
        "nuttx/fs/fatfs",
        {"README.md": "# FatFS", "fatfs.c": "// fatfs"},
    ),
    (
        "apps",
        "apps",
        {
            "README.md": "# Apps",
            "Makefile": "# apps makefile",
            "system/init.c": "// init",
        },
    ),
    ("apps-system-adb", "apps/system/adb", {"README.md": "# ADB", "adb.c": "// adb"}),
    (
        "apps-system-core",
        "apps/system/core",
        {"README.md": "# Core", "core.c": "// core"},
    ),
    (
        "build",
        "build",
        {"envsetup.sh": "#!/bin/bash\n# envsetup", "Makefile": "# build"},
    ),
    ("frameworks", "frameworks", {"README.md": "# Frameworks"}),
    ("frameworks-system", "frameworks/system", {"README.md": "# System"}),
    (
        "frameworks-system-core",
        "frameworks/system/core",
        {"README.md": "# FW Core", "core.c": "// fw core"},
    ),
    (
        "frameworks-system-kvdb",
        "frameworks/system/kvdb",
        {"README.md": "# KVDB", "kvdb.c": "// kvdb"},
    ),
    (
        "frameworks-connectivity",
        "frameworks/connectivity",
        {"README.md": "# Connectivity", "bt.c": "// bt"},
    ),
    (
        "external-lib-a",
        "external/lib-a",
        {"README.md": "# Lib A", "lib_a.c": "// lib a"},
    ),
    (
        "external-lib-b",
        "external/lib-b",
        {"README.md": "# Lib B", "lib_b.c": "// lib b"},
    ),
]

# Top-level symlink files to create in source (simulating real vela project)
TOP_LEVEL_SYMLINKS = {
    "build.sh": "nuttx/tools/Unix.mk",  # simulates build.sh -> nuttx/tools/build.sh
}

# Top-level config files
TOP_LEVEL_CONFIGS = {
    "CLAUDE.md": "# Project Rules\n",
}


def _run(cmd, cwd=None, check=True, env=None):
    """Run a command, return CompletedProcess."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        env=merged_env,
    )


def _git(args, cwd, env=None):
    """Run a git command."""
    return _run(["git"] + args, cwd=cwd, env=env)


def _create_bare_repo(bare_path: Path, files: dict, branch="master"):
    """Create a bare git repo with initial commit containing given files."""
    bare_path.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "--bare", str(bare_path)])

    # Use a temp working dir to create initial commit
    tmp_work = bare_path.parent / f"_tmp_{bare_path.name}"
    tmp_work.mkdir(parents=True, exist_ok=True)
    try:
        _run(["git", "init", str(tmp_work)])
        _git(["config", "user.email", "test@test.com"], cwd=tmp_work)
        _git(["config", "user.name", "Test"], cwd=tmp_work)

        for rel_path, content in files.items():
            fpath = tmp_work / rel_path
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content)

        _git(["add", "-A"], cwd=tmp_work)
        _git(["commit", "-m", "Initial commit"], cwd=tmp_work)
        _git(["remote", "add", "origin", str(bare_path)], cwd=tmp_work)
        _git(["push", "origin", f"HEAD:refs/heads/{branch}"], cwd=tmp_work)
    finally:
        shutil.rmtree(tmp_work, ignore_errors=True)


def _create_manifest_repo(bare_path: Path, remotes_dir: Path, branch="master"):
    """Create the manifest bare repo with default.xml."""
    manifest_xml = textwrap.dedent(
        f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <manifest>
          <remote name="local" fetch="{remotes_dir}" />
          <default revision="{branch}" remote="local" sync-j="4" />

    """
    )
    for bare_name, checkout_path, _ in REPO_DEFS:
        manifest_xml += f'      <project name="{bare_name}" path="{checkout_path}" />\n'
    manifest_xml += "    </manifest>\n"

    bare_path.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "--bare", str(bare_path)])

    tmp_work = bare_path.parent / "_tmp_manifest"
    tmp_work.mkdir(parents=True, exist_ok=True)
    try:
        _run(["git", "init", str(tmp_work)])
        _git(["config", "user.email", "test@test.com"], cwd=tmp_work)
        _git(["config", "user.name", "Test"], cwd=tmp_work)
        (tmp_work / "default.xml").write_text(manifest_xml)
        _git(["add", "default.xml"], cwd=tmp_work)
        _git(["commit", "-m", "Add manifest"], cwd=tmp_work)
        _git(["remote", "add", "origin", str(bare_path)], cwd=tmp_work)
        _git(["push", "origin", f"HEAD:refs/heads/{branch}"], cwd=tmp_work)
    finally:
        shutil.rmtree(tmp_work, ignore_errors=True)


class RepoTestEnv:
    """Holds paths and metadata for the test repo environment."""

    def __init__(self, base_dir: Path, remotes_dir: Path, source_dir: Path):
        self.base_dir = base_dir
        self.remotes_dir = remotes_dir
        self.source_dir = source_dir
        self.repo_defs = REPO_DEFS

    @property
    def all_repo_paths(self) -> list[str]:
        """All sub-repo checkout paths, sorted."""
        return sorted(path for _, path, _ in REPO_DEFS)

    def bare_repo_path(self, bare_name: str) -> Path:
        """Get the path to a bare repo by its name."""
        return self.remotes_dir / f"{bare_name}.git"

    def source_repo_path(self, checkout_path: str) -> Path:
        """Get the path to a checked-out repo in source."""
        return self.source_dir / checkout_path

    def new_workspace_path(self, name: str = "ws") -> Path:
        """Get a fresh workspace path under base_dir."""
        ws_dir = self.base_dir / "workspaces" / name
        ws_dir.parent.mkdir(parents=True, exist_ok=True)
        return ws_dir


@pytest.fixture(scope="session")
def repo_env(tmp_path_factory) -> RepoTestEnv:
    """
    Create a complete repo test environment. Session-scoped (shared by all tests).

    Creates:
    - 12 bare git repos as remotes
    - 1 manifest bare repo
    - 1 source directory via repo init + repo sync
    - Top-level symlinks and config files in source
    """
    base_dir = tmp_path_factory.mktemp("rw-test")
    remotes_dir = base_dir / "remotes"
    source_dir = base_dir / "source"
    remotes_dir.mkdir()
    source_dir.mkdir()

    # Create bare repos
    for bare_name, _, files in REPO_DEFS:
        _create_bare_repo(remotes_dir / f"{bare_name}.git", files)

    # Create manifest repo
    _create_manifest_repo(remotes_dir / "manifest.git", remotes_dir)

    # repo init + sync
    _run(
        [
            "repo",
            "init",
            "-u",
            str(remotes_dir / "manifest.git"),
            "-b",
            "master",
            "--no-repo-verify",
        ],
        cwd=source_dir,
    )
    _run(
        ["repo", "sync", "--no-repo-verify", "--no-clone-bundle"],
        cwd=source_dir,
    )

    # Create top-level symlinks
    for link_name, target in TOP_LEVEL_SYMLINKS.items():
        (source_dir / link_name).symlink_to(target)

    # Create top-level config files
    for fname, content in TOP_LEVEL_CONFIGS.items():
        (source_dir / fname).write_text(content)

    return RepoTestEnv(base_dir, remotes_dir, source_dir)


@pytest.fixture
def workspace_dir(repo_env: RepoTestEnv, tmp_path) -> Path:
    """
    Provide a fresh workspace directory for each test.
    Automatically cleans up git worktrees on teardown.
    """
    ws_dir = tmp_path / "workspace"

    yield ws_dir

    # Cleanup: remove any git worktrees that were created
    if ws_dir.exists():
        # Find all .git files (worktree markers) and remove them properly
        try:
            git_files = list(ws_dir.rglob(".git"))
        except (FileNotFoundError, PermissionError):
            git_files = []
        for git_file in git_files:
            if git_file.is_file():
                # This is a worktree - read the gitdir to find the source repo
                content = git_file.read_text().strip()
                if content.startswith("gitdir:"):
                    gitdir = content[len("gitdir:") :].strip()
                    # Find the main repo and remove the worktree
                    worktree_path = git_file.parent
                    try:
                        _git(
                            ["worktree", "remove", "--force", str(worktree_path)],
                            cwd=worktree_path,
                        )
                    except (subprocess.CalledProcessError, FileNotFoundError):
                        pass

        # Force remove the directory if it still exists
        shutil.rmtree(ws_dir, ignore_errors=True)

    # Also clean up any .workspaces.json that might have been created in source
    ws_index = repo_env.source_dir / ".workspaces.json"
    if ws_index.exists():
        ws_index.unlink()
