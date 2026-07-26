"""Parse the Author's inline speaker markup into clean prose plus offset spans.

Pure: no I/O, no model calls. The markers are unambiguous by construction
(explicit open/close tags), so extraction is deterministic code -- the LLM is
involved only when this module reports a problem it cannot resolve.

Offsets are into the CLEAN prose, so the span list and the stored chapter prose
agree by construction. tests/speech/test_markers.py pins that as a round-trip
property.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SPEECH = "speech"
THOUGHT = "thought"
_KINDS = (SPEECH, THOUGHT)

# Non-greedy body so adjacent spans do not swallow the text between them.
_TAG_RE = re.compile(
    r'<(?P<kind>speech|thought)\s+char="(?P<char>[^"]*)"\s*>(?P<body>.*?)</(?P=kind)\s*>',
    re.DOTALL,
)

# Any tag-ish remnant left after well-formed pairs are consumed is malformed.
_REMNANT_RE = re.compile(r"</?(?:speech|thought)\b[^>]*>?")


@dataclass(frozen=True)
class RawSpan:
    kind: str
    char_name: str
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class ParseResult:
    clean_prose: str
    spans: list[RawSpan]
    problems: list[str]


def parse_markers(marked: str) -> ParseResult:
    """Strip speaker markup and return the clean prose with its spans.

    Never raises: malformed input is reported in `problems` and the prose is
    still cleaned as far as possible, because a chapter must remain
    committable even when its markup is wrong.
    """
    out: list[str] = []
    spans: list[RawSpan] = []
    problems: list[str] = []
    cursor = 0
    clean_len = 0

    for match in _TAG_RE.finditer(marked):
        before = marked[cursor:match.start()]
        out.append(before)
        clean_len += len(before)

        body = match.group("body")
        if _REMNANT_RE.search(body):
            problems.append(
                f"nested speaker tag inside <{match.group('kind')} "
                f"char={match.group('char')!r}>"
            )

        start = clean_len
        out.append(body)
        clean_len += len(body)
        spans.append(RawSpan(
            kind=match.group("kind"),
            char_name=match.group("char").strip(),
            start=start,
            end=clean_len,
            text=body,
        ))
        cursor = match.end()

    tail = marked[cursor:]
    for remnant in _REMNANT_RE.finditer(tail):
        problems.append(f"unclosed or stray speaker tag: {remnant.group(0)!r}")
    out.append(_REMNANT_RE.sub("", tail))

    return ParseResult(clean_prose="".join(out), spans=spans, problems=problems)
