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

    # Kinds whose outer tag was already reported via a "nested" problem: the
    # regex below stops at the first same-kind closing tag (the inner one),
    # so the outer's own closing tag is always left behind as a lone
    # remnant. That remnant is the other half of the SAME malformed
    # construct, not a second defect, so it is consumed silently here rather
    # than raising a duplicate "unclosed" problem.
    pending_nested_kinds: list[str] = []

    def strip_remnants(segment: str) -> str:
        """Remove any stray tag-ish text from a plain-prose segment.

        Every removed remnant is either folded into `pending_nested_kinds`
        (already accounted for) or reported as its own "unclosed or stray"
        problem -- one problem per genuinely-unaccounted-for remnant.
        """
        pieces: list[str] = []
        pos = 0
        for remnant in _REMNANT_RE.finditer(segment):
            pieces.append(segment[pos:remnant.start()])
            token = remnant.group(0)
            closing = re.match(r"^</(speech|thought)\s*>?$", token)
            if closing and closing.group(1) in pending_nested_kinds:
                pending_nested_kinds.remove(closing.group(1))
            else:
                problems.append(f"unclosed or stray speaker tag: {token!r}")
            pos = remnant.end()
        pieces.append(segment[pos:])
        return "".join(pieces)

    for match in _TAG_RE.finditer(marked):
        before = strip_remnants(marked[cursor:match.start()])
        out.append(before)
        clean_len += len(before)

        body = match.group("body")
        if _REMNANT_RE.search(body):
            problems.append(
                f"nested speaker tag inside <{match.group('kind')} "
                f"char={match.group('char')!r}>"
            )
            pending_nested_kinds.append(match.group("kind"))
            # The nested markup must never survive into clean prose or the
            # span's own text -- only the dialogue/thought body it wraps.
            body = _REMNANT_RE.sub("", body)

        start = clean_len
        out.append(body)
        clean_len += len(body)
        spans.append(RawSpan(
            kind=match.group("kind"),
            char_name=match.group("char").strip().replace("&quot;", '"'),
            start=start,
            end=clean_len,
            text=body,
        ))
        cursor = match.end()

    out.append(strip_remnants(marked[cursor:]))

    return ParseResult(clean_prose="".join(out), spans=spans, problems=problems)
