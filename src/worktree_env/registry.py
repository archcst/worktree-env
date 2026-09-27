"""Concurrent, sticky allocation of per-worktree port blocks."""

from __future__ import annotations

import fcntl
import json
import os
import socket
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Set, Tuple

from .config import PortPool
from .paths import AppPaths
from .profiles import Profile, port_claims_value
from .utils import WteError

Registry = Dict[str, Any]


def load_registry(paths: AppPaths) -> Registry:
    """Load the registry and reject corruption instead of silently resetting it."""
    if not paths.registry.exists():
        return {}
    try:
        raw = json.loads(paths.registry.read_text() or "{}")
    except (OSError, json.JSONDecodeError) as exc:
        raise WteError(f"cannot read port registry {paths.registry}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WteError(f"port registry root must be an object: {paths.registry}")
    return raw


def save_registry(paths: AppPaths, registry: Registry) -> None:
    """Atomically replace the registry while preserving a valid old copy on crash."""
    paths.root.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(registry, indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".ports-", suffix=".tmp", dir=paths.root)
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, paths.registry)
        try:
            directory_fd = os.open(str(paths.root), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Directory fsync is not available on every supported filesystem.
            pass
    finally:
        if temp_path.exists():
            temp_path.unlink()


@contextmanager
def registry_lock(paths: AppPaths) -> Iterator[None]:
    """Serialize registry reads, allocation, garbage collection, and writes."""
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.lock.touch(exist_ok=True)
    try:
        os.chmod(paths.lock, 0o600)
    except OSError:
        pass
    with paths.lock.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def is_live_worktree(path: str) -> bool:
    """Return whether a registered worktree directory still exists."""
    return Path(path).is_dir()


def prune_registry(registry: Registry) -> Registry:
    """Drop entries whose worktree directory no longer exists."""
    return {
        path: metadata
        for path, metadata in registry.items()
        if isinstance(metadata, dict) and is_live_worktree(path)
    }


def port_is_free(port: int) -> bool:
    """Return whether a new process can bind the port on loopback."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _used_by_other_worktrees(registry: Registry, current: str) -> Set[int]:
    used: Set[int] = set()
    for path, metadata in registry.items():
        if path == current or not isinstance(metadata, dict) or not Path(path).exists():
            continue
        ports = metadata.get("ports") or {}
        if not isinstance(ports, dict):
            continue
        for raw_port in ports.values():
            try:
                used.add(int(raw_port))
            except (TypeError, ValueError):
                continue
    return used


def _reusable_saved_ports(
    ids: List[str],
    saved: Any,
    pool: PortPool,
    used: Set[int],
) -> Dict[str, int]:
    """Collect saved ports that remain valid for the current claim ids."""
    reusable: Dict[str, int] = {}
    if not isinstance(saved, dict):
        return reusable
    for port_id in ids:
        try:
            value = int(saved[port_id])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            pool.start <= value <= pool.end
            and value not in used
            and value not in reusable.values()
        ):
            reusable[port_id] = value
    return reusable


def _candidate_ports(pool: PortPool, first: int) -> Iterator[int]:
    """Yield pool ports from ``first`` upward, wrapping once at the top."""
    start = max(first, pool.start)
    for port in range(start, pool.end + 1):
        yield port
    for port in range(pool.start, min(start, pool.end + 1)):
        yield port


def _claim_free_port(
    port_id: str, pool: PortPool, unavailable: Set[int], first: int
) -> int:
    """Return the first bindable pool port at or after ``first``."""
    for port in _candidate_ports(pool, first):
        if port in unavailable:
            continue
        if port_is_free(port):
            return port
    raise WteError(f"no free port in pool {pool.start}-{pool.end} for claim {port_id!r}")


def _claim_fresh_block(
    ids: List[str], pool: PortPool, used: Set[int]
) -> Tuple[int, Dict[str, int]]:
    """Claim the first free contiguous block for every claim id."""
    count = len(ids)
    last_start = pool.end - count + 1
    for start in range(pool.start, last_start + 1):
        ports = list(range(start, start + count))
        if set(ports) & used:
            continue
        if all(port_is_free(port) for port in ports):
            return start, dict(zip(ids, ports))
    raise WteError(f"no free block of {count} ports in pool {pool.start}-{pool.end}")


def allocate_ports(
    profile: Profile,
    root: Path,
    registry: Registry,
    pool: PortPool,
    rebalance: bool = False,
) -> Tuple[int, Dict[str, int]]:
    """Reuse valid saved ports and allocate only claims that are missing.

    Adding or removing a claim never moves ports already saved for other
    claim ids, so the allocation may become non-contiguous. Pass
    ``rebalance=True`` to discard the saved allocation and claim one fresh
    contiguous block instead.
    """
    claims = port_claims_value(profile)
    if not isinstance(claims, list) or not claims:
        raise WteError(f"profile {profile.get('name')} has no ports to claim")
    try:
        ids = [str(item["id"]) for item in claims]
    except (TypeError, KeyError) as exc:
        raise WteError(f"profile {profile.get('name')} has an invalid port claim") from exc

    current = str(root)
    used = _used_by_other_worktrees(registry, current)
    existing = registry.get(current) if isinstance(registry.get(current), dict) else None
    profile_file = Path(profile.get("_file") or "").name
    saved: Dict[str, int] = {}
    if existing and not rebalance:
        same_profile = existing.get("profile") == profile.get("name")
        # File identity preserves allocations created before profiles had unique names.
        same_file = bool(profile_file) and existing.get("file") == profile_file
        if same_profile or same_file:
            saved = _reusable_saved_ports(ids, existing.get("ports"), pool, used)

    if not saved:
        return _claim_fresh_block(ids, pool, used)

    ports = dict(saved)
    taken = set(ports.values())
    # Extend the highest saved port first so ordinary growth keeps the block
    # contiguous when the following ports are free.
    extension = max(taken) + 1
    for port_id in ids:
        if port_id in ports:
            continue
        value = _claim_free_port(port_id, pool, used | taken, extension)
        ports[port_id] = value
        taken.add(value)
    return min(ports.values()), ports
