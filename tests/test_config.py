import pytest

from worktree_env.config import (
    initialize_config,
    initialize_profile_template,
    load_port_pool,
)
from worktree_env.paths import AppPaths
from worktree_env.utils import WteError


def test_init_generates_documented_port_range(tmp_path, monkeypatch):
    monkeypatch.setenv("WTE_CONFIG_HOME", str(tmp_path / "config"))
    paths = AppPaths.discover()

    assert initialize_config(paths) is True
    assert paths.config.read_text() == (
        "# Inclusive range used for per-worktree port allocation.\n"
        "port-range:\n"
        "  start: 20000\n"
        "  end: 29999\n"
    )
    assert load_port_pool(paths).start == 20000


def test_init_template_is_commented_and_not_an_active_yaml_profile(app_paths):
    template, created = initialize_profile_template(app_paths)

    assert created is True
    assert template.name == "project.example.yaml.template"
    text = template.read_text()
    assert "# A unique identifier" in text
    assert "# Port IDs form one contiguous block" in text
    assert "# Optional shared files or directories" in text
    assert "# Optional generated files" in text
    assert "# Optional worktree initializers" in text
    assert "main-worktree:" in text
    assert "port-claims:" in text
    assert "link-files:" in text
    assert "write-files:\n  - target:" in text
    assert "setup-commands:" in text
    assert "skip-if:" in text
    assert "$HOME/.config/example-app/frontend.env" in text
    assert "$HOME/.config/example-app/backend.env" in text
    assert "cwd: apps/frontend" in text
    assert "cwd: apps/backend" in text
    assert "Monitor refreshes automatically" in text


def test_legacy_port_range_key_remains_supported(app_paths):
    app_paths.config.write_text("port_range:\n  start: 41000\n  end: 41099\n")

    pool = load_port_pool(app_paths)

    assert (pool.start, pool.end) == (41000, 41099)


def test_legacy_pool_key_is_rejected(app_paths):
    app_paths.config.write_text("pool:\n  start: 41000\n  end: 41099\n")

    with pytest.raises(WteError, match="renamed to 'port-range'"):
        load_port_pool(app_paths)
