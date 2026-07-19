# Authoring Skills — Research Notes

Companion to `2026-07-19-authoring-skills-blueprint-design.md`. Three research
streams: narrative craft (primary), deepagents interfaces (verified against
installed 0.6.12), and the codebase survey that framed the design.

---

## 1. Narrative craft — computable structures

### The convergent beat map

The major frameworks are the same skeleton at different resolutions.
Normalized to manuscript-%:

| Position | Save the Cat | 7-Point (Wells) | Three-Act | Story Circle | Function |
|---|---|---|---|---|---|
| 0% | Opening Image | Hook | — | You (comfort) | Baseline value / status quo |
| ~10% | Catalyst | Plot Turn 1 | Inciting Incident | Need | Outside disturbance |
| ~20–25% | Break into Two | (Plot Turn 1) | End Act 1 | Go | Commitment; new world |
| ~25–37% | Pinch 1 | Pinch 1 | — | Search | Antagonist pressure |
| **50%** | **Midpoint** | **Midpoint** | Midpoint | Find | **Reactive→proactive flip; false victory/defeat** |
| ~62–75% | Pinch 2 | Pinch 2 | — | Take | Crushing pressure; loss |
| ~75% | All Is Lost | — | Crisis | — | Lowest point |
| ~80% | Break into Three | Plot Turn 2 | End Act 2 | Return | Revelation → fightback |
| ~88–99% | Finale | Resolution | Climax | Change | Value settled |
| 100% | Final Image | — | Denouement | (changed) | Mirror of opening |

Save the Cat's 15 beats with canonical %: Opening Image 0–1, Theme Stated ~5,
Set-Up 1–10, Catalyst ~10, Debate 10–20, Break into Two ~20, B Story ~22, Fun
and Games 20–50, Midpoint ~50, Bad Guys Close In 50–75, All Is Lost ~75, Dark
Night of the Soul 75–80, Break into Three ~80, Finale 80–99, Final Image 100.
Cross-beat invariants: Theme Stated must be answered by the Finale; the B
Story supplies the lesson that resolves the A story; Midpoint and All Is Lost
carry opposite polarity.

Kishōtenketsu (ki → shō → ten ~75% → ketsu) is the conflict-optional
counterexample: tension via recontextualization, no antagonist required —
templates must not require conflict fields to be non-null.

Story Grid adds: the five commandments recursive at every level (inciting
incident → progressive complications → crisis → climax → resolution), crisis
typed as best-bad-choice vs irreconcilable-goods, turning points typed
action vs revelation, and per-genre **obligatory scenes** (checkable: Love
requires lovers-meet/break-up/proof-of-love; Thriller requires
hero-at-mercy-of-villain; Crime requires discovery/exposure).

### Scene-level craft

- Scene/Sequel (Swain): proactive Scene = goal → conflict → disaster;
  reactive Sequel = reaction → dilemma → decision. Chain check: each
  decision seeds the next scene's goal. Cadence: runs of all-Scenes =
  breathless, all-Sequels = saggy.
- Value shift (Story Grid): every scene tracks one value spectrum and must
  flip polarity start→end ("every scene turns" — the single most automatable
  check; non-turning scene = exposition flag).
- Outcome taxonomy: yes | yes_but | no_and | no. Body of the book should run
  on yes_but/no_and; a clean `yes` before ~90% is premature tension release.

### Threads, promises, pacing

- Sanderson: promise → progress → payoff per thread. Checks: every promise
  paid; payoff after promise; heavily-promised threads show regular progress.
- Setup–payoff ledger (Chekhov's gun generalized): kind ∈ foreshadow | plant
  | red_herring; every non-red-herring setup paid; every payoff pre-seeded
  (no deus ex machina); red herring = sanctioned subversion.
- Braiding: no plotline dark beyond a max chapter gap; POV cadence balanced
  or intentionally weighted; cliffhanger POVs returned to within a window.
- Tension: rising sawtooth; global max near climax (~90%), local peak at
  midpoint, deliberate troughs after peaks; escalating try-fail stakes.
- POV thread ≠ plotline (separate axes); story order ≠ chronological order
  (Aeon Timeline's lesson).

### Character arcs (Weiland)

Internal architecture: ghost/wound → lie believed → want (external, expresses
the lie) vs need (accept the truth = ¬lie). Five arc types with fixed
start/end beliefs:

| Arc | Start | End | Rule |
|---|---|---|---|
| Positive change | Lie | Truth | Want sacrificed for Need |
| Flat | Truth | Truth | Changes the world, not self |
| Disillusionment | Lie | bleak Truth | Truth won, tragically |
| Fall | Lie | worse Lie | Rejects available Truth |
| Corruption | Truth | Lie | Abandons held Truth |

Arc pivots co-locate with structural beats: midpoint = first truth-glimpse,
~75% = the lie's maximal cost, climax = the final lie/truth decision.

### Software precedents

- **Plottr**: timeline grid, plotlines (rows) × chapters (columns), scene
  cards with custom attributes, 30+ overlayable beat templates — the
  reference visualization for the Outline board.
- **Scrivener**: binder + corkboard + per-doc labels/keywords (freeform).
- **Aeon Timeline**: events × entities, story-order vs chronological-order.
- **Dramatica**: four throughlines (Objective/Main/Influence/Relationship),
  storyform as a constraint system — precedent for Brain-as-linter over
  declared story variables.
- Computational-narrative residue worth keeping: controlled vocabularies of
  scene functions/roles (Propp), and plot-as-typed-graph (nodes =
  scenes/beats, edges = causes/sets-up/pays-off/advances).

### Highest-value mechanical checks (MVP set)

1. Every non-red-herring setup/promise has a later payoff; every payoff has a
   prior setup.
2. Required beats fulfilled within ± tolerance of ideal %.
3. Declared genre's obligatory scenes present.
4. No thread dark beyond a window; planned resolution windows honored;
   resolution congestion/drought flagged.
5. Arc pivots align to beats; arc outcome matches arc type; arc stagnation.
6. Midpoint flips reactive→proactive; global tension max near climax.
7. Every scene/chapter turns a value (polarity shift).

Sources: storygrid.com (five commandments, value shift, spreadsheet),
kindlepreneur.com & reedsy.com (Save the Cat), studiobinder.com (story
circle), writingexcuses.com 7.41 (seven-point), brandonsanderson.com 2025
lecture 2 (promise/progress/payoff), helpingwritersbecomeauthors.com (arcs),
docs.plottr.com (timeline/plotlines), dramatica.com (throughlines),
advancedfictionwriting.com (scene/sequel), mythicscribes.com (kishōtenketsu).

---

## 2. deepagents 0.6.12 — verified interface facts

Verified against the installed package (not just docs):

- `BackendProtocol` (`deepagents.backends.protocol`): sync + async pairs —
  `ls/als`, `read/aread(file_path, offset=0, limit=2000)`,
  `write/awrite(file_path, content)`,
  `edit/aedit(file_path, old_string, new_string, replace_all=False)`,
  `glob/aglob`, `grep/agrep`, plus `ls_info/glob_info/grep_raw` variants and
  `upload_files/download_files`. Async variants exist in 0.6.12 —
  `CanonBackend`'s async-first approach is correct.
- Result types are TypedDict-style with error channels:
  `LsResult{error, entries: [FileInfo]}`, `ReadResult{error, file_data}`,
  `WriteResult{error, path, files_update}`, `EditResult{error, path,
  files_update, occurrences}`, `GlobResult{error, matches}`,
  `GrepResult{error, matches: [GrepMatch{path, line, text}]}`,
  `FileInfo{path, is_dir?, size?, modified_at?}`.
- `CompositeBackend(default=..., routes={prefix: backend})` — longest prefix
  wins; mixes e.g. read-only canon + writable state paths.
- `StateBackend` = thread-scoped ephemeral files (LangGraph state);
  `StoreBackend(namespace=...)` = durable cross-thread files over a LangGraph
  `BaseStore`.
- `create_deep_agent(..., backend=, tools=, subagents=, skills=, memory=,
  interrupt_on=, response_format=, store=, checkpointer=)`; returns a
  compiled LangGraph graph (`ainvoke` for async).
- Skills: directories of `SKILL.md` (YAML frontmatter `name` + `description`,
  body < ~5k tokens, optional `references/`, `scripts/`, `assets/`).
  Three-layer progressive disclosure: name+description at startup → body on
  activation → references pulled via `read_file` on demand.
- `memory=` = AGENTS.md-style always-loaded standing context (push);
  distinct from Store-backed files (pull).
- Context-engineering guidance (LangChain "Doubling down on Deep Agents"):
  offload context to files; prefer pull over push; oversized tool results
  auto-archived to files; subclass backends to add guardrails/validation.

---

## 3. Codebase survey — facts the design leans on

- ~42 event types in `canon/events.py` with locked-decision docstrings;
  minted-once ids; terminal/absorbing states. Recipe for new types: payload →
  read model/table → `Projector._project()` branch → `ReadStore` query →
  policy classification → intent schema + commit wrapper → optional canon_fs
  renderer, brain faculty, TUI tab.
- Writes flow through `Committer`/`GatingCommitter` + `AutonomyPolicy`
  (full_auto | gated_retcons | gated_canon | gated_all); agents emit typed
  structured output + intents, validated against active-id sets at commit.
- `canon_fs/` (`CanonBackend`, renderers, `search_canon`) is built and
  tested; runner wiring (§4 of the pull-tools spec) is not yet implemented —
  it is the prerequisite for all agent-facing tooling in this design.
- Story Brain = pure functions over `ReadStore` (staleness, sag/spike,
  leaks, paradoxes, theme similarity) → `context.py` prompt notes + TUI
  alarm strip. Never persisted; recomputed fresh.
- TUI pattern: pure `*_model.py` render functions + thin Textual widgets;
  Brain tabs keyed 1–4; new views = new model + ReadStore query + tab.
- Domain today is chapter-grained (no scene aggregate); story shape is
  emergent (agent-declared or mined), never authored — the gap this design
  fills.
