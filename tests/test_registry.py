import json
from pathlib import Path

import pytest

from worktree_env.config import PortPool
from worktree_env.registry import (
    allocate_ports,
    load_registry,
    prune_registry,
    save_registry,
)
from worktree_env.utils import WteError


def test_allocation_is_sticky_and_contiguous(app_paths, tmp_path, monkeypatch):
    root = tmp_path / "worktree"
    root.mkdir()
    profile = {
        "name": "example",
        "_file": str(tmp_path / "example.yaml"),
        "ports": [{"id": "frontend"}, {"id": "backend"}],
    }
    monkeypatch.setattr("worktree_env.registry.port_is_free", lambda _port: True)

    start, ports = allocate_ports(profile, root, {}, PortPool(42000, 42010))
    registry = {
        str(root): {
            "profile": "example",
            "file": "example.yaml",
            "ports": ports,
        }
    }
    second_start, second_ports = allocate_ports(profile, root, registry, PortPool(42000, 42010))

    assert start == second_start == 42000
    assert ports == second_ports == {"frontend": 42000, "backend": 42001}


def _profile(tmp_path, *ids):
    return {
        "name": "example",
        "_file": str(tmp_path / "example.yaml"),
        "port-claims": [{"id": port_id} for port_id in ids],
    }


def _registered(root: Path, ports: dict) -> dict:
    return {str(root): {"profile": "example", "file": "example.yaml", "ports": ports}}


def test_adding_a_claim_keeps_existing_ports(tmp_path, monkeypatch):
    root = tmp_path / "worktree"
    root.mkdir()
    monkeypatch.setattr("worktree_env.registry.port_is_free", lambda _port: True)
    registry = _registered(root, {"console": 42004, "host": 42005, "gateway": 42006})

    block_start, ports = allocate_ports(
        _profile(tmp_path, "console", "host", "gateway", "core"),
        root,
        registry,
        PortPool(42000, 42010),
    )

    assert block_start == 42004
    assert ports == {
        "console": 42004,
        "host": 42005,
        "gateway": 42006,
        "core": 42007,
    }


def test_adding_a_claim_extends_past_occupied_ports(tmp_path, monkeypatch):
    root = tmp_path / "worktree"
    root.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr("worktree_env.registry.port_is_free", lambda _port: True)
    registry = _registered(root, {"web": 42000})
    registry[str(other)] = {"profile": "example", "file": "other.yaml", "ports": {"x": 42001}}

    _block_start, ports = allocate_ports(
        _profile(tmp_path, "web", "api"), root, registry, PortPool(42000, 42010)
    )

    assert ports == {"web": 42000, "api": 42002}


def test_removing_a_claim_keeps_remaining_ports(tmp_path, monkeypatch):
    root = tmp_path / "worktree"
    root.mkdir()
    monkeypatch.setattr("worktree_env.registry.port_is_free", lambda _port: True)
    registry = _registered(root, {"console": 42004, "host": 42005, "admin": 42006})

    _block_start, ports = allocate_ports(
        _profile(tmp_path, "console", "host"), root, registry, PortPool(42000, 42010)
    )

    assert ports == {"console": 42004, "host": 42005}


def test_saved_port_taken_by_another_worktree_is_reallocated_alone(tmp_path, monkeypatch):
    root = tmp_path / "worktree"
    root.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr("worktree_env.registry.port_is_free", lambda _port: True)
    registry = _registered(root, {"web": 42000, "api": 42001})
    registry[str(other)] = {"profile": "example", "file": "other.yaml", "ports": {"x": 42001}}

    _block_start, ports = allocate_ports(
        _profile(tmp_path, "web", "api"), root, registry, PortPool(42000, 42010)
    )

    assert ports == {"web": 42000, "api": 42002}


def test_extension_wraps_at_the_top_of_the_pool(tmp_path, monkeypatch):
    root = tmp_path / "worktree"
    root.mkdir()
    monkeypatch.setattr("worktree_env.registry.port_is_free", lambda _port: True)
    registry = _registered(root, {"web": 42005, "api": 42006})

    _block_start, ports = allocate_ports(
        _profile(tmp_path, "web", "api", "core"),
        root,
        registry,
        PortPool(42000, 42006),
    )

    assert ports == {"web": 42005, "api": 42006, "core": 42000}


def test_rebalance_discards_the_saved_block(tmp_path, monkeypatch):
    root = tmp_path / "worktree"
    root.mkdir()
    monkeypatch.setattr("worktree_env.registry.port_is_free", lambda _port: True)
    registry = _registered(root, {"web": 42008, "api": 42009})

    block_start, ports = allocate_ports(
        _profile(tmp_path, "web", "api"),
        root,
        registry,
        PortPool(42000, 42010),
        rebalance=True,
    )

    assert block_start == 42000
    assert ports == {"web": 42000, "api": 42001}


def test_saved_port_outside_the_pool_is_reallocated_alone(tmp_path, monkeypatch):
    root = tmp_path / "worktree"
    root.mkdir()
    monkeypatch.setattr("worktree_env.registry.port_is_free", lambda _port: True)
    registry = _registered(root, {"web": 39999, "api": 42005})

    _block_start, ports = allocate_ports(
        _profile(tmp_path, "web", "api"), root, registry, PortPool(42000, 42010)
    )

    assert ports == {"web": 42006, "api": 42005}


def test_atomic_registry_round_trip_and_corruption_error(app_paths):
    save_registry(app_paths, {"/tmp/example": {"ports": {"web": 42000}}})
    assert load_registry(app_paths)["/tmp/example"]["ports"]["web"] == 42000

    app_paths.registry.write_text("not json")
    with pytest.raises(WteError, match="cannot read port registry"):
        load_registry(app_paths)


def test_prune_drops_missing_directories(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    registry = {str(live): {}, str(tmp_path / "missing"): {}}

    assert list(prune_registry(registry)) == [str(live)]
