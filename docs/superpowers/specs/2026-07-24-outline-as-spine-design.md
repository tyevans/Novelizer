# Outline as the Story's Spine — Design

**Date:** 2026-07-24
**Status:** Approved (brainstorming), pending implementation plan
**Branch:** `worktree-outline-as-spine`

## Problem

The system already treats story structure as first-class, event-sourced data: a
**Blueprint** (framework + target length + genre), **Beats** (structural anchors),
**Chapter briefs** (a rolling wave of near-term plans), and a **threads plan / ledger**
(promise payoff tracking). The **Plotter** owns and mints this outline; the **Author**
consumes it; it is mounted read-only for agents at `/outline/*`; and a live "Outline"
board already renders in the Story Brain panel.

Three gaps remain against the goal of making the outline the *spine* of everything:

1. **No gate.** "Don't work before the outline exists" is a prompt-level norm only.
   The architecture is deliberately outline-*optional*: at genesis the Author is
   maximally ready (`readiness = 1.0`) while the Plotter cannot act until prose or
   world exists (`readiness = 0.0`). So chapter 1 is drafted *before* any blueprint,
   and the Plotter retrofits structure onto existing prose.
2. **Weak visibility / direction.** The outline is discoverable by agents via the
   `/outline/*` mount, but nothing points them at it forcefully, and it is absent
   from the navigable TUI canon browser.
3. **TUI ergonomics.** The Outline board is a read-only summary with no drill-down,
   and the Story Brain panes are small and non-scrolling — unpleasant to read.

## Goals

- Make the first-pass outline the genesis artifact: the **Plotter goes first**,
  proposing a blueprint from the **premise alone**.
- Introduce a **soft gate**: prose (and its downstream) is suppressed until an active
  blueprint exists — *prioritize, don't block*, with a fallback so unattended runs
  never deadlock.
- Make the outline **obvious** and agents **directed to it**, without adding a second
  source of truth.
- Give the outline a **roomy, navigable, scrollable** home in the TUI.

## Non-Goals

- **No real on-disk outline file.** The event log + `/outline/*` virtual mount remain
  the single source of truth. A materialized file would be a second, drift-prone
  source of truth and violates the project's event-sourcing principle.
- **No hard architectural block.** The gate is soft (readiness-driven). We are not
  making the scheduler refuse to dispatch prose agents.
- **No new approval machinery.** Unattended runs are handled by a timeout fallback,
  not auto-approval or new proposal flows.
- No unrelated refactoring of the agents, scheduler, or canon projection.

## Key Decisions (from brainstorming)

| Decision | Choice |
| --- | --- |
| Gate strength | **Soft** — prioritize via readiness, don't block; retrofit remains a fallback |
| Gate location | **Readiness layer (Approach A)** — a shared helper, no `agent_kit` scheduler changes |
| Plotter cold-start | **Premise alone** — Plotter mints the first blueprint with no world yet |
| Outline persistence | **Stays event-sourced**; visibility via the existing `/outline/*` mount + prompts |
| Unattended runs | **Soft-gate timeout fallback** — after N Plotter passes with no active blueprint, the Author un-suppresses and drafts provisionally |
| TUI drill-down | **Route into the canon browser** (roomy detail pane); keep the brain board as the glanceable dashboard |

## Architecture

### Genesis flow (new ordering)

```
premise-seed
    │
    ▼
Plotter proposes blueprint  ──(premise alone; no world/prose required)
    │
    ▼
[approval: human framing at creation, or :approve, or timeout fallback]
    │
    ▼
active blueprint  ──►  World Architect + Author + downstream proceed against it
```

Today's chain is `seed → World Architect builds world → Plotter (once world exists) →
Author`. The new chain puts the Plotter first: `premise → Plotter blueprint → (World
Architect + Author) against the blueprint`.

### Component changes

#### 1. Plotter goes first (`novelizer/agents/plotter.py`)

- **`readiness()`** (currently returns `0.0` when `not chapters and not world`,
  `plotter.py:124-145`): when a **premise-seed is present** and there is **no active
  blueprint and no pending blueprint proposal**, return `1.0`. The Plotter becomes the
  top-priority agent at genesis. The existing brief-runway / late-beat logic is
  unchanged for the steady state.
- **Premise visibility.** The Plotter polls `list_unconsumed_signals(target_agent=
  "plotter")` (`plotter.py:156`). The premise-seed is currently authored for the World
  Architect. Fix so the Plotter sees the premise: either broadcast the premise-seed to
  all agents, or mint it as a distinct `kind` the Plotter reads. (Exact mechanism —
  broadcast vs. dedicated kind — pinned in the implementation plan after confirming
  `DirectorSignal` targeting semantics.) The premise must remain visible to the World
  Architect too.
- **Prompt** (`PLOTTER_SYSTEM_PROMPT`, `plotter.py:24-33`): sharpen "propose a
  blueprint when none exists" to "propose a blueprint **from the premise, before any
  prose or world exists** — you go first."

Blueprint adoption remains `_ALWAYS_GATED` (`novelizer/canon/policy.py:16`): the
Plotter's blueprint is a *proposal* requiring Director approval, exactly as today. We
do not change that policy.

#### 2. The soft gate — readiness helper (Approach A)

- **New shared helper**, e.g. `novelizer/brain/gate.py::blueprint_gate(read) -> bool`
  (or a `ReadStore` convenience) — the single source of truth for "is there an active
  blueprint?" Returns `True` when `get_active_blueprint()` is not `None`.
- **Gated agent: the Author** (`novelizer/agents/author.py:255-257`). Its `readiness()`
  multiplies to `0.0` while the gate is closed **and** the fallback has not fired
  (see below). This is the whole prose pipeline's choke point: Editor, Structure
  Analyst, Continuity Checker, Retconner, and Summarizer already gate on "drafts
  exist," and no drafts appear until the Author runs — so gating the Author alone
  closes the downstream. We gate the Author *explicitly* (not scattered checks) and add
  a test asserting the gated set, so the policy is legible in one place.
- **Not gated:** Plotter (goes first), World Architect (builds world *against* the
  approved blueprint), Muse (inspiration). These legitimately run at/after genesis.

Why the readiness layer and not the scheduler: the scheduler dispatches purely by
readiness score and reserves its one hard `gate_provider` for infra catch-up
(embedding/KG lag, `runtime.py:82-85`). Readiness *is* the soft signal — using it keeps
the gate genuinely soft and leaves shared `agent_kit` code untouched.

#### 3. Timeout fallback (unattended runs)

- The Author's readiness un-suppresses when the Plotter has made **≥ N passes since the
  premise** with still no active blueprint. This is **derived, not wall-clock** (the
  scheduler is event-driven): count Plotter-authored events since the premise-seed
  (e.g., Plotter feed remarks / blueprint-proposal attempts) as the "idle Plotter
  cycle" measure. Exact counter pinned in the plan; `N` is a setting (default e.g. 3).
- When the fallback fires, the Author drafts **provisionally** and is told so (see
  Prompts). This is precisely today's retrofit behavior, now reached only as a
  deadlock-avoidance escape hatch rather than the default path.

#### 4. Visibility & direction (prompts)

- **Author prompt** (`author.py:46-81`): with the gate active, the "no blueprint
  adopted" prose path is the fallback case, not the norm. Rewrite that guidance to
  point at the Plotter/outline, and name concrete `/outline/` paths so "directed
  towards it as needed" is literal.
- **Fallback honesty:** in the fallback (drafting with no blueprint), the prompt
  explicitly states "no outline exists yet — you are drafting ahead of the Plotter;
  keep it provisional."
- **Gated agents** name the concrete `/outline/*` files in their prompts where they
  should consult the outline.

#### 5. TUI

Division of labor:

- **Story Brain "Outline" board** (`brain_panel.py`, `brain_model.py::outline_tab`)
  stays the *glanceable status dashboard* — blueprint header, beat drift strip,
  threads×chapters grid, open-briefs strip. Unchanged in role.
- **Canon browser** (`novelizer/tui/widgets/browser.py`, `browser_model.py`) becomes
  the *navigate + drill-down + roomy reading* surface:
  - Add an **Outline section** to `browser_sections()` (`browser_model.py:87-98`,
    currently hardcoded to chapters/characters/world/flags/threads/themes) with nodes
    for **blueprint**, **beats**, **open/fulfilled briefs**, **threads-plan**, and
    **ledger**.
  - The browser already reads `read_store` records directly and already has a
    `detail_view` pattern (`browser_model.py:129-163`) — so drill-down lands in the
    browser's large detail pane with minimal new code.
- **Scrolling fix:** wrap the Story Brain tab bodies in scroll containers and set sane
  minimum sizes (`brain_panel.py` / `brain_model.py`) so the board stops truncating.

## Data flow

- Gate state is **derived** on every readiness call from `get_active_blueprint()` — no
  new persisted state, no new event types, no projection changes for the gate itself.
- The fallback counter is **derived** from existing Plotter-authored events relative to
  the premise-seed — again no new state.
- The premise-seed is an existing `director_signal.created` (`kind=seed`) event; the
  only change is making it visible to the Plotter.
- TUI Outline browser nodes read existing `read_store` accessors
  (`get_active_blueprint`, `list_beats`, `list_briefs`, thread/ledger reads).

## Error handling & edge cases

- **No premise at all** (empty story, no seed): **refined from the original plan
  above.** As implemented, the genesis fallback (`genesis_fallback_open` in
  `novelizer/brain/gate.py`) only opens once a blueprint has actually been *proposed*
  and the World Architect has built world — both require a premise seed to have arrived
  in the first place. With no seed, neither precondition is ever met, so the fallback
  never fires: the Plotter has nothing to outline from, no blueprint gets proposed, and
  the Author stays at readiness `0.0` indefinitely. This is a deliberate behavior change
  from the outline-optional baseline (which would have auto-drafted from nothing): a
  premise-less story now **waits** for a director `:seed` rather than drafting on its
  own. Not a regression — a room with no story idea at all had nothing worth drafting
  anyway.
- **Blueprint proposed but never approved** (attended run, human walks away): the
  fallback opens once the blueprint proposal exists and world has been built (not a
  wall-clock or pass-count timer — progress-based, matching the scheduler's
  event-driven design); prose proceeds provisionally. If the human later approves, the
  gate closes back to the normal path and subsequent chapters draft against the active
  blueprint.
- **Blueprint retargeted/superseded mid-book:** the gate checks *active* blueprint;
  supersession is audit-preserving and always leaves an active row, so the gate does
  not spuriously reopen mid-book.
- **Premise visible to two consumers:** broadcasting the premise-seed must not break
  the World Architect's existing seed consumption (it must still see and act on it).

## Testing

- **Gate helper:** unit test `blueprint_gate` true/false against active/no-active
  blueprint.
- **Author readiness:** `0.0` when no active blueprint and fallback not fired; normal
  (draft-backlog-based) when a blueprint is active; un-suppressed after N Plotter
  passes with no blueprint.
- **Plotter readiness:** `1.0` at genesis when a premise-seed exists and no
  blueprint/proposal; unchanged in steady state.
- **Gated-set assertion:** a test enumerating which agents are gated, so the policy is
  pinned in one place.
- **Genesis integration:** premise-seed → Plotter proposes blueprint → (adopt) →
  Author drafts; assert the Author does **not** draft before the blueprint under normal
  (non-fallback) conditions.
- **Fallback integration:** premise-seed, blueprint never adopted, N Plotter passes →
  Author drafts provisionally.
- **TUI:** browser exposes the Outline section and its nodes render detail views;
  brain tab bodies are scrollable. Follow `docs/TESTING-TUI.md` conventions (pilot
  tests are load-flaky; compare identical scopes for parity).

## Files touched (anticipated)

- `novelizer/agents/plotter.py` — readiness, premise visibility, prompt
- `novelizer/agents/author.py` — gated readiness, prompt rewrite + fallback honesty
- `novelizer/brain/gate.py` (new) — `blueprint_gate` helper + fallback counter
- `novelizer/director/commands.py` / signal targeting — premise-seed visibility to Plotter
- `novelizer/settings/…` — `N` fallback threshold setting
- `novelizer/tui/widgets/browser_model.py`, `browser.py` — Outline browser section + detail views
- `novelizer/tui/widgets/brain_panel.py`, `brain_model.py` — scroll containers / min sizes
- Tests under `tests/agents/`, `tests/brain/`, `tests/tui/`, `tests/canon_fs/`

## Open implementation details (to pin in the plan)

- Exact premise-seed visibility mechanism (broadcast target vs. dedicated `kind`).
- Exact fallback counter (which Plotter-authored event to count) and default `N`.
- Whether `blueprint_gate` lives in `brain/` or as a `ReadStore` method.
