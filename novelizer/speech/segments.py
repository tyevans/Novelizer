"""Turn parsed spans into a dense, ordered segment list covering the whole chapter.

Gaps between tagged spans become explicit narration segments. That completeness
is the point: a voicing pipeline reads this list front to back and needs the
connective tissue, not just the dialogue.
"""
from __future__ import annotations

from dataclasses import dataclass

from novelizer.speech.markers import RawSpan

NARRATION = "narration"


@dataclass(frozen=True)
class Segment:
    index: int
    kind: str
    char_name: str
    start: int
    end: int
    text: str


def segment_prose(clean_prose: str, spans: list[RawSpan]) -> list[Segment]:
    """Interleave narration gaps with the given spans, densely indexed.

    Zero-length gaps are dropped so adjacent spans do not produce empty
    narration segments.
    """
    segments: list[Segment] = []
    cursor = 0

    def _emit(kind: str, char_name: str, start: int, end: int) -> None:
        if end <= start:
            return
        segments.append(Segment(
            index=len(segments), kind=kind, char_name=char_name,
            start=start, end=end, text=clean_prose[start:end],
        ))

    for span in sorted(spans, key=lambda s: s.start):
        _emit(NARRATION, "", cursor, span.start)
        _emit(span.kind, span.char_name, span.start, span.end)
        cursor = span.end

    _emit(NARRATION, "", cursor, len(clean_prose))
    return segments
