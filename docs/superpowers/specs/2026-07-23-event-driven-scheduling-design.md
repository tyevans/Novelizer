# Event-driven scheduling — design

**Date:** 2026-07-23
**Status:** approved, in implementation
**Supersedes:** nothing
**Implementation plan:** see the approved plan for phase breakdown and verification

## Problem

The writers' room sits idle for long stretches while work is plainly available.

The cause is in `agent_kit/scheduler.py:99-102`. `tick()` ANDs two independent gates, and the clock gate runs *first* — filtering agents out before they are ever scored by the work gate:

```python
eligible = [a for a in self._agents
            if not a.paused and a.name not in self._in_flight and a.ready_for_interval(now)]
```

With `max_concurrent_agents=2` and intervals of 300/900/300/240/180/120/60s
(`novelizer/settings/models.py:74-85`), the common steady state is **two free
dispatch slots and zero eligible agents**. A fresh draft sits unedited for up
to 120s and unchecked for continuity for up to 900s purely because a timer has
not elapsed — while the pool is empty.

The room is not busy. It is waiting on clocks.

### Two related findings

**Background work escapes the budget entirely.** `novelizer/tui/app.py:154-168`
runs `projector.catch_up()`, `index_catch_up()`, and `kg_catch_up()` on a 0.5s
loop, outside the scheduler and uncounted by `max_concurrent_agents`. KG
extraction makes LLM calls (`kg_projector.py:170`). So there are two
independent LLM consumers sharing one endpoint with no shared ceiling — the
likely source of past 429 pile-ups.

**Intervals are load-bearing today.** `note_pass()` (3× interval),
`note_rate_limited()` (3× interval), and the failure-path `mark_ran()` in
`scheduler.py:188` all express backoff *in units of interval*. Deleting
intervals without replacing that backpressure would turn a failing agent into
a hot loop that starves every other agent of slots. This is why the change is
a redesign and not a deletion.

## Invariant

> The room runs flat out as long as it is making progress, and quiets itself
> exactly when it stops making progress.

Idle means *converged*, not *"a timer hasn't elapsed."*

## Settled constraints

Confirmed with the user before design:

- **LLM backend:** vLLM-style continuous batching, 4–8 usable concurrency.
- **Priority:** background work ranks *higher* than agent runs — catch up before agents act.
- **Blocking:** *strict* — agents fully paused until the backlog drains.

## Design

### 1. Progress replaces the clock

`ready_for_interval(now)` becomes `ready(now) -> now >= max(_fail_until, _idle_until)`.
`interval` and `_last_run` stop gating dispatch entirely.

### 2. Progress is measured, not declared

`Committer` already stamps every commit with the ambient run id
(`novelizer/canon/committer.py:21,44,46`), and `EventStore.events_for_run()`
already exists (`novelizer/canon/event_store.py:94`). So "did this run make
progress?" has one exact answer: **did it commit anything to canon?**

This is a single injectable probe in the chassis — `progress_probe:
Callable[[str], Awaitable[bool]] | None`, mirroring the established
`override_provider` seam — rather than hand-written `_fingerprint()` methods on
each of the seven agents that lack one. It measures what the agent actually
did rather than a proxy for it, and it holds automatically for agents added
later.

One trap: `AGENT_REMARKED` (`novelizer/agents/base.py:54-60`) is itself a canon
commit, so an agent that only chatters would falsely read as progress. The
probe excludes a chatter set.

Default `None` ⇒ assume progress (fail open), so kit consumers that do not wire
a probe keep today's behavior.

### 3. Two backoff ladders, in seconds

Replacing the interval-multiplier scheme. Both are consulted via `max()`:

- `_fail_until` / `_fail_streak` — base ~2s, exponential, capped, reset on any successful run.
- `_idle_until` / `_idle_streak` — base ~5s, exponential, cap ~300s, reset on progress.

`note_pass()` survives as an early-exit optimization: an agent that *knows* it
has nothing to do can say so without waiting for the probe. Its four callers
keep working unchanged.

The existing `_fingerprint()` / watermark machinery is untouched — it gates
*readiness scoring*, which is a different question from *did I make progress*.

### 4. Backpressure moves from intervals to concurrency

AIMD, fleet-wide, on a shared `AdaptivePool`: on a 429, halve the limit (floor
1) and set a cooldown; on sustained success, additively recover toward
`llm_pool_size`. The 429 detector already exists and is provider-agnostic
(`agent_kit/base.py:24`, walks the `__cause__`/`__context__` chain).

`asyncio.Semaphore` cannot shrink, so the pool is a small purpose-built class
over `_limit` / `_active` / `asyncio.Condition`.

**A permit covers one whole agent run, not one LLM call.** Per-call permits
would have to be acquired inside langchain callbacks, where an exception leaks
the permit; per-run acquisition has exactly one owner and one release site.
Both the scheduler and the KG drain draw from the same pool — that shared
ceiling is the property that does not hold today.

### 5. Strict background gate

`Scheduler` gains `gate_provider: Callable[[], Awaitable[bool]] | None`,
mirroring `override_provider` exactly. Consulted once per tick, before scoring.
Closed ⇒ dispatch nothing and emit eligibility reason `"background catch-up"`.

Novelizer's provider: `indexer.lag() == 0 and kg_projector.lag() == 0`.

In-flight runs are **never killed** when the gate closes. The gate blocks new
dispatch only.

### 6. Poison-event skip is mandatory, not optional

Both projectors `break` on first failure today (`indexer.py:89`,
`kg_projector.py:70`), pinning the cursor until the event succeeds. Combined
with strict gating this is a **permanent whole-room deadlock**: one
consistently-failing KG extraction freezes every agent, forever.

After `poison_skip_after` consecutive failures on the same sequence, log an
error, emit telemetry, and advance past it. Failure counts are in-memory and
reset on restart — acceptable, and documented.

### 7. Drain becomes the critical path, so it goes parallel

Strict gating means the room waits on the drain, so a sequential drain would
simply become the new source of idleness.

- Dedupe by `aggregate_id` within the pending window — re-embedding chapter 7
  four times because it was revised four times is pure waste.
- Partition by `aggregate_id` and run partitions concurrently under pool
  permits; same-aggregate events stay ordered within their partition.
- Advance the cursor over the **longest contiguous success prefix** — the only
  safe rule when later items may have succeeded while an earlier one failed.

### 8. Visible progress is part of the design

Strict gating without visible progress is indistinguishable from a hang. New
telemetry `BACKGROUND_PROGRESS(kind, done, total)` drives an `indexing 42/210`
readout, and the new `"background catch-up"` eligibility reason explains *why*
agents are held. Without these, strict gating merely relabels the idleness this
work exists to remove.

### 9. Settings

New: `llm_pool_size` (default 6), `background_drain_concurrency`, four backoff
base/cap values, `poison_skip_after`.

The seven `*_interval` keys stay **accepted-and-ignored with a deprecation
note**. They live in `STORY_OVERRIDABLE_KEYS`, so existing `story.toml` /
`config.toml` files carry them; removing them outright would hard-error on load
for every existing story.

## Consequences, accepted

- **Every chapter the Author writes immediately creates lag and pauses the
  whole room** until it is embedded and KG-extracted. That is the direct
  consequence of "strict + background-first," and it was chosen deliberately.
- **The Author will write near-continuously.** Its readiness is
  `max(0.0, 1.0 - drafts/3)` (`author.py:263-265`) — maximal when there are no
  drafts. Today the 300s interval is the only thing pacing chapter production.
  Afterwards, pacing comes from the Editor draining drafts (readiness → 0 at 3
  drafts) and from the background gate. This is the intended "flat out while
  making progress" behavior, but it is a visible change in the room's rhythm.
- **In-flight runs are never cancelled** by the gate closing.

## Verification approach

Red/green + property-based, per project principles.

The deadlock regression is written **first**: a permanently-failing KG event
must not freeze agents past `poison_skip_after` attempts. It guards the
sharpest edge in the design.

The no-progress property is asserted **generically against the chassis**, not
per agent, so it holds for all eleven registered agents and any added later.
Specific hot-loop regressions cover Retconner and Triage, which both clear
`_deferred` and restart the pass when every candidate has failed once
(`retconner.py:86-90`, `triage.py:75-77`) — behavior the interval currently
throttles.

## Alternatives considered and rejected

**Shorten the intervals.** Treats the symptom. The gate ordering is the bug;
any interval short enough to feel responsive is short enough to hot-loop a
failing agent.

**Per-agent `_fingerprint()` on all eleven agents.** Was the original plan.
Rejected once `events_for_run` was found: seven bespoke methods to approximate
a signal the event log already records exactly, with drift risk as agents
change.

**Soft background priority (agents throttled, not paused).** Rejected by the
user in favor of strict blocking.

## Deferred

- Per-agent priority weighting.
- Persisting poison-skip counts across restarts.
- Bespoke per-agent fingerprints, unless an agent demonstrably backs off
  wrongly in the live run.
