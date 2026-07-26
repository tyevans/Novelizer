# Speech Attribution and Voicing Export

**Date:** 2026-07-25
**Status:** Approved design, not yet implemented

## Problem

Prose carries no record of who is speaking. Downstream voicing software needs
one: a linear, ordered stream of utterances, each bound to a character, so it
can assign a voice per speaker.

Recovering that record after the fact does not work. A rapid exchange carries no
dialogue tags at all —

> "This is how much?" "That's twenty." "Dollars?!" "Yeah, twenty dollars."

— and neither a heuristic parser nor an inference agent can reliably say who
speaks the third line. Only the Author knows. So the Author writes it down at
the moment of authorship, and a later agent formalizes what was written.

## Approach

Two stages.

**Stage 1, authoring.** The Author wraps utterances in explicit tags as it
writes. Marked prose lands in `chapter.created` / `chapter.revised`.

**Stage 2, attribution.** A new Attributor agent parses those tags, resolves
speaker names to character ids, strips the markup, and commits both the clean
prose and a structured span list in a single event. After the Attributor runs,
`chapter.prose` is ordinary text and the annotation lives in a projection.

Clean prose is canon. The marked draft survives in the event log, so nothing is
destroyed, but no consumer downstream of attribution has a stripping obligation.

### Rejected alternatives

- *Post-hoc inference only.* Fails on unattributed exchanges, which is the
  common case in dialogue-heavy fiction.
- *Deterministic parsing only.* Same failure, plus pronoun-only tags.
- *Markers stay in canonical prose, renderers strip.* Permanent stripping
  obligation on every current and future prose consumer.
- *Two prose fields on one event.* Doubles storage and asks a single generation
  to emit the same text twice, consistently.

## Marker contract

```
<speech char="Mira">"Twenty dollars."</speech>
<thought char="Mira">Twenty. She had four.</thought>
```

Dialogue and rendered interior thought are tagged. Narration is left bare.
Thought is tagged separately because voicing pipelines usually give it a
distinct treatment even when the character is the same.

Explicit open/close tags are used rather than a lighter prefix syntax
(`[[mira]] "…"`) precisely because extent is unambiguous and malformation is
*detectable*. A dropped closing tag is a loud error, not a silent misparse.

`char` carries the character's canonical name or a known alias. The Author does
not mint ids.

## Fleet awareness

Between the Author writing a chapter and the Attributor processing it, raw
markers are visible to every other prose consumer — `search_canon`, the
continuity miner, the Summarizer, `canon_fs/render.py`, the EPUB export.

A short marker-awareness paragraph is injected into every agent prompt through
the existing `AGENT_REGISTRY`-derived prompt-surface sweep, not hand-edited into
each prompt: these tags may appear in prose; treat them as invisible; never
reproduce or comment on them.

## The Attributor agent

Modeled on `novelizer/agents/summarizer.py`, which is the established shape for
a per-chapter agent that reads prose and emits one narrow event.

**Backlog.** A log fold via
`novelizer.brain.watermarks.current_done_ids(chapter.attributed, chapter.revised)`.
No extra state table. A revised chapter re-attributes automatically.

**Parsing is deterministic.** The markers are unambiguous, so extraction and
stripping are plain code, not an LLM call. The model is invoked only to repair
prose the parser reports as malformed.

**Speaker resolution.** A lowercased name-and-alias map built from
`list_characters()`, following the shape already used in
`novelizer/store/kg_projector.py`, with `slugify_character_name()` as a fallback
guess. The Attributor never creates a character.

**Segments include narration.** Gaps between tagged spans are synthesized into
narration segments so the span list is a complete linearization of the chapter,
not a dialogue-only sidecar. Voicing needs the connective tissue.

**Event.** One `chapter.attributed`, carrying the clean prose and the ordered
span list.

**Ordering.** Registered in `AGENT_REGISTRY` immediately after `author` and
ahead of `editor`, so nothing treating prose as final sees markup.

### Failure handling

Malformed or unresolvable input never blocks a chapter. The Attributor commits
what it resolved, marks the remainder with `character_id = null`, and raises a
flag through the existing flag system so Triage and FlagLabeler surface it in
the TUI. A chapter with unresolved speakers still exports; those segments carry
a null voice.

## Storage

One new projection table, populated by a `@projects` handler as a faithful fold
with no dedupe:

```
speech_segments(chapter_id, segment_index, kind, character_id,
                character_name, start_offset, end_offset, text)
```

`kind` is one of `speech`, `thought`, `narration`. Offsets are against the clean
prose, so the table and `chapters.data->prose` agree by construction.
`segment_index` is dense and ordered.

The same event's handler also updates the chapter row's prose to the clean text.

## Export

Reachable from the ctrl+k command palette as `export_voicing`, via a
`VoicingExportScreen` alongside the existing `ExportScreen`, writing under
`<story_root>/export/`. No CLI is added; the existing EPUB export stays where it
is.

The screen is a thin shell over a pure function in `novelizer/export/voicing.py`:

```python
def build_voicing_export(chapters, segments, *, chunk_by, chunk_size) -> list[Chunk]
```

**Format.** JSON only. Each segment serializes as
`{kind, character_id, character_name, text, chapter_id, chapter_ordinal, segment_index}`.
Emitters sit behind a one-method interface so an SSML target can be added later
without touching the chunker. SSML is not built now — it would lock the design
to one engine's voice-tag conventions before there is a concrete consumer.

**Chunking.** Three modes:

- `segment` — one chunk per span. The atom.
- `chapter` — segments nested under each chapter.
- `budget` — consecutive segments packed to a character budget, splitting only
  on sentence boundaries.

Budget packing never merges across a speaker change, a kind change, or a chapter
boundary. The chunking knob cannot corrupt attribution.

## Testing

Red/green, with properties where an invariant exists.

- **Round-trip property.** For generated marked prose, stripping and then
  re-applying spans by offset reconstructs the original. This is what keeps
  offsets honest.
- **Chunking properties.** Concatenated chunk text equals the clean prose; no
  chunk spans two speakers or two kinds; every chunk is within budget or is a
  single unsplittable segment.
- **Malformed input.** Unclosed tag, nested tag, unknown speaker — each asserts
  the chapter still commits, a flag is raised, and the unresolved segment
  carries a null id.
- **Projection fold.** Replaying `chapter.attributed` twice yields identical
  table state, with no dedupe logic in the handler.
- **Seam test.** Real Author output shape parsed by the real parser. Hand-built
  fixtures on both sides of a seam verify each side and never that they agree.

## Touch points

- `novelizer/canon/events.py` — `chapter.attributed` type and payload
- `novelizer/canon/projections/chapters.py` — `@projects` handler
- `novelizer/canon/projector.py` — `speech_segments` DDL in `_CREATE`
- `novelizer/canon/policy.py` — write allowance for the Attributor
- `novelizer/agents/attributor.py` — new agent
- `novelizer/agents/registry.py` — one line, after `author`
- `novelizer/agents/author.py` — marker contract in the system prompt
- `novelizer/export/voicing.py` — chunker and JSON emitter
- `novelizer/tui/` — `VoicingExportScreen`, palette command
