# Story Brain Polish — Design

**Date:** 2026-07-19
**Status:** approved in brainstorm; awaiting director review of this written spec
**Feeds into:** three implementation plans (P1 visual, P2 zoom, P3 Pulse — see Phasing)
**Grounding:** builds on the Mission Control design pass
(`2026-07-18-mission-control-design-pass-design.md`) and its Phase 2 delivery
(`brain_model.py` / `brain_panel.py`). Phase 2 explicitly deferred selection and
targeting inside tabs; this spec is that deferred work plus the depth pass the
brainstorm scoped.

## Why

The Story Brain works but reads flat: four tabs of plain text lines, one sparkline,
nothing selectable, nothing that synthesizes. The vision doc promises a director's
control room where "personality is the product"; the brain today is a state display,
not a character. This pass makes it the cast's shared whiteboard — glanceable in the
compact panel, workable in a full-screen zoom mode, and capable of *saying things*
(attributed, prioritized, actionable insights) rather than only listing state.

## Decisions from the brainstorm

1. **Scope:** all three depth dimensions — visual richness, interactivity, liveness —
   sequenced into phases (not a single-dimension pass).
2. **Real estate:** keep the compact panel as the glanceable summary; add a
   maximized Brain mode (`BrainScreen`) where the active tab takes the whole
   terminal. Drill-in interactivity lives in the zoomed view.
3. **Data:** new projections are allowed. The event log is the source of truth;
   anything needing genuinely new *events* is flagged explicitly (one candidate —
   see Knowledge provenance).
4. **Voice:** cast-voiced insights. Alarms and callouts attribute to the responsible
   agent with their `identity.py` glyph/color. The brain is the cast's whiteboard.
5. **Approach:** "the cast's whiteboard" — a new Pulse synthesis view leads; the four
   existing tabs become polished drill-ins; zoom mode carries the deep variants.

## Section 1 — Information architecture

Five views instead of four, with **Pulse** as the new default tab, plus zoom:

```
├─ STORY BRAIN ─── 0 Pulse · 1 Shape · 2 Threads · 3 Secrets · 4 Cause ──┤
│  ∿ ch 6 · rising ▂▃▅▆▄▇ · 2 alarms · 1 debt due                        │
│                                                                        │
│  ⚖ Continuity   PARADOX — ch 6 pays a debt ch 8 creates                │
│  ∿ Analyst      ch 5 sags — nothing pushes back after the storm        │
│  ♥ Keeper       "the letter" is one reveal from public (3 of 4 know)   │
│  · since ch 5   +2 threads · secret spread +1 · tension +0.15          │
├─ Shape ⚠1 · Threads ⚠1 · Secrets · Cause ⚠1 ───────────────────────────┤
```

- **Pulse** (key `0`) is the synthesis view and the default active tab. Keys `1`–`4`
  keep their tabs.
- **Zoom mode** (key `b`) pushes a full-screen `BrainScreen` — same five tabs, same
  tab keys, each view gets the whole terminal and becomes keyboard-navigable.
  `b`/`escape` pops back. The compact panel stays read-only; zoom is where you work.
- **The alarm strip is unchanged** — four domains. Pulse synthesizes the alarms; it
  is not a fifth alarm source.
- Every view remains a pure `records → model` function; `BrainPanel` and
  `BrainScreen` are thin shells; one `_brain_loop` polls for both at 1 Hz.

## Section 2 — The Pulse view

A ranked stack of **insight cards** under a one-line **health header**. All
rendering is deterministic templates over projection data — no LLM calls; renders at
1 Hz, unit-testable, never blocks on the API.

**Health header** — one line: current chapter, pacing label with the spark inline,
alarm total, most urgent obligation ("1 debt due" when a causal edge's effect
chapter is unwritten/near). Dim by default; alarm count in alarm color when nonzero.

**Insight cards** — one line each in the compact panel; two in zoom (second line =
evidence/detail). Anatomy: `glyph agent-label statement`; glyph + label in the
owning agent's identity color; alarm-class statements in `ALARM_STYLE`. Ownership by
domain:

| Insight | Owner |
|---|---|
| sag / spike / pacing | ∿ Structure Analyst |
| secret spread / leak proximity | ♥ Character Keeper |
| paradoxes | ⚖ Continuity Checker |
| stale threads | ✎ Author ("the boy's gift has sat idle 5 chapters — plant or pay it off") |

**Ranking** — fixed severity order, stable within tiers by chapter position:

1. Paradoxes (story is broken)
2. Leak proximity (secret one reveal from public, or spread jumped)
3. Stale threads
4. Sag / spike
5. Info deltas (below the fold: the dim "since ch N" line)

**Voice library** — 2–3 phrase templates per insight type, in each agent's register
(Analyst clinical-anxious, Keeper protective, Continuity legalistic). Template
choice is a stable hash of the subject id: a given insight keeps its phrasing across
refreshes (no flicker), different subjects vary. Templates live as data in a new
`novelizer/tui/widgets/insights.py`, tested for totality (every insight type ×
every template renders).

**Delta line** — "since ch 5: +2 threads · secret spread +1 · tension +0.15",
computed against the last chapter snapshot (Section 5). Dim; omitted when nothing
changed.

**Empty states** — house voice: *"The room is quiet. Nothing owed, nothing leaking,
nothing stale."* A truly empty story defers to the existing per-domain empty lines.

**Cap** — compact shows the top N cards that fit (severity-ranked, so truncation
only hides the least urgent); zoom shows all.

## Section 3 — Per-tab visual upgrades

**Shape.** Compact replaces the `Sparkline` *widget* with a model-rendered text spark — one
block cell per chapter, so the marker row beneath it aligns exactly (the widget
scales data to its width, which makes per-chapter alignment impossible; the
scaled full-width chart lives in P2's zoom mode) —
sag/spike flags positioned under their chapters — plus the existing axis/pacing
line:

```
tension  ▂▃▅▆▂▇        pacing: rising
              ⚠sag
ch 1 ▸ ch 6
```

Zoom renders a real chart: block-character columns ~12 rows tall, y-axis labels
(0–1), one column per chapter with its number beneath, sag/spike columns colored
with callout text alongside, and a `╌╌` guide line at mean tension. Pure function
returning rows of `Text`; no plotting library.

**Threads.** Live rows gain an **age heat bar** — chapters since last touch,
`▰▰▰▱▱`, colored dim (fresh) → amber (approaching threshold) → alarm (stale) —
staleness becomes a visible gradient instead of a binary flip:

```
⚠ The boy's gift      ▰▰▰▰▰  stale — last touched ch 2, 5 ago
· The unraveling sky  ▰▱▱▱▱  open — touched ch 6
· Doug's dress        ▰▰▱▱▱  advanced — touched ch 5
✓ 2 paid off · 1 abandoned
```

Bar length/heat derive from existing `last_chapter_id` + `chapters_elapsed_since`
and the settings threshold — no new data. Zoom adds a right-hand detail gutter for
the selected thread (state history from Section 5's thread-history projection).

**Secrets.** The matrix keeps its glyph grammar; the plain "N know" summary becomes
a **spread meter** — `●●●○ 3/4` — whose color heats as spread approaches everyone
(the same leak-proximity signal the Pulse card uses; one derivation, two surfaces).
Zoom widens columns to short names instead of initials and adds a selected-cell
inspector ("Elara learned *the letter* in ch 4 — from the Boy", from the knowledge
provenance projection).

**Cause.** Zoom upgrades to **chain rendering** — edges sharing chapters merge into
indented cause trees with box-drawing connectors; paradox back-edges drawn
explicitly:

```
ch 2 "The Storm"
 └─▶ ch 5 "The Naming" — the mast breaks, forcing the landing
      └─▶ ch 6 "Ashore" — they meet the keeper
ch 8 "The Debt" ──▶ ch 6 "Ashore"   ⚠ PARADOX (effect precedes cause)
```

Compact keeps the flat list (chains don't fit in a few rows) but sorts paradoxes to
the top and colors the arrow glyph by edge state.

Compact and zoom variants are the same pure function with a `wide: bool`, or
separate `*_zoom` functions where the shapes genuinely differ — the implementation
plan picks per tab; the seam (pure, no Textual, no I/O) is non-negotiable either
way.

## Section 4 — Zoom mode & interactivity

**`BrainScreen`** — a pushed Textual screen (same pattern as the approval screen):
five tabs across the top, active view filling the terminal, one-line footer of
contextual actions. Keys `0`–`4` switch tabs; `b`/`escape` pops. Selection exists
*only* in zoom — `BrainPanel` stays read-only and untouched by cursor state.

**Navigation.** `j`/`k` and arrows move a row cursor over the active view's rows
(threads, secret rows, causal edges, Pulse cards). Highlighted row renders with
background emphasis; detail gutters (Threads/Secrets) follow the cursor. In Shape
the cursor moves per-chapter along the chart, showing that chapter's exact scores.

**Enter = go to the evidence.** On a Pulse card: jump to the owning tab with the
subject row pre-highlighted. On a thread/secret/edge/chapter: open the detail
gutter, or hand off to a richer existing surface (chapter → reading view).

**Actions prefill, never execute.** The selected row exposes ≤3 actions in the
footer; every action *prefills the existing director command input* with the target
resolved — e.g. stale thread + `n` → `:thread nudge the-boys-gift`; secret + `x` →
the reveal command; paradox edge + `d` → the edge-drop command. The director sees
and confirms before anything runs. This delivers "nobody retypes a slug" without
giving the brain a write path — it stays a pure reader; the command layer stays the
single mutation seam.

**Model split.** Cursor/selection state lives in the screen widget only. Pure model
functions gain at most an optional `selected_index` for emphasis rendering. What
actions a row offers and what command each prefills is pure data on the row models
(`ThreadRow.actions -> list[(key, label, command)]`), unit-testable.

## Section 5 — Liveness & data

Derivable from existing data (no new projections): thread age bars
(`last_chapter_id` + `chapters_elapsed_since`), leak proximity (knowledge matrix),
debt-due (causal edges whose effect chapter is unwritten).

Three new projections, all folds over events the log already records:

1. **Chapter snapshots** — on each chapter-approved event, stamp domain counts
   (open threads, per-secret spread, latest tension). The Pulse delta line is
   current live counts minus the last snapshot — durable across restarts, unlike
   in-memory poll diffing. Read query: `latest_chapter_snapshot()`.
2. **Thread history** — per-thread ordered (chapter, state-change/touch) list from
   thread events; powers the zoom detail gutter ("planted ch 1 → advanced ch 3 →
   idle since"). Read query: `thread_history(thread_id)`.
3. **Knowledge provenance** — per (secret, character): source chapter and teller;
   powers the cell inspector. If existing knowledge events already carry
   source/chapter this is a projection reshape. **This is the one place new event
   fields might be needed** — the P3 plan must verify event payloads first;
   everything else in this design is confirmed derivable.

**Refresh mechanics unchanged**: the 1 Hz `_brain_loop` adds these queries to its
single-snapshot fetch. No push/reactive machinery — at 1 Hz, polling is liveness.

**Motion, kept honest**: no animation loops. The only motion is change emphasis — a
row whose content changed since the previous refresh renders one cycle with a brief
highlight style (pure function of `(row, changed: bool)`), so the brain visibly
ticks when the room acts.

## Section 6 — Architecture & testing

- `brain_model.py` + new `insights.py` stay pure: records in, `Text`/row models
  out, no Textual imports, no I/O.
- `BrainPanel` unchanged in kind; `BrainScreen` is a new thin shell owning only
  cursor state.
- New projections follow existing store patterns; agent identity comes from the
  existing `novelizer/tui/identity.py`.
- Testing: red/green + property-based, per house rules. Pure-function coverage for
  every renderer; phrase-library totality tests; property tests for ranking
  stability and chart invariants (column count == chapter count, values clamp to
  axis); the `docs/TESTING-TUI.md` pytest-wedge recipe for the screen shells. Test
  runs happen in worktrees, never the main checkout.

## Phasing

Three shippable slices, each with its own implementation plan:

- **P1 — Visual.** Section 3 compact variants (Shape marker row, thread heat bars,
  secrets spread meter, cause paradox-first sort). No new data, no new surfaces.
  The home screen stops being flat immediately.
- **P2 — Zoom.** `BrainScreen`, zoom renderers (chart, chains, wide matrix,
  gutters), cursor/enter/prefill actions. Thread-history and knowledge-provenance
  projections land here if the gutters/inspector ship in P2, else stub the gutters
  to what existing data serves and move the projections to P3.
- **P3 — Pulse.** Chapter-snapshot projection, `insights.py` voice library, the
  Pulse tab + delta line + health header, change-emphasis ticks, Pulse-card →
  evidence jumps.

P1 has no dependency on projections; nothing blocks it.

## Out of scope

- The Room view redesign (deferred by the original design pass; still deferred).
- LLM-generated insight text (voice is deterministic templates by design).
- Any new write path from the brain (actions prefill commands only).
- Feed, proposals, browser, Engine Room, status bar — untouched.
