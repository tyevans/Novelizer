# Speech Attribution and Voicing Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tag every utterance in the prose with the character who speaks it, then export the book as an ordered, voice-attributed segment stream for downstream text-to-speech.

**Architecture:** The Author writes inline `<speech char="…">` / `<thought char="…">` tags. A new deterministic-first Attributor agent parses those tags, resolves speaker names to character ids, strips the markup, and commits clean prose plus an ordered span list in one `chapter.attributed` event. A projection stores segments; a pure chunker turns them into JSON for voicing, reachable from the ctrl+k palette.

**Tech Stack:** Python 3.12+, pydantic v2, aiosqlite (event store + projections), Textual (TUI), pytest + hypothesis, `uv` for dependency and test running.

Design spec: `docs/superpowers/specs/2026-07-25-speech-attribution-design.md`

## Global Constraints

- **Run tests with `uv run pytest`.** Never run the suite from the main checkout — only from this worktree.
- **Event sourcing is non-negotiable.** Projection handlers are a faithful fold: never dedupe inside a handler, never mutate state the log does not carry.
- **TDD, red then green.** Every task writes a failing test first and runs it to confirm it fails for the expected reason.
- **Property-based tests use `hypothesis`,** which is already a dev dependency.
- **New event types must be bucketed in `novelizer/canon/policy.py`.** `chapter.attributed` is mechanical bookkeeping from a deterministic agent — same class as `chapter.mined` — so it goes in `_NEVER_GATED`.
- **Projection tables are declared only in `novelizer/canon/projector.py::_CREATE`.** `PROJECTION_TABLES` is derived from that DDL by regex; never hand-maintain a table list.
- **Marker syntax is exactly** `<speech char="Name">…</speech>` and `<thought char="Name">…</thought>`. Double quotes, lowercase tag names, `char` attribute only.
- **Commit after every task.** Conventional-commit prefixes (`feat:`, `test:`, `docs:`, `fix:`).

---

## File Structure

**Create:**
- `novelizer/speech/__init__.py` — package marker
- `novelizer/speech/markers.py` — pure parser: marked prose → clean prose + spans + problems
- `novelizer/speech/segments.py` — pure segmentation: clean prose + spans → dense segment list including narration
- `novelizer/speech/resolve.py` — pure speaker-name → character-id resolution
- `novelizer/agents/attributor.py` — the Attributor agent
- `novelizer/export/voicing.py` — pure chunker + JSON emitter
- `novelizer/tui/voicing_export_screen.py` — modal screen
- `tests/speech/test_markers.py`, `tests/speech/test_segments.py`, `tests/speech/test_resolve.py`
- `tests/agents/test_attributor.py`
- `tests/export/test_voicing.py`
- `tests/agents/test_speech_marker_note.py`

**Modify:**
- `novelizer/canon/events.py` — `CHAPTER_ATTRIBUTED` type + `ChapterAttributed` payload
- `novelizer/canon/policy.py` — add to `_NEVER_GATED`
- `novelizer/canon/projector.py` — `speech_segments` DDL in `_CREATE`
- `novelizer/canon/projections/chapters.py` — `chapter_attributed` handler
- `novelizer/canon/read_store.py` — `list_speech_segments()`
- `novelizer/agents/prompts.py` — `SPEECH_MARKER_NOTE`
- `novelizer/agents/author.py` — marker contract in the Author prompt
- every tooled agent builder — append `SPEECH_MARKER_NOTE`
- `novelizer/agents/registry.py` — one line, after `author.SPEC`
- `novelizer/tui/app.py` — `export_voicing` palette command

The `novelizer/speech/` package is pure: no database, no LLM, no I/O. That is what makes the parser and chunker property-testable in isolation, and it is where the correctness of the whole feature lives.

---

### Task 1: Marker parser

The parser turns the Author's marked prose into clean prose plus offset spans. Everything downstream depends on those offsets being exactly right, so this task is where the round-trip property gets pinned.

**Files:**
- Create: `novelizer/speech/__init__.py`
- Create: `novelizer/speech/markers.py`
- Test: `tests/speech/test_markers.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `RawSpan(kind: str, char_name: str, start: int, end: int, text: str)` — frozen dataclass. `kind` is `"speech"` or `"thought"`. `start`/`end` are offsets into the **clean** prose; `text` equals `clean[start:end]`.
  - `ParseResult(clean_prose: str, spans: list[RawSpan], problems: list[str])` — frozen dataclass.
  - `parse_markers(marked: str) -> ParseResult`

- [ ] **Step 1: Write the failing test**

Create `tests/speech/__init__.py` (empty) and `tests/speech/test_markers.py`:

```python
from novelizer.speech.markers import parse_markers


def test_extracts_speech_span_and_strips_tags():
    marked = 'She turned. <speech char="Mira">"Twenty dollars."</speech> The rain kept on.'
    result = parse_markers(marked)
    assert result.clean_prose == 'She turned. "Twenty dollars." The rain kept on.'
    assert result.problems == []
    assert len(result.spans) == 1
    span = result.spans[0]
    assert span.kind == "speech"
    assert span.char_name == "Mira"
    assert span.text == '"Twenty dollars."'
    assert result.clean_prose[span.start:span.end] == span.text


def test_extracts_thought_spans_too():
    marked = '<thought char="Mira">Twenty. She had four.</thought>'
    result = parse_markers(marked)
    assert result.clean_prose == "Twenty. She had four."
    assert result.spans[0].kind == "thought"
    assert result.spans[0].char_name == "Mira"


def test_untagged_prose_yields_no_spans():
    result = parse_markers("The rain kept on.")
    assert result.clean_prose == "The rain kept on."
    assert result.spans == []
    assert result.problems == []


def test_unclosed_tag_is_reported_as_a_problem():
    result = parse_markers('<speech char="Mira">"Twenty dollars."')
    assert result.problems
    assert "unclosed" in result.problems[0].lower()


def test_nested_tag_is_reported_as_a_problem():
    marked = '<speech char="Mira">"He said <speech char="Jon">go</speech> and left."</speech>'
    result = parse_markers(marked)
    assert result.problems
    assert "nested" in result.problems[0].lower()


def test_multiple_spans_have_ascending_non_overlapping_offsets():
    marked = (
        '<speech char="A">"One."</speech> mid '
        '<speech char="B">"Two."</speech>'
    )
    result = parse_markers(marked)
    assert len(result.spans) == 2
    assert result.spans[0].end <= result.spans[1].start
    for span in result.spans:
        assert result.clean_prose[span.start:span.end] == span.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/speech/test_markers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.speech'`

- [ ] **Step 3: Write the implementation**

Create `novelizer/speech/__init__.py` as an empty file.

Create `novelizer/speech/markers.py`:

```python
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
```

Note on the nested case: the inner well-formed pair is matched first by
`finditer`, so the outer text is scanned as a remnant. Both a `nested` problem
and correct stripping result; the test asserts only that the problem is
reported.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/speech/test_markers.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Add the round-trip property test**

Append to `tests/speech/test_markers.py`:

```python
from hypothesis import given, strategies as st

_plain = st.text(
    alphabet=st.characters(blacklist_characters="<>\"", min_codepoint=32),
    min_size=0, max_size=40,
)
_names = st.sampled_from(["Mira", "Jon", "The Warden"])


@st.composite
def _marked_prose(draw):
    parts = []
    for _ in range(draw(st.integers(min_value=0, max_value=5))):
        parts.append(draw(_plain))
        if draw(st.booleans()):
            kind = draw(st.sampled_from(["speech", "thought"]))
            name = draw(_names)
            body = draw(_plain)
            parts.append(f'<{kind} char="{name}">{body}</{kind}>')
    return "".join(parts)


@given(_marked_prose())
def test_offsets_always_address_their_own_text(marked):
    result = parse_markers(marked)
    for span in result.spans:
        assert result.clean_prose[span.start:span.end] == span.text


@given(_marked_prose())
def test_spans_never_overlap_and_are_ordered(marked):
    result = parse_markers(marked)
    previous_end = 0
    for span in result.spans:
        assert span.start >= previous_end
        previous_end = span.end
```

- [ ] **Step 6: Run the property tests**

Run: `uv run pytest tests/speech/test_markers.py -v`
Expected: PASS, 8 tests

- [ ] **Step 7: Commit**

```bash
git add novelizer/speech/ tests/speech/
git commit -m "feat(speech): parse inline speaker markup into clean prose and offset spans"
```

---

### Task 2: Segmentation with narration fill

Spans alone are a dialogue-only sidecar. Voicing needs the connective tissue, so gaps between spans become explicit narration segments and the result is a dense, ordered linearization of the whole chapter.

**Files:**
- Create: `novelizer/speech/segments.py`
- Test: `tests/speech/test_segments.py`

**Interfaces:**
- Consumes: `RawSpan`, `parse_markers` from Task 1
- Produces:
  - `NARRATION: str = "narration"`
  - `Segment(index: int, kind: str, char_name: str, start: int, end: int, text: str)` — frozen dataclass. `char_name` is `""` for narration.
  - `segment_prose(clean_prose: str, spans: list[RawSpan]) -> list[Segment]`

- [ ] **Step 1: Write the failing test**

Create `tests/speech/test_segments.py`:

```python
from hypothesis import given, strategies as st

from novelizer.speech.markers import parse_markers
from novelizer.speech.segments import NARRATION, segment_prose


def _segments(marked):
    parsed = parse_markers(marked)
    return parsed.clean_prose, segment_prose(parsed.clean_prose, parsed.spans)


def test_fills_narration_between_spans():
    clean, segs = _segments(
        'He waited. <speech char="Mira">"Twenty."</speech> Rain fell.'
    )
    assert [s.kind for s in segs] == ["narration", "speech", "narration"]
    assert segs[0].text == "He waited. "
    assert segs[1].char_name == "Mira"
    assert segs[2].text == " Rain fell."


def test_indexes_are_dense_and_ordered():
    _, segs = _segments(
        '<speech char="A">"One."</speech> mid <speech char="B">"Two."</speech>'
    )
    assert [s.index for s in segs] == list(range(len(segs)))


def test_untagged_prose_is_one_narration_segment():
    _, segs = _segments("Just prose.")
    assert len(segs) == 1
    assert segs[0].kind == NARRATION
    assert segs[0].char_name == ""


def test_empty_prose_yields_no_segments():
    assert segment_prose("", []) == []


def test_adjacent_spans_produce_no_empty_narration():
    _, segs = _segments(
        '<speech char="A">"One."</speech><speech char="B">"Two."</speech>'
    )
    assert [s.kind for s in segs] == ["speech", "speech"]


@given(st.text(alphabet=st.characters(blacklist_characters="<>\"", min_codepoint=32), max_size=60))
def test_segments_concatenate_to_the_clean_prose(text):
    clean, segs = _segments(text)
    assert "".join(s.text for s in segs) == clean
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/speech/test_segments.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.speech.segments'`

- [ ] **Step 3: Write the implementation**

Create `novelizer/speech/segments.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/speech/test_segments.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add novelizer/speech/segments.py tests/speech/test_segments.py
git commit -m "feat(speech): segment chapters densely, filling narration between spans"
```

---

### Task 3: Speaker resolution

The Author writes names; the domain keys on ids. This resolves one to the other against the character roster, and — critically — never invents a character when it cannot.

**Files:**
- Create: `novelizer/speech/resolve.py`
- Test: `tests/speech/test_resolve.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (takes plain `Character`-shaped objects)
- Produces:
  - `build_name_index(characters) -> dict[str, str]` — lowercased name and alias → character id
  - `resolve_speaker(name: str, index: dict[str, str]) -> str | None`

- [ ] **Step 1: Write the failing test**

Create `tests/speech/test_resolve.py`:

```python
from novelizer.speech.resolve import build_name_index, resolve_speaker
from novelizer.store.models import Character


def _roster():
    return [
        Character(id="mira", name="Mira", aliases=["The Warden"]),
        Character(id="jon-vale", name="Jon Vale", aliases=[]),
    ]


def test_resolves_canonical_name_case_insensitively():
    index = build_name_index(_roster())
    assert resolve_speaker("mira", index) == "mira"
    assert resolve_speaker("MIRA", index) == "mira"


def test_resolves_an_alias():
    index = build_name_index(_roster())
    assert resolve_speaker("The Warden", index) == "mira"


def test_resolves_by_slug_fallback_when_name_is_unknown():
    # "Jon Vale" slugs to "jon-vale"; a stray spelling that slugs the same still lands.
    index = build_name_index(_roster())
    assert resolve_speaker("jon  vale", index) == "jon-vale"


def test_unknown_speaker_returns_none():
    index = build_name_index(_roster())
    assert resolve_speaker("Nobody", index) is None


def test_blank_speaker_returns_none():
    index = build_name_index(_roster())
    assert resolve_speaker("   ", index) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/speech/test_resolve.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.speech.resolve'`

- [ ] **Step 3: Write the implementation**

Create `novelizer/speech/resolve.py`:

```python
"""Resolve a freeform speaker name to a character id.

Follows the name+alias lowercase map already used by
novelizer/store/kg_projector.py, with slugify_character_name as a fallback so a
spacing or punctuation variant still lands on the right character.

Never mints an id: an unresolvable speaker returns None and is flagged
upstream. Inventing a character here would let a typo in prose create canon.
"""
from __future__ import annotations

from novelizer.canon.characters import slugify_character_name


def build_name_index(characters) -> dict[str, str]:
    """Map every lowercased canonical name and alias to its character id."""
    index: dict[str, str] = {}
    for character in characters:
        index[character.name.lower()] = character.id
        for alias in character.aliases:
            index[alias.lower()] = character.id
    return index


def resolve_speaker(name: str, index: dict[str, str]) -> str | None:
    """Return the character id for `name`, or None if it cannot be resolved."""
    cleaned = name.strip()
    if not cleaned:
        return None
    direct = index.get(cleaned.lower())
    if direct is not None:
        return direct
    slug = slugify_character_name(cleaned)
    return slug if slug in set(index.values()) else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/speech/test_resolve.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
git add novelizer/speech/resolve.py tests/speech/test_resolve.py
git commit -m "feat(speech): resolve speaker names to character ids without minting"
```

---

### Task 4: The event, the table, and the projection

This is the domain half: a new event type, its payload, the gating bucket, the table, and a faithful-fold handler that also rewrites the chapter's prose clean.

**Files:**
- Modify: `novelizer/canon/events.py`
- Modify: `novelizer/canon/policy.py:17-62` (the `_NEVER_GATED` set)
- Modify: `novelizer/canon/projector.py` (the `_CREATE` DDL string)
- Modify: `novelizer/canon/projections/chapters.py`
- Modify: `novelizer/canon/read_store.py`
- Test: `tests/canon/test_speech_attribution_projection.py`

**Interfaces:**
- Consumes: `Segment` from Task 2 (only conceptually — the payload carries plain pydantic models)
- Produces:
  - `EventType.CHAPTER_ATTRIBUTED = "chapter.attributed"`
  - `AttributedSegment(chapter_id: str, index: int, kind: str, character_id: str | None, character_name: str, start_offset: int, end_offset: int, text: str)` — pydantic model. `chapter_id` defaults to `""` on the event payload (the event already names the chapter) and is populated by `ReadStore.list_speech_segments`, where callers need it to group.
  - `ChapterAttributed(chapter_id: str, prose: str, segments: list[AttributedSegment], problems: list[str])` — pydantic model
  - `ReadStore.list_speech_segments(chapter_id: str | None = None) -> list[AttributedSegment]`

- [ ] **Step 1: Write the failing test**

Create `tests/canon/test_speech_attribution_projection.py`. Follow the fixture style already used in the neighbouring `tests/canon/` projection tests — if a `projector`/`read_store` fixture exists there, reuse it rather than building a new one.

```python
import pytest

from novelizer.canon.events import AttributedSegment, ChapterAttributed, EventType
from novelizer.store.models import Chapter


def _segments():
    return [
        AttributedSegment(index=0, kind="narration", character_id=None,
                          character_name="", start_offset=0, end_offset=4, text="He. "),
        AttributedSegment(index=1, kind="speech", character_id="mira",
                          character_name="Mira", start_offset=4, end_offset=9, text='"Hi."'),
    ]


@pytest.mark.asyncio
async def test_attribution_replaces_prose_and_stores_segments(canon):
    chapter = Chapter(id="ch1", title="One", prose='He. <speech char="Mira">"Hi."</speech>')
    await canon.commit("author", EventType.CHAPTER_CREATED, chapter.id, chapter)
    await canon.commit(
        "attributor", EventType.CHAPTER_ATTRIBUTED, "ch1",
        ChapterAttributed(chapter_id="ch1", prose='He. "Hi."', segments=_segments(), problems=[]),
    )

    stored = await canon.read.get_chapter("ch1")
    assert stored.prose == 'He. "Hi."'

    segments = await canon.read.list_speech_segments("ch1")
    assert [s.index for s in segments] == [0, 1]
    assert segments[1].character_id == "mira"
    assert stored.prose[segments[1].start_offset:segments[1].end_offset] == '"Hi."'


@pytest.mark.asyncio
async def test_replaying_the_same_event_twice_is_idempotent(canon):
    chapter = Chapter(id="ch1", title="One", prose='He. <speech char="Mira">"Hi."</speech>')
    await canon.commit("author", EventType.CHAPTER_CREATED, chapter.id, chapter)
    payload = ChapterAttributed(chapter_id="ch1", prose='He. "Hi."', segments=_segments(), problems=[])
    await canon.commit("attributor", EventType.CHAPTER_ATTRIBUTED, "ch1", payload)
    await canon.commit("attributor", EventType.CHAPTER_ATTRIBUTED, "ch1", payload)

    segments = await canon.read.list_speech_segments("ch1")
    assert len(segments) == 2, "handler must replace this chapter's rows, not append"


@pytest.mark.asyncio
async def test_re_attribution_replaces_the_previous_segment_set(canon):
    chapter = Chapter(id="ch1", title="One", prose="x")
    await canon.commit("author", EventType.CHAPTER_CREATED, chapter.id, chapter)
    await canon.commit(
        "attributor", EventType.CHAPTER_ATTRIBUTED, "ch1",
        ChapterAttributed(chapter_id="ch1", prose='He. "Hi."', segments=_segments(), problems=[]),
    )
    await canon.commit(
        "attributor", EventType.CHAPTER_ATTRIBUTED, "ch1",
        ChapterAttributed(
            chapter_id="ch1", prose="Only narration.",
            segments=[AttributedSegment(index=0, kind="narration", character_id=None,
                                        character_name="", start_offset=0, end_offset=15,
                                        text="Only narration.")],
            problems=[],
        ),
    )
    segments = await canon.read.list_speech_segments("ch1")
    assert len(segments) == 1
    assert segments[0].kind == "narration"


@pytest.mark.asyncio
async def test_attribution_is_never_gated(canon):
    from novelizer.canon.policy import _NEVER_GATED
    assert EventType.CHAPTER_ATTRIBUTED in _NEVER_GATED
```

If `tests/canon/` has no reusable `canon` fixture providing `.commit`, `.read`,
build one in this file mirroring the setup used by the nearest existing
projection test, and adjust the calls above to match its API.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_speech_attribution_projection.py -v`
Expected: FAIL — `ImportError: cannot import name 'AttributedSegment'`

- [ ] **Step 3: Add the event type and payloads**

In `novelizer/canon/events.py`, add to the `EventType` class next to `CHAPTER_SUMMARIZED` (around line 45):

```python
    CHAPTER_ATTRIBUTED = "chapter.attributed"
```

And add the payload models next to `ChapterSummarized` (around line 352):

```python
class AttributedSegment(BaseModel):
    """One voiced unit of a chapter: an utterance, an interior thought, or the
    narration between them. Offsets address the CLEAN prose carried on the same
    event, so the segment list and the stored chapter agree by construction.

    character_id is None when the speaker could not be resolved against the
    roster -- the segment still exports, carrying a null voice, because a typo
    in one tag must not cost the chapter its attribution.

    chapter_id is empty on the event -- the event already names its chapter --
    and is filled in by the read store, where callers grouping many chapters
    need it on the row."""

    chapter_id: str = ""
    index: int
    kind: str
    character_id: str | None = None
    character_name: str = ""
    start_offset: int
    end_offset: int
    text: str


class ChapterAttributed(BaseModel):
    """Payload for chapter.attributed -- the Attributor's pass over one chapter
    revision. Carries the prose with speaker markup STRIPPED, which the
    projection installs as the chapter's canonical text, plus the dense segment
    list projected into speech_segments. `problems` records malformed markup
    the parser could not resolve; it is surfaced as a flag, not an error."""

    chapter_id: str
    prose: str
    segments: list[AttributedSegment] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Bucket the event as never-gated**

In `novelizer/canon/policy.py`, add to `_NEVER_GATED` immediately after `EventType.CHAPTER_SUMMARIZED` (line 39):

```python
    EventType.CHAPTER_ATTRIBUTED,
```

- [ ] **Step 5: Add the table**

In `novelizer/canon/projector.py`, append to the `_CREATE` DDL string, after the `chapter_summaries` block:

```sql
CREATE TABLE IF NOT EXISTS speech_segments (
    chapter_id TEXT NOT NULL, segment_index INTEGER NOT NULL,
    kind TEXT NOT NULL, character_id TEXT, character_name TEXT NOT NULL DEFAULT '',
    start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL, text TEXT NOT NULL,
    PRIMARY KEY (chapter_id, segment_index)
);
```

- [ ] **Step 6: Write the projection handler**

In `novelizer/canon/projections/chapters.py`, append:

```python
@projects(EventType.CHAPTER_ATTRIBUTED)
async def chapter_attributed(ctx: ProjectionContext) -> None:
    """Install the stripped prose as canon and replace this chapter's segments.

    The DELETE is not a dedupe -- it is this event's full meaning. An
    attribution pass supersedes the previous pass for the same chapter, so
    replaying the log must land on the last pass's rows, not the union of
    every pass. A chapter that re-attributes with fewer segments must lose the
    extras.
    """
    p = ctx.payload
    existing = await load_record(ctx, "chapters", Chapter, p["chapter_id"])
    if existing is None:
        logger.warning(
            "chapter.attributed for unknown chapter_id=%s -- no-op", p["chapter_id"],
        )
        return
    cleaned = existing.model_copy(update={"prose": p["prose"]})
    await ctx.execute(
        upsert("chapters", _CHAPTER_COLUMNS, "?,?,?,?"),
        (cleaned.id, cleaned.model_dump_json(),
         cleaned.editorial_status.value, cleaned.supersedes_id),
    )
    await ctx.execute("DELETE FROM speech_segments WHERE chapter_id = ?", (p["chapter_id"],))
    for seg in p.get("segments", []):
        await ctx.execute(
            "INSERT INTO speech_segments (chapter_id, segment_index, kind, character_id,"
            " character_name, start_offset, end_offset, text) VALUES (?,?,?,?,?,?,?,?)",
            (p["chapter_id"], seg["index"], seg["kind"], seg.get("character_id"),
             seg.get("character_name", ""), seg["start_offset"], seg["end_offset"],
             seg["text"]),
        )
```

Check how sibling handlers call `ctx.execute` for non-upsert statements and
match that signature exactly.

- [ ] **Step 7: Add the read-side accessor**

In `novelizer/canon/read_store.py`, next to `list_chapters`, following the
querying style of the surrounding methods:

```python
    async def list_speech_segments(self, chapter_id: Optional[str] = None) -> list[AttributedSegment]:
        """Ordered voiced segments, chapter by chapter. Ordering is
        (chapter_id, segment_index); callers wanting reading order pair this
        with list_chapters(), whose creation order is the chapter ordinal."""
        sql = ("SELECT chapter_id, segment_index, kind, character_id, character_name,"
               " start_offset, end_offset, text FROM speech_segments")
        params: tuple = ()
        if chapter_id is not None:
            sql += " WHERE chapter_id = ?"
            params = (chapter_id,)
        sql += " ORDER BY chapter_id, segment_index"
        rows = await self._fetchall(sql, params)
        return [
            AttributedSegment(
                chapter_id=r[0], index=r[1], kind=r[2], character_id=r[3],
                character_name=r[4], start_offset=r[5], end_offset=r[6], text=r[7],
            )
            for r in rows
        ]
```

Import `AttributedSegment` from `novelizer.canon.events` at the top, and match
the module's existing row-fetching helper — if it is not `self._fetchall`, use
whatever the neighbouring methods use.

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_speech_attribution_projection.py -v`
Expected: PASS, 4 tests

- [ ] **Step 9: Run the surrounding canon suite for regressions**

Run: `uv run pytest tests/canon -q`
Expected: PASS — the new table must not disturb `PROJECTION_TABLES` derivation or rebuild tests.

- [ ] **Step 10: Commit**

```bash
git add novelizer/canon tests/canon/test_speech_attribution_projection.py
git commit -m "feat(canon): add chapter.attributed event, speech_segments table, and projection"
```

---

### Task 5: The Attributor agent

Deterministic first: parse, resolve, commit. The model is called only to repair markup the parser flagged, and a failed repair still commits what was parsed.

**Files:**
- Create: `novelizer/agents/attributor.py`
- Modify: `novelizer/agents/registry.py:12-19`
- Test: `tests/agents/test_attributor.py`

**Interfaces:**
- Consumes: `parse_markers`/`ParseResult` (Task 1), `segment_prose`/`Segment`/`NARRATION` (Task 2), `build_name_index`/`resolve_speaker` (Task 3), `ChapterAttributed`/`AttributedSegment`/`EventType.CHAPTER_ATTRIBUTED` (Task 4)
- Produces:
  - `Attributor` agent class
  - `build_attributor_runner(settings, callbacks=None)`
  - `SPEC: AgentSpec` with `name="attributor"`

- [ ] **Step 1: Write the failing test**

Create `tests/agents/test_attributor.py`. Mirror the fake-runner and stub-store
style of the nearest existing agent test (`tests/agents/test_summarizer*.py` if
present) rather than inventing new scaffolding.

```python
import pytest

from novelizer.agents.attributor import Attributor
from novelizer.canon.events import EventType
from novelizer.store.models import Chapter, Character


class _Runner:
    """The Attributor must not call the model on well-formed markup."""
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, _payload):
        self.calls += 1
        return {"structured_response": None}


@pytest.mark.asyncio
async def test_attributes_a_clean_chapter_without_calling_the_model(attributor_env):
    env = attributor_env(
        chapters=[Chapter(id="ch1", title="One",
                          prose='He waited. <speech char="Mira">"Twenty."</speech>')],
        characters=[Character(id="mira", name="Mira", aliases=[])],
    )
    await env.agent._run()

    committed = env.committed_of_type(EventType.CHAPTER_ATTRIBUTED)
    assert len(committed) == 1
    payload = committed[0]
    assert payload.prose == 'He waited. "Twenty."'
    assert [s.kind for s in payload.segments] == ["narration", "speech"]
    assert payload.segments[1].character_id == "mira"
    assert env.runner.calls == 0


@pytest.mark.asyncio
async def test_unresolvable_speaker_gets_a_null_id_and_raises_a_flag(attributor_env):
    env = attributor_env(
        chapters=[Chapter(id="ch1", title="One",
                          prose='<speech char="Nobody">"Hi."</speech>')],
        characters=[Character(id="mira", name="Mira", aliases=[])],
    )
    await env.agent._run()

    payload = env.committed_of_type(EventType.CHAPTER_ATTRIBUTED)[0]
    assert payload.segments[0].character_id is None
    assert payload.segments[0].character_name == "Nobody"
    assert env.committed_of_type(EventType.FLAG_CREATED), "unresolved speaker must be flagged"


@pytest.mark.asyncio
async def test_malformed_markup_still_commits_and_flags(attributor_env):
    env = attributor_env(
        chapters=[Chapter(id="ch1", title="One", prose='<speech char="Mira">"Hi."')],
        characters=[Character(id="mira", name="Mira", aliases=[])],
    )
    await env.agent._run()

    assert env.committed_of_type(EventType.CHAPTER_ATTRIBUTED), "a malformed chapter must not block"
    assert env.committed_of_type(EventType.FLAG_CREATED)


@pytest.mark.asyncio
async def test_already_attributed_chapters_are_skipped(attributor_env):
    env = attributor_env(
        chapters=[Chapter(id="ch1", title="One", prose="Plain.")],
        characters=[],
    )
    await env.agent._run()
    first = len(env.committed_of_type(EventType.CHAPTER_ATTRIBUTED))
    await env.agent._run()
    assert len(env.committed_of_type(EventType.CHAPTER_ATTRIBUTED)) == first


@pytest.mark.asyncio
async def test_readiness_is_backlog_proportional(attributor_env):
    env = attributor_env(
        chapters=[Chapter(id=f"ch{i}", title=str(i), prose="Plain.") for i in range(3)],
        characters=[],
    )
    assert await env.agent.readiness() > 0
    await env.agent._run()
    assert await env.agent.readiness() == 0.0
```

Build the `attributor_env` fixture in this file: it constructs an `Attributor`
over a fake read store (`list_chapters`, `list_characters`, `list_flags`
returning `[]`), a recording committer exposing `committed_of_type`, a fake
event store whose `events_since` returns the events the recording committer has
seen, and the `_Runner` above.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_attributor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.agents.attributor'`

- [ ] **Step 3: Write the implementation**

Create `novelizer/agents/attributor.py`:

```python
"""Formalize the Author's inline speaker markup into structured attribution.

The markers are unambiguous by construction, so this agent is deterministic in
the common case and never calls the model: parse, resolve names to ids, commit
clean prose plus segments. The model is reached for only when the parser
reports markup it cannot make sense of, and a failed repair still commits what
was parsed -- a typo in one tag must not cost a chapter its attribution.

Backlog is a pure log fold (brain/watermarks.current_done_ids), so a revised
chapter re-attributes with no mutable state. Same shape as the Summarizer.
"""
from __future__ import annotations

import logging

from novelizer.agents.base import BaseAgent, Runner
from novelizer.brain.watermarks import current_done_ids
from novelizer.canon.committer import Committer
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import AttributedSegment, ChapterAttributed, EventType
from novelizer.canon.read_store import ReadStore
from novelizer.speech.markers import parse_markers
from novelizer.speech.resolve import build_name_index, resolve_speaker
from novelizer.speech.segments import NARRATION, segment_prose

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You repair malformed speaker markup in prose. The prose uses
<speech char="Name">...</speech> for spoken dialogue and
<thought char="Name">...</thought> for rendered interior thought.

You are shown prose whose markup is broken -- an unclosed tag, a nested tag, a
malformed attribute. Return the SAME prose with the markup corrected: close what
is open, unnest what is nested, and leave every character of the actual prose
untouched. Never add, remove or reword prose. Never invent a speaker: if you
cannot tell who speaks, drop the tag and leave the text bare."""

FLAG_CATEGORY = "attribution"


class Attributor(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        event_store: EventStore,
        interval: int = 120,
        personality: str = "",
    ) -> None:
        super().__init__(runner, read_store, committer, interval,
                         name="attributor", personality=personality)
        self._events = event_store

    async def _unattributed(self) -> list:
        chapters = await self._read.list_chapters()
        done = current_done_ids(
            await self._events.events_since(0, event_types=[EventType.CHAPTER_ATTRIBUTED]),
            await self._events.events_since(0, event_types=[EventType.CHAPTER_REVISED]),
        )
        return [c for c in chapters if c.id not in done]

    async def readiness(self) -> float:
        pending = await self._unattributed()
        if not pending:
            return 0.0
        return await self._gate_on_watermark(min(1.0, len(pending) / 3))

    async def poll(self) -> dict:
        return {"pending": await self._unattributed()}

    async def work(self, ctx: dict) -> dict:
        index = build_name_index(await self._read.list_characters())
        results: dict[str, tuple[ChapterAttributed, list[str]]] = {}
        for chapter in ctx["pending"]:
            results[chapter.id] = await self._attribute(chapter, index)
        return results

    async def _attribute(self, chapter, index) -> tuple[ChapterAttributed, list[str]]:
        parsed = parse_markers(chapter.prose)
        if parsed.problems:
            repaired = await self._repair(chapter.prose)
            if repaired is not None:
                reparsed = parse_markers(repaired)
                if not reparsed.problems:
                    parsed = reparsed

        segments: list[AttributedSegment] = []
        unresolved: list[str] = []
        for seg in segment_prose(parsed.clean_prose, parsed.spans):
            character_id = None
            if seg.kind != NARRATION:
                character_id = resolve_speaker(seg.char_name, index)
                if character_id is None:
                    unresolved.append(seg.char_name)
            segments.append(AttributedSegment(
                index=seg.index, kind=seg.kind, character_id=character_id,
                character_name=seg.char_name, start_offset=seg.start,
                end_offset=seg.end, text=seg.text,
            ))

        problems = list(parsed.problems)
        for name in sorted(set(unresolved)):
            problems.append(f"unresolved speaker {name!r}")
        payload = ChapterAttributed(
            chapter_id=chapter.id, prose=parsed.clean_prose,
            segments=segments, problems=problems,
        )
        return payload, problems

    async def _repair(self, marked: str) -> str | None:
        try:
            result = await self._runner.ainvoke(
                {"messages": [{"role": "user", "content": marked}]}
            )
        except Exception:
            logger.warning("%s: repair call raised; committing the parsed result as-is",
                           self.name, exc_info=True)
            return None
        text = result.get("structured_response") or result.get("output")
        return text if isinstance(text, str) and text.strip() else None

    async def commit(self, results: dict, ctx: dict) -> None:
        from novelizer.agents.schemas import FlagDraft

        drafts: list[FlagDraft] = []
        for chapter_id, (payload, problems) in results.items():
            await self._committer.commit(
                self.name, EventType.CHAPTER_ATTRIBUTED, chapter_id, payload,
            )
            for problem in problems:
                drafts.append(FlagDraft(
                    description=f"chapter {chapter_id}: {problem}",
                    related_entry_ids=[chapter_id],
                ))
        await self._file_flags(drafts, FLAG_CATEGORY)

    async def _run(self) -> None:
        ctx = await self.poll()
        if not ctx["pending"]:
            self.note_pass()
            return
        results = await self.work(ctx)
        await self.commit(results, ctx)


def build_attributor_runner(settings, callbacks=None):
    from agent_kit import build_chat_model
    from deepagents import create_deep_agent
    # Repair is transcription, not composition: run cold.
    model = build_chat_model(
        settings.agent_model, settings.llm_base_url, settings.llm_api_key,
        temperature=0.0, max_tokens=settings.llm_max_tokens, callbacks=callbacks,
    )
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT)


from novelizer.agents.registry_types import AgentContext, AgentSpec, AgentTier


def _construct(ctx: AgentContext) -> Attributor:
    runner = ctx.runner_for("attributor", build_attributor_runner)
    return Attributor(
        runner, ctx.read, ctx.committer, ctx.events,
        personality=ctx.personalities.get("attributor", ""),
    )


SPEC = AgentSpec(name="attributor", tool_grant=None, construct=_construct,
                 tier=AgentTier.FULL)
```

Confirm the exact helper names against `novelizer/agents/base.py` — `_file_flags`
is the flag-filing helper described around line 78, `_gate_on_watermark` and
`note_pass` are used verbatim by the Summarizer. Confirm `FlagDraft`'s field
names in `novelizer/agents/schemas.py`. If `_run` needs the Summarizer's
fingerprint/watermark dance, copy it; the simpler form above is correct only if
`note_pass`/`_gate_on_watermark` suffice for this agent.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_attributor.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Register the agent**

In `novelizer/agents/registry.py`, add `attributor` to the import list and place
its SPEC immediately after `author.SPEC`:

```python
from novelizer.agents import (
    author, attributor, world_architect, character_keeper, editor,
    continuity_checker, retconner, curator, structure_analyst, summarizer, plotter, muse, triage,
    flaglabeler,
)
...
AGENT_REGISTRY: list[AgentSpec] = [
    world_architect.SPEC, character_keeper.SPEC, muse.SPEC,
    plotter.SPEC, author.SPEC, attributor.SPEC,
    editor.SPEC, continuity_checker.SPEC, retconner.SPEC, curator.SPEC, structure_analyst.SPEC,
    summarizer.SPEC,
    triage.SPEC,
    flaglabeler.SPEC,
]
```

The Attributor must precede the Editor: nothing that treats prose as final may see markup.

- [ ] **Step 6: Run the registry-derived suites**

Run: `uv run pytest tests/agents -q`
Expected: PASS. Several suites iterate `AGENT_REGISTRY` and will now include
`attributor` — notably `test_registry_tier.py`, `test_apply_settings_rebuild_coverage.py`,
`test_skills_wiring.py`, and `test_output_conventions_note.py`. If one fails
because the new agent lacks a surface it demands, add that surface to
`attributor.py`; do not weaken the test.

- [ ] **Step 7: Commit**

```bash
git add novelizer/agents/attributor.py novelizer/agents/registry.py tests/agents/test_attributor.py
git commit -m "feat(agents): add the Attributor, formalizing inline speaker markup"
```

---

### Task 6: Author contract and fleet-wide marker awareness

The Author must emit the markers, and every other agent must know to ignore them during the window before attribution runs.

**Files:**
- Modify: `novelizer/agents/prompts.py`
- Modify: `novelizer/agents/author.py` (the `AUTHOR_SYSTEM_PROMPT` text and the builder at `:418`)
- Modify: each tooled agent builder that concatenates `OUTPUT_CONVENTIONS_NOTE`
- Test: `tests/agents/test_speech_marker_note.py`

**Interfaces:**
- Consumes: nothing
- Produces: `prompts.SPEECH_MARKER_NOTE: str`, re-exported as `novelizer.agents.author.SPEECH_MARKER_NOTE` (matching how `RETRIEVAL_NOTE` is re-exported at `author.py:173-174`)

- [ ] **Step 1: Write the failing test**

Create `tests/agents/test_speech_marker_note.py`:

```python
"""Every agent that can see prose must know the markers are invisible.

Derived from AGENT_REGISTRY rather than a hand-written list: an agent added
later would otherwise silently miss the note. A test that copies the value it
guards protects nothing, so this asserts the note reaches each builder's real
system prompt.
"""
import inspect

import pytest

from novelizer.agents import prompts
from novelizer.agents.registry import AGENT_REGISTRY


def test_note_names_both_tags():
    assert "<speech" in prompts.SPEECH_MARKER_NOTE
    assert "<thought" in prompts.SPEECH_MARKER_NOTE


@pytest.mark.parametrize("spec", AGENT_REGISTRY, ids=lambda s: s.name)
def test_every_tooled_agent_prompt_carries_the_marker_note(spec, prompt_for_spec):
    prompt = prompt_for_spec(spec)
    if prompt is None:
        pytest.skip(f"{spec.name} builds no system prompt")
    assert "<speech" in prompt, f"{spec.name} prompt lacks the speaker-marker note"


def test_author_prompt_states_the_marker_contract():
    from novelizer.agents.author import AUTHOR_SYSTEM_PROMPT
    assert '<speech char="' in AUTHOR_SYSTEM_PROMPT
    assert '<thought char="' in AUTHOR_SYSTEM_PROMPT
```

Build `prompt_for_spec` in this file by copying the technique
`tests/agents/test_output_conventions_note.py` already uses to reach each
agent's assembled system prompt — read that file first and reuse its approach
exactly rather than inventing a second one.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_speech_marker_note.py -v`
Expected: FAIL — `AttributeError: module 'novelizer.agents.prompts' has no attribute 'SPEECH_MARKER_NOTE'`

- [ ] **Step 3: Add the shared note**

In `novelizer/agents/prompts.py`, append:

```python
# Prose carries inline speaker markup between the Author writing a chapter and
# the Attributor formalizing it. That window is short but real, and an agent
# that quotes or "corrects" the tags would corrupt canon -- so every agent that
# can see prose is told they are furniture.
SPEECH_MARKER_NOTE = (
    "\n\n## Speaker markup\n"
    "Prose may contain speaker tags: <speech char=\"Name\">\"...\"</speech> around spoken "
    "dialogue and <thought char=\"Name\">...</thought> around interior thought. They mark who "
    "is speaking for downstream narration and are stripped from the finished book. Read "
    "straight through them as if they were not there. Do not quote them, do not comment on "
    "them, do not treat them as an error or a style problem, and never reproduce them in "
    "anything you write except prose you are authoring."
)
```

- [ ] **Step 4: State the contract in the Author prompt**

In `novelizer/agents/author.py`, append to `AUTHOR_SYSTEM_PROMPT`:

```
## Marking who speaks

Wrap every line of spoken dialogue in a speaker tag, and every passage of
rendered interior thought in a thought tag:

    He stopped at the counter. <speech char="Mira">"Twenty dollars."</speech>
    <thought char="Jon">Twenty. He had four.</thought> He counted it out anyway.

Rules:
- Tag EVERY utterance, including short ones in a rapid exchange where no "she
  said" tells the reader who is speaking. That case is exactly why the tags
  exist -- nothing downstream can recover it from the prose alone.
- Use the character's canonical name or a known alias, spelled as it appears in
  canon. Never invent an id or a slug.
- Leave narration untagged. Do not tag reported or summarized speech that is not
  in quotation marks.
- Tags wrap the utterance including its quotation marks, and never nest.
```

- [ ] **Step 5: Append the note in every builder**

In `novelizer/agents/author.py`, re-export next to the existing aliases at
`:173-174`:

```python
SPEECH_MARKER_NOTE = prompts.SPEECH_MARKER_NOTE
```

Then in each builder that currently reads
`system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE + OUTPUT_CONVENTIONS_NOTE`,
append `+ SPEECH_MARKER_NOTE` and import it alongside `RETRIEVAL_NOTE_BASE`.
The sites are:

- `novelizer/agents/author.py:418`
- `novelizer/agents/plotter.py:337`
- `novelizer/agents/retconner.py:197`
- `novelizer/agents/structure_analyst.py:206`
- `novelizer/agents/triage.py:195`
- `novelizer/agents/world_architect.py:201`
- `novelizer/agents/continuity_checker.py:581`
- `novelizer/agents/editor.py:342`
- `novelizer/agents/curator.py:203`
- `novelizer/agents/character_keeper.py` (find the equivalent line)
- `novelizer/agents/muse.py` (find the equivalent line)

Use the test from Step 1 as the authority on which agents need it — it is
derived from the registry, so run it to discover any site this list missed.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_speech_marker_note.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add novelizer/agents/ tests/agents/test_speech_marker_note.py
git commit -m "feat(agents): teach the Author the marker contract and the fleet to ignore it"
```

---

### Task 7: Voicing export — chunker and JSON emitter

A pure function from chapters and segments to chunks. The chunking knob is configurable but constrained: it may group segments, never blend two voices.

**Files:**
- Create: `novelizer/export/voicing.py`
- Test: `tests/export/test_voicing.py`

**Interfaces:**
- Consumes: `AttributedSegment` (Task 4)
- Produces:
  - `Chunk(chapter_id: str, chapter_ordinal: int, kind: str, character_id: str | None, character_name: str, text: str, segment_indexes: list[int])` — frozen dataclass
  - `build_voicing_export(chapters, segments, *, chunk_by: str, chunk_size: int) -> list[Chunk]`
  - `render_json(chunks: list[Chunk], *, title: str) -> str`
  - `render_annotated(chunks: list[Chunk]) -> str`
  - `CHUNK_MODES: tuple[str, ...] = ("segment", "chapter", "budget")`
  - `FORMATS: tuple[str, ...] = ("json", "annotated")`

- [ ] **Step 1: Write the failing test**

Create `tests/export/test_voicing.py` (and `tests/export/__init__.py` if the
package marker is missing):

```python
import json

import pytest
from hypothesis import given, strategies as st

from novelizer.canon.events import AttributedSegment
from novelizer.export.voicing import build_voicing_export, render_annotated, render_json
from novelizer.store.models import Chapter


def _chapter(cid="ch1"):
    return Chapter(id=cid, title="One", prose="")


def _seg(index, kind, cid, name, text):
    return AttributedSegment(index=index, kind=kind, character_id=cid, character_name=name,
                             start_offset=0, end_offset=len(text), text=text)


def _mixed():
    return {"ch1": [
        _seg(0, "narration", None, "", "He waited. "),
        _seg(1, "speech", "mira", "Mira", '"One."'),
        _seg(2, "speech", "mira", "Mira", '"Two."'),
        _seg(3, "speech", "jon", "Jon", '"Three."'),
    ]}


def test_segment_mode_emits_one_chunk_per_segment():
    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by="segment", chunk_size=0)
    assert len(chunks) == 4
    assert chunks[1].character_id == "mira"


def test_chapter_mode_emits_one_chunk_per_chapter():
    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by="chapter", chunk_size=0)
    assert len(chunks) == 1
    assert chunks[0].segment_indexes == [0, 1, 2, 3]


def test_budget_mode_packs_same_voice_segments():
    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by="budget", chunk_size=100)
    # narration | mira("One." + "Two.") | jon
    assert len(chunks) == 3
    assert chunks[1].text == '"One.""Two."'
    assert chunks[1].character_id == "mira"


def test_budget_mode_never_merges_across_a_speaker_change():
    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by="budget", chunk_size=10_000)
    speakers = [c.character_id for c in chunks]
    assert speakers == [None, "mira", "jon"]


def test_budget_mode_never_merges_across_a_chapter_boundary():
    chapters = [_chapter("ch1"), _chapter("ch2")]
    segments = {
        "ch1": [_seg(0, "speech", "mira", "Mira", '"A."')],
        "ch2": [_seg(0, "speech", "mira", "Mira", '"B."')],
    }
    chunks = build_voicing_export(chapters, segments, chunk_by="budget", chunk_size=10_000)
    assert len(chunks) == 2
    assert [c.chapter_id for c in chunks] == ["ch1", "ch2"]


def test_chapter_ordinal_follows_chapter_order():
    chapters = [_chapter("ch1"), _chapter("ch2")]
    segments = {"ch1": [_seg(0, "narration", None, "", "a")],
                "ch2": [_seg(0, "narration", None, "", "b")]}
    chunks = build_voicing_export(chapters, segments, chunk_by="segment", chunk_size=0)
    assert [c.chapter_ordinal for c in chunks] == [1, 2]


def test_an_oversized_single_segment_is_not_dropped():
    segments = {"ch1": [_seg(0, "speech", "mira", "Mira", "x" * 500)]}
    chunks = build_voicing_export([_chapter()], segments, chunk_by="budget", chunk_size=10)
    assert len(chunks) == 1
    assert len(chunks[0].text) == 500


def test_unknown_chunk_mode_is_rejected():
    with pytest.raises(ValueError):
        build_voicing_export([_chapter()], _mixed(), chunk_by="nonsense", chunk_size=0)


def test_render_annotated_rebuilds_the_marked_prose():
    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by="segment", chunk_size=0)
    assert render_annotated(chunks) == (
        'He waited. '
        '<speech char="Mira">"One."</speech>'
        '<speech char="Mira">"Two."</speech>'
        '<speech char="Jon">"Three."</speech>'
    )


def test_render_annotated_refuses_chapter_chunks():
    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by="chapter", chunk_size=0)
    with pytest.raises(ValueError):
        render_annotated(chunks)


def test_render_annotated_leaves_narration_bare():
    segments = {"ch1": [_seg(0, "narration", None, "", "Just prose.")]}
    chunks = build_voicing_export([_chapter()], segments, chunk_by="segment", chunk_size=0)
    assert render_annotated(chunks) == "Just prose."


def test_render_annotated_round_trips_through_the_parser():
    """The rendering and the parser are the two halves of one contract."""
    from novelizer.speech.markers import parse_markers

    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by="segment", chunk_size=0)
    reparsed = parse_markers(render_annotated(chunks))
    assert reparsed.problems == []
    assert reparsed.clean_prose == "".join(s.text for s in _mixed()["ch1"])
    assert [s.char_name for s in reparsed.spans] == ["Mira", "Mira", "Jon"]


def test_render_json_round_trips():
    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by="segment", chunk_size=0)
    data = json.loads(render_json(chunks, title="Book"))
    assert data["title"] == "Book"
    assert len(data["chunks"]) == 4
    assert data["chunks"][1]["character_name"] == "Mira"


@given(st.integers(min_value=1, max_value=200))
def test_every_chunk_is_within_budget_or_a_single_segment(size):
    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by="budget", chunk_size=size)
    for chunk in chunks:
        assert len(chunk.text) <= size or len(chunk.segment_indexes) == 1


@given(st.sampled_from(["segment", "chapter", "budget"]), st.integers(min_value=1, max_value=200))
def test_chunking_never_loses_or_reorders_text(mode, size):
    chunks = build_voicing_export([_chapter()], _mixed(), chunk_by=mode, chunk_size=size)
    expected = "".join(s.text for s in _mixed()["ch1"])
    assert "".join(c.text for c in chunks) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/export/test_voicing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.export.voicing'`

- [ ] **Step 3: Write the implementation**

Create `novelizer/export/voicing.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/export/test_voicing.py -v`
Expected: PASS, 15 tests

- [ ] **Step 5: Commit**

```bash
git add novelizer/export/voicing.py tests/export/
git commit -m "feat(export): chunk attributed segments into a voicing JSON document"
```

---

### Task 8: The export screen and palette command

The last mile: a modal that mirrors `ExportScreen`, wired into ctrl+k.

**Files:**
- Create: `novelizer/tui/voicing_export_screen.py`
- Modify: `novelizer/tui/app.py` (add `_app_open_voicing_export` next to `_app_open_export` at :620, and an `AppCommand` in `APP_COMMANDS`)
- Test: `tests/tui/test_voicing_export_screen.py`

**Interfaces:**
- Consumes: `build_voicing_export`, `render_json`, `CHUNK_MODES` (Task 7); `ReadStore.list_speech_segments` (Task 4)
- Produces: `VoicingExportScreen(runtime)`

- [ ] **Step 1: Write the failing test**

TUI tests in this repo are load-sensitive; see `docs/TESTING-TUI.md`. Keep this
test off the app harness and exercise the screen's logic directly.

Create `tests/tui/test_voicing_export_screen.py`:

```python
import json
from pathlib import Path

import pytest

from novelizer.canon.events import AttributedSegment
from novelizer.store.models import Chapter


@pytest.mark.asyncio
async def test_writes_a_json_document_under_export(tmp_path, voicing_runtime):
    runtime = voicing_runtime(
        tmp_path,
        chapters=[Chapter(id="ch1", title="One", prose='He. "Hi."')],
        segments=[AttributedSegment(index=0, kind="speech", character_id="mira",
                                    character_name="Mira", start_offset=4,
                                    end_offset=9, text='"Hi."')],
    )
    from novelizer.tui.voicing_export_screen import VoicingExportScreen

    screen = VoicingExportScreen(runtime)
    screen.title_value = "Book"
    screen.chunk_by = "segment"
    path = await screen.write_export()

    assert path.parent == tmp_path / "export"
    assert path.suffix == ".json"
    data = json.loads(Path(path).read_text())
    assert data["chunks"][0]["character_name"] == "Mira"


@pytest.mark.asyncio
async def test_annotated_format_writes_marked_prose(tmp_path, voicing_runtime):
    runtime = voicing_runtime(
        tmp_path,
        chapters=[Chapter(id="ch1", title="One", prose='He. "Hi."')],
        segments=[
            AttributedSegment(chapter_id="ch1", index=0, kind="narration", character_id=None,
                              character_name="", start_offset=0, end_offset=4, text="He. "),
            AttributedSegment(chapter_id="ch1", index=1, kind="speech", character_id="mira",
                              character_name="Mira", start_offset=4, end_offset=9, text='"Hi."'),
        ],
    )
    from novelizer.tui.voicing_export_screen import VoicingExportScreen

    screen = VoicingExportScreen(runtime)
    screen.export_format = "annotated"
    screen.chunk_by = "segment"
    path = await screen.write_export()

    assert path.suffix == ".txt"
    assert Path(path).read_text() == 'He. <speech char="Mira">"Hi."</speech>'


@pytest.mark.asyncio
async def test_annotated_format_rejects_chapter_chunking(tmp_path, voicing_runtime):
    runtime = voicing_runtime(
        tmp_path,
        chapters=[Chapter(id="ch1", title="One", prose="x")],
        segments=[AttributedSegment(chapter_id="ch1", index=0, kind="narration",
                                    character_id=None, character_name="",
                                    start_offset=0, end_offset=1, text="x")],
    )
    from novelizer.tui.voicing_export_screen import VoicingExportScreen

    screen = VoicingExportScreen(runtime)
    screen.export_format = "annotated"
    screen.chunk_by = "chapter"
    assert await screen.write_export() is None
    assert "per-chapter" in screen._error


@pytest.mark.asyncio
async def test_reports_an_error_when_no_segments_exist(tmp_path, voicing_runtime):
    runtime = voicing_runtime(tmp_path, chapters=[], segments=[])
    from novelizer.tui.voicing_export_screen import VoicingExportScreen

    screen = VoicingExportScreen(runtime)
    assert await screen.write_export() is None
    assert "no attributed" in screen._error.lower()
```

Build the `voicing_runtime` fixture in this file: a simple object with
`.settings.db_path = str(tmp_path / "story.db")`, `.settings.story_title`, and a
`.read` stub exposing `list_chapters()` and `list_speech_segments()`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_voicing_export_screen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.tui.voicing_export_screen'`

- [ ] **Step 3: Write the screen**

Create `novelizer/tui/voicing_export_screen.py`:

```python
"""Voicing export as a modal drill-in, reachable from the command palette
(AppCommand "export_voicing" in app.py). Mirrors ExportScreen's shape.

write_export() holds all the logic and no widgets so it can be tested without
mounting the app -- TUI harness tests in this repo are load-flaky (see
docs/TESTING-TUI.md)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from novelizer.export.voicing import build_voicing_export, render_annotated, render_json
from novelizer.settings.discovery import slugify

DEFAULT_CHUNK_SIZE = 800


class VoicingExportScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self.title_value = runtime.settings.story_title or Path(runtime.settings.db_path).parent.name
        self.chunk_by = "budget"
        self.chunk_size = DEFAULT_CHUNK_SIZE
        self.export_format = "json"
        self._error: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="voicing_box") as box:
            box.border_title = "EXPORT FOR VOICING"
            yield Input(value=self.title_value, placeholder="Title", id="voicing_title")
            yield Select(
                [("Voicing JSON", "json"), ("Annotated prose", "annotated")],
                id="voicing_format", allow_blank=False, value=self.export_format,
            )
            yield Select(
                [("Per segment", "segment"), ("Per chapter", "chapter"),
                 ("Packed to budget", "budget")],
                id="voicing_chunk_by", allow_blank=False, value=self.chunk_by,
            )
            yield Input(value=str(self.chunk_size), placeholder="Chunk size (characters)",
                        id="voicing_chunk_size")
            yield Static("", id="voicing_error")
            yield Button("Export", id="voicing_confirm")

    def action_close(self) -> None:
        self.dismiss()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "voicing_title":
            self.title_value = event.value
        elif event.input.id == "voicing_chunk_size":
            self.chunk_size = int(event.value) if event.value.strip().isdigit() else DEFAULT_CHUNK_SIZE

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "voicing_chunk_by":
            self.chunk_by = event.value
        elif event.select.id == "voicing_format":
            self.export_format = event.value

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "voicing_confirm":
            path = await self.write_export()
            if path is None:
                return
            self.dismiss()
            message = f"» exported voicing document → {path}"
            try:
                from textual.widgets import RichLog

                self.app.query_one("#feed", RichLog).write(message)
            except Exception:
                pass
            self.app.messages.append(message)

    async def write_export(self) -> Path | None:
        """Build and write the document. Returns its path, or None on error."""
        chapters = await self.runtime.read.list_chapters()
        rows = await self.runtime.read.list_speech_segments()
        if not rows:
            self._set_error(
                "No attributed segments yet — the Attributor has not run on any chapter."
            )
            return None

        by_chapter: dict[str, list] = {c.id: [] for c in chapters}
        for row in rows:
            by_chapter.setdefault(row.chapter_id, []).append(row)

        chunk_by = self.chunk_by
        if self.export_format == "annotated" and chunk_by == "chapter":
            # render_annotated has no speaker to re-wrap on a chapter chunk and
            # refuses rather than silently dropping every tag.
            self._set_error(
                "Annotated prose needs per-segment or budget chunking, not per-chapter."
            )
            return None

        chunks = build_voicing_export(
            chapters, by_chapter, chunk_by=chunk_by, chunk_size=self.chunk_size,
        )
        if self.export_format == "annotated":
            document = render_annotated(chunks)
            suffix = ".txt"
        else:
            document = render_json(chunks, title=self.title_value)
            suffix = ".json"

        story_root = Path(self.runtime.settings.db_path).parent
        export_dir = story_root / "export"
        export_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        final_path = export_dir / f"{slugify(self.title_value)}-voicing-{stamp}{suffix}"
        tmp_path = final_path.with_suffix(f"{suffix}.tmp")
        try:
            tmp_path.write_text(document, encoding="utf-8")
            tmp_path.rename(final_path)
        except OSError as e:
            self._set_error(f"write failed: {e}")
            return None
        return final_path

    def _set_error(self, text: str) -> None:
        self._error = text
        try:
            self.query_one("#voicing_error", Static).update(text)
        except Exception:
            pass
```

This relies on `AttributedSegment.chapter_id` being populated by
`ReadStore.list_speech_segments`, which Task 4 does. It is empty on the event
payload and filled in on read — verify that before debugging a grouping bug
here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tui/test_voicing_export_screen.py -v`
Expected: PASS, 4 tests

- [ ] **Step 5: Wire the palette command**

In `novelizer/tui/app.py`, add next to `_app_open_export` (:620):

```python
def _app_open_voicing_export(app: NovelizerApp) -> None:
    from novelizer.tui.voicing_export_screen import VoicingExportScreen

    if app.screen is not app.default_screen:
        return
    app.push_screen(VoicingExportScreen(app.runtime))
```

And in `APP_COMMANDS`, immediately after the `export_epub` entry:

```python
    AppCommand("export_voicing", "Export for voicing (JSON)", _app_open_voicing_export),
```

- [ ] **Step 6: Run the TUI command tests**

Run: `uv run pytest tests/tui -q -p no:randomly`
Expected: PASS. If a failure looks like a load flake rather than a real break,
re-run that test alone before investigating — `docs/TESTING-TUI.md` documents
this and `tui-tests-red-on-main` records that the band can be red on plain main.
Check parity against `main` before attributing a failure to this branch.

- [ ] **Step 7: Commit**

```bash
git add novelizer/tui tests/tui/test_voicing_export_screen.py
git commit -m "feat(tui): export attributed prose for voicing from the command palette"
```

---

### Task 9: Seam test, docs, and the full suite

Everything so far tested one side of each boundary. This task pins that the Author's real output and the real parser agree, then verifies the whole suite.

**Files:**
- Create: `tests/agents/test_author_attributor_seam.py`
- Modify: `docs/reference/` and `docs/explanation/` as the docs sync identifies
- Test: the full suite

**Interfaces:**
- Consumes: everything

- [ ] **Step 1: Write the seam test**

The recurring failure in this repo is fixtures that verify each side of a seam
and never that they agree. This test takes the marker contract *as stated in
the Author's own prompt* and runs it through the real parser.

Create `tests/agents/test_author_attributor_seam.py`:

```python
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
    example = _TAGGED_LINE.findall(AUTHOR_SYSTEM_PROMPT)[0]
    parsed = parse_markers(example)
    segments = segment_prose(parsed.clean_prose, parsed.spans)
    assert "".join(s.text for s in segments) == parsed.clean_prose
    assert [s.index for s in segments] == list(range(len(segments)))


def test_the_prompt_names_no_tag_the_parser_ignores():
    tags = set(re.findall(r"<(\w+) char=", AUTHOR_SYSTEM_PROMPT))
    assert tags <= {"speech", "thought"}, f"prompt teaches unknown tags: {tags}"
```

- [ ] **Step 2: Run the seam test**

Run: `uv run pytest tests/agents/test_author_attributor_seam.py -v`
Expected: PASS, 3 tests. A failure here means the prompt example and the parser
disagree — fix whichever is wrong, do not relax the test.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS. Run this from the worktree only, never the main checkout.

Note the run takes several minutes. Do not background it and poll; wait for it.

- [ ] **Step 4: Fix any regressions**

Registry-derived suites are the likely source: `attributor` is now the
fourteenth agent and several tests assert per-agent surfaces. Fix the agent, not
the test.

- [ ] **Step 5: Sync the docs**

Run the `syncing-diataxis-docs` skill. At minimum this feature needs:
- a reference entry for the `chapter.attributed` event and the `speech_segments` table
- a reference entry for the Attributor in the agent roster
- an explanation of why attribution is authored inline rather than inferred —
  the multi-turn unattributed exchange argument is the reason the whole design
  looks like this, and it will not be obvious to a future reader

- [ ] **Step 6: Commit**

```bash
git add tests/agents/test_author_attributor_seam.py docs/
git commit -m "test: pin the Author/Attributor seam; document speech attribution"
```

- [ ] **Step 7: Push and open a draft PR**

```bash
git push -u origin worktree-speech-attribution-spec
gh pr create --draft --title "Speech attribution and voicing export" \
  --body "Implements docs/superpowers/specs/2026-07-25-speech-attribution-design.md

The Author tags dialogue and interior thought inline; a new Attributor agent
parses the tags, resolves speakers to character ids, strips the markup, and
commits clean prose plus an ordered segment list. Export reaches a voicing
pipeline as JSON through the ctrl+k palette, with configurable chunking that
cannot merge across a speaker change.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Self-Review

**Spec coverage.** Marker contract → Task 6. Fleet awareness → Task 6. Attributor
(log-fold backlog, deterministic parse, LLM repair only, name+alias resolution,
narration fill, one narrow event, ordering after `author`) → Tasks 1–5. Failure
handling (commit partial, null id, raise flag) → Tasks 1 and 5. Storage
(`speech_segments`, offsets against clean prose, prose rewrite) → Task 4.
Export (palette, JSON, three chunk modes, no merging across speaker/kind/chapter)
→ Tasks 7–8. Testing (round-trip property, chunking properties, malformed input,
projection fold, seam test) → Tasks 1, 2, 4, 5, 7, 9. Every spec section maps to
a task.

**Type consistency.** `RawSpan` (Task 1) → `segment_prose` (Task 2) → `Segment`
with `.index/.kind/.char_name/.start/.end/.text` → `AttributedSegment` with
`.index/.kind/.character_id/.character_name/.start_offset/.end_offset/.text`
(Task 4) → `Chunk` (Task 7). The rename from `char_name`/`start`/`end` at the
pure-parser boundary to `character_name`/`start_offset`/`end_offset` at the
event boundary is deliberate and happens in exactly one place, `Attributor._attribute`.
