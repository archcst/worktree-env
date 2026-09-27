from pathlib import Path

from worktree_env.profiles import find_profile, main_worktree_root, validate_profiles


def test_linked_worktree_matches_its_main_profile(app_paths, git_worktrees):
    main, linked = git_worktrees
    (app_paths.profiles / "example.yaml").write_text(
        "name: example\n"
        "match:\n"
        f"  main_worktree: {main}\n"
        "ports:\n"
        "  - id: web\n"
    )

    profile = find_profile(app_paths, linked)

    assert profile is not None
    assert profile["name"] == "example"
    assert main_worktree_root(linked) == main


def test_validation_rejects_duplicate_main_worktrees(app_paths, git_worktrees):
    main, _ = git_worktrees
    for name in ("one", "two"):
        (app_paths.profiles / f"{name}.yaml").write_text(
            f"name: {name}\n"
            f"match:\n  main_worktree: {main}\n"
            "ports:\n  - id: web\n"
        )

    errors, _warnings = validate_profiles(app_paths)

    assert any("also configured" in error for error in errors)


def test_validation_accepts_setup_command_and_args_lists(app_paths, tmp_path):
    (app_paths.profiles / "example.yaml").write_text(
        "name: example\n"
        f"match:\n  main-worktree: {tmp_path}\n"
        "port-claims:\n  - id: web\n"
        "setup-commands:\n"
        "  - command: [npm]\n"
        "    args: [install]\n"
        "    cwd: .\n"
    )

    errors, _warnings = validate_profiles(app_paths)

    assert not errors


def test_validation_rejects_initializer_string_command(app_paths, tmp_path):
    (app_paths.profiles / "example.yaml").write_text(
        "name: example\n"
        f"match:\n  main_worktree: {tmp_path}\n"
        "ports:\n  - id: web\n"
        "init:\n"
        "  - command: npm install\n"
        "    cwd: .\n"
    )

    errors, _warnings = validate_profiles(app_paths)

    assert any(
        "init[0].command must be a non-empty string list" in error for error in errors
    )


def test_validation_accepts_worktree_template_variables(app_paths, tmp_path):
    (app_paths.profiles / "example.yaml").write_text(
        "name: example\n"
        f"match:\n  main-worktree: {tmp_path}\n"
        "port-claims:\n  - id: web\n"
        "write-files:\n"
        "  - target: compose.env\n"
        "    body: |\n"
        "      PROJECT=im-hub-${worktree_slug}\n"
        "      NAME=${worktree}\n"
    )

    errors, _warnings = validate_profiles(app_paths)

    assert not errors


def test_validation_rejects_port_ids_reserved_for_template_variables(app_paths, tmp_path):
    (app_paths.profiles / "example.yaml").write_text(
        "name: example\n"
        f"match:\n  main-worktree: {tmp_path}\n"
        "port-claims:\n  - id: worktree\n"
    )

    errors, _warnings = validate_profiles(app_paths)

    assert any("is reserved" in error for error in errors)
