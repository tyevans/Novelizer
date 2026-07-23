# Documentation Backlog

The Diataxis documentation backlog for this repo, built from a full per-package
survey on 2026-07-22. Ground truth is the code; each item below was proposed by
a research pass that read the actual source, not the docs.

**How to use this file:** pick items top-down within a package (they're ranked),
write the doc in the Diataxis voice for its category, then check it off. When a
doc lands, move its line to the "Done" section at the bottom with the commit.
The `/syncing-diataxis-docs` skill keeps landed docs current as code changes;
this backlog tracks docs that don't exist yet.

Categories: **[T]** tutorial · **[H]** how-to · **[R]** reference · **[E]** explanation

Current Diataxis coverage (docs/, before this backlog):

| Package | Tutorial | How-to | Reference | Explanation |
|---|---|---|---|---|
| novelizer | QUICKSTART (mega-doc, not clean tutorial) | — | — | architecture-boundaries (shared) |
| substrate | — | — | — | architecture-boundaries (shared), README |
| tui_kit | — | wire-a-new-domain-onto-tui_kit | — | extraction spec (historical) |
| research_domain | — | — | research-domain-cli | architecture-boundaries (shared) |

## novelizer

The flagship BC and the least documented relative to its surface. The first five
proposed docs landed 2026-07-23 (see Done); remaining items below.

- [ ] **P2 [H]** `docs/how-to/cast-the-room-voices.md` — scaffold + activate prose profiles, agent personalities, character voices; user vs shipped packs. CLI-scaffold + settings-activate flow is non-obvious.
- [ ] **P2 [H]** `docs/how-to/steer-a-story.md` — blueprint framing, `retarget`, `plan-resolution`/`plan-reveal`, autonomy dial, approvals/escalations, completion. The "director verbs" bundle.
- [ ] **P2 [E]** `docs/explanation/event-sourcing-and-canon.md` — why event-sourced; EventStore/Projector/ReadStore split; canon read-only to agents; the "only way to change canon is a declared intent" invariant.
- [ ] **P3 [E]** `docs/explanation/story-brain.md` — what each analyzer measures (staleness, sag/spike, promise ledger, resolution pacing, arcs, leaks/paradoxes, completion) and how the six Brain tabs map to them.
- [ ] **P3 [R]** `docs/reference/events.md` — the ~55-type event catalog from `canon/events.py` (payloads, legacy aliases). Developer/extender audience.
- [ ] **P3 (cleanup)** Split `docs/QUICKSTART.md`: install/first-run head → the tutorial; feature-tour sections → the voices/steering how-tos and story-brain explanation. Do after the docs above exist so nothing is orphaned.

## substrate

`substrate/README.md` covers the event-registry/event-store/projection/runtime
core well — don't duplicate it. The gap: 6 of 12 `__all__` names
(`PostgresEmbeddingStore`, `PostgresDepsStore`, `AgentSpec`, `ToolGrant`,
`SubagentGrant`, `AgentContext`) appear in no user-facing doc.

- [ ] **P1 [R]** `docs/reference/substrate-api.md` — signature reference for all 12 public names, including the 6 undocumented ones.
- [ ] **P1 [H]** `docs/how-to/semantic-search-with-substrate.md` — `PostgresEmbeddingStore`: dimensions/model keying, `upsert`, `nearest` KNN, pgvector/HNSW requirements.
- [ ] **P1 [E]** `docs/explanation/projection-lifecycle.md` — dirty-set → `invalidate` → `catch_up` → `recompute_dirty`; why recompute-on-catch-up rather than incremental; the `_EventView`/lookup-dict-refresh pattern `ResearchRuntime` relies on. Most footgun-prone part of building a domain.
- [ ] **P2 [H]** `docs/how-to/track-derived-dependencies.md` — `PostgresDepsStore.declare_edge`/`blast_radius`, recursive-CTE transitive-descendant semantics.
- [ ] **P2 [E]** `docs/explanation/autonomy-gating.md` — how `is_gated` turns `GatingTier` + domain `tier_order` + `current_tier_index` into an autonomy dial; fiction vs research tier orders contrasted.
- [ ] **P3 [E]** `docs/explanation/agent-registry.md` — `AgentSpec.construct(ctx)`, `AgentContext` fields, `ToolGrant`/`SubagentGrant` settings-gating.
- [ ] **P3 [T]** `docs/tutorial/first-substrate-domain.md` — build-along: one event type, one projection, one runtime. README's walkthrough is a reading exercise keyed to finished code, not a build-along.

## tui_kit

`docs/how-to/wire-a-new-domain-onto-tui_kit.md` is comprehensive for the
port-a-domain task — don't duplicate it.

- [ ] **P1 [H]** `docs/how-to/style-tui_kit-widgets.md` — the undocumented CSS contract: tui_kit ships zero CSS; consumers must define `er-vitals`, `er-stream`, `er-stream-scroll`, `lsp-vitals`, `lsp-stream`, `lsp-stream-scroll` (novelizer's are in `novelizer/tui/app.tcss`). A consumer today gets broken layout with no doc saying why. Biggest undocumented contract in the package.
- [ ] **P1 [R]** `docs/reference/tui_kit-api.md` — every `contracts` dataclass with exact fields, `LiveRunState`/`Block` fields, widget/function signatures. The how-to flags kwargs mismatch at construction as a silent-failure trap; there's no lookup surface.
- [ ] **P2 [R]** `docs/reference/tui_kit-event-model.md` — event-folding semantics: block-merge rules, `repeat_count` collapsing, delegate subagent indent, `ToolSummaryReady` back-matching on `normalize_input_summary`, `stream_attached=False` on mid-run restart. Currently discoverable only by reading `run_model.py`. (May merge into the API reference if it stays small.)
- [ ] **P2 [T]** `docs/tutorial/build-a-minimal-agent-console.md` — from-scratch "watch 2 fake agents run" mini-app: tui_kit + a hand-fed event list, no novelizer, no telemetry bus.
- [ ] **P3 [E]** `docs/explanation/why-tui_kit-is-pure.md` — the design principle (pure reducer core + thin widgets + domain adapter, import-linter enforced) as an evergreen doc; the extraction spec records it only as history.

## research_domain

`docs/reference/research-domain-cli.md` covers CLI syntax/behavior — don't
duplicate it. The confusing part for readers: `events.py`, `event_types.py`,
and `roles.py` are deliberately declarative and unwired (proofs of substrate
primitives, exercised only by tests), and no doc says so.

- [ ] **P1 [R]** `docs/reference/research-domain-events.md` — the declarative surface the CLI ref omits: four pydantic payload schemas, gating tiers + `RESEARCH_TIER_ORDER`, the six `AgentSpec` role stubs.
- [ ] **P1 [E]** `docs/explanation/research-domain-model.md` — claim vs source; corroboration vs refutation vs correction; what question each projection answers; why half the package is intentionally unwired.
- [ ] **P2 [T]** `docs/tutorial/first-research-stream.md` — append `claim.proposed` → `claim.refuted` → `claim.corrected`, `show` all three projections after each, watch them update.
- [ ] **P3 [H]** `docs/how-to/inspect-research-projections.md` — task recipes mapping real questions ("which claims contradict X?") to the right projection.
- [ ] **P3 (cleanup) [H]** `docs/how-to/build-a-domain-on-substrate.md` — promote the substrate README's "Building a new domain" walkthrough into docs/ for discoverability. Optional; skip if the README stays the canonical home.

## Cross-cutting notes

- Repo convention is singular Diataxis dirs: `docs/how-to/`, `docs/reference/`,
  `docs/explanation/` (and `docs/tutorial/` when created).
- Historical material (`docs/superpowers/`, `docs/submilestones/`,
  `docs/agent-prompting/`) stays as-is; it is design record, not user docs.
  Where a spec contains evergreen content (e.g. runtime-design appendix tables,
  tui_kit extraction rationale), the play is to mirror it into a proper
  Diataxis doc, not to link users at specs.

## Done

- [x] **P1 [R]** `docs/reference/novelizer-cli.md` — every `novelizer` subcommand, args, options, autonomy levels. The primary user CLI has no reference; only the synthetic `research-domain` one exists.
- [x] **P1 [R]** `docs/reference/configuration.md` — all ~50 `EffectiveSettings` fields, defaults, 4-layer precedence (defaults ← global ← story.toml ← `NOVELIZER_*` env), story-overridable vs global-only vs forbidden keys, restart-required set.
- [x] **P1 [T]** `docs/tutorial/first-story.md` — linear install → configure endpoint → seed → watch first chapter → read it. One happy path, no options.
- [x] **P1 [H]** `docs/how-to/connect-a-local-llm.md` — llama.cpp / Ollama / vLLM via OpenAI-compatible endpoints, model pick in wizard, `llm_max_tokens`/timeout gotchas, per-agent tool-disable for weak tool-callers. Biggest first-run friction point.
- [x] **P1 [E]** `docs/explanation/how-the-room-works.md` — the ten-agent fleet, scheduler cadence/concurrency, gated commit path (intent → committer → autonomy policy → proposal or append), coordination only through the event log.

  All five landed in the novelizer full Diataxis pass, 2026-07-23 (verified against source by a dedicated verification wave).
