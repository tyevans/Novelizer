# Speech attribution: `chapter.attributed` and `speech_segments`

This reference covers the event that records who is speaking in a chapter and
the projection table it populates. For the reasoning behind authoring
attribution inline rather than inferring it afterward, see
[Why attribution is authored, not inferred](../explanation/speech-attribution-inline.md).
For the agent that produces this event, see
[The Attributor](attributor-agent.md).

## `chapter.attributed`

Payload class: `ChapterAttributed` (`novelizer/canon/events.py`).

| Field | Type | Notes |
|---|---|---|
| `chapter_id` | `str` | The chapter this pass covers. |
| `prose` | `str` | The chapter's prose with all `<speech>`/`<thought>` markup **stripped**. This is the text the projector installs as the chapter's canonical `prose` — see Invariant 1 below. |
| `segments` | `list[AttributedSegment]` | The dense, ordered linearization of the chapter (see below). Defaults to `[]`. |
| `problems` | `list[str]` | Human-readable descriptions of markup the parser or repair pass could not resolve — an unclosed tag, a nested tag, an unresolved speaker name. Not an error list: a chapter with problems still commits. Surfaced as flags, category `attribution`, not raised as exceptions. |

`AttributedSegment` (also `novelizer/canon/events.py`) — one voiced unit:

| Field | Type | Notes |
|---|---|---|
| `chapter_id` | `str` | Empty (`""`) on the event itself, since the event already names its chapter under `ChapterAttributed.chapter_id`. Filled in by the read store on `list_speech_segments()`, where callers grouping segments across many chapters need it on the row. |
| `index` | `int` | Dense, 0-based position within the chapter's segment list — see Invariant 3. |
| `kind` | `str` | One of `speech`, `thought`, `narration`. |
| `character_id` | `str \| None` | The resolved character's id, or `None` when the speaker could not be resolved against the roster. Always `None` for `narration`. |
| `character_name` | `str` | The freeform name or alias as the Author wrote it (empty for `narration`). Preserved even when `character_id` is `None`, so a human or a later repair pass can see what was actually typed. |
| `start_offset` / `end_offset` | `int` | Half-open `[start, end)` character offsets — see Invariant 1. |
| `text` | `str` | The segment's exact text, equal to `prose[start_offset:end_offset]`. |

## `speech_segments` table

Populated by the `@projects(EventType.CHAPTER_ATTRIBUTED)` handler in
`novelizer/canon/projections/chapters.py`; DDL lives in `novelizer/canon/projector.py`.

```sql
CREATE TABLE IF NOT EXISTS speech_segments (
    chapter_id TEXT NOT NULL, segment_index INTEGER NOT NULL,
    kind TEXT NOT NULL, character_id TEXT, character_name TEXT NOT NULL DEFAULT '',
    start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL, text TEXT NOT NULL,
    PRIMARY KEY (chapter_id, segment_index)
);
```

| Column | Type | Notes |
|---|---|---|
| `chapter_id` | `TEXT` | Part of the primary key. |
| `segment_index` | `INTEGER` | Part of the primary key; the same dense `index` as `AttributedSegment.index`. |
| `kind` | `TEXT` | `speech`, `thought`, or `narration`. |
| `character_id` | `TEXT`, nullable | `NULL` when unresolved or narration. |
| `character_name` | `TEXT` | Defaults to `''`. |
| `start_offset` / `end_offset` | `INTEGER` | Into the chapter's clean `prose`. |
| `text` | `TEXT` | The segment's own text. |

Read access is `ReadStore.list_speech_segments(chapter_id=None)`
(`novelizer/canon/read_store.py`), which fills in `chapter_id` on each returned
`AttributedSegment` and, unfiltered, returns segments across every attributed
chapter.

`chapter.attributed` is in the never-gated set
(`novelizer/canon/policy.py`) — it commits at every autonomy level, since it
formalizes markup the Author already wrote rather than introducing new canon
content.

## Invariants

1. **Offsets address the clean prose carried on the same event.** `start_offset`/`end_offset` on every segment are positions into `ChapterAttributed.prose` — the stripped text, not the Author's original marked-up draft. Because the projector installs that same `prose` string as the chapter's canonical text in the same handler invocation, the table and `chapters.data->prose` agree by construction: there is no window where the projected chapter text and the segment offsets could refer to different strings.

2. **A re-attribution replaces the chapter's rows, it does not add to them.** The projection handler runs `DELETE FROM speech_segments WHERE chapter_id = ?` before inserting the new segment set. A chapter revised and re-attributed a second time ends up with exactly the second pass's rows — never the union of both passes. This is a faithful fold on the *chapter's current attribution*, not an accumulating log of every pass; replaying the event log twice lands on the same table state either way.

3. **Segments are dense and complete.** `segment_index` runs 0..N-1 with no gaps, and the segments for one chapter — read back in index order — form a full linearization of that chapter's clean prose: every character of `prose` belongs to exactly one segment, speech/thought spans as authored and the untagged prose between them as `narration`.
