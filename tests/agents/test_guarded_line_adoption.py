import pytest
from novelizer.agents.base import BaseAgent

CASES = [
    ("character_keeper", "In character"),
    ("structure_analyst", "In character"),
    ("editor", "In character"),          # cast line only, not the voice line
    ("continuity_checker", "In character"),
    ("retconner", "In character"),
    ("world_architect", "In character"),
    ("author_cast", "In character"),
    ("author_voice", "Write in this prose voice"),
]


@pytest.mark.parametrize("name,label", CASES)
def test_guarded_line_byte_identical_to_prior_inline_pattern(name, label):
    value = "some casting note"
    old = f"\n\n{label}: {value}" if value else ""
    new = BaseAgent._guarded_line(label, value)
    assert new == old

    old_empty = f"\n\n{label}: {''}" if "" else ""
    new_empty = BaseAgent._guarded_line(label, "")
    assert new_empty == old_empty == ""
