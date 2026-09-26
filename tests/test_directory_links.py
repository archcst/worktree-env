import os
from types import SimpleNamespace

import pytest
import yaml

from worktree_env.cli import _cmd_doctor
from worktree_env.profiles import validate_profiles
from worktree_env.projector import apply_secrets, apply_worktree
from worktree_env.registry import load_registry
from worktree_env.utils import WteError


@pytest.mark.parametrize("key", ["link-files", "secrets"])
def test_directory_projection_is_shared_and_idempotent(
    app_paths, git_worktrees, tmp_path, monkeypatch, key
):
    main, linked = git_worktrees
    source = tmp_path / "certs"
    source.mkdir()
    (source / "nested").mkdir()
    (source / "nested/cert.pem").write_text("test certificate")
    (app_paths.profiles / "example.yaml").write_text(yaml.safe_dump({
        "name": "example",
        "match": {"main-worktree": str(main)},
        "port-claims": [{"id": "web"}],
        key: [{"source": str(source), "target": "app/certs"}],
    }))
    monkeypatch.setattr("worktree_env.registry.port_is_free", lambda _: True)

    first = apply_worktree(app_paths, linked)
    target = linked / "app/certs"
    assert target.is_symlink()
    assert target.resolve() == source.resolve()
    assert (target / "nested/cert.pem").read_text() == "test certificate"
    (source / "new.pem").write_text("new certificate")
    assert (target / "new.pem").read_text() == "new certificate"
    inode = target.lstat().st_ino

    second = apply_worktree(app_paths, linked)
    assert first.ports == second.ports
    assert target.lstat().st_ino == inode
    assert str(linked) in load_registry(app_paths)


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "dangling"])
def test_existing_directory_link_targets(tmp_path, kind):
    root = tmp_path / "worktree"
    root.mkdir()
    source = tmp_path / "shared"
    source.mkdir()
    target = root / "certs"
    if kind == "file":
        target.write_text("original")
    elif kind == "directory":
        target.mkdir()
        (target / "original").write_text("keep")
    else:
        old = tmp_path / "old"
        if kind == "symlink":
            old.mkdir()
            (old / "original").write_text("keep")
        target.symlink_to(old, target_is_directory=True)
    profile = {"link-files": [{"source": str(source), "target": "certs"}]}

    if kind == "directory":
        with pytest.raises(WteError, match="target is a directory"):
            apply_secrets(profile, root)
        assert not target.is_symlink()
        assert (target / "original").read_text() == "keep"
    else:
        apply_secrets(profile, root)
        assert target.is_symlink()
        assert target.resolve() == source.resolve()
        if kind == "symlink":
            assert (old / "original").read_text() == "keep"


@pytest.mark.parametrize("kind", ["missing", "dangling", "fifo"])
def test_invalid_sources_are_skipped_without_replacing_targets(tmp_path, capsys, kind):
    root = tmp_path / "worktree"
    root.mkdir()
    source = tmp_path / "source"
    if kind == "dangling":
        source.symlink_to(tmp_path / "missing")
    elif kind == "fifo":
        os.mkfifo(source)
    target = root / "certs"
    target.write_text("keep")

    apply_secrets({"link-files": [{"source": str(source), "target": "certs"}]}, root)

    assert target.read_text() == "keep"
    assert "source is missing or not a file or directory" in capsys.readouterr().err


@pytest.mark.parametrize("source_path,target", [
    ("certs", "certs"),
    ("certs", "certs/child"),
    ("certs/child", "certs"),
    (".", "certs"),
])
def test_directory_source_target_overlap_is_rejected(tmp_path, source_path, target):
    source = tmp_path / source_path
    source.mkdir(parents=True, exist_ok=True)
    with pytest.raises(WteError, match="source and target overlap"):
        apply_secrets({"link-files": [{"source": str(source), "target": target}]}, tmp_path)
    assert source.is_dir()
    assert not source.is_symlink()


@pytest.mark.parametrize("links_key,writes_key,target_key", [
    ("link-files", "write-files", "target"),
    ("secrets", "writes", "path"),
])
@pytest.mark.parametrize("other_target", ["certs", "certs/port.env", "app"])
def test_overlapping_projections_fail_before_changes(
    app_paths, git_worktrees, tmp_path, links_key, writes_key, target_key, other_target
):
    main, linked = git_worktrees
    source = tmp_path / "source"
    source.mkdir()
    link_target = "app/certs" if other_target == "app" else "certs"
    profile = {
        "name": "example",
        "match": {"main-worktree": str(main)},
        "port-claims": [{"id": "web"}],
        links_key: [{"source": str(source), "target": link_target}],
        writes_key: [{target_key: other_target, "body": "PORT=${web}"}],
    }
    (app_paths.profiles / "example.yaml").write_text(yaml.safe_dump(profile))
    errors, _ = validate_profiles(app_paths)
    assert any("overlapping projection targets" in error for error in errors)
    with pytest.raises(WteError, match="overlapping projection targets"):
        apply_worktree(app_paths, linked)
    assert not (linked / link_target).exists()
    assert not list(source.iterdir())
    assert str(linked) not in load_registry(app_paths)


def test_nested_links_fail_before_changes(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    root = tmp_path / "worktree"
    root.mkdir()
    with pytest.raises(WteError, match="overlapping projection targets"):
        apply_secrets({"link-files": [
            {"source": str(source), "target": "certs"},
            {"source": str(source), "target": "certs/nested"},
        ]}, root)
    assert not list(root.iterdir())


def test_target_parent_cannot_escape_via_symlink(tmp_path):
    root = tmp_path / "worktree"
    root.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    (root / "outside").symlink_to(source, target_is_directory=True)
    with pytest.raises(WteError, match="target escapes the worktree"):
        apply_secrets({"link-files": [{
            "source": str(source), "target": "outside/certs",
        }]}, root)
    assert not list(source.iterdir())


def test_doctor_accepts_directory_sources(app_paths, tmp_path, monkeypatch, capsys):
    source = tmp_path / "certs"
    source.mkdir()
    (app_paths.profiles / "example.yaml").write_text(yaml.safe_dump({
        "name": "example",
        "match": {"main-worktree": str(tmp_path)},
        "port-claims": [{"id": "web"}],
        "link-files": [{"source": str(source), "target": "certs"}],
    }))
    monkeypatch.setattr("worktree_env.cli.hooks_status", lambda _: {
        "installed": False, "current_hooks_path": None,
    })
    monkeypatch.setattr("worktree_env.cli.monitor_status", lambda _: SimpleNamespace(
        installed=False, active=False, detail="not installed",
    ))
    assert _cmd_doctor(app_paths) == 0
    assert "secret source" not in capsys.readouterr().out
