"""Turn attributed segments into chunks a text-to-speech pipeline can read.

Pure: takes chapters and their segments, returns chunks. The chunking mode is
the caller's knob, but it can only ever GROUP segments -- a chunk never spans
two speakers, two kinds, or two chapters, so no chunking choice can blur the
attribution the Attributor established.

Two emitters: JSON for the pipeline, annotated prose for a human checking the
attribution by eye. Both are render_* functions over the same chunks, so an
SSML target can be added without touching the chunker; building SSML now would
bind the format to one engine's voice-tag conventions before there is a
consumer to bind to.

The annotated rendering is DERIVED, never stored. Clean prose plus segments
already carries everything the marked-up prose does -- storing a second copy
would be duplicate state with no third source to arbitrate a drift.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

CHUNK_MODES: tuple[str, ...] = ("segment", "chapter", "budget")
FORMATS: tuple[str, ...] = ("json", "annotated")


@dataclass(frozen=True)
class Chunk:
    chapter_id: str
    chapter_ordinal: int
    kind: str
    character_id: str | None
    character_name: str
    text: str
    segment_indexes: list[int]


def build_voicing_export(chapters, segments, *, chunk_by: str, chunk_size: int) -> list[Chunk]:
    """Linearize chapters into voiced chunks.

    `chapters` is an ordered sequence (creation order is the chapter ordinal);
    `segments` maps chapter_id to that chapter's dense, ordered segment list.
    """
    if chunk_by not in CHUNK_MODES:
        raise ValueError(f"unknown chunk_by {chunk_by!r}; expected one of {CHUNK_MODES}")

    chunks: list[Chunk] = []
    for ordinal, chapter in enumerate(chapters, start=1):
        rows = segments.get(chapter.id, [])
        if not rows:
            continue
        if chunk_by == "segment":
            chunks.extend(_one_per_segment(chapter.id, ordinal, rows))
        elif chunk_by == "chapter":
            chunks.append(_whole_chapter(chapter.id, ordinal, rows))
        else:
            chunks.extend(_by_budget(chapter.id, ordinal, rows, chunk_size))
    return chunks


def _one_per_segment(chapter_id, ordinal, rows) -> list[Chunk]:
    return [
        Chunk(chapter_id=chapter_id, chapter_ordinal=ordinal, kind=r.kind,
              character_id=r.character_id, character_name=r.character_name,
              text=r.text, segment_indexes=[r.index])
        for r in rows
    ]


def _whole_chapter(chapter_id, ordinal, rows) -> Chunk:
    # A whole-chapter chunk has no single voice; it is a container for callers
    # that want per-chapter files and will re-read segment detail themselves.
    return Chunk(
        chapter_id=chapter_id, chapter_ordinal=ordinal, kind="chapter",
        character_id=None, character_name="",
        text="".join(r.text for r in rows),
        segment_indexes=[r.index for r in rows],
    )


def _by_budget(chapter_id, ordinal, rows, chunk_size) -> list[Chunk]:
    chunks: list[Chunk] = []
    buffer: list = []

    def flush() -> None:
        if not buffer:
            return
        chunks.append(Chunk(
            chapter_id=chapter_id, chapter_ordinal=ordinal, kind=buffer[0].kind,
            character_id=buffer[0].character_id, character_name=buffer[0].character_name,
            text="".join(r.text for r in buffer),
            segment_indexes=[r.index for r in buffer],
        ))
        buffer.clear()

    for row in rows:
        same_voice = bool(buffer) and (
            buffer[0].kind == row.kind and buffer[0].character_id == row.character_id
        )
        fits = same_voice and sum(len(r.text) for r in buffer) + len(row.text) <= chunk_size
        if not fits:
            flush()
        buffer.append(row)
    flush()
    return chunks


def render_json(chunks: list[Chunk], *, title: str) -> str:
    """Serialize chunks as the voicing pipeline's input document."""
    return json.dumps(
        {"title": title, "chunks": [asdict(c) for c in chunks]},
        ensure_ascii=False, indent=2,
    )


def render_annotated(chunks: list[Chunk]) -> str:
    """Rebuild the marked-up prose, for reading the attribution by eye.

    Round-trips through novelizer.speech.markers.parse_markers: this function
    and that parser are the two halves of one contract, and
    tests/export/test_voicing.py pins that they agree.

    Chunks are dense and ordered, so concatenating them with the non-narration
    ones re-wrapped reproduces the prose exactly -- no offsets needed.
    """
    parts: list[str] = []
    for chunk in chunks:
        if chunk.kind == "chapter":
            # A whole-chapter chunk has already flattened its speakers away.
            # Rendering it bare would silently drop every tag, so refuse: the
            # caller wants chunk_by="segment" or "budget".
            raise ValueError(
                "render_annotated needs voiced chunks; chunk_by='chapter' has no speaker "
                "to re-wrap. Use chunk_by='segment' or 'budget'."
            )
        if chunk.kind == "narration" or not chunk.character_name:
            parts.append(chunk.text)
        else:
            parts.append(
                f'<{chunk.kind} char="{chunk.character_name}">{chunk.text}</{chunk.kind}>'
            )
    return "".join(parts)
