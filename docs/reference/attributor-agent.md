# The Attributor

The Attributor is the fleet's eleventh agent (`novelizer/agents/attributor.py`,
registered in `AGENT_REGISTRY` — `novelizer/agents/registry.py` — immediately
after `author` and ahead of `editor`, so nothing downstream that treats prose
as final ever sees markup). It turns the Author's inline speaker markup into
structured attribution: a `chapter.attributed` event carrying clean prose and
an ordered segment list. See
[`chapter.attributed` and `speech_segments`](speech-attribution.md) for the
event and table this agent produces, and
[Why attribution is authored, not inferred](../explanation/speech-attribution-inline.md)
for why the markup exists in the first place.

| Property | Value |
|---|---|
| Role | Formalizes the Author's `<speech>`/`<thought>` markup into clean prose plus a segment list. Writes no prose of its own. |
| Default cadence | 120 s (`default_agent_interval`) |
| Readiness | `min(1.0, pending/3)` where `pending` is the count of chapters not yet attributed for their current revision, watermark-gated (`_gate_on_watermark`). Zero when nothing is pending. |
| Backlog | A pure log fold, not a state table: `current_done_ids(chapter.attributed, chapter.revised)` (`novelizer/brain/watermarks.py`) against `list_chapters()`. |
| Flag category | `attribution` |

## What triggers it

The backlog is every chapter whose most recent `chapter.attributed` (if any)
does not cover its most recent `chapter.created`/`chapter.revised`. A brand
new chapter is pending until attributed once; a chapter revised after being
attributed becomes pending again automatically — there is no separate
re-attribution trigger to invoke, revision alone is enough.

## Deterministic by default; the model is reached only to repair markup

Parsing (`novelizer/speech/markers.py`) and speaker resolution
(`novelizer/speech/resolve.py`) are plain code: the markers are unambiguous by
construction, so extraction, offset computation, and name-to-id lookup never
need a model call. The Attributor's `_repair` method invokes the model
(`build_attributor_runner`, temperature `0.0`, "run cold" — repair is
transcription, not composition) **only** when `parse_markers` reports a
problem it cannot resolve on its own — an unclosed tag, a nested tag, a
malformed attribute. The repair prompt instructs the model to fix only the
markup and leave every character of prose untouched; if the repair call fails,
raises, or returns unusable output, the agent logs a warning and commits the
original parse's result rather than blocking on the model.

## Never creates a character

Speaker resolution (`build_name_index` / `resolve_speaker`) looks a name up
against the roster's canonical names and aliases, with
`slugify_character_name()` as a fallback for spacing or punctuation variants.
It never mints a character id — inventing one here would let a typo in a
`char="..."` attribute silently create canon. An unresolvable name simply
resolves to `character_id = None`.

## Unresolvable speakers never block the chapter

Every failure mode — malformed markup the repair pass couldn't fix, or a
speaker name that resolves to nothing — is recorded, not raised. The
Attributor still commits the chapter's clean prose and its full segment list;
the affected segment carries `character_id = None`, and the problem is
appended to `ChapterAttributed.problems`. Those problems are filed as
`attribution`-category flags for Triage and FlagLabeler to surface in the TUI,
using the same own-rejections bookkeeping other agents use so a flag Triage
already dismissed is not re-filed verbatim on the next pass. A chapter with
unresolved speakers exports fine; the unresolved segments simply carry a null
voice.

## A revised chapter re-attributes automatically

Because the backlog is a fold over `chapter.attributed` vs.
`chapter.created`/`chapter.revised` rather than a one-shot flag, any
`chapter.revised` event makes that chapter pending again on the very next
readiness check — no separate command or flag needed. The projection handler
for `chapter.attributed` deletes the chapter's prior `speech_segments` rows
before inserting the new set (see Invariant 2 in
[`chapter.attributed` and `speech_segments`](speech-attribution.md)), so the
re-attribution fully replaces the previous pass rather than layering on top of
it.
