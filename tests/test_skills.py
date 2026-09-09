"""Validate the distributed skill and its documented offline installation."""

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from worktree_env.profiles import validate_profiles
from worktree_env.projector import render_template

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "skills" / "wte-setup"
READMES = ("README.md", "README.zh-CN.md")


def install_block(readme):
    blocks = re.findall(r"```bash\n(.*?)```", (REPO / readme).read_text(), re.S)
    matches = [block for block in blocks if "cp -R skills/wte-setup" in block]
    assert len(matches) == 1
    return matches[0]


def run_install(readme, home):
    return subprocess.run(
        ["bash", "-c", install_block(readme)],
        cwd=REPO,
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
    )


def test_skill_metadata():
    text = (SKILL / "SKILL.md").read_text()
    assert text.startswith("---\n")
    metadata = yaml.safe_load(text.split("---", 2)[1])
    assert metadata["name"] == SKILL.name
    assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", metadata["name"])
    assert len(metadata["name"]) <= 64
    assert 0 < len(metadata["description"]) <= 1024
    assert len(metadata.get("compatibility", "")) <= 500


def test_skill_references_are_self_contained():
    documents = list(SKILL.rglob("*.md"))
    assert documents
    for path in documents:
        content = path.read_text()
        assert content.count("```") % 2 == 0, path
        for link in re.findall(r"\]\(([^)]+)\)", content):
            if "://" in link or link.startswith("#"):
                continue
            target = (path.parent / link.split("#")[0]).resolve()
            target.relative_to(SKILL.resolve())
            assert target.is_file(), (path, link)


def test_skill_yaml_examples(app_paths, tmp_path):
    content = (SKILL / "references" / "configuration.md").read_text()
    blocks = re.findall(r"```yaml\n(.*?)```", content, re.S)
    assert blocks
    profiles = []
    for block in blocks:
        example = yaml.safe_load(block)
        assert isinstance(example, dict)
        if "name" not in example:
            continue
        example["match"]["main-worktree"] = str(tmp_path)
        (app_paths.profiles / "example.yaml").write_text(yaml.safe_dump(example))
        errors, _warnings = validate_profiles(app_paths)
        assert not errors
        ports = {claim["id"]: 21000 + i for i, claim in enumerate(example["port-claims"])}
        for spec in example.get("write-files", []):
            assert "${" not in render_template(spec["body"], ports)
        profiles.append(example)
    assert profiles


def test_readmes_use_the_same_installation():
    assert install_block(READMES[0]) == install_block(READMES[1])


@pytest.mark.parametrize("readme", READMES)
def test_documented_install_copies_complete_skill(readme, tmp_path):
    home = tmp_path / "home with spaces"
    home.mkdir()
    result = run_install(readme, home)
    assert result.returncode == 0, result.stderr
    installed = home / ".agents" / "skills" / "wte-setup"
    expected = {p.relative_to(SKILL): p.read_bytes() for p in SKILL.rglob("*") if p.is_file()}
    actual = {p.relative_to(installed): p.read_bytes() for p in installed.rglob("*") if p.is_file()}
    assert actual == expected
    assert {p.name for p in home.iterdir()} == {".agents"}


@pytest.mark.parametrize("readme", READMES)
@pytest.mark.parametrize("kind", ("directory", "symlink", "dangling-symlink"))
def test_documented_install_preserves_existing_destination(readme, kind, tmp_path):
    dest = tmp_path / ".agents" / "skills" / "wte-setup"
    dest.parent.mkdir(parents=True)
    original = tmp_path / "personal-skill"
    if kind == "directory":
        dest.mkdir()
        marker = dest / "SKILL.md"
    else:
        dest.symlink_to(original, target_is_directory=True)
        marker = original / "SKILL.md"
        if kind == "symlink":
            original.mkdir()
    if kind != "dangling-symlink":
        marker.write_text("personal instructions\n")

    result = run_install(readme, tmp_path)

    assert result.returncode != 0
    assert "Already installed" in result.stderr
    if kind != "directory":
        assert dest.is_symlink()
        assert os.readlink(dest) == str(original)
    if kind == "dangling-symlink":
        assert not original.exists()
    else:
        assert marker.read_text() == "personal instructions\n"
        assert not (marker.parent / "references").exists()
