# Engine Room & Telemetry — Design

**Date:** 2026-07-18
**Status:** Approved design, pre-implementation
**Related:** [MILESTONES.md](../../MILESTONES.md) (fits M5 UX polish; motivated by the
scheduler-starvation bug fixed in c70b8f6, which was invisible until diagnosed by hand)

## Problem

The TUI's views are about the *story* (Story Shape, Thread Board, Who-Knows-What,
Causeway). The *machinery* — which agent is running, what the LLM is doing, why
nothing is happening, what the room did overnight — is invisible. A crashed agent
silently starving the scheduler looked identical to a healthy idle room.

## Requirements (from brainstorm)

Priority order established with the user:

1. **Live-run visibility first.** Primary lens: *"what is it doing right now?"* —
   watch the machine work, not just a health strip.
2. **Durable historical trace second.** *"What did the room do overnight?"* must be
   answerable after a restart.
3. **Decision context (what prompt/context a call was fed, why the scheduler picked
   an agent) folds into the trace**, not a separate feature.

Depth of live view: stream the actual model output tokens as they arrive (core),
with a vitals summary layered in (agent, task, model, token count, elapsed,
attempt). Full prompt inspection is available but **off by default, toggleable**.

Placement: **thin + thick** — a one-line activity strip always visible in Mission
Control, and a full Engine Room view one keypress away.

Concurrency assumption: the scheduler runs agents strictly one at a time
(`scheduler.py` awaits `agent.run_once()` sequentially), so the live view handles
exactly one stream in flight. If the scheduler ever goes concurrent, the live pane
needs a multiplexing revisit — noted as out of scope.

## Architecture

Principle: **machinery facts are events too, but they live in their own log.**

Three new pieces:

### 1. Telemetry store

A second `EventStore` instance on its own SQLite file (`telemetry.db` beside the
story's domain event log). Coarse-grained machinery events only:

| Event | Payload highlights |
|---|---|
| `scheduler.picked` | agent name, tick timestamp |
| `scheduler.eligibility_changed` | agent name, eligible/ineligible, reason (paused / interval not elapsed / readiness 0) — emitted on *change*, not per tick |
| `agent.run_started` | run_id, agent name, task label |
| `llm.call_started` | run_id, model, **full rendered prompt payload** (system prompt, brain context, voice pack) |
| `llm.call_finished` | run_id, token counts, duration, attempt number |
| `llm.call_failed` | run_id, error summary, duration, attempt number |
| `agent.run_finished` | run_id, duration, summary of domain events produced |
| `agent.run_failed` | run_id, exception type + message, phase (LLM call vs. intent commit) |

The stored prompt payload powers prompt inspection in both live view and trace.
Telemetry replay/projection reuses the existing `Projector` pattern.

### 2. Ephemeral live channel (`TelemetryBus`)

An in-process pub/sub carrying high-frequency signals that are **never
persisted**: individual streamed tokens and heartbeats. Persisted telemetry events
(item 1) are also mirrored onto the bus, so live consumers get everything from one
subscription. Individual token deltas are not durably lost in any meaningful
sense: the finished chapter already lands in the domain log.

### 3. Correlation

Every agent run mints a `run_id`. Every domain event committed during that run
carries it (threaded through the existing committer as a small additive envelope
field; existing events are unaffected). This is the join key: "this chapter came
from run X, which was fed prompt Y and took 52s."

### Consumers

- Mission Control strip and Engine Room live pane subscribe to the bus (zero
  polling).
- Engine Room trace pane is a projection over the telemetry store, tailing it
  live so historical and live views agree without a refresh action.
- On startup the live view seeds its state from the last few telemetry events so
  it is never blank.

## Instrumentation points

Four touch points, all in existing code:

1. **Scheduler** (`scheduler.py`) — emits `scheduler.picked` on selection and
   throttled eligibility-change events. Eligibility is already computed in one
   place, so "why not running" falls out of predicates it already evaluates.
2. **`BaseAgent.run_once` wrapper** — template method mints the `run_id`, emits
   `run_started` / `run_finished` / `run_failed` (catching, recording, and
   re-raising exceptions), and stashes the `run_id` where the committer picks it
   up. Subclasses' existing `run_once` bodies move to a protected `_run` — one
   mechanical rename per agent, no logic changes.
3. **LLM callback handler** — async LangChain callback attached to runner
   invocations: `on_llm_start` → `llm.call_started` (with rendered prompt);
   `on_llm_new_token` → bus only; `on_llm_end` / `on_llm_error` →
   `llm.call_finished` / `llm.call_failed` with vitals. Streaming is enabled on
   the chat model in `build_chat_model` so tokens arrive incrementally.
4. **Committer** — gains the ambient `run_id` and stamps it into domain-event
   envelopes (additive).

The telemetry writer is fire-and-forget with a guard: a telemetry write failure
logs a warning and drops the event. It must never take down an agent run or the
scheduler.

## TUI components

### Mission Control activity strip

One-line widget docked in the existing Mission Control layout:

- Live run: `▶ author · drafting · 3.4k tok · 52s · attempt 1`
- Idle: `idle · next: editor in 12s` (scheduler's near future)
- Failure: red `✗ author crashed 2m ago (see Engine Room)`, held until the next
  successful run.

Subscribes to the bus; no polling.

### Engine Room view

A new sibling view (same navigation pattern as Story Shape / Causeway), two
stacked regions:

- **Live pane (top, ~2/3):** header line with current run vitals (agent, task
  label, model, ticking token count, elapsed, attempt); body streams model output
  tokens as they arrive, auto-scrolling with the usual scroll-to-detach behavior.
  `p` toggles **prompt inspection**: the pane splits to show the exact prompt
  payload of the call in flight, with system prompt / brain context / voice pack
  sections collapsible. Off by default.
- **Trace pane (bottom, ~1/3, expandable):** reverse-chronological table over the
  telemetry store, one row per machinery event
  (`12:04:32 author run ✓ 52s 3.4k tok`, `12:03:58 scheduler picked author`,
  `12:01:11 editor run ✗ TimeoutError`). Enter drills into detail; for
  `llm.call_started` rows that means the full stored prompt — where "why did it
  do that" gets answered historically. Detail view links a run to its domain
  events by `run_id` ("produced: chapter.drafted ch-12"). Tails the store live.

## Error handling & data lifecycle

- **Telemetry store write fails** → warn-and-drop; the bus mirror still fires, so
  live view degrades gracefully and the trace just has a gap.
- **Agent crash mid-stream** → `agent.run_failed` records exception type, message,
  and phase; live pane freezes the partial stream and marks it `✗ crashed`; strip
  goes red. The scheduler-starvation class of bug becomes visible in seconds.
- **TUI restarts mid-run** → live view reconstructs from the telemetry store; a
  `run_started` without a matching finish renders "run in progress (stream not
  attached — restarted mid-run)" rather than pretending to stream.
- **No telemetry yet** (fresh story or pre-feature story) → explicit empty states,
  never an error.
- **Prompt payloads** are the only large records (tens of KB per call); stored
  as-is. A day-long run at ~1 call/minute is tens of MB of local SQLite —
  acceptable for MVP. Retention pruning is deliberately deferred (see below).
- **The telemetry log is disposable by contract:** deleting `telemetry.db` loses
  machinery history and affects nothing else. No domain projection may ever read
  from it — that boundary keeps the domain log the sole source of truth.

## Testing

House rules: red/green TDD, black-box first, Hypothesis where invariants
generalize.

- **TelemetryBus:** subscribers receive published items in order; a slow or dead
  subscriber never blocks publishers or other subscribers (bounded queue,
  drop-oldest); unsubscribe stops delivery.
- **Instrumentation (black-box, fake runners):** a run emits `run_started` →
  `llm.call_*` → `run_finished` in order with one shared `run_id`; a crashing
  runner yields `run_failed` with the exception summary *and* the scheduler
  continues to the next tick (regression armor for the starvation bug); a
  fault-injected telemetry store never fails the agent run (warn-and-drop
  verified).
- **Correlation property (Hypothesis):** for arbitrary interleavings of runs and
  commits, every domain event committed during a run carries exactly that run's
  `run_id`, and no `run_id` appears in the domain log that the telemetry log
  doesn't know.
- **Trace projection property:** replaying any generated telemetry event sequence
  yields trace rows 1:1 with run/call events — replay never drops or duplicates
  (same invariant style as the causal-edge test).
- **TUI (Textual pilot, taller-viewport pattern):** strip renders live/idle/failed
  states from synthetic bus traffic; Engine Room streams tokens into the live
  pane; `p` toggles prompt inspection off-by-default; trace rows drill into
  detail including the stored prompt; restart-mid-run shows the
  "stream not attached" state.
- **Live smoke:** one entry in the existing live-smoke suite — run the room
  briefly against the real endpoint, assert the telemetry log contains a
  completed run with nonzero token vitals and a prompt payload that round-trips.

## Out of scope

- Retention/pruning of `telemetry.db` (revisit when a real story's file gets
  annoying).
- Multi-stream live view (scheduler is sequential today; revisit if it goes
  concurrent).
- Cost accounting / pricing (token counts are recorded; dollars are not).
- Web UI surfaces.
