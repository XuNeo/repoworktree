"""Smoke test to verify the test infrastructure works."""

from pathlib import Path


def test_repo_env_created(repo_env):
    """Verify the repo test environment was created successfully."""
    assert repo_env.source_dir.exists()
    assert (repo_env.source_dir / ".repo").is_dir()
    assert (repo_env.source_dir / ".repo" / "project.list").exists()


def test_repo_env_has_all_repos(repo_env):
    """Verify all 13 sub-repos are checked out."""
    project_list = (repo_env.source_dir / ".repo" / "project.list").read_text()
    paths = sorted(line.strip() for line in project_list.strip().splitlines() if line.strip())
    assert len(paths) == 13
    assert paths == repo_env.all_repo_paths


def test_repo_env_nested_repos(repo_env):
    """Verify nested repos exist and are independent git repos."""
    # apps/ and apps/system/adb/ should both exist
    apps = repo_env.source_dir / "apps"
    adb = repo_env.source_dir / "apps" / "system" / "adb"
    assert apps.is_dir()
    assert adb.is_dir()
    assert (adb / "adb.c").exists()

    # frameworks/system/core/ should exist
    fw_core = repo_env.source_dir / "frameworks" / "system" / "core"
    assert fw_core.is_dir()
    assert (fw_core / "core.c").exists()


def test_repo_env_top_level_files(repo_env):
    """Verify top-level symlinks and config files."""
    # build.sh should be a symlink
    build_sh = repo_env.source_dir / "build.sh"
    assert build_sh.is_symlink()

    # CLAUDE.md should be a regular file
    claude_md = repo_env.source_dir / "CLAUDE.md"
    assert claude_md.is_file()
    assert not claude_md.is_symlink()


def test_workspace_dir_fixture(repo_env, workspace_dir):
    """Verify workspace_dir fixture provides a usable path."""
    assert isinstance(workspace_dir, Path)
    assert not workspace_dir.exists()  # Should not exist yet
    # Should be writable
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "test.txt").write_text("hello")
    assert (workspace_dir / "test.txt").read_text() == "hello"
