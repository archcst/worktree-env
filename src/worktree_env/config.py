"""Machine-wide port-pool configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml

from .paths import AppPaths
from .utils import WteError

DEFAULT_POOL_START = 20000
DEFAULT_POOL_END = 29999
DEFAULT_PROFILE_TEMPLATE_NAME = "project.example.yaml.template"
DEFAULT_CONFIG = """# Inclusive range used for per-worktree port allocation.\nport-range:\n  start: 20000\n  end: 29999\n"""
DEFAULT_PROFILE_TEMPLATE = """# Copy this file to a root-level *.yaml file, then edit every example value.
# Example: cp project.example.yaml.template my-project.yaml
# When enabled, the Monitor refreshes automatically after profile changes.

# A unique identifier stored in the local port registry.
name: example-fullstack

match:
  # Exact path of this project's dedicated main worktree. Linked worktrees
  # created from it are matched automatically, regardless of their location.
  main-worktree: $HOME/code/example-app

# Port IDs form one contiguous block in declaration order. Use each ID as a
# ${placeholder} in write-files below.
port-claims:
  - id: frontend
  - id: backend

# Optional shared files or directories. Sources stay outside Git; targets become
# symlinks inside each matched worktree. Existing real directories are preserved.
# Targets must not overlap each other or write-files targets.
link-files:
  - source: $HOME/.config/example-app/frontend.env
    target: apps/frontend/.env
  - source: $HOME/.config/example-app/backend.env
    target: apps/backend/.env
  # - source: $HOME/.config/example-app/certs
  #   target: apps/backend/certs

# Optional generated files. Each target is overwritten completely on sync.
# Bodies support ${port id} placeholders plus the built-in ${worktree} and
# ${worktree_slug} (lowercase, [a-z0-9-]) per-worktree variables.
write-files:
  - target: apps/frontend/.env.development
    body: |
      VITE_PORT=${frontend}
      VITE_API_URL=http://127.0.0.1:${backend}

  - target: apps/backend/.env.development
    body: |
      PORT=${backend}
      CORS_ORIGIN=http://127.0.0.1:${frontend}

# Optional worktree initializers started by post-checkout, the Monitor, or wte sync.
# Commands are trusted local configuration executed directly in the background.
# args are appended to command, and skip-if is resolved relative to cwd.
setup-commands:
  - command: [npm]
    args: [install]
    cwd: apps/frontend
    skip-if: node_modules
  - command: [uv]
    args: [sync]
    cwd: apps/backend
    skip-if: .venv
"""


@dataclass(frozen=True)
class PortPool:
    """Inclusive bounds of the machine-local port pool."""

    start: int
    end: int

    def validate(self) -> None:
        if not 1 <= self.start <= 65535:
            raise WteError(f"port-range.start is outside 1-65535: {self.start}")
        if not 1 <= self.end <= 65535:
            raise WteError(f"port-range.end is outside 1-65535: {self.end}")
        if self.end < self.start:
            raise WteError("port-range.end must be greater than or equal to port-range.start")


def _load_yaml_mapping(path: Path) -> Dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise WteError(f"cannot read YAML file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WteError(f"YAML root must be a mapping: {path}")
    return raw


def load_port_pool(paths: AppPaths) -> PortPool:
    """Load the configured pool, using defaults when config.yaml is absent."""
    if not paths.config.exists():
        pool = PortPool(DEFAULT_POOL_START, DEFAULT_POOL_END)
        pool.validate()
        return pool
    data = _load_yaml_mapping(paths.config)
    if "pool" in data and "port-range" not in data and "port_range" not in data:
        raise WteError("config key 'pool' was renamed to 'port-range'")
    if "port-range" in data:
        raw_pool = data["port-range"]
    else:
        # ``port_range`` remains supported for configs created by older releases.
        raw_pool = data.get("port_range") or {}
    if not isinstance(raw_pool, dict):
        raise WteError(f"port-range must be a mapping: {paths.config}")
    try:
        start = int(raw_pool.get("start") or DEFAULT_POOL_START)
        end = int(raw_pool.get("end") or DEFAULT_POOL_END)
    except (TypeError, ValueError) as exc:
        raise WteError("port-range.start and port-range.end must be integers") from exc
    pool = PortPool(start, end)
    pool.validate()
    return pool


def initialize_config(paths: AppPaths) -> bool:
    """Create the default config if absent; return whether it was created."""
    paths.ensure()
    if paths.config.exists():
        return False
    paths.config.write_text(DEFAULT_CONFIG)
    return True


def initialize_profile_template(paths: AppPaths) -> tuple[Path, bool]:
    """Create the commented project template without making it an active profile."""
    paths.ensure()
    template = paths.root / DEFAULT_PROFILE_TEMPLATE_NAME
    if template.exists():
        return template, False
    template.write_text(DEFAULT_PROFILE_TEMPLATE)
    return template, True
