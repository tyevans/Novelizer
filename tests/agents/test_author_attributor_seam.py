"""The Author's contract and the Attributor's parser must agree.

Hand-built fixtures on both sides of a seam verify each side and never that
they agree -- so this test extracts the example the Author prompt actually
shows the model and parses it with the real parser. If someone edits the prompt
example into a shape the parser rejects, this fails.
"""
import re

from novelizer.agents.author import AUTHOR_SYSTEM_PROMPT
from novelizer.speech.markers import parse_markers
from novelizer.speech.segments import segment_prose

_TAGGED_LINE = re.compile(r'^.*<(?:speech|thought) char="[^"]+">.*$', re.MULTILINE)


def test_the_prompt_example_parses_cleanly():
    examples = _TAGGED_LINE.findall(AUTHOR_SYSTEM_PROMPT)
    assert examples, "the Author prompt must show at least one worked marker example"
    for line in examples:
        result = parse_markers(line)
        assert result.problems == [], f"prompt example does not parse: {line!r}"
        assert result.spans, f"prompt example produced no spans: {line!r}"


def test_the_prompt_example_segments_densely():
    examples = _TAGGED_LINE.findall(AUTHOR_SYSTEM_PROMPT)
    assert examples, "the Author prompt must show at least one worked marker example"
    example = examples[0]
    parsed = parse_markers(example)
    segments = segment_prose(parsed.clean_prose, parsed.spans)
    assert "".join(s.text for s in segments) == parsed.clean_prose
    assert [s.index for s in segments] == list(range(len(segments)))


def test_the_prompt_names_no_tag_the_parser_ignores():
    tags = set(re.findall(r"<(\w+) char=", AUTHOR_SYSTEM_PROMPT))
    assert tags, "expected to find at least one speaker tag in the prompt"
    assert tags <= {"speech", "thought"}, f"prompt teaches unknown tags: {tags}"
