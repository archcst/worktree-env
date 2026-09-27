import argparse

import pytest

from worktree_env.cli import (
    _build_parser,
    _cmd_sync,
    _warn_if_legacy_distribution_installed,
)


def test_public_cli_contains_only_the_six_user_commands():
    parser = _build_parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )

    assert set(subparsers.choices) == {
        "init",
        "sync",
        "list",
        "doctor",
        "monitor",
        "uninstall",
    }
    monitor_parser = subparsers.choices["monitor"]
    monitor_subparsers = next(
        action
        for action in monitor_parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    assert set(monitor_subparsers.choices) == {"enable", "disable"}


def test_sync_does_not_accept_a_worktree_path():
    parser = _build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["sync", "/tmp/another-worktree"])


def test_sync_runs_setup_commands(app_paths, tmp_path, monkeypatch):
    calls = []
    result = object()
    monkeypatch.setattr("worktree_env.cli.worktree_root", lambda: tmp_path)
    monkeypatch.setattr(
        "worktree_env.cli.apply_worktree",
        lambda paths, root, setup, rebalance=False: calls.append(
            (paths, root, setup, rebalance)
        )
        or result,
    )
    monkeypatch.setattr("worktree_env.cli.print_apply_result", lambda value: None)

    assert _cmd_sync(app_paths) == 0
    assert calls == [(app_paths, tmp_path, True, False)]


def test_sync_rebalance_flag_reaches_the_projector(app_paths, tmp_path, monkeypatch):
    calls = []
    result = object()
    monkeypatch.setattr("worktree_env.cli.worktree_root", lambda: tmp_path)
    monkeypatch.setattr(
        "worktree_env.cli.apply_worktree",
        lambda paths, root, setup, rebalance=False: calls.append(
            (paths, root, setup, rebalance)
        )
        or result,
    )
    monkeypatch.setattr("worktree_env.cli.print_apply_result", lambda value: None)

    args = _build_parser().parse_args(["sync", "--rebalance"])

    assert args.rebalance is True
    assert _cmd_sync(app_paths, rebalance=args.rebalance) == 0
    assert calls == [(app_paths, tmp_path, True, True)]


def test_legacy_distribution_prints_migration_instructions(monkeypatch, capsys):
    monkeypatch.setattr(
        "worktree_env.cli.metadata.distribution", lambda _name: object()
    )

    _warn_if_legacy_distribution_installed()

    error = capsys.readouterr().err
    assert "renamed to worktree-env" in error
    assert "uv tool uninstall git-worktree-env" in error
    assert "~/.config/wte" in error
