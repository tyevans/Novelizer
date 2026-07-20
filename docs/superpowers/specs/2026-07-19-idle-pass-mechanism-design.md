# Idle-Pass Mechanism for Revision Agents

**Date:** 2026-07-19
**Status:** Approved

## Problem

Maintenance/revision agents with floored readiness scores — CharacterKeeper
(floor 0.2, `novelizer/agents/character_keeper.py`), WorldArchitect (floor
0.2), ContinuityChecker (floor 0.1) — are dispatched by the scheduler even
when nothing new has happened in the story. Each idle dispatch runs a full
LLM call; the model, having nothing to act on, returns a `feed_note` like
"The analysis is complete. I'm done until you provide more…" which lands in
the feed via `BaseAgent._remark`. With `max_concurrent_agents=2`, these idle
runs occupy dispatch slots the Author could use, so a running story appears
stuck in agents announcing they have nothing to do.

There is currently no way for an agent to say "no work here": completion is
implicit (`work()` returns `None` → `commit()` no-ops), and readiness never
reflects "I already analyzed exactly this story state."

## Design

Two complementary layers.

### Layer 1 — Readiness watermark (structural; avoids the LLM call)

Applies to **CharacterKeeper and ContinuityChecker** — the analytical agents.
**WorldArchitect is excluded**: it is generative, its floor readiness of 0.2
is intentional pressure to keep enriching an "ever-expanding world," and its
inputs (world entries it writes itself, director seeds, the active hand)
barely change externally — a watermark would silence it permanently after one
run. It gets Layer 2 only.

Each watermarked agent implements `_fingerprint() -> tuple`, a small tuple of
the *external* state it cares about:

- **CharacterKeeper:** (chapter count, latest chapter id, open retcon count)
- **ContinuityChecker:** (chapter count, latest chapter id, count of
  not-yet-mined chapters, secret-reference count, causal-edge count) — the
  inputs to both its LLM analysis and its deterministic leak/paradox/mining
  passes

`BaseAgent` stores the fingerprint captured at the end of the agent's last
*successful* run (after its own commits, so the agent's own writes are
included in the watermark and never re-trigger it). `readiness()` returns
`0.0` when the current fingerprint equals the stored one — the agent is not
dispatched at all, no LLM call happens. When external material appears (new
chapter, new retcon), the fingerprint differs and the existing floored score
applies.

Watermarks are **in-memory only**. A process restart costs at most one
redundant check per agent; persisting them is not worth the complexity.

### Layer 2 — Explicit pass verdict (for runs that find nothing)

The three output schemas (`KeeperOutput`, WorldArchitect's output,
`ContinuityOutput`) gain:

```python
no_action: bool = False
```

System prompts gain an instruction: *"If nothing needs your attention, set
no_action=true, leave all lists empty, and give a one-line feed_note in
character saying you're standing aside so the story can continue."*

On `commit()` with `no_action=True`:

1. Skip all entity/intent commits (canon is never mutated on a pass).
2. Post the `feed_note` as the remark; if the LLM omitted one, use the
   default: **"Nothing needs my attention — carry on with the story."**
3. Call `BaseAgent.note_pass()`: sets `_backoff_until = now + interval * 3`.
   `ready_for_interval` and `seconds_until_ready` respect `_backoff_until`
   in addition to the normal interval gate.

So an agent that ran on genuinely new material but chose not to act steps
back for 3 intervals instead of 1, freeing dispatch slots.

**ContinuityChecker caveat:** its `commit()` also runs deterministic passes
(leak detection, paradox detection, prose mining of unmined chapters) that
are independent of the LLM's analysis verdict. A `no_action=True` verdict
skips only the LLM-derived retcon commits — the deterministic passes always
run — and `note_pass()` is applied only when the deterministic passes also
had nothing to commit (no mined chapters, no new leaks/paradoxes filed).

### What resumes the storytelling

Nothing new. The Author's readiness (`1.0 - drafts/3`,
`novelizer/agents/author.py:77-79`) already makes it the top-scored agent
once maintenance agents go quiet; the problem was purely that idle agents
were outscoring and out-slotting it.

**Non-goals:** no Author-boosting signal, no new `SignalKind`, no changes to
Editor/Retconner/StructureAnalyst/Author (their readiness already reaches
0.0 when there is no work), no changes to the Director↔agent chat feature.

## Error handling

- A failed run never updates the watermark or backoff; the existing
  `mark_ran`-on-failure interval backoff (`novelizer/scheduler.py:175-180`)
  governs retries.
- `no_action` defaults to `False`; absent field ⇒ existing behavior.
- The pass backoff is in-memory and cleared implicitly by time passing;
  a `seed`/`focus`/`override` DirectorSignal path is unaffected (override
  dispatch in `Scheduler.tick` bypasses readiness but still honors
  `ready_for_interval` — an operator who wants an immediate re-run can
  still get one after the backoff window, or by restarting).
- The watermark is recorded only when the run fully accounted for the state
  it fingerprints: if the chapter components moved mid-run (concurrent
  Author commit), or — for the ContinuityChecker — any chapter is still
  unmined at record time (failed mining pass must honor its "retry next
  poll" contract), the watermark is left clear so the next tick
  re-dispatches.

## Testing

Red/green TDD, property-based where it pays:

- **Watermark:** readiness is 0.0 when fingerprint unchanged since last
  successful run; floor score restored when a new chapter/retcon appears;
  agent's own commits do not re-trigger it; failed run leaves watermark
  unset/stale so the agent retries; WorldArchitect readiness is unchanged.
- **ContinuityChecker pass:** `no_action=True` still runs leak/paradox/mining
  passes; backoff applied only when those also committed nothing.
- **Pass verdict:** `no_action=True` commit produces no canon events except
  `agent.remarked`; default remark text used when `feed_note` empty;
  `note_pass()` extends `seconds_until_ready` to ~3× interval.
- **Property-based:** for arbitrary `no_action=True` outputs (random
  populated lists), commit never mutates canon.
- All test runs happen in a worktree, never the main checkout.
