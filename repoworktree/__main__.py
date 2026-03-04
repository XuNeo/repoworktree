"""
repoworktree (rwt) — CLI tool for creating isolated workspaces
in repo-managed multi-repository projects using git worktree + symlink.

Usage: rwt <command> [options]
"""

import argparse
import shutil
import sys
from pathlib import Path

from repoworktree.scanner import scan_repos, build_trie
from repoworktree.metadata import (
    WorktreeEntry,
    create_workspace_metadata,
    save_workspace_metadata,
    load_workspace_metadata,
    load_workspace_index,
    save_workspace_index,
    detect_workspace,
)
from repoworktree.layout import build_workspace, teardown_workspace
from repoworktree.worktree import remove_worktree, get_head


def _find_source_root(path=None):
    """Find repo source root by looking for .repo/ directory."""
    start = Path(path).resolve() if path else Path.cwd().resolve()
    for p in [start, *start.parents]:
        if (p / ".repo").is_dir():
            return p
    return None


def _resolve_source(args) -> Path:
    """Resolve source directory from args or auto-detect."""
    source = getattr(args, "source", None)
    source_dir = _find_source_root(source)
    if source_dir is None:
        print("Error: Cannot find repo-managed directory. Use -s/--source to specify.", file=sys.stderr)
        sys.exit(1)
    return source_dir


def _resolve_workspace(args) -> Path:
    """Resolve workspace directory from args or auto-detect."""
    target = getattr(args, "target", None) or getattr(args, "workspace", None)
    if target:
        p = Path(target)
        if p.is_dir() and (p / ".workspace.json").exists():
            return p
        # Try finding by name in source index
        source_dir = _find_source_root()
        if source_dir:
            index = load_workspace_index(source_dir)
            entry = index.find_by_name(target)
            if entry:
                return Path(entry["path"])
        print(f"Error: Workspace not found: {target}", file=sys.stderr)
        sys.exit(1)

    ws = detect_workspace()
    if ws is None:
        print("Error: Not inside a workspace. Specify path or name.", file=sys.stderr)
        sys.exit(1)
    return ws


def _parse_pin(pin_str: str | None) -> dict[str, str]:
    """Parse --pin 'repo=version,repo=version' into dict."""
    if not pin_str:
        return {}
    result = {}
    for item in pin_str.split(","):
        item = item.strip()
        if "=" not in item:
            print(f"Error: Invalid pin format: {item}. Expected repo=version.", file=sys.stderr)
            sys.exit(1)
        repo, version = item.split("=", 1)
        result[repo.strip()] = version.strip()
    return result


# ── Command handlers ──────────────────────────────────────────────


def cmd_create(args):
    """Create a new workspace."""
    source_dir = _resolve_source(args)
    ws_path = Path(args.path).resolve()
    name = args.name or ws_path.name
    pin_map = _parse_pin(args.pin)

    if ws_path.exists():
        print(f"Error: Path already exists: {ws_path}", file=sys.stderr)
        print(f"Use 'rwt destroy {ws_path}' first.", file=sys.stderr)
        return 1

    # Scan repos
    all_repos = scan_repos(source_dir)

    # Determine worktree set
    if args.all_worktree:
        worktree_set = set(all_repos)
    elif args.worktree:
        worktree_set = set(r.strip() for r in args.worktree.split(","))
        # Validate paths
        invalid = worktree_set - set(all_repos)
        if invalid:
            print(f"Error: Unknown sub-repo paths: {', '.join(sorted(invalid))}", file=sys.stderr)
            return 1
    else:
        worktree_set = set()

    # Validate pin targets are in worktree set
    for repo in pin_map:
        if repo not in worktree_set:
            print(f"Error: Pinned repo '{repo}' is not in worktree list.", file=sys.stderr)
            return 1

    # Build trie
    trie = build_trie(all_repos, worktree_set)

    # Atomic create: build in .tmp, then rename
    tmp_path = ws_path.parent / f"{ws_path.name}.tmp"
    if tmp_path.exists():
        shutil.rmtree(tmp_path)

    try:
        print(f"Creating workspace '{name}' at {ws_path}")
        print(f"  Source: {source_dir}")
        print(f"  Worktrees: {len(worktree_set)}/{len(all_repos)} repos")
        if args.checkout:
            print(f"  Checkout: {args.checkout}")

        build_workspace(source_dir, tmp_path, trie, branch=args.branch, pin_map=pin_map,
                        checkout=args.checkout)

        # Write metadata
        worktree_entries = []
        for repo_path in sorted(worktree_set):
            if not (source_dir / repo_path).exists():
                continue  # skipped during build (not present in source checkout)
            worktree_entries.append(WorktreeEntry(
                path=repo_path,
                branch=args.branch,
                pinned=pin_map.get(repo_path),
            ))
        meta = create_workspace_metadata(
            source=str(source_dir),
            name=name,
            worktrees=worktree_entries,
        )
        save_workspace_metadata(tmp_path, meta)

        # Rename to final path
        tmp_path.rename(ws_path)

        # Register in source index
        index = load_workspace_index(source_dir)
        index.register(name, str(ws_path), meta.created)
        save_workspace_index(source_dir, index)

        print(f"Done.")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        # Rollback: clean up worktrees and tmp dir
        if tmp_path.exists():
            try:
                teardown_workspace(source_dir, tmp_path, trie)
            except Exception:
                shutil.rmtree(tmp_path, ignore_errors=True)
        return 1


def cmd_destroy(args):
    """Destroy a workspace."""
    # Resolve workspace
    target = args.target
    p = Path(target)
    source_dir = None

    if p.is_dir() and (p / ".workspace.json").exists():
        ws_path = p.resolve()
        meta = load_workspace_metadata(ws_path)
        source_dir = Path(meta.source)
    else:
        # Try finding by name
        source_dir = _find_source_root()
        if source_dir:
            index = load_workspace_index(source_dir)
            entry = index.find_by_name(target)
            if entry:
                ws_path = Path(entry["path"])
                if ws_path.exists():
                    meta = load_workspace_metadata(ws_path)
                    source_dir = Path(meta.source)
                else:
                    # Workspace dir gone, just clean up index
                    index.unregister(entry["path"])
                    save_workspace_index(source_dir, index)
                    print(f"Workspace directory already removed. Cleaned up index.")
                    return 0
            else:
                print(f"Error: Workspace not found: {target}", file=sys.stderr)
                return 1
        else:
            print(f"Error: Workspace not found: {target}", file=sys.stderr)
            return 1

    if not ws_path.exists():
        print(f"Error: Workspace directory does not exist: {ws_path}", file=sys.stderr)
        return 1

    # Check for dirty worktrees unless --force
    if not args.force:
        from repoworktree.worktree import has_local_changes, has_local_commits, get_head
        blockers = []
        for wt in meta.worktrees:
            wt_path = ws_path / wt.path
            src_path = source_dir / wt.path
            if not wt_path.exists():
                continue
            if has_local_changes(wt_path):
                blockers.append(f"  {wt.path}: uncommitted changes")
            elif src_path.exists():
                src_head = get_head(src_path)
                if has_local_commits(wt_path, src_head):
                    blockers.append(f"  {wt.path}: unpushed local commits")
        if blockers:
            print("Error: Cannot destroy workspace:", file=sys.stderr)
            for b in blockers:
                print(b, file=sys.stderr)
            print("Use --force to destroy anyway.", file=sys.stderr)
            return 1

    print(f"Destroying workspace '{meta.name}' at {ws_path}")

    # Remove git worktrees (deepest first)
    sorted_wts = sorted(meta.worktrees, key=lambda w: w.path.count("/"), reverse=True)
    for wt in sorted_wts:
        wt_path = ws_path / wt.path
        source_repo = source_dir / wt.path
        if wt_path.exists() and (wt_path / ".git").is_file():
            try:
                remove_worktree(source_repo, wt_path, force=True)
                print(f"  Removed worktree: {wt.path}")
            except Exception as e:
                print(f"  Warning: failed to remove worktree {wt.path}: {e}", file=sys.stderr)
                # Fallback: prune from source side
                try:
                    import subprocess
                    subprocess.run(
                        ["git", "worktree", "prune"],
                        cwd=source_repo, capture_output=True,
                    )
                except Exception:
                    pass

    # Delete workspace directory
    if ws_path.exists():
        shutil.rmtree(ws_path)

    # Unregister from index
    if source_dir:
        index = load_workspace_index(source_dir)
        try:
            index.unregister(str(ws_path))
            save_workspace_index(source_dir, index)
        except ValueError:
            pass  # Not in index, that's fine

    print(f"Done.")
    return 0


def cmd_list(args):
    """List all workspaces."""
    import json as json_mod

    source_dir = _resolve_source(args)
    index = load_workspace_index(source_dir)
    entries = index.list_all()

    if not entries:
        print("No workspaces found.")
        return 0

    if args.json_output:
        print(json_mod.dumps(entries, indent=2))
        return 0

    # Table output
    print(f"{'NAME':<24s} {'PATH':<40s} {'CREATED'}")
    for e in entries:
        name = e.get("name", "?")
        path = e.get("path", "?")
        created = e.get("created", "?")
        # Check if workspace still exists
        ws_path = Path(path)
        if ws_path.exists():
            try:
                meta = load_workspace_metadata(ws_path)
                wt_count = len(meta.worktrees)
                all_count = len(scan_repos(Path(meta.source)))
                name_col = f"{name} ({wt_count}/{all_count})"
            except Exception:
                name_col = name
        else:
            name_col = f"{name} (missing)"
        print(f"{name_col:<24s} {path:<40s} {created}")

    return 0


def cmd_status(args):
    """Show workspace status."""
    import json as json_mod
    from repoworktree.worktree import get_head, has_local_changes, has_local_commits

    ws_path = _resolve_workspace(args)
    meta = load_workspace_metadata(ws_path)
    source_dir = Path(meta.source)
    all_repos = scan_repos(source_dir)

    wt_info = []
    for wt in meta.worktrees:
        wt_path = ws_path / wt.path
        src_path = source_dir / wt.path
        info = {"path": wt.path, "branch": wt.branch, "pinned": wt.pinned}

        if wt_path.is_dir() and (wt_path / ".git").is_file():
            info["head"] = get_head(wt_path)
            info["dirty"] = has_local_changes(wt_path)
            src_head = get_head(src_path)
            info["local_commits"] = has_local_commits(wt_path, src_head)
            info["up_to_date"] = (info["head"] == src_head) or info["local_commits"]
        else:
            info["missing"] = True

        wt_info.append(info)

    if args.json_output:
        output = {
            "name": meta.name,
            "path": str(ws_path),
            "source": meta.source,
            "created": meta.created,
            "worktrees": wt_info,
            "symlinked": len(all_repos) - len(meta.worktrees),
        }
        print(json_mod.dumps(output, indent=2))
        return 0

    # Human-readable output
    print(f"Workspace: {meta.name}")
    print(f"Path:      {ws_path}")
    print(f"Source:    {meta.source}")
    print(f"Created:   {meta.created}")
    print()

    if not wt_info:
        print(f"Worktree repos: 0/{len(all_repos)} (all symlinked)")
        return 0

    print(f"Worktree repos ({len(wt_info)}/{len(all_repos)}):")
    for info in wt_info:
        if info.get("missing"):
            status = "missing"
        else:
            parts = []
            if info.get("pinned"):
                parts.append(f"pinned={info['pinned'][:8]}")
            if info.get("dirty"):
                parts.append("dirty")
            if info.get("local_commits"):
                parts.append("local commits")
            if not parts:
                parts.append("clean")
            status = ", ".join(parts)

        branch_str = f" [{info['branch']}]" if info.get("branch") else ""
        print(f"  {info['path']:<30s}{branch_str} {status}")

    print(f"\nSymlinked repos: {len(all_repos) - len(meta.worktrees)}")
    return 0


def cmd_promote(args):
    """Promote a sub-repo from symlink to worktree."""
    from repoworktree.promote import promote, PromoteError

    ws_path = _resolve_workspace(args)
    meta = load_workspace_metadata(ws_path)
    source_dir = Path(meta.source)
    all_repos = scan_repos(source_dir)

    try:
        promote(ws_path, source_dir, args.repo_path, all_repos,
                branch=args.branch, pin_version=args.pin)
        print(f"Promoted {args.repo_path} to worktree.")
        return 0
    except PromoteError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_demote(args):
    """Demote a sub-repo from worktree to symlink."""
    from repoworktree.promote import demote, DemoteError
    from repoworktree.worktree import DirtyWorktreeError

    ws_path = _resolve_workspace(args)
    meta = load_workspace_metadata(ws_path)
    source_dir = Path(meta.source)
    all_repos = scan_repos(source_dir)

    try:
        demote(ws_path, source_dir, args.repo_path, all_repos, force=args.force)
        print(f"Demoted {args.repo_path} to symlink.")
        return 0
    except (DemoteError, DirtyWorktreeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_sync(args):
    """Sync workspace worktrees."""
    from repoworktree.sync import sync

    ws_path = _resolve_workspace(args)
    meta = load_workspace_metadata(ws_path)
    source_dir = Path(meta.source)

    report = sync(ws_path, source_dir, rebase=args.rebase)

    if not report.results:
        print("No worktrees to sync.")
        return 0

    for r in report.results:
        if r.action == "updated":
            print(f"  {r.path:<30s} updated to {r.new_head[:8]}")
        elif r.action == "rebased":
            print(f"  {r.path:<30s} rebased onto {r.new_head[:8]}")
        elif r.action == "already_up_to_date":
            print(f"  {r.path:<30s} already up to date")
        elif r.action == "skipped":
            print(f"  {r.path:<30s} skipped ({r.reason})")

    return 0


def cmd_pin(args):
    """Pin a sub-repo to a specific version."""
    from repoworktree.worktree import get_head, checkout_detached

    ws_path = _resolve_workspace(args)
    meta = load_workspace_metadata(ws_path)

    entry = meta.find_worktree(args.repo_path)
    if not entry:
        print(f"Error: Not a worktree: {args.repo_path}", file=sys.stderr)
        return 1

    wt_path = ws_path / args.repo_path
    version = args.version or get_head(wt_path)

    # Checkout to pinned version if different from current
    current_head = get_head(wt_path)
    if version != current_head:
        try:
            checkout_detached(wt_path, version)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    meta.pin_worktree(args.repo_path, version)
    save_workspace_metadata(ws_path, meta)
    print(f"Pinned {args.repo_path} to {version[:8]}")
    return 0


def cmd_unpin(args):
    """Unpin a sub-repo."""
    ws_path = _resolve_workspace(args)
    meta = load_workspace_metadata(ws_path)

    entry = meta.find_worktree(args.repo_path)
    if not entry:
        print(f"Error: Not a worktree: {args.repo_path}", file=sys.stderr)
        return 1
    if not entry.pinned:
        print(f"{args.repo_path} is not pinned.")
        return 0

    meta.unpin_worktree(args.repo_path)
    save_workspace_metadata(ws_path, meta)
    print(f"Unpinned {args.repo_path}")
    return 0


def cmd_export(args):
    """Export changes from workspace."""
    from repoworktree.export import export

    ws_path = _resolve_workspace(args)
    meta = load_workspace_metadata(ws_path)
    source_dir = Path(meta.source)
    output_dir = Path(args.output).resolve()

    report = export(ws_path, source_dir, output_dir, fmt=args.export_format)

    if not report.results:
        print("No worktrees to export.")
        return 0

    if not report.exported:
        print("Nothing to export (no worktrees with local commits).")
        return 0

    print("Exported:")
    for r in report.exported:
        print(f"  {r.path:<30s} ({r.commit_count} commit{'s' if r.commit_count != 1 else ''}) → {r.output_file}")

    if report.skipped:
        for r in report.skipped:
            print(f"  {r.path:<30s} skipped ({r.reason})")

    return 0


# ── CLI definition ────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rwt",
        description="repoworktree: isolated workspaces for repo-managed projects",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── create ──
    p_create = sub.add_parser("create", help="Create a new workspace")
    p_create.add_argument("path", help="Workspace target path")
    p_create.add_argument("-n", "--name", default=None,
                          help="Workspace name (default: directory name)")
    p_create.add_argument("-w", "--worktree", default=None,
                          help="Comma-separated sub-repo paths to create as git worktree")
    p_create.add_argument("--all", action="store_true", dest="all_worktree",
                          help="Create git worktree for ALL sub-repos")
    p_create.add_argument("-s", "--source", default=None,
                          help="Source repo checkout path (default: auto-detect)")
    p_create.add_argument("--pin", default=None,
                          help="Pin versions: repo=version[,repo=version,...]")
    p_create.add_argument("-b", "--branch", default=None,
                          help="Create named branch for worktrees instead of detached HEAD")
    p_create.add_argument("--checkout", default=None, metavar="REF",
                          help="Check out this branch or tag for all worktrees (default: source HEAD)")
    p_create.set_defaults(func=cmd_create)

    # ── destroy ──
    p_destroy = sub.add_parser("destroy", help="Destroy a workspace")
    p_destroy.add_argument("target", help="Workspace path or name")
    p_destroy.add_argument("-f", "--force", action="store_true",
                           help="Force destroy even with uncommitted changes")
    p_destroy.set_defaults(func=cmd_destroy)

    # ── list ──
    p_list = sub.add_parser("list", help="List all workspaces")
    p_list.add_argument("-s", "--source", default=None,
                        help="Source repo checkout path (default: auto-detect)")
    p_list.add_argument("--json", action="store_true", dest="json_output",
                        help="Output in JSON format")
    p_list.set_defaults(func=cmd_list)

    # ── status ──
    p_status = sub.add_parser("status", help="Show workspace status")
    p_status.add_argument("target", nargs="?", default=None,
                          help="Workspace path or name (default: current directory)")
    p_status.add_argument("--json", action="store_true", dest="json_output",
                          help="Output in JSON format")
    p_status.set_defaults(func=cmd_status)

    # ── promote ──
    p_promote = sub.add_parser("promote",
                               help="Promote a sub-repo from symlink to worktree")
    p_promote.add_argument("repo_path", help="Sub-repo path (e.g. nuttx, frameworks/system/core)")
    p_promote.add_argument("-W", "--workspace", default=None,
                           help="Workspace path or name (default: auto-detect from CWD)")
    p_promote.add_argument("--pin", default=None,
                           help="Checkout to specific version")
    p_promote.add_argument("-b", "--branch", default=None,
                           help="Create named branch")
    p_promote.set_defaults(func=cmd_promote)

    # ── demote ──
    p_demote = sub.add_parser("demote",
                              help="Demote a sub-repo from worktree to symlink")
    p_demote.add_argument("repo_path", help="Sub-repo path")
    p_demote.add_argument("-W", "--workspace", default=None,
                          help="Workspace path or name (default: auto-detect from CWD)")
    p_demote.add_argument("-f", "--force", action="store_true",
                          help="Force demote even with uncommitted changes")
    p_demote.set_defaults(func=cmd_demote)

    # ── sync ──
    p_sync = sub.add_parser("sync", help="Sync workspace worktrees to latest")
    p_sync.add_argument("-W", "--workspace", default=None,
                        help="Workspace path or name (default: auto-detect from CWD)")
    p_sync.add_argument("--rebase", action="store_true",
                        help="Rebase local commits onto latest")
    p_sync.set_defaults(func=cmd_sync)

    # ── pin ──
    p_pin = sub.add_parser("pin", help="Pin a sub-repo to a specific version")
    p_pin.add_argument("repo_path", help="Sub-repo path")
    p_pin.add_argument("version", nargs="?", default=None,
                       help="Commit/tag/branch to pin to (default: current HEAD)")
    p_pin.add_argument("-W", "--workspace", default=None,
                       help="Workspace path or name (default: auto-detect from CWD)")
    p_pin.set_defaults(func=cmd_pin)

    # ── unpin ──
    p_unpin = sub.add_parser("unpin", help="Unpin a sub-repo")
    p_unpin.add_argument("repo_path", help="Sub-repo path")
    p_unpin.add_argument("-W", "--workspace", default=None,
                         help="Workspace path or name (default: auto-detect from CWD)")
    p_unpin.set_defaults(func=cmd_unpin)

    # ── export ──
    p_export = sub.add_parser("export", help="Export changes from workspace")
    p_export.add_argument("-W", "--workspace", default=None,
                          help="Workspace path or name (default: auto-detect from CWD)")
    p_export.add_argument("--format", choices=["patch", "bundle"], default="patch",
                          dest="export_format", help="Export format (default: patch)")
    p_export.add_argument("-o", "--output", default=".",
                          help="Output directory (default: current directory)")
    p_export.set_defaults(func=cmd_export)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
