# search_canon contextual summarization

**Date:** 2026-07-25
**Status:** approved, ready to plan

## Problem

`search_canon` returns a ranked list of hit lines — `(kind) <path> — '<title>' [id: <id>]`
— and nothing else. The agent then spends three to six `read_file` round-trips
opening those files to find out whether any of them actually bear on the question
it was asking. The tool knows *what* matched but says nothing about *what canon
says*, and it has never known *why* the agent was asking.

## Goal

Give `search_canon` the agent's intent, and have it return a short grounded
synthesis of the top hits' actual content alongside — never instead of — the
existing hit lines.

## Signature

```python
async def search_canon(
    query: str,
    purpose: str,
    kinds: list[str] | None = None,
    summarize: bool = True,
) -> str
```

`purpose` is **required**. An optional context argument gets dropped by every
agent under prompt pressure, and a summarizer contextualizing against `None` is
worse than no summarizer. Requiring it also makes the agent state its intent,
which tends to sharpen the query itself.

`summarize=False` is the documented escape hatch for an agent that wants the raw
index and will do its own reading.

## Flow

1. **Search** — unchanged. Every existing early return survives verbatim:
   - `ValueError` from the store → return its message (it names the valid kinds).
   - `EmptyIndexError` → the "Search unavailable (semantic index is empty …)"
     text. This path is load-bearing; see `novelizer/runtime.py:684` and the
     690-consecutive-miss incident.
   - any other exception → the generic "Search unavailable (<Type>) …" text.
   - no hits → `"No results."`

   None of these reach the summarizer. A dead search must degrade exactly as it
   does today.

2. **Build the hit-line block** — unchanged, including the `SEARCH_RESULT_CAP`
   truncation notice and the inline entity formatting.

3. **Short-circuit** — if `summarize is False`, or no `settings_provider` was
   wired in, or `settings_provider().search_summarize` is off, return the
   hit-line block **byte for byte** as today. This is the regression anchor.

4. **Gather excerpts** — for the first `SUMMARY_SOURCE_CAP = 5` hits:
   - file-backed kinds → `await backend.aread(path, limit=SUMMARY_BODY_LINES)`,
     with `sliced_read`'s line-number prefixes stripped. Reading through
     `CanonBackend` (rather than re-rendering from `read_store`) means the
     summarizer sees exactly the bytes the agent's own `read_file` would show.
   - `entity` hits → the already-inline description/relations line.
   - `arc` / `brief` / `promise` hits → title only; they have no readable file.
   - a read error on any one hit → skip that hit, keep the rest.

5. **Summarize** — one call to a chat model built with
   `build_chat_model(settings.agent_model, settings.llm_base_url,
   settings.llm_api_key, temperature=0.0,
   max_tokens=SUMMARY_MAX_TOKENS, callbacks=callbacks)`.
   Temperature 0: this is extraction, not prose.

6. **Return**

   ```
   CONTEXT (for: <purpose>)
   <summary>

   RESULTS (cite these ids)
   <hit lines, unchanged>
   ```

The hit lines are never replaced, reordered, filtered, or renumbered. Agents cite
ids and open files from that block; no LLM gets to mediate between them and
their citations.

## Grounding

The summarizer prompt:

- states the agent's `query` and `purpose`;
- supplies the excerpts, each labelled with its kind, title and id;
- forbids asserting anything not present in the excerpts;
- requires it to say plainly when the excerpts do not answer the purpose;
- caps the answer at roughly 120 words.

Given this repo's history, a confidently-wrong summary is strictly worse than no
summary. "The excerpts don't speak to this" is a correct and useful answer.

## Degradation

If the summarizer call raises, times out, or returns empty, return the plain
hit-line block — no `CONTEXT` header, no error text, no apology. A search that
produced correct results must never fail because a nicety failed. The failure is
logged, not surfaced into the agent's context.

## Wiring

- `build_search_canon_tool(embedding_store, read_store, kg_store)` gains
  `backend=None, settings_provider=None, callbacks=None`. All three default to
  `None`, and a missing `settings_provider` simply disables summarization — so
  every existing test construction keeps working unchanged.
- **`settings_provider` is a zero-arg callable, not a `Settings` object, and this
  is not incidental.** `_phase_a_toolkit()` runs exactly once during `start()`
  (`novelizer/runtime.py:400`) and caches its result on `self._canon_backend` /
  `self._canon_tools`; every later `apply_settings` rebuild *reuses* those cached
  tools rather than rebuilding them. A `Settings` captured at construction would
  therefore freeze the summarizer's model, temperature and kill switch at
  start-up values for the life of the process, and no settings reload would ever
  reach it. Reading `settings_provider()` at call time is what makes the kill
  switch actually switchable.
- `runtime._phase_a_toolkit()` builds the `CompositeBackend` before the tool
  list already, so it passes `backend=backend,
  settings_provider=lambda: self.settings, callbacks=self._llm_callbacks`.
  `self.settings` is the right source here: `apply_settings` assigns it last, so
  it always describes what is actually running — which is precisely what a
  live tool should honour.
- **Callbacks are not optional in practice.** Without them these calls are
  invisible to the Engine Room and bypass the shared AIMD/rate-limit pool, and
  they are real token spend on the hot path of every pull-mode agent.
- New setting `search_summarize: bool = True` — a global kill switch. When off,
  `summarize=True` is ignored and the tool behaves exactly as it does today.

## Tool description

The docstring is prompt surface — it is the only thing that teaches agents to
pass `purpose`. It gains:

- what `purpose` is for and that it must be a real sentence about the decision at
  hand, not a restatement of the query;
- that the response leads with a `CONTEXT` block synthesized from the top hits,
  and that the `RESULTS` block below it remains the citation source of truth;
- when to pass `summarize=False` (you want the raw index and will read yourself).

## Constants

| name | value | why |
|---|---|---|
| `SEARCH_RESULT_CAP` | 20 (unchanged) | existing hit-line cap |
| `SUMMARY_SOURCE_CAP` | 5 | how many hits get their bodies read |
| `SUMMARY_BODY_LINES` | 120 | per-file read limit; a 4k-word chapter must not blow context |
| `SUMMARY_MAX_TOKENS` | 400 | generation cap on the summary |

## Testing

Red/green, with a fake chat model and a fake backend:

1. `summarize=False` output is byte-identical to the current tool's output.
2. `settings_provider` absent, or `search_summarize=False` → same byte-identical
   output.
3. Happy path → output starts with `CONTEXT (for: <purpose>)` and the full
   hit-line block still appears verbatim below `RESULTS`.
4. Summarizer raises → bare hit-line block, no `CONTEXT`, no error text.
5. Summarizer returns empty/whitespace → same as (4).
6. `purpose` and `query` both reach the prompt the model receives.
7. At most `SUMMARY_SOURCE_CAP` bodies are read, even with 20 hits.
8. Per-file read passes `limit=SUMMARY_BODY_LINES`.
9. A read error on one hit does not lose the other excerpts.
10. Every pre-summarizer early return (empty index, store `ValueError`, generic
    failure, no hits) makes zero model calls.
11. Entity hits contribute their inline text and trigger no file read.
12. `tests/canon_fs/test_search_description.py` extended: the description
    mentions `purpose`, `summarize`, and the `RESULTS`-is-authoritative rule.
13. Flipping `search_summarize` on the object returned by `settings_provider`
    changes the tool's behaviour on the *next* call, with no rebuild — the
    regression test for the cached-toolkit trap described under Wiring.

## Cost

Every semantic search now costs one extra LLM call plus up to five file reads,
on the hot path of every pull-mode agent. Across a day-long run that is real
money. Mitigations are the `search_summarize` kill switch and per-call
`summarize=False`. This is a deliberate trade, not an oversight.

## Out of scope

- Caching summaries across identical (query, purpose) pairs.
- Letting the summarizer drop or reorder hits.
- A separate cheaper model for summarization — it uses `agent_model` like every
  other agent-side call in the codebase.
