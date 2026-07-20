"""Structure test: outlining/SKILL.md must name only real BEAT_TEMPLATES
keys as adoptable `framework` values.

M10 review finding: the skill narrated Save the Cat / Seven-Point / Story
Circle as choices to adopt, but `BEAT_TEMPLATES` only has "six-position"
and "kishotenketsu" -- naming any other framework in a `BlueprintPlan`
silently mints zero beats at commit. This test pins the fix: every real
key must be named, and the non-adoptable framework names must not appear
in their lowercase-hyphenated (i.e. adoptable-`framework`-value) form.
"""

from __future__ import annotations

import importlib.resources

from novelizer.canon.beat_templates import BEAT_TEMPLATES

NON_ADOPTABLE_FRAMEWORK_VALUES = ["save-the-cat", "seven-point", "story-circle"]


def _outlining_skill_text() -> str:
    root = importlib.resources.files("novelizer.skills_packs")
    return (root / "outlining" / "SKILL.md").read_text(encoding="utf-8")


def test_skill_mentions_every_real_beat_template_key() -> None:
    text = _outlining_skill_text()
    for key in BEAT_TEMPLATES:
        assert key in text, f"outlining/SKILL.md never mentions real framework key {key!r}"


def test_skill_does_not_present_non_template_names_as_adoptable_values() -> None:
    text = _outlining_skill_text()
    for bogus_value in NON_ADOPTABLE_FRAMEWORK_VALUES:
        assert bogus_value not in text, (
            f"outlining/SKILL.md contains {bogus_value!r} in adoptable-framework-value "
            "form (lowercase-hyphenated) -- it must only appear (if at all) as prose "
            "reference material, not something an agent could pass as `framework`"
        )
