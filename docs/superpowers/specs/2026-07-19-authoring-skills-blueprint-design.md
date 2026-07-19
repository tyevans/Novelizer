# Authoring Skills & Story Blueprint — Design

**Date:** 2026-07-19
**Status:** Draft — awaiting Director review
**Extends:** vision design (2026-07-17), canon pull tools design (2026-07-19)
**Depends on:** canon pull tools §4 wiring (backend/tools composition in runner builders) — designed, not yet implemented

## Problem

The writers' room is entirely **bottom-up**. The Author drafts the next chapter;
threads, themes, secrets, causal edges, and tension are recorded *as they
surface from prose* — declared by agents or mined afterward. The Story Brain
derives shape (staleness, sag/spike, leaks, paradoxes) but there is no authored
plan anywhere in canon: no outline, no beat targets, no planned resolution
windows, no character-arc declarations. Consequences:

- Nothing **drives the story toward anything**. Threads resolve when an agent
  happens to pay them off; there is no notion of "thread X should resolve
  around chapter 20" and no alarm when chapter 24 arrives without it.
- Structure is judged only in hindsight. The StructureAnalyst scores tension
  per chapter, but there is no target curve to compare against — a sagging
  middle is a vibe, not a measurable deviation from an adopted shape.
- Character interiority (lie/want/need) lives implicitly in prose and
  CharacterKeeper notes; arcs cannot be checked against structural beats.
- Agents have craft *instructions* baked into system prompts but no craft
  *reference material* they can pull on demand, and no scratch space to think
  in — every deliberation happens inside one context window.

The goal: give the room **authoring skills** — the ability to rough-sketch a
story's shape, plan the pace of thread resolution, declare arcs and promises,
and then continuously compare the emerging actual against the authored plan —
expressed through event sourcing, visualized in Mission Control, and exposed to
agents as pull-oriented deepagents tooling.

## Research foundations (condensed)

Full findings live with this spec's research notes; the load-bearing results:

**Narrative craft converges on ~7 computable concepts.** Across three-act,
Save the Cat, Story Circle, Hero's Journey, seven-point, Kishōtenketsu, and
Story Grid, the same skeleton recurs at different resolutions. Normalized to
manuscript-%, ~6 positions carry the structure (catalyst ~10%, threshold
~25%, midpoint 50% with a reactive→proactive flip, low point ~75%, final turn
~80%, climax ~90%). The recurring entities: **beats** (named slots with ideal
positions and tolerance), **threads/plotlines** (with Sanderson's
promise → progress → payoff lifecycle), a **setup–payoff ledger** (Chekhov's
gun, foreshadowing, red herrings), **character arcs** (Weiland: ghost → lie →
want vs. need; five arc types with fixed start/end beliefs), **value shifts**
(Story Grid: every scene turns a value's polarity), and **tension** as a
derived curve with expected peaks/troughs.

**The craft frameworks imply mechanical checks**, which is exactly what the
Story Brain already is — a linter over canon. Highest-value checks: every
non-red-herring setup gets a later payoff; payoffs are pre-seeded (no deus ex
machina); required beats land within tolerance of their ideal position; no
plotline goes dark beyond a window; arc pivots co-locate with structural
beats; the global tension max lands near the climax, not mid-book.

**Existing tools validate the visualization.** Plottr's core view — a grid of
plotlines (rows) × chapters (columns) with cards in cells, plus overlayable
beat templates — is the reference UI for interwoven-thread planning, and maps
directly onto a Textual DataTable-style Brain tab. Aeon Timeline's lesson:
keep story order and chronological order separate axes. Dramatica's lesson:
the plan can be treated as a constraint system the engine checks, not prose.

**deepagents 0.6.12 provides the exact extension surface.** `BackendProtocol`
(ls/read/write/edit/glob/grep + async variants, structured results) with
`CompositeBackend` path routing; `StateBackend` (thread-scoped scratch files)
vs `StoreBackend` (durable); `skills=` loading Claude-style SKILL.md packs
with three-layer progressive disclosure (name+description at startup → body on
activation → `references/` pulled on demand); `memory=` for always-loaded
AGENTS.md-style files. `CanonBackend` already implements the read side.

## Goals

1. An **authored story blueprint** in canon: adopted structural framework with
   beat targets, rolling chapter briefs, planned thread-resolution windows, a
   promise ledger, and declared character arcs.
2. **Plan-vs-actual intelligence**: Brain faculties that compare the emerging
   story against the blueprint and raise specific, actionable alarms.
3. **Mission Control visualization**: an outline board (threads × chapters),
   arc lanes, and a target-vs-actual tension overlay.
4. **deepagents-native authoring tooling**: blueprint readable through the
   canon filesystem; a writable workspace for agent drafting; craft knowledge
   as SKILL.md packs pulled on demand.
5. A clear **worldbuilding → novel pipeline**: how a seeded world becomes a
   premise, a blueprint, briefs, and finally chapters.

## Non-goals

- No change to the canon write discipline: structured intents through the
  `GatingCommitter` remain the only mutation path (locked in the pull-tools
  spec). Filesystem `write`/`edit` never mutate canon.
- No scene-level aggregate in this milestone. The chapter remains the atomic
  drafting unit; briefs carry scene-ish fields (value shift, outcome) at
  chapter grain. (Open question #1.)
- No branching/alternate-timeline planning; the blueprint is single-track,
  revised by supersession.
- No changes to the observation-side machinery (miners, StructureAnalyst
  scoring) beyond new Brain comparators.

## Approaches considered

**A. Fully structured plan (events only).** Every planning artifact is an
aggregate; agents interact with the plan exclusively through intents; no
free-form space. *Pro:* maximal auditability, everything projectable/checkable.
*Con:* forces deliberation through rigid schemas; agents lose the "think in
files" capability that deepagents' context-engineering guidance (offload
context, pull on demand) says sustains long autonomous work.

**B. Files-only plan (deepagents-native).** The outline is markdown the agents
write in a persistent Store-backed workspace; no new events. *Pro:* zero
schema work, maximal flexibility, idiomatic deepagents. *Con:* no minted ids,
so commit-time citation validation is impossible; nothing projects into the
ReadStore, so the Brain can't check it and the TUI can't show it; no gating —
the plan escapes the autonomy dial entirely. Breaks the system's spine.

**C. Hybrid (chosen).** The *decided* plan is event-sourced (approach A's
aggregates, intent-written, gated, projected, visualized); the *deliberation*
happens in a writable scratch workspace (approach B's files, explicitly
non-canon); craft knowledge ships as skills packs. Structure where checks need
it, files where thinking happens. This mirrors how the room already treats
prose: drafts are structured output, canon is events.

## Domain model — the Blueprint

New aggregates in the World Canon context, following the established recipe
(payload models in `canon/events.py` with locked-decision docstrings, read
models + tables, `Projector._project()` branches, `ReadStore` queries, policy
gating, intents). Ids are minted once via the existing slug discipline.

### Blueprint (story shape)

The adopted structural framework. One active blueprint per story; adoption of
a new one supersedes.

- `blueprint.adopted` — `{blueprint_id, framework, target_chapter_count,
  genre, beats: [{beat_id, name, ideal_pct, tolerance_pct,
  expected_polarity}], obligatory_scenes: [str]}`. Beats are minted with the
  blueprint from a template (see Skills packs); `ideal_pct` × target count
  yields a target chapter window per beat.
- `blueprint.retargeted` — `{blueprint_id, target_chapter_count}` (book grew
  or shrank; windows recompute in projection).
- `beat.fulfilled` — `{beat_id, chapter_id, note}`. Emitted by the Plotter
  (it owns the blueprint) when it judges a drafted chapter carried the beat.
  Re-emission supersedes — the room may later re-judge which chapter truly
  carried the midpoint.

The default template is the normalized six-position convergent map (catalyst /
threshold / midpoint / low point / final turn / climax); richer templates
(Save the Cat's 15, seven-point, Story Circle, Kishōtenketsu) ship as
reference data in the skills packs and can be adopted instead. Kishōtenketsu
matters structurally: templates must not *require* conflict/antagonist fields.

### Chapter briefs (rolling outline)

The plan for a near-future chapter — the Plotter's main output and the
Author's main input. Rolling-wave: briefs exist only 1–3 chapters ahead and
are revised freely until fulfilled.

- `chapter_brief.drafted` — `{brief_id, target_ordinal, pov_character_id?,
  goal, threads_to_touch: [thread_id], beats_to_hit: [beat_id],
  promises_to_progress: [promise_id], value_shift: {value, from, to},
  planned_outcome: yes|yes_but|no_and|no, synopsis}`.
- `chapter_brief.superseded` — `{brief_id, superseded_by_brief_id}`.
- `chapter_brief.fulfilled` — `{brief_id, chapter_id}` — emitted at commit
  when the Author cites the brief it drafted against. Fulfilled and
  superseded are terminal.

The `planned_outcome` enum encodes the try-fail escalation discipline: the
body of a book should run on `yes_but`/`no_and`; a clean `yes` planned before
the climax window is itself a Brain flag.

### Thread resolution planning + promise ledger

Threads already carry the promise→progress→payoff lifecycle
(`planted/touched/paid_off`). Two additions:

- `thread.resolution_planned` — `{thread_id, target_chapter_window: [lo, hi],
  planned_payoff_note}`. Re-emission supersedes (re-planning pace is normal;
  the event history *is* the record of schedule slips). This is the direct
  answer to "plan the pace of thread resolution."
- `secret.reveal_planned` — `{secret_id, target_chapter_window}` — same
  shape for the who-knows-what machinery.

The **promise ledger** generalizes setup/payoff below thread scale — planted
objects, foreshadowing images, running motifs (Chekhov's gun):

- `promise.made` — `{promise_id, kind: foreshadow|plant|red_herring,
  setup_chapter_id, description, target_chapter_window?, thread_id?}`.
- `promise.progressed` — `{promise_id, chapter_id, note}`.
- `promise.paid` — `{promise_id, chapter_id, note}` (terminal).
- `promise.released` — `{promise_id, reason}` (terminal; the sanctioned exit
  for red herrings and deliberate abandonment).

Distinction, in the ubiquitous language: a *thread* is a plotline with
ongoing life; a *promise* is a discrete planted expectation with a discrete
payoff. Promises may link to a thread; neither subsumes the other.

### Character arcs

Weiland's internal architecture, declared and trackable:

- `arc.declared` — `{arc_id, character_id, arc_type:
  positive|flat|disillusionment|fall|corruption, ghost, lie, truth, want,
  need}`. One active arc per character; re-declaration supersedes.
- `arc.pivot_planned` — `{arc_id, beat_id, description}` — pins internal
  pivots to structural beats (midpoint truth-glimpse, 75% low, climax
  decision).
- `arc.advanced` — `{arc_id, chapter_id, note}` — evidence of movement,
  declared by CharacterKeeper or mined.
- `arc.resolved` — `{arc_id, chapter_id, outcome: truth_embraced|
  lie_embraced|truth_tragic|world_changed}` (terminal). The Brain checks
  outcome against `arc_type` (a fall arc resolving `truth_embraced` is a
  contradiction alarm, not an error — the story may have earned it, but the
  Director should see it).

Relationship arcs are deferred; the `value_shift` field on briefs plus a
future `RelationshipArc` aggregate would follow the same pattern.

## Write path

New intent schemas in `agents/schemas.py` mirroring the existing pattern:
`BriefIntent`, `ResolutionPlanIntent`, `PromiseIntent`, `ArcIntent`,
`BeatIntent` — each an action enum + cited ids + payload fields, validated at
commit time against active-id sets exactly like `ThreadIntent` today
(unknown/terminal ids dropped with a warning). Blueprint adoption is a
Director-initiated or Plotter-proposed act and always routes through the
proposal queue regardless of autonomy level (it re-frames the whole book).

`AutonomyPolicy` gains a **plan** category covering blueprint/brief/
resolution/promise/arc events, dialable independently of canon prose events:
planning is cheap to approve and cheap to revise, so its default sits at
`full_auto` even when prose is gated. (Open question #3.)

## Agent changes

**New agent: the Plotter** (writers' room role: showrunner/outliner). A new
`BaseAgent` subclass with the standard `poll()`/`work()`/`commit()` shape:

- *poll:* chapter map, blueprint + beat status, active briefs, thread/promise/
  arc state, Brain notes (below), Director signals, Muse inspiration.
- *work:* one `create_deep_agent` call with `response_format=PlotterOutput`
  (briefs + intents), pull tools enabled (canon FS, outline FS, search,
  workspace), skills: outlining, promise-payoff, pacing.
- *commit:* intents through the committer; brief drafting/supersession.

Rationale for a new agent over extending the StructureAnalyst: single
responsibility — the analyst *observes* (scores what is), the Plotter *plans*
(decides what should be). Keeping them separate keeps the plan-vs-actual
comparison honest; an agent that both scores and plans will grade its own
homework.

**Author:** poll gains a "next brief" block (the brief for the chapter it is
about to draft, verbatim — this is push, it is the assignment); prompt
instructs it to honor or knowingly deviate and remark on deviation. Commit
emits `chapter_brief.fulfilled` alongside `chapter.created`.

**CharacterKeeper:** gains `ArcIntent` (declare/advance/resolve) — it already
owns character interiority.

**ContinuityChecker:** mining extended to spot unpaid promises referenced in
prose (same shape as secret-leak mining).

No other agent changes. The Muse's inspiration hands become natural raw
material for `promise.made`.

## Story Brain — plan-vs-actual faculties

New pure functions in `brain/`, same contract as existing faculties (ReadStore
data in, findings out, never persisted), each surfaced as a `context.py`
prompt note and an alarm-strip entry:

| Faculty | Check | Alarm example |
|---|---|---|
| `ledger.py` | non-red-herring promises unpaid past their window; payoffs with no prior setup (generalizes secret-leak logic) | "promise 'the sealed letter' (ch 3) unpaid; window closed ch 18" |
| `resolution_pacing.py` | threads past planned resolution window; resolution congestion (too many windows in the same span) and droughts (long spans with none) | "3 threads all planned to resolve ch 19–21" |
| `beat_drift.py` | fulfilled beats outside tolerance; next expected beat overdue given chapter count | "midpoint not yet fulfilled at 58% of target length" |
| `arc_alignment.py` | arc pivots not co-located with their beats; arcs with no `advanced` in N chapters; resolution outcome vs arc type | "Kessa's arc: no movement in 6 chapters" |
| `tension_target.py` | interpolate a target tension curve from beat positions/polarities; compare to StructureAnalyst actuals (extends sag/spike with *directed* deviation) | "tension max currently ch 11 (44%); blueprint expects climax ~ch 23" |

Existing `staleness.py` stays (undirected "gone dark" heuristic) and gains a
sharper sibling in `resolution_pacing.py` (directed, plan-aware).

## deepagents surface

### Backend composition

The runner builders (pull-tools §4 wiring, extended) compose:

```python
backend = CompositeBackend(
    default=CanonBackend(read_store),          # existing read-only canon tree
    routes={
        "/outline/":   OutlineBackend(read_store),   # read-only blueprint views
        "/workspace/": StateBackend(),               # writable, run-scoped scratch
    },
)
```

**`OutlineBackend`** (new module `canon_fs/outline.py`, same thin-router +
pure-renderer split as `CanonBackend`):

```
/outline/blueprint.md        # framework, genre, obligatory scenes, target length
/outline/beats.md            # table: beat | ideal% | window | status | fulfilled-by
/outline/briefs/NNN-slug.md  # active briefs; frontmatter carries brief_id
/outline/threads-plan.md     # per-thread planned windows vs current state
/outline/ledger.md           # promise ledger: open | paid | released, windows
/outline/arcs/slug.md        # arc sheet: type, lie/truth/want/need, pivots, progress
```

Writes refuse with the established message ("declare intents"). Every file's
frontmatter carries exact record ids, feeding the cite-ids-exactly discipline.

**`/workspace/`** is the deepagents "think in files" space: `StateBackend`,
scoped to the run's thread, never canon, never projected. Agents draft
outlines, compare alternatives, and keep working notes there; `write_todos`
stays enabled for the Plotter and Author. Tool telemetry (pull-tools §6)
makes workspace activity visible in the Engine Room. A durable
`StoreBackend`-backed `/notes/` (cross-run agent memory) is deliberately
deferred — it's a memory feature, not an authoring feature.

`search_canon` gains kinds `brief`, `promise`, `arc` via the existing
incremental indexer pattern.

### Skills packs (craft knowledge, pulled on demand)

Ship Claude-style skills as package data (`novelizer/skills_packs/`), passed
via `create_deep_agent(skills=[...])` per agent. Progressive disclosure means
near-zero context cost until a skill activates.

| Pack | Body teaches | `references/` |
|---|---|---|
| `outlining` | adopting a framework, rolling-wave briefs, beat targeting | beat tables for each framework (names, ideal %, tolerances), obligatory scenes per genre |
| `promise-payoff` | promise/progress/payoff, Chekhov's gun, red herrings, reveal sequencing | ledger checklists |
| `character-arcs` | ghost/lie/want/need, the five arc types, pivot-to-beat mapping | arc-type invariant table |
| `scene-sequel` | goal-conflict-disaster / reaction-dilemma-decision, MRUs, chapter hooks | outcome taxonomy (yes-but/no-and) |
| `pacing` | tension curves, breathing room, POV cadence, escalation | curve exemplars |

Assignment: Plotter gets outlining + promise-payoff + pacing; Author gets
scene-sequel + pacing; CharacterKeeper gets character-arcs; chat personas get
all five (the Director consults them about craft). Beat templates live here
as data files, so adopting a framework = the Plotter reading a reference file
and emitting `blueprint.adopted` — craft knowledge and canon stay separate.

Skills are per-install in v1; per-story override directories are future work.

## TUI — Mission Control

Following the pure-model + thin-widget pattern and TESTING-TUI harness:

- **Brain tab `5` — Outline board.** The Plottr-style grid: rows = threads
  (+ a beats header row), columns = chapters (drafted + planned briefs).
  Cell glyphs: `●` planted, `·` touched, `◆` paid off, `░` planned window,
  `!` past window. Beat markers sit in the header at their target columns;
  unfulfilled beats past position render in alarm color. Brief columns to the
  right of the last drafted chapter render dimmed (the future).
- **Brain tab `6` — Arcs.** One lane per declared arc: type, lie→truth line,
  pivot markers pinned to beat columns, last-advanced chapter, resolution
  status. Misaligned/stagnant arcs surface in the alarm strip.
- **Shape tab (existing) —** overlay the interpolated target tension curve on
  the actual sparkline; deviation shading where they diverge.
- **Threads tab (existing) —** gains a ledger section (open promises with
  windows) and planned-resolution badges on the thread board.
- Approval screen and alarm strip work unchanged; plan events flow through
  the same proposal queue when gated.

New widget models: `outline_board_model.py`, `arcs_tab_model.py`, extensions
to `shape_tab`/`threads_tab` models — all pure render functions over new
`ReadStore` queries.

## From worldbuilding to a novel — the pipeline

The lifecycle the blueprint enables, all in the existing steering model:

1. **Seed** — Director seeds premise/genre (existing signal + setup wizard).
2. **Worldbuild** — WorldArchitect/CharacterKeeper populate canon (existing).
3. **Frame** — the Plotter reads the world, the seed, and Muse inspiration;
   proposes `blueprint.adopted` (framework, genre, target length) — always a
   proposal, Director approves; declares founding threads, promises, and arcs
   from the world material via intents.
4. **Rolling outline** — the Plotter keeps 1–3 chapter briefs ahead of the
   Author, planning thread touches, beat targets, and resolution windows;
   revises briefs as actuals drift.
5. **Draft** — the Author writes against the current brief (honoring or
   knowingly deviating), pulls canon/outline/skills as needed.
6. **Observe** — miners and the StructureAnalyst record what actually
   happened (existing).
7. **Compare** — Brain faculties diff plan vs. actual; alarms and prompt
   notes flow to the Plotter (re-plan), the Author (course-correct), and the
   Director (steer or adjudicate).
8. **Converge** — as beats fulfill and windows close, the Plotter steers
   remaining threads toward the climax window; `blueprint.retargeted` if the
   book is running long or short.

Steps 4–7 loop; the blueprint is a living aggregate, not a waterfall artifact.

## Error handling

- Intent citations of unknown/terminal blueprint ids: dropped with a warning
  at commit (existing pattern).
- Briefs targeting an already-drafted ordinal: rejected at commit with a
  warning (plan the future, not the past).
- Overlapping beat fulfillment claims: last event wins in projection; the
  Brain flags multi-claimed chapters.
- Workspace writes are unvalidated by design; canon/outline writes refuse
  with the intent-direction message (existing).
- A story with no blueprint runs exactly as today: every new faculty and tab
  degrades to "no plan adopted" (empty state, no alarms). Bottom-up remains a
  fully supported mode.

## Testing

Standing rule: **all test runs in a worktree, never the main checkout.**

- Property tests (Hypothesis): ledger invariants (setup precedes payoff;
  terminal states absorb; released red herrings never alarm), beat-window
  arithmetic under retargeting, projection replay/rebuild equivalence for all
  new tables, outline-board model rendering (grid dimensions, glyph
  placement) over generated canon histories.
- Unit tests: renderers (pure), each Brain faculty over seeded ReadStores,
  intent commit-time validation including citation-drop warnings.
- Fake-runner harness: Plotter and Author brief-consumption loops, including
  deviation remarks and `chapter_brief.fulfilled` emission.
- TUI: widget-model tests per TESTING-TUI.md; pytest wedge for tab wiring.

## Milestones

- **M6a — Ledger & resolution pacing** (smallest slice, immediate value, no
  new agent): promise events + `thread.resolution_planned` +
  `secret.reveal_planned`, projections, `ledger.py` +
  `resolution_pacing.py`, Threads-tab extensions. The Author gains
  `PromiseIntent` (it plants and pays off in prose); the ContinuityChecker
  gains both intents via mining; resolution windows come from Director
  signals until the Plotter exists in M6b.
- **M6b — Blueprint & Plotter**: blueprint/beat/brief aggregates, the Plotter
  agent, Author brief consumption, `beat_drift.py` + `tension_target.py`,
  Outline board tab, OutlineBackend. Requires pull-tools §4 wiring first.
- **M6c — Arcs**: arc aggregate, CharacterKeeper ArcIntent, `arc_alignment.py`,
  Arcs tab.
- **M6d — Skills & workspace**: skills packs, `/workspace/` mount, per-agent
  skills assignment, chat-persona craft access.

Each milestone follows spec → plan → red/green TDD → review, per the standing
gates.

## Open questions for the Director

1. **Granularity.** Chapter-grained briefs for now (recommended), or
   introduce a scene aggregate immediately? Scene-level is where value-shift
   checking gets sharp, but it roughly doubles the schema and touches the
   Author's output contract.
2. **Plotter as a new agent** (recommended: observers shouldn't grade their
   own plans) vs. extending the StructureAnalyst?
3. **Autonomy default for the plan category**: `full_auto` planning with only
   `blueprint.adopted` forced through proposals (recommended), or gate all
   plan events initially?
4. **First-ship beat template**: the normalized six-position map as default
   with richer templates as skills-pack data (recommended), or Save the Cat's
   15 beats as the default?
5. **Brief authorship in chat**: should the Director be able to draft/edit
   briefs directly from a TUI screen (a "writers' meeting" flow), or steer
   only via signals and proposals in v1 (recommended)?
