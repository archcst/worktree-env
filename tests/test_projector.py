import pytest

from worktree_env.projector import apply_worktree, render_template, run_initializers
from worktree_env.registry import load_registry
from worktree_env.utils import WteError


def test_render_template_injects_worktree_variables(tmp_path):
    body = "NAME=${worktree}\nSLUG=${worktree_slug}\nPORT=${web}\n"

    rendered = render_template(body, {"web": 41000}, tmp_path / "Feature_X.Hub")

    assert rendered == "NAME=Feature_X.Hub\nSLUG=feature-x-hub\nPORT=41000\n"


def test_render_template_sanitizes_punctuation_only_names(tmp_path):
    rendered = render_template("SLUG=${worktree_slug}", {}, tmp_path / "___")

    assert rendered == "SLUG=worktree"


def test_render_template_without_root_rejects_worktree_variables():
    with pytest.raises(WteError, match="cannot render port template"):
        render_template("NAME=${worktree}", {})


@pytest.mark.parametrize(
    ("main_key", "claims_key", "links_key", "writes_key", "target_key"),
    [
        ("main-worktree", "port-claims", "link-files", "write-files", "target"),
        ("main_worktree", "ports", "secrets", "writes", "path"),
    ],
)
def test_projection_links_secrets_and_writes_ports(
    app_paths,
    git_worktrees,
    tmp_path,
    monkeypatch,
    main_key,
    claims_key,
    links_key,
    writes_key,
    target_key,
):
    main, linked = git_worktrees
    secret = tmp_path / "backend.env"
    secret.write_text("TOKEN=test\n")
    (app_paths.profiles / "example.yaml").write_text(
        "name: example\n"
        f"match:\n  {main_key}: {main}\n"
        f"{claims_key}:\n  - id: frontend\n  - id: backend\n"
        f"{links_key}:\n"
        f"  - source: {secret}\n"
        "    target: app/.env\n"
        f"{writes_key}:\n"
        f"  - {target_key}: app/.env.development\n"
        "    body: |\n"
        "      PORT=${frontend}\n"
        "      API=http://127.0.0.1:${backend}\n"
    )
    generated_path = linked / "app/.env.development"
    generated_path.parent.mkdir(parents=True)
    generated_path.write_text("KEEP=old\n")
    monkeypatch.setattr("worktree_env.registry.port_is_free", lambda _port: True)

    result = apply_worktree(app_paths, linked)

    assert result is not None
    assert (linked / "app/.env").is_symlink()
    assert (linked / "app/.env").resolve() == secret.resolve()
    generated = generated_path.read_text()
    assert "KEEP=old" not in generated
    assert "PORT=41000" in generated
    assert "API=http://127.0.0.1:41001" in generated
    assert load_registry(app_paths)[str(linked)]["profile"] == "example"


@pytest.mark.parametrize(
    ("setup_key", "skip_key"),
    [("setup-commands", "skip-if"), ("init", "skip_if")],
)
def test_initializers_execute_command_and_args_without_a_shell(
    tmp_path, monkeypatch, capsys, setup_key, skip_key
):
    calls = []
    profile = {
        "name": "example",
        setup_key: [
            {
                "command": ["tool", "subcommand"],
                "args": ["literal;value", "two words"],
                "cwd": ".",
                skip_key: "missing-marker",
            }
        ],
    }
    monkeypatch.setattr(
        "worktree_env.projector.tempfile.gettempdir", lambda: str(tmp_path)
    )
    monkeypatch.setattr(
        "worktree_env.projector.subprocess.Popen",
        lambda argv, **kwargs: calls.append((argv, kwargs)),
    )

    run_initializers(profile, tmp_path)

    assert calls[0][0] == ["tool", "subcommand", "literal;value", "two words"]
    assert calls[0][1]["cwd"] == str(tmp_path.resolve())
    assert "tool subcommand 'literal;value' 'two words'" in capsys.readouterr().err


def test_failed_projection_does_not_commit_a_registry_entry(
    app_paths, git_worktrees, monkeypatch
):
    main, linked = git_worktrees
    (app_paths.profiles / "example.yaml").write_text(
        "name: example\n"
        f"match:\n  main_worktree: {main}\n"
        "ports:\n  - id: web\n"
        "writes:\n  - path: generated.env\n    body: PORT=${web}\n"
    )
    monkeypatch.setattr("worktree_env.registry.port_is_free", lambda _port: True)

    def fail_writes(*_args):
        raise WteError("write failed")

    monkeypatch.setattr("worktree_env.projector.apply_writes", fail_writes)

    with pytest.raises(WteError, match="write failed"):
        apply_worktree(app_paths, linked)

    assert str(linked) not in load_registry(app_paths)
