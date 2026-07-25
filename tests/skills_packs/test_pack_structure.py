"""Structure tests for the M10 "Craft" skills packs.

Each pack lives at novelizer/skills_packs/<name>/SKILL.md (+ references/).
These tests check packaging and frontmatter shape only -- they do not
validate craft content accuracy (that's a human/self-review concern).
"""

from __future__ import annotations

import importlib.resources

import pytest

# Derived from the shipped package, not hand-listed: a hand-maintained copy is
# how a pack gets added without ever being shape-checked (the same drift that
# left the Curator out of the fleet-wide prompt sweeps). The roster itself is
# pinned where being wrong actually costs something -- what the skills
# container hands agents -- by tests/canon_fs/test_skills_seam.py's
# EXPECTED_PACKS, which the last test here cross-checks against.
_HIDDEN = {"__pycache__"}

PACK_NAMES = sorted(
    entry.name
    for entry in importlib.resources.files("novelizer.skills_packs").iterdir()
    if entry.is_dir() and entry.name not in _HIDDEN
)


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


def test_shipped_packs_match_the_registered_roster() -> None:
    """The derivation above sweeps whatever ships; this is the one place that
    says what SHOULD ship. A pack added on disk but never added to the seam
    test's roster is a pack nobody decided to route -- and one dropped from
    disk while still listed there is guidance agents are promised and cannot
    read."""
    from tests.canon_fs.test_skills_seam import EXPECTED_PACKS

    assert PACK_NAMES, "derivation found no packs -- it is broken"
    assert set(PACK_NAMES) == EXPECTED_PACKS
