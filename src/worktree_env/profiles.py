"""Project-profile loading, validation, and worktree matching."""

from __future__ import annotations

import json
import re
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

from .paths import AppPaths
from .utils import (
    WteError,
    WORKTREE_TEMPLATE_IDS,
    expand_home,
    run_git,
    safe_worktree_target,
)

Profile = Dict[str, Any]
PORT_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def aliased_value(
    mapping: Dict[str, Any], canonical: str, *legacy: str, default: Any = None
) -> Any:
    """Read a canonical configuration key, falling back to legacy aliases."""
    for key in (canonical, *legacy):
        if key in mapping:
            return mapping[key]
    return default


def _selected_key(mapping: Dict[str, Any], canonical: str, *legacy: str) -> str:
    """Return the key whose value :func:`aliased_value` will select."""
    return next((key for key in (canonical, *legacy) if key in mapping), canonical)


def parse_profile(path: Path) -> Profile:
    """Parse one YAML or JSON profile and attach its source path."""
    try:
        if path.suffix.lower() in (".yaml", ".yml"):
            raw = yaml.safe_load(path.read_text()) or {}
        else:
            raw = json.loads(path.read_text() or "{}")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise WteError(f"cannot parse profile {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WteError(f"profile root must be a mapping: {path}")
    raw["_file"] = str(path)
    return raw


def load_profiles(paths: AppPaths, strict: bool = True) -> List[Profile]:
    """Load profiles, preferring YAML when files share the same stem."""
    if not paths.profiles.is_dir():
        return []
    rank = {".yaml": 0, ".yml": 1, ".json": 2}
    files: List[Path] = []
    for pattern in ("*.yaml", "*.yml", "*.json"):
        files.extend(paths.profiles.glob(pattern))
    files = [path for path in files if path.resolve() != paths.config.resolve()]

    profiles: List[Profile] = []
    seen = set()
    for path in sorted(files, key=lambda item: (item.stem, rank[item.suffix.lower()])):
        if path.stem in seen:
            continue
        seen.add(path.stem)
        try:
            profile = parse_profile(path)
        except WteError:
            if strict:
                raise
            continue
        if profile.get("name"):
            profiles.append(profile)
        elif strict:
            raise WteError(f"profile has no name: {path}")
    return profiles


def worktree_root(cwd: Optional[Path] = None) -> Path:
    """Return the root of the Git worktree containing ``cwd``."""
    return Path(run_git("rev-parse", "--show-toplevel", cwd=cwd)).resolve()


def main_worktree_root(root: Path) -> Path:
    """Return the main worktree associated with ``root``.

    Normal and linked worktrees share the main worktree's ``.git`` directory.
    The porcelain listing is a fallback for repositories whose common Git
    directory uses a non-standard location.
    """
    try:
        common = Path(run_git("rev-parse", "--git-common-dir", cwd=root))
    except WteError:
        return root.resolve()
    common = (root / common).resolve() if not common.is_absolute() else common.resolve()
    if common.name == ".git":
        return common.parent

    try:
        listing = run_git("worktree", "list", "--porcelain", cwd=root)
        first = next(
            line[len("worktree ") :]
            for line in listing.splitlines()
            if line.startswith("worktree ")
        )
        return Path(first).resolve()
    except (WteError, StopIteration):
        return root.resolve()


def configured_main_worktree(profile: Profile) -> Optional[Path]:
    """Return the normalized main-worktree path configured by a profile."""
    match = profile.get("match") or {}
    raw = (
        aliased_value(match, "main-worktree", "main_worktree")
        if isinstance(match, dict)
        else None
    )
    if not raw:
        return None
    return expand_home(str(raw)).resolve()


def find_profile(paths: AppPaths, root: Path) -> Optional[Profile]:
    """Find the unique profile whose main worktree owns ``root``."""
    actual = main_worktree_root(root)
    hits = [
        profile
        for profile in load_profiles(paths)
        if configured_main_worktree(profile) == actual
    ]
    if not hits:
        return None
    if len(hits) > 1:
        sources = ", ".join(Path(item["_file"]).name for item in hits)
        raise WteError(f"multiple profiles match main worktree {actual}: {sources}")
    return hits[0]


def port_claims_value(profile: Profile) -> Any:
    """Read port claims while preserving the older ``ports``/``services`` fallback."""
    if "port-claims" in profile:
        return profile["port-claims"]
    return profile.get("ports") or profile.get("services") or []


def _port_claims(profile: Profile) -> Sequence[Dict[str, Any]]:
    raw = port_claims_value(profile)
    return raw if isinstance(raw, list) else []


def projection_target_errors(profile: Profile, root: Optional[Path] = None) -> List[str]:
    """Reject overlapping targets before any links or files are projected."""
    errors: List[str] = []
    targets: List[Tuple[str, Path]] = []
    for canonical, legacy in (("link-files", "secrets"), ("write-files", "writes")):
        entries = aliased_value(profile, canonical, legacy, default=[]) or []
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            value = (
                aliased_value(entry, "target", "path")
                if canonical == "write-files" else entry.get("target")
            )
            if not value:
                continue
            label = f"{canonical}[{index}].target"
            target = Path(str(value))
            if target == Path(".") or target.is_absolute() or ".." in target.parts:
                errors.append(f"{label} must be a relative path inside the worktree")
                continue
            if root is not None:
                try:
                    target = safe_worktree_target(root, str(value))
                except WteError as exc:
                    errors.append(f"{label}: {exc}")
                    continue
            for other_label, other in targets:
                if target == other or target in other.parents or other in target.parents:
                    errors.append(f"overlapping projection targets: {other_label} and {label}")
            targets.append((label, target))
    return errors


def validate_profiles(paths: AppPaths) -> Tuple[List[str], List[str]]:
    """Validate all profiles without requiring their worktrees to exist."""
    errors: List[str] = []
    warnings: List[str] = []
    try:
        profiles = load_profiles(paths)
    except WteError as exc:
        return [str(exc)], warnings

    names: Dict[str, Path] = {}
    main_roots: Dict[Path, Path] = {}
    for profile in profiles:
        source = Path(profile["_file"])
        label = source.name
        name = profile.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}: name must be a non-empty string")
        elif name in names:
            errors.append(f"{label}: duplicate profile name {name!r} (also in {names[name].name})")
        else:
            names[name] = source

        match = profile.get("match") or {}
        if isinstance(match, dict):
            main_key = _selected_key(match, "main-worktree", "main_worktree")
            main_raw = aliased_value(match, "main-worktree", "main_worktree")
        else:
            main_key = "main-worktree"
            main_raw = None
        main_root = configured_main_worktree(profile)
        if main_root is None:
            errors.append(f"{label}: match.{main_key} is required")
        elif not expand_home(str(main_raw)).is_absolute():
            errors.append(
                f"{label}: match.{main_key} must be an absolute or home-relative path"
            )
        elif main_root in main_roots:
            errors.append(
                f"{label}: main worktree is also configured by {main_roots[main_root].name}"
            )
        else:
            main_roots[main_root] = source
            if not main_root.exists():
                warnings.append(f"{label}: main worktree does not exist: {main_root}")

        if "port-claims" in profile:
            claims_key = "port-claims"
        elif profile.get("ports") or "services" not in profile:
            claims_key = "ports"
        else:
            claims_key = "services"
        claims = _port_claims(profile)
        if not claims:
            errors.append(f"{label}: {claims_key} must contain at least one claim")
            continue
        ids: List[str] = []
        for index, claim in enumerate(claims):
            port_id = claim.get("id") if isinstance(claim, dict) else None
            if not isinstance(port_id, str) or not PORT_ID.match(port_id):
                errors.append(f"{label}: {claims_key}[{index}].id is invalid")
            elif port_id in WORKTREE_TEMPLATE_IDS:
                errors.append(
                    f"{label}: {claims_key}[{index}].id {port_id!r} is reserved "
                    "for write-files template variables"
                )
            elif port_id in ids:
                errors.append(f"{label}: duplicate port id {port_id!r}")
            else:
                ids.append(port_id)

        mapping = {port_id: "0" for port_id in ids}
        mapping.update(dict.fromkeys(WORKTREE_TEMPLATE_IDS, "0"))
        writes_key = _selected_key(profile, "write-files", "writes")
        writes = aliased_value(profile, "write-files", "writes", default=[])
        if writes is None:
            writes = []
        if not isinstance(writes, list):
            errors.append(f"{label}: {writes_key} must be a list")
            continue
        for index, spec in enumerate(writes):
            if not isinstance(spec, dict):
                errors.append(f"{label}: {writes_key}[{index}] must be a mapping")
                continue
            target_key = _selected_key(spec, "target", "path")
            write_target = aliased_value(spec, "target", "path")
            if not write_target:
                errors.append(
                    f"{label}: {writes_key}[{index}].{target_key} is required"
                )
            elif Path(str(write_target)).is_absolute() or ".." in Path(
                str(write_target)
            ).parts:
                errors.append(
                    f"{label}: {writes_key}[{index}].{target_key} must stay inside "
                    "the worktree"
                )
            try:
                Template(str(spec.get("body") or "")).substitute(mapping)
            except (KeyError, ValueError) as exc:
                errors.append(
                    f"{label}: {writes_key}[{index}].body has an invalid variable: {exc}"
                )

        links_key = _selected_key(profile, "link-files", "secrets")
        links = aliased_value(profile, "link-files", "secrets", default=[])
        if links is None:
            links = []
        if not isinstance(links, list):
            errors.append(f"{label}: {links_key} must be a list")
        else:
            for index, entry in enumerate(links):
                if not isinstance(entry, dict):
                    errors.append(f"{label}: {links_key}[{index}] must be a mapping")
                    continue
                if not entry.get("source"):
                    errors.append(f"{label}: {links_key}[{index}].source is required")
                target = entry.get("target")
                if not target:
                    errors.append(f"{label}: {links_key}[{index}].target is required")
                elif Path(str(target)).is_absolute() or ".." in Path(str(target)).parts:
                    errors.append(
                        f"{label}: {links_key}[{index}].target must stay inside the worktree"
                    )

        errors.extend(f"{label}: {error}" for error in projection_target_errors(profile))

        setup_key = _selected_key(profile, "setup-commands", "init")
        setup_commands = aliased_value(
            profile, "setup-commands", "init", default=[]
        )
        if setup_commands is None:
            setup_commands = []
        if not isinstance(setup_commands, list):
            errors.append(f"{label}: {setup_key} must be a list")
        else:
            for index, entry in enumerate(setup_commands):
                if not isinstance(entry, dict):
                    errors.append(f"{label}: {setup_key}[{index}] must be a mapping")
                    continue
                command = entry.get("command")
                if (
                    not isinstance(command, list)
                    or not command
                    or not all(isinstance(value, str) for value in command)
                    or not command[0].strip()
                ):
                    errors.append(
                        f"{label}: {setup_key}[{index}].command must be a non-empty string list"
                    )
                args = entry.get("args", [])
                if not isinstance(args, list) or not all(
                    isinstance(value, str) for value in args
                ):
                    errors.append(
                        f"{label}: {setup_key}[{index}].args must be a string list"
                    )
                cwd = entry.get("cwd")
                if not isinstance(cwd, str) or not cwd.strip():
                    errors.append(f"{label}: {setup_key}[{index}].cwd is required")

    if not profiles:
        warnings.append(f"no profiles found in {paths.profiles}")
    return errors, warnings
