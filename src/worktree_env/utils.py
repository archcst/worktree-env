"""Small shared helpers for subprocesses, paths, and diagnostics."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from string import Template
from typing import Any, Dict, Optional


class WteError(RuntimeError):
    """Base exception for expected user-facing failures."""


def log(message: str) -> None:
    """Write a consistently prefixed diagnostic to stderr."""
    sys.stderr.write(f"[wte] {message}\n")


def run_git(*args: str, cwd: Optional[Path] = None) -> str:
    """Run Git and return stripped stdout, raising a user-facing error."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git {' '.join(args)} failed"
        raise WteError(detail)
    return result.stdout.strip()


def expand_home(value: str) -> Path:
    """Expand ``~``, ``$HOME``, and ``${HOME}`` in a configured path."""
    text = str(value).strip()
    home = str(Path.home())
    text = text.replace("${HOME}", home).replace("$HOME", home)
    return Path(os.path.expanduser(text))


def expand_profile_path(
    value: str,
    profile: Dict[str, Any],
    root: Optional[Path] = None,
) -> Path:
    """Expand profile variables and resolve a relative path against ``root``."""
    text = str(value).strip()
    home = str(Path.home())
    text = text.replace("${HOME}", home).replace("$HOME", home)
    text = Template(text).safe_substitute(
        name=profile.get("name") or "",
        HOME=home,
    )
    path = Path(os.path.expanduser(text))
    if not path.is_absolute() and root is not None:
        path = root / path
    return path


WORKTREE_TEMPLATE_IDS = ("worktree", "worktree_slug")


def worktree_template_values(name: str) -> Dict[str, str]:
    """Return built-in template values derived from a worktree name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return {"worktree": name, "worktree_slug": slug or "worktree"}


def is_within(path: Path, root: Path) -> bool:
    """Return whether a resolved path is inside a resolved root (Python 3.9)."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def safe_worktree_target(root: Path, value: str) -> Path:
    """Resolve a profile target and reject paths outside the worktree."""
    raw = Path(str(value))
    target = raw if raw.is_absolute() else root / raw
    # Resolve the parent but not the final component: an existing target may be
    # a secret symlink that intentionally points outside the worktree.
    target = target.parent.resolve(strict=False) / target.name
    if not is_within(target.parent, root):
        raise WteError(f"target escapes the worktree: {value}")
    return target
