"""Structure tests for the M10 "Craft" skills packs.

Each pack lives at novelizer/skills_packs/<name>/SKILL.md (+ references/).
These tests check packaging and frontmatter shape only -- they do not
validate craft content accuracy (that's a human/self-review concern).
"""

from __future__ import annotations

import importlib.resources

import pytest

PACK_NAMES = [
    "outlining",
    "promise-payoff",
    "character-arcs",
    "scene-sequel",
    "pacing",
]


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Naive split on '---' delimiters: '---\\n<yaml>\\n---\\n<body>'."""
    assert text.startswith("---"), "SKILL.md must start with frontmatter delimiter"
    parts = text.split("---", 2)
    assert len(parts) == 3, "SKILL.md must have exactly one frontmatter block"
    _, frontmatter, body = parts
    return frontmatter, body


def _parse_simple_yaml(frontmatter: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in frontmatter.strip().splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


@pytest.mark.parametrize("pack_name", PACK_NAMES)
def test_skill_md_exists(pack_name: str) -> None:
    root = importlib.resources.files("novelizer.skills_packs")
    skill_md = root / pack_name / "SKILL.md"
    assert skill_md.is_file(), f"missing {pack_name}/SKILL.md"


@pytest.mark.parametrize("pack_name", PACK_NAMES)
def test_frontmatter_name_matches_dir(pack_name: str) -> None:
    root = importlib.resources.files("novelizer.skills_packs")
    text = (root / pack_name / "SKILL.md").read_text(encoding="utf-8")
    frontmatter, _ = _split_frontmatter(text)
    fields = _parse_simple_yaml(frontmatter)
    assert fields.get("name") == pack_name


@pytest.mark.parametrize("pack_name", PACK_NAMES)
def test_frontmatter_description_nonempty(pack_name: str) -> None:
    root = importlib.resources.files("novelizer.skills_packs")
    text = (root / pack_name / "SKILL.md").read_text(encoding="utf-8")
    frontmatter, _ = _split_frontmatter(text)
    fields = _parse_simple_yaml(frontmatter)
    assert fields.get("description", "").strip()


@pytest.mark.parametrize("pack_name", PACK_NAMES)
def test_body_word_count_within_budget(pack_name: str) -> None:
    root = importlib.resources.files("novelizer.skills_packs")
    text = (root / pack_name / "SKILL.md").read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)
    word_count = len(body.split())
    assert word_count <= 2000, f"{pack_name}/SKILL.md body is {word_count} words (limit 2000)"


@pytest.mark.parametrize("pack_name", PACK_NAMES)
def test_references_nonempty(pack_name: str) -> None:
    root = importlib.resources.files("novelizer.skills_packs")
    refs_dir = root / pack_name / "references"
    assert refs_dir.is_dir(), f"missing {pack_name}/references/"
    ref_files = [f for f in refs_dir.iterdir() if f.is_file()]
    assert ref_files, f"{pack_name}/references/ has no files"
    for ref_file in ref_files:
        content = ref_file.read_text(encoding="utf-8")
        assert content.strip(), f"{ref_file} is empty"


def test_packaging_lists_five_packs() -> None:
    root = importlib.resources.files("novelizer.skills_packs")
    dir_names = {entry.name for entry in root.iterdir() if entry.is_dir()}
    for pack_name in PACK_NAMES:
        assert pack_name in dir_names, f"{pack_name} not discoverable via importlib.resources"
