"""Command-line interface for wte."""

from __future__ import annotations

import argparse
import json
import sys
from importlib import metadata
from typing import Optional, Sequence

from . import __version__
from .config import initialize_config, initialize_profile_template, load_port_pool
from .hooks import hooks_status, install_hooks, uninstall_hooks
from .paths import AppPaths
from .profiles import aliased_value, load_profiles, validate_profiles, worktree_root
from .projector import apply_worktree, print_apply_result
from .reconciler import (
    install_monitor,
    monitor_status,
    reconcile_with_retry,
    refresh_profile_file_monitor,
    refresh_worktree_monitor,
    uninstall_monitor,
)
from .registry import load_registry, prune_registry
from .utils import WteError, expand_profile_path, log, run_git

INTERNAL_HOOK_COMMAND = "_hook"
INTERNAL_RECONCILE_COMMAND = "_reconcile"
INTERNAL_PROFILES_CHANGED_COMMAND = "_profiles_changed"
INTERNAL_PROFILE_SET_CHANGED_COMMAND = "_profile_set_changed"
LEGACY_DISTRIBUTION = "git-worktree-env"


def _warn_if_legacy_distribution_installed() -> None:
    """Tell users of the transition package how to adopt the new package name."""
    try:
        metadata.distribution(LEGACY_DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return

    print(
        "[wte] warning: git-worktree-env has been renamed to worktree-env.\n"
        "[wte] migrate: uv tool uninstall git-worktree-env && "
        "uv tool install worktree-env\n"
        "[wte] then run 'wte init' to refresh the Git hook; refresh the monitor "
        "with 'wte monitor enable' if used.\n"
        "[wte] your existing ~/.config/wte configuration will be preserved.",
        file=sys.stderr,
    )


def _build_parser() -> argparse.ArgumentParser:
    """Build the public CLI without exposing internal integration commands."""
    parser = argparse.ArgumentParser(
        prog="wte",
        description="Per-worktree ports, secrets, and local environment setup.",
    )
    parser.add_argument("--version", action="version", version=f"wte {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser(
        "init",
        help="create local configuration and install the Git hook dispatcher",
    )
    sync_parser = commands.add_parser(
        "sync", help="synchronize and run setup commands for the current worktree"
    )
    sync_parser.add_argument(
        "--rebalance",
        action="store_true",
        help="discard the saved allocation and reallocate every port as one fresh block",
    )
    commands.add_parser("list", help="list live worktree port allocations")
    commands.add_parser("doctor", help="diagnose configuration and host integration")
    monitor_parser = commands.add_parser(
        "monitor",
        help="manage optional host monitoring for sandbox-created worktrees",
    )
    monitor_commands = monitor_parser.add_subparsers(dest="monitor_command", required=True)
    monitor_commands.add_parser("enable", help="install or refresh host monitoring")
    monitor_commands.add_parser("disable", help="remove host monitoring")
    commands.add_parser(
        "uninstall",
        help="remove Git hooks while preserving configuration and state",
    )
    return parser


def _cmd_init(paths: AppPaths) -> int:
    config_created = initialize_config(paths)
    template, template_created = initialize_profile_template(paths)
    dispatcher = install_hooks(paths)
    print(f"[wte] config: {paths.config} ({'created' if config_created else 'kept'})")
    print(f"[wte] project template: {template} ({'created' if template_created else 'kept'})")
    print(f"[wte] hooks: {dispatcher.parent}")
    return 0


def _cmd_sync(paths: AppPaths, rebalance: bool = False) -> int:
    result = apply_worktree(paths, worktree_root(), setup=True, rebalance=rebalance)
    if result is None:
        raise WteError("the current worktree does not match any project profile")
    print_apply_result(result)
    return 0


def _cmd_list(paths: AppPaths) -> int:
    registry = prune_registry(load_registry(paths))
    print(json.dumps(registry, indent=2, sort_keys=True) if registry else "(empty)")
    return 0


def _print_validation(paths: AppPaths) -> int:
    load_port_pool(paths)
    errors, warnings = validate_profiles(paths)
    for warning in warnings:
        print(f"[wte] warning: {warning}")
    for error in errors:
        print(f"[wte] error: {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"[wte] configuration is valid ({len(load_profiles(paths))} profile(s))")
    return 0


def _cmd_doctor(paths: AppPaths) -> int:
    failures = 0
    print(f"[wte] config: {paths.root}")
    try:
        print(f"[wte] git: {run_git('--version')}")
    except WteError as exc:
        print(f"[wte] error: {exc}", file=sys.stderr)
        failures += 1

    failures += int(_print_validation(paths) != 0)
    try:
        registry = load_registry(paths)
        print(f"[wte] registry: {paths.registry} ({len(registry)} allocation(s))")
    except WteError as exc:
        print(f"[wte] error: {exc}", file=sys.stderr)
        failures += 1

    status = hooks_status(paths)
    if status["installed"]:
        print(f"[wte] hooks: installed at {status['expected_hooks_path']}")
        print(f"[wte] hook executable: {status.get('wte_executable')}")
    else:
        print(f"[wte] warning: hooks are not installed (current: {status['current_hooks_path']})")

    monitor = monitor_status(paths)
    if monitor.installed and monitor.active:
        print(f"[wte] reconciler: active via {monitor.detail} ({len(monitor.watch_paths)} path(s))")
    else:
        print(f"[wte] warning: reconciler is not active ({monitor.detail})")

    for profile in load_profiles(paths, strict=False):
        for entry in aliased_value(profile, "link-files", "secrets", default=[]) or []:
            if not isinstance(entry, dict) or not entry.get("source"):
                continue
            source = expand_profile_path(str(entry["source"]), profile).resolve()
            if not (source.is_file() or source.is_dir()):
                print(f"[wte] warning: secret source is missing or not a file or directory: {source}")
    return 1 if failures else 0


def _cmd_monitor(paths: AppPaths, command: str) -> int:
    if command == "enable":
        monitor = install_monitor(paths)
        if not monitor.installed:
            raise WteError(monitor.detail)
        print(f"[wte] reconciler: {monitor.detail} watching {len(monitor.watch_paths)} path(s)")
        return 0
    uninstall_monitor(paths)
    print("[wte] reconciler monitoring disabled")
    return 0


def _cmd_uninstall(paths: AppPaths) -> int:
    uninstall_monitor(paths)
    previous = uninstall_hooks(paths)
    print("[wte] hooks and reconciler uninstalled; configuration and state were preserved")
    print(f"[wte] restored core.hooksPath: {previous or '(unset)'}")
    return 0


def _cmd_internal_hook(paths: AppPaths) -> int:
    """Synchronize after checkout without ever blocking the Git operation."""
    try:
        result = apply_worktree(paths, worktree_root(), setup=True)
        if result is not None:
            print_apply_result(result)
        return 0
    except Exception as exc:  # Git hooks must fail open, including unexpected errors.
        log(f"hook skipped: {exc}")
        return 0


def _cmd_internal_reconcile(paths: AppPaths) -> int:
    """Reconcile host-visible worktrees after a common-dir filesystem event."""
    try:
        result = reconcile_with_retry(paths)
        print(
            f"[wte] reconcile: discovered={result.discovered} "
            f"applied={result.applied} pending={result.pending}"
        )
        return 0 if result.pending == 0 else 1
    except Exception as exc:
        log(f"reconcile failed: {exc}")
        return 1


def _cmd_internal_profile_set_changed(paths: AppPaths) -> int:
    """Refresh profile-file watches after profiles are added or removed."""
    try:
        files_refreshed = refresh_profile_file_monitor(paths, force=True)
        monitor_refreshed = refresh_worktree_monitor(paths)
        result = reconcile_with_retry(paths)
        print(
            f"[wte] profile-set: files_refreshed={str(files_refreshed).lower()} "
            f"monitor_refreshed={str(monitor_refreshed).lower()} "
            f"discovered={result.discovered} applied={result.applied} "
            f"pending={result.pending}"
        )
        return 0 if result.pending == 0 else 1
    except Exception as exc:
        log(f"profile-set refresh failed: {exc}")
        return 1


def _cmd_internal_profiles_changed(paths: AppPaths) -> int:
    """Refresh repository watches and reconcile after a profile-set change."""
    try:
        refreshed = refresh_worktree_monitor(paths)
        result = reconcile_with_retry(paths)
        print(
            f"[wte] profiles: monitor_refreshed={str(refreshed).lower()} "
            f"discovered={result.discovered} applied={result.applied} "
            f"pending={result.pending}"
        )
        return 0 if result.pending == 0 else 1
    except Exception as exc:
        log(f"profile refresh failed: {exc}")
        return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse arguments and return a process exit status."""
    arguments = list(argv) if argv is not None else sys.argv[1:]
    paths = AppPaths.discover()
    if arguments and arguments[0] == INTERNAL_HOOK_COMMAND:
        return _cmd_internal_hook(paths)
    if arguments and arguments[0] == INTERNAL_RECONCILE_COMMAND:
        return _cmd_internal_reconcile(paths)
    if arguments and arguments[0] == INTERNAL_PROFILES_CHANGED_COMMAND:
        return _cmd_internal_profiles_changed(paths)
    if arguments and arguments[0] == INTERNAL_PROFILE_SET_CHANGED_COMMAND:
        return _cmd_internal_profile_set_changed(paths)

    _warn_if_legacy_distribution_installed()
    args = _build_parser().parse_args(arguments)
    try:
        if args.command == "init":
            return _cmd_init(paths)
        if args.command == "sync":
            return _cmd_sync(paths, rebalance=args.rebalance)
        if args.command == "list":
            return _cmd_list(paths)
        if args.command == "doctor":
            return _cmd_doctor(paths)
        if args.command == "monitor":
            return _cmd_monitor(paths, args.monitor_command)
        if args.command == "uninstall":
            return _cmd_uninstall(paths)
        raise WteError(f"unknown command: {args.command}")
    except WteError as exc:
        log(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
