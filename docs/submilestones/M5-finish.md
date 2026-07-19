# M5 · Finish — Sub-Milestone Breakdown

M5 is the last milestone: it closes the reliability gap M4 left open, gives the Story
Brain its final faculty (Theme), matures voice enforcement, and turns the project from
"runs in this checkout" into "a stranger installs it and it works." Like M3/M4, it's
decomposed into just-in-time sub-milestones, each independently shippable and testable,
planned one at a time and executed via subagent-driven development (spec-informed plan →
fresh-subagent-per-task → two-stage review → whole-branch review → merge).

Parent milestone in [`../MILESTONES.md`](../MILESTONES.md); end-state design (Story Brain
faculties, event domains, view priority, install/first-run promises) in
[`../superpowers/specs/2026-07-17-novelizer-vision-design.md`](../superpowers/specs/2026-07-17-novelizer-vision-design.md).

M4 closed with a known gap, recorded in its closeout note: 20+ live runs against the
usage-not-revelation leak fixture never produced a leak→catch pass, because the live
Author was *consistently semantically correct* — it never leaked, because
`known_secrets_note()` worked. Self-declared intents alone cannot exercise the Continuity
Checker's catch path when the agents behave well; the checker needs a second, independent
way of noticing things the agents never declared. M5.1 builds that: LLM-driven prose
mining that backfills canon events for facts the room's own agents failed to emit. It is
the reliability centerpiece of this milestone, not a nice-to-have, and it retroactively
finishes the "undeclared leak/plant" non-goal both M3 and M4 deferred to "the same future
bucket."

## Sub-milestones

| # | Name | Delivers | Done when | Status |
|---|------|----------|-----------|--------|
| M5.1 | **Prose mining in the Continuity Checker** | The Continuity Checker's existing free-text LLM pass is extended with a second, structured **mining pass**: given recent chapter prose plus the current knowledge matrix, active-thread list, and causal-edge list (all already available in `poll()`'s `ctx`), a new `MinedFactsOutput` structured-response schema asks the LLM to report facts the prose *shows* that the log does *not yet have a covering event for* — undeclared secret uses/learns/reveals, undeclared thread touches/plants/payoffs, and undeclared causal links. Mined facts auto-commit through the *exact same* `Committer` calls the self-declared path already uses, but **only for the `_NEVER_GATED` event types** (`secret.referenced`/`secret.learned`, `thread.touched`/`thread.planted`/`thread.paid_off`, `causal_edge.declared`) — no new fact-bearing event types, no bypass of `AutonomyPolicy`. A mined **reveal** is the exception: `secret.revealed` is a `_CANON_EVENTS` gated type, and a prose-miner *inferring* a reveal is strictly less trustworthy than an agent declaring one — so mined reveals are **never auto-committed at any autonomy level**; they always escalate to a `retcon_request.created` tagged `MINED_SOURCE_TAG` (see Locked decision 3), leaving the reveal decision to a human/Retconner. Every mined event's payload gets a `source` field (mirroring M4.2's `LEAK_SOURCE_TAG` precedent) fixed to `"mined"` so mined facts are always distinguishable from agent-declared ones in the log and in any read-model built from it; deterministic analyzers (`find_leaks`/`find_paradoxes`, `StalenessAnalyzer`) are unmodified — they read committed events regardless of source, so a mined `secret.referenced` is exactly as leak-checkable as a declared one. **Dedup against declared intents**: dedup is **log-only** — the declaring agents (Author/Editor) run in *their own* poll cycles, asynchronously relative to the Checker, so there is no in-process "declared path first" ordering to lean on inside the Checker. Before committing a mined fact, the mining pass checks the already-committed state fetched in the *same poll snapshot*: secrets via `ReadStore.list_secret_references` rows (which carry `chapter_id`) plus the knowledge matrix for learns; causal edges via exact `(cause, effect)` triple match against `list_causal_edges` rows; threads via a raw event-log scan for `thread.*` events citing the `(thread_id, chapter_id)` pair — named honestly as a mining-only log read, because `ThreadsProjection` keeps aggregate state, not per-chapter touch history, and M5.1 does not add a projection for it. A mined fact matching any covering committed event is skipped. Residual race window (an agent commits a declared intent between the Checker's snapshot and its commit) can produce a duplicate event — this is benign by construction: `secret.learned` is INSERT-OR-IGNORE, a duplicate `secret.referenced` doesn't change any `find_leaks` verdict, and a duplicate `thread.touched` only re-stamps `last_chapter_id`, so no analyzer output is corrupted. Mining runs once per chapter, not once per checker cycle: a `mined_chapter_ids` field is not introduced as a new event type — instead the mining prompt is only run against chapters not yet present in a `chapter.mined` marker event (new, `_NEVER_GATED`, bookkeeping-only, payload is just the chapter id), giving idempotency without re-reading the whole log's prose every cycle. **Retcon filing for ambiguous mined facts**: when the LLM's mining pass reports a fact but is *not confident* it maps cleanly onto an existing secret/thread id (e.g. prose implies knowledge of something not in the active list), it doesn't invent an id — it files a `retcon_request.created` event tagged with a new `MINED_SOURCE_TAG = "[source: prose_miner]"` constant instead, routing the ambiguous case to a human/Retconner decision rather than silently minting bad canon, exactly mirroring how M4.1 drops unknown-id intents rather than guessing. | (a) CI-mechanical: seed a chapter's prose containing an undeclared secret use (character dialogue referencing a secret's content) with no `secret.referenced` event for it in the log, plus a `FakeRunner` preset to return a `MinedFactsOutput` declaring that use → run `ContinuityChecker.run_once()` → assert the resulting `secret.referenced` event exists, tagged `source="mined"` → assert `find_leaks` now flags it (mining feeds the existing deterministic detector, it doesn't bypass it) → assert a second `run_once()` against the same chapter does not re-commit the same mined fact (idempotency via `chapter.mined`) → assert an ambiguous-mining fixture (mined fact citing an unknown id) produces a `retcon_request.created` tagged `MINED_SOURCE_TAG` instead of a bad event → assert a mined-**reveal** fixture produces a `retcon_request.created` tagged `MINED_SOURCE_TAG` and **no** `secret.revealed` event (and no proposal), at every autonomy level — mined reveals never auto-commit. (b) live_llm smoke: the actual reliability claim — seed a chapter written by the *real* Author where a character's dialogue plausibly uses a secret's content but the Author's own `knowledge_intents` did *not* declare a `uses` intent for it (the scenario 20+ M4 runs failed to produce because the Author kept declaring correctly) — this is engineered directly rather than hoped for, by omitting `known_secrets_note()` from the Author's context for this one fixture chapter, giving the Author no guardrail and a real chance to leak in prose without declaring it — then run the real mining pass and confirm the leak is mined, committed, and reaches the open retcon queue with no manual intervention. This finally exercises the leak→catch path M4 could observe every piece of except the catch itself. | **complete — CI-proven AND live-observed (2026-07-18)**: `tests/agents/test_prose_mining_live_llm.py` PASSED against gemma-4-26b-qat in 74s — unguarded Author leaked in prose, mining pass extracted the undeclared `uses` fact, mined `secret.referenced` (source="mined") committed, next-cycle `find_leaks` filed the retcon. Three live fixes were needed and are on the branch: `max_tokens=4096` cap (calls previously free-ran 42k tokens), mining via `ProviderStrategy` json_schema (gemma emitted correct facts as fenced text the tool-calling strategy dropped), mining at temperature 0.2 (0.8 free-ran inside constrained JSON to the cap). Casing note: the live miner emitted `character_id="Kestrel"` for canonical id `kestrel` — M5.3's normalization item is confirmed necessary. |
| M5.2 | **Theme & motif tracking + voice enforcement maturity** | New `theme.*` event domain (`introduced`, `developed`) per the vision doc's event list, following the exact M3.1/M4.1 identity pattern: a theme id is minted at `introduced` time from a freeform title, slugged via a new `novelizer.canon.themes.slugify_theme_name` (third sibling of `slugify_thread_name`/`slugify_secret_name`); `developed` intents cite an id from the active-theme list, unknown ids dropped with a logged warning, no new lifecycle beyond `introduced`/`developed` (themes don't get "paid off" or "revealed" — the vision doc scopes theme tracking to the Story Browser, not a Brain view, so there is no staleness/leak-style analyzer to build, only a `ThemeProjection` and `ReadStore.list_themes()`/`get_theme()` feeding a new `themes` **section** in the Story Browser — the browser is a `Tree` widget driven by `browser_sections()` (`novelizer/tui/widgets/browser_model.py`), so themes land as a fourth-sibling section alongside chapters/characters/world/retcons, NOT as a tab; no tab architecture exists in this TUI and none is introduced). Author/Editor gain an optional `theme_intents` field, `_commit_theme_intents` added to `BaseAgent` as a third sibling of `_commit_thread_intents`/`_commit_knowledge_intents` (same collision/dedup rules, separate method, separate event types — same rationale M4.1 gave for not literally reusing thread's method). `theme.*` added to `AutonomyPolicy._NEVER_GATED` (bookkeeping, same class as `thread.*`). **Voice enforcement maturity**: building on M2.3's voice cards + Editor citation, the Editor's structured output gains a `voice_drift_flags` field (which character, which line, why it reads off-voice, citing the specific voice-card trait it contradicts) that commits into the existing `retcon_request.created` path tagged with a new `VOICE_SOURCE_TAG = "[source: voice_drift]"` constant — voice drift becomes a first-class, queryable retcon category rather than free-text buried in the Editor's general contradiction notes, using the same routing M4.2 established for leaks/paradoxes (reuse the seam, not a parallel notification path). No new autonomy policy needed (`retcon_request.created` is already ungated below `gated_all`, per M4 Locked decision #5). | (a) CI-mechanical: declaring a `theme_intents` entry commits a `theme.introduced`/`theme.developed` event and updates `list_themes()` after `catch_up()`; a property test asserts theme state is monotonic-appending (introduced → developed*, no terminal state to protect, simpler than the thread lattice); a fixture where the Editor's `FakeRunner` returns a `voice_drift_flags` entry produces a `retcon_request.created` tagged `VOICE_SOURCE_TAG` in the open queue. (b) live_llm smoke: seed a character with an established voice card and a chapter drafted with dialogue that violates a specific voice-card trait, run the real Editor, confirm a voice-drift retcon request citing that trait lands in the queue — proving citation-grounded enforcement, not generic "this feels off" prose. | complete (CI-proven; live smoke see closeout) |
| M5.3 | **UX polish, performance, deferred-backlog cleanup** | Sweeps the M4 deferred-items backlog that is mechanical rather than design-risky, so master carries no silent debt into the final acceptance pass: character-id casing normalization at the commit-helper boundary (fold to a canonical case once, at the seam, rather than trusting agent output); `_guarded_line(label, value)` DRY helper added to `BaseAgent` and adopted by every agent currently duplicating the "In character:"/"Write in this prose voice:" pattern; `prose[:200]` prior-chapter summary window in `author.py` becomes a named setting (`prior_chapter_summary_chars`, default 200, in the existing settings layer — global/story/env precedence unchanged); `ctx.get(...)` vs `ctx[...]` made consistent across agents (pick `ctx[...]` for required keys, `ctx.get(...)` only for genuinely optional ones, per a short written rule in this doc's Locked decisions); the four M1.3 CLI commands (`autonomy`, `proposals`, `approve`, `reject`) gain `CliRunner`-based tests plus the previously-missing Literal-rejection test and caplog assertions flagged in M4's backlog. Also lands the two M4 non-goal items explicitly promised a re-evaluation window: injecting the causal graph into the Author's prompt (now evaluated with real Causeway data from M4 in hand — see Locked decisions for the verdict) and configurable **staleness/sag-spike** thresholds via the settings layer (`find_leaks`/`find_paradoxes` are binary structural checks with no numeric parameter — there is nothing to configure there; the actual dials are `STALENESS_THRESHOLD_CHAPTERS` in `brain/staleness.py` and `SAG_SPIKE_DELTA` in `brain/sag_spike.py`, and those are what become settings, wired into the existing `settings_screen.py` from the parallel settings-config work — this corrects M4's non-goal wording, which named "leak/paradox thresholds" that structurally don't exist). Editor "revise" is changed to actually revise the flagged draft rather than prompting the Author to write a new chapter — this was flagged as a correctness bug in M4's backlog, but it is **not** cleanup-sized (no chapter-update event exists today; `events.py` has only `chapter.created`/`chapter.status_changed`, and the Editor's revise verdict just fires a `DirectorSignal` for a fresh chapter), so it is scoped explicitly by Locked decision 10 as M5.3's one new-event-domain item: a `chapter.revised` event, a ChaptersProjection fold that replaces the chapter's prose in the read model (the log keeps full history for free), and an Author revise branch that consumes a revise-target signal by rewriting the flagged chapter instead of minting a new id. If M5.3's plan review finds this crowding out the sweep, it gets promoted to its own branch rather than trimmed. `focus` remains context-only (see non-goals — not promoted to solo-override in M5) and the CLI-vs-TUI `ProposalService` duplication is resolved by having the CLI construct its `ProposalService` from the same `runtime.proposals` factory the TUI uses, removing the second construction path. **Concurrent agent execution (user-directed 2026-07-18)**: the Scheduler is currently strictly serial — `tick()` awaits one agent's full `run_once()` before anything else happens, so the inference endpoint sits idle between calls and the room can never saturate it. M5.3 adds a `max_concurrent_agents` setting (settings layer, env-overridable, default preserving current behavior only if 1 is chosen as default — see Locked decision 11 for the default, which is 2) and reworks `Scheduler.tick()` to dispatch up to K ready agents as concurrent asyncio tasks (top-K by readiness instead of top-1), with an agent never dispatched while its previous run is still in flight. `EmbeddingStore` (chromadb-backed, currently unwired) is wired for one real use: theme/motif similarity — this **depends on M5.2's theme events landing first**, and it is *extension*, not mere wiring: `embeddings.py` today builds only `world_entries`/`characters`/`chapters` collections, so a `themes` collection is new code, and the constructor gains an **injectable `embedding_function` parameter** (defaulting to the existing OpenAI-compatible one) so CI fixtures use a deterministic fake embedding function — the remote embed endpoint has been observed down and no CI test may depend on it. When a new `theme.introduced` event fires, the theme's title is embedded and compared against existing theme embeddings, and a near-duplicate match is surfaced as an Editor-facing suggestion ("this may be the same theme as X") rather than auto-merged, keeping identity minting a director/agent decision, not a similarity-threshold decision. | (a) CI-mechanical, sweep-style: one test per backlog item above confirming the specific behavior (casing normalized at commit boundary; `_guarded_line` produces byte-identical output to the strings it replaces; setting is read from story/env override; CLI commands covered by `CliRunner`; Editor "revise" produces a `chapter.revised` event referencing the original chapter id — not a new `chapter.created` — and the read model shows the revised prose under the same chapter id with the chapter count unchanged; embedding-similarity suggestion fires above a fixed default threshold in a seeded fixture with two near-duplicate theme titles, using an injected deterministic fake embedding function — no live embed endpoint in CI). No live_llm component — this sub-milestone is entirely mechanical/UX/perf cleanup, not new LLM-judgment surface, so it has no part (b). | **complete (2026-07-19)** — all sweep items landed (casing normalization live-motivated by M5.1's "Kestrel" observation; `_guarded_line` byte-identical adoption ×7; `prior_chapter_summary_chars`/`staleness_threshold_chapters`/`sag_spike_delta` settings; CLI tests + single ProposalService path; causal-graph-in-Author verdict: ADOPTED via `causal_flags_note` reuse; EmbeddingStore themes collection + injectable embedding_function + suggestion-not-merge; `chapter.revised` per Locked decision 10). Concurrency pool (Locked decision 11) shipped with all four CI proof obligations PLUS a live observation: pool=2 against qwen3.6-27b-mtp dispatched author/checker/world-architect/character-keeper with two agents genuinely in flight for ~90s of a 150s window. Characterization findings recorded during the sweep: projector approve/reject updates the SQL status column but not the proposal JSON blob (use `list_proposals(status=)`, not `get_proposal`, after status changes); `NOVELIZER_DB_PATH` is inert (no `EnvOverrides` field). |
| M5.4 | **Packaging, docs, and the stranger acceptance walkthrough** | `pyproject.toml` already declares `[tool.uv] package = true` and a `novelizer` console script — this sub-milestone verifies and hardens that path rather than building it from scratch: confirm `uv tool install .` (and, once published, `uv tool install novelizer`) produces a working `novelizer` binary on a clean environment with no repo checkout required beyond config; audit `dependencies` in `pyproject.toml` for anything dev-only that leaked into the runtime set. README is rewritten from its current M0-era framing ("Currently at milestone M0...") to describe the shipped M0–M5 product: what Novelizer is, the six-agent Room, the Story Brain views, install (`uv tool install`), first-run (setup wizard — already exists per `novelizer/tui/setup_wizard.py`, verify it still matches this doc's description), casting a room, seeding a world, and reading the feed. A new `docs/QUICKSTART.md` walks the exact stranger path end to end with copy-pasteable commands. This sub-milestone carries the **M5 (and whole-project) done-when** as an explicit, literal acceptance checklist (below) — it is the milestone that proves the vision doc's promise, not just M5's own deliverables. | The acceptance walkthrough below, run against a genuinely clean environment (fresh `$HOME`-equivalent config dir, no pre-existing `~/.config/novelizer`), is both the CI-mechanical check (steps 1–4, scriptable, no live model needed beyond a reachable OpenAI-compatible endpoint the environment already provides) and the live_llm smoke (steps 5–7, the actual "coherent novella" judgment call, which is inherently a human read, not an assertion — recorded as a documented manual run per M1–M4 precedent for claims no CI oracle can verify). | **complete (2026-07-19)** — steps 1–4 executed live in an isolated environment (see the M5.4 closeout note below for the full record, including the wizard's manual-equivalent config write, the story-resolution split-brain caught and corrected during step 4b, and the shared-checkout DB-lock incident and its test-isolation fix); steps 5–7 (day-long run + human coherence read) explicitly handed to the user per M4 precedent. |

### M5.4 acceptance walkthrough (this is the M5 / whole-project done-when)

1. On a machine with no prior Novelizer config, run `uv tool install novelizer` (or
   `uv tool install .` from a clean checkout) and confirm `novelizer` is on `PATH`.
2. Run `novelizer`. Confirm the first-run setup wizard opens (no crash on missing global
   config), point it at a reachable OpenAI-compatible endpoint, test the connection, pick
   models, save.
3. Cast the room without hand-editing any TOML file: recast at least one agent's
   personality/prose profile away from defaults via `novelizer voice-scaffold` (which
   writes the voice-pack TOML for you) plus the settings flow for picking the active
   pack/profile, then restart — there is deliberately no in-TUI casting editor (see
   non-goals). Seed a new world via the existing seed flow (CLI `seed` or the TUI
   command line).
4. Set autonomy above the most conservative level and let the room run unattended.
5. Confirm chapters accumulate in the feed, the Story Shape/Thread Board/Who-Knows-What/
   Causeway views populate, and at least one retcon request — from any source tag
   (LLM contradiction, leak, paradox, mined fact, or voice drift) — is auto-filed over the
   run, demonstrating the reliability fix from M5.1 is live, not just CI-proven.
6. Leave it running roughly a day (per the milestone's literal wording); come back and
   read the accumulated chapters as prose, not as a log dump.
7. Judge: is it a coherent novella — consistent character voices, no unresolved leaks
   presented as if they were fine, threads/themes that were planted getting touched or
   explicitly left open, not silently forgotten? Record the verdict and any friction
   points in this doc's closeout note (following M4's closeout-note precedent) rather than
   claiming success without having actually read the output.

### M5.4 closeout note (2026-07-19): steps 1–4 executed live; steps 5–7 handed to the user

**What was mechanically verified (steps 1–4), with the receipts:**

- **Step 1 (install):** `scripts/verify_install.sh` performed a real `uv tool install .`
  into an isolated `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR`, confirmed `novelizer` on PATH and
  `--help` exits 0 with all subcommands listed. The CI-mechanical twin
  (`tests/test_install_smoke.py`) runs the same first-contact commands in-process on every
  suite run. Dependency audit moved `pytest`/`hypothesis`/`pytest-asyncio` out of the
  runtime set into a `[dependency-groups] dev` group — the installed tool no longer drags
  test frameworks.
- **Step 2 (wizard):** the setup wizard is an interactive Textual app; per the plan, this
  was the one manual-equivalent step — the global config was written via the wizard's own
  `write_global_config` function (same fields, same writer), pointing at the live endpoint
  (`http://192.168.1.14:8080/v1/`, `qwen3.6-27b-mtp` for both author and agent models).
  `novelizer voices`/`novelizer chapters` against that fresh config ran without crashing.
- **Step 3 (cast + seed):** `novelizer voice-scaffold saltglass "Briny, glittering,
  fragmentary sentences; tide-worn imagery; no similes"` wrote the profile with no TOML
  hand-editing; it was activated through the settings layer (`prose_profile = "saltglass"`,
  the settings-screen flow headless-equivalent). `novelizer seed` injected the Salt Court
  premise ("tide-priests who read law in the ebb; Mara, an archivist, guards a secret: the
  drowned heir lives") into a fresh `stories/default/world.db`.
- **Step 4 (unattended run):** autonomy set to `gated_retcons`; the room ran headless for
  5 minutes against the live endpoint with the concurrency pool at its default of 2.
  Result: 27 events, 1 draft chapter (*"The Archivist's Tide"*, 2,052 chars, first-person
  archivist narration visibly in the saltglass voice: "The Salt Court does not build its
  laws in stone. It reads them in the ebb."), 9 Salt Court world entries, 3 planted
  threads (`the-living-heir`, `the-fake-circlet`, `tide-law`), 2 themes, and one live
  guardrail catch (the Author's `learn` intent for an unknown secret id `torian_lives` was
  dropped at the commit seam, exactly as designed). Agent dispatch spread:
  world_architect ×3, character_keeper ×2, author ×1, continuity_checker ×1. The process
  was stopped cleanly and `novelizer chapters` read the DB intact afterward. No retcon was
  filed in this short window — recorded honestly per the plan's own framing: retcon
  accumulation is a step-5/6 longer-run outcome, and the retcon chain is separately
  live-proven by M5.1's two-cycle leak smoke and M5.2's voice-drift smoke.

**Two findings caught by actually running it (both fixed on this branch):**

1. **Story-resolution split-brain.** The first step-4b attempt drove `Runtime` with a bare
   `load_effective_settings()`, which does *no* story resolution and defaults to the legacy
   flat `stories/world.db` — while the CLI's `seed`/`autonomy` commands had resolved to
   `stories/default/`. The room ran against an *unseeded* DB and cheerfully invented its own
   world (an "Oakhaven" with "Glass Wastes" — none of it Salt Court). Anyone embedding
   `Runtime` directly must pass `load_effective_settings(story_dir=StoryDirectory(...))`
   the way the CLI/TUI do. The corrected run (evidence above) picked up the seed. TUI/CLI
   users cannot hit this; it is an embedder's gotcha, recorded here.
2. **No read-only autonomy query.** Bare `novelizer autonomy` (no LEVEL) is a usage error,
   not a status display. The walkthrough script tripped on it; the README's per-agent
   override examples were also corrected to use the lowercase agent id (display-cased names
   create silently-ineffective overrides).

**Incident during this sub-milestone (root-caused and fixed):** full-suite runs in a
shared checkout read the developer's real `~/.config/novelizer/config.toml`
(`last_opened_story`) and opened — and write-locked — their live story DB out from under
their running session. Fixed with autouse isolation fixtures (`tests/director/conftest.py`
sandboxes `XDG_CONFIG_HOME` + cwd for every CLI test; `tests/test_install_smoke.py` gained
a throwaway-cwd fixture for the same class of leak, which was separately creating a real
`stories/default/` in the repo root). Verified: a full suite run (838 passed) no longer
creates `stories/` or touches any real config. Standing policy going forward: post-merge
master verification runs in a detached temp worktree, never the main checkout.

**What was NOT performed — steps 5–7 are the user's to run.** Leaving the room running
for roughly a day, watching chapters/views/retcons accumulate, and reading the result as
prose to judge "coherent novella" is inherently a human call and was not done by this
branch. `docs/QUICKSTART.md` is the exact path to follow. The walkthrough's seeded story
lives in `/tmp/novelizer-stranger.NkoV/stories/default/` (a `/tmp` path — it will not
survive a reboot; re-seeding via QUICKSTART takes under a minute if it's gone).

### M5.3 Task 7 verdict — causal graph in the Author's prompt

**Verdict: adopted.** `Author.poll()`'s ctx now includes `causal_edges` (via
`self._read.list_causal_edges()`, mirroring how `themes`/`threads`/`secrets` are already
fetched), and `_summarize` calls the existing `causal_flags_note(ctx["causal_edges"],
[c.id for c in ctx["chapters"]])` — the same function `editor.py` already uses — appended
to the prompt exactly like the `stale_threads_note`/`known_secrets_note` blocks that
precede it.

Rationale, condensed from the plan's four criteria: **cost** is genuinely zero until
causal edges exist (empty string, same "nothing to report" shape as every prior brain-note
addition, pinned by a byte-identical-when-silent test); **signal quality** closes a real
gap — the Author already declares new `causal_intents` (M4.2) but previously did so blind
to existing edges, so it had no ordering context when deciding what edge to declare;
**risk** is prompt-length growth on the highest-frequency agent in the room, but it is
prose content, not response-format grammar, so it's lower-risk than the `ProviderStrategy`
concerns M5.2 flagged; **precedent** — `known_secrets_note`/`stale_threads_note` are
exactly this shape and are load-bearing for M5.1's reliability story, so this addition is
consistent with, not a departure from, the established pattern. No live-quality evidence
surfaced during implementation that contradicts the plan's recommended verdict, so
"adopt, scoped small" stands as written.

Implementation: `novelizer/agents/author.py` (`Author.poll()` ctx key, `_summarize` call,
import from `novelizer.brain.context`), tests in `tests/agents/test_author.py`
(`test_summarize_omits_causal_flags_block_when_no_edges`,
`test_author_prompt_includes_causal_flags_when_edges_flagged`,
`test_author_prompt_byte_identical_to_pre_causal_shape_when_no_edges`). Full suite green
(678 passed, 6 deselected live_llm) after the change.

## Locked decisions

1. **Prose mining commits through the identical `Committer`/event-type seam as
   self-declared intents — no `mined.*` event domain, no parallel machinery.** The only
   difference between a declared and a mined fact is a `source` field on the same payload
   models (`"declared"` default vs `"mined"`), so every deterministic analyzer built in
   M3/M4 (`find_leaks`, `find_paradoxes`, `StalenessAnalyzer`) works on mined facts for
   free, with zero changes to their code. This was the explicit design constraint from the
   milestone brief and is the load-bearing decision the rest of M5.1 hangs off. Adding
   `source: str = "declared"` to the existing payload models is replay-compatible: no
   payload model in `novelizer/canon/events.py` sets `extra="forbid"` (verified — no
   `model_config` overrides at all), so old events without the field parse with the
   default. **Scope carve-out**: mining auto-commits only the `_NEVER_GATED` fact types;
   `secret.revealed` (a gated `_CANON_EVENTS` type) is never auto-committed by mining —
   see decision 3.

2. **Mining is per-chapter and idempotent via a new `chapter.mined` marker event, not a
   new persisted "already mined" flag bolted onto chapters or a re-scan of the full
   log's prose every cycle.** `chapter.mined` is `_NEVER_GATED` bookkeeping (same class as
   `thread.*`/`secret.created`/`secret.learned` — recording that mining ran, not changing
   canon). This keeps mining's cost bounded (one LLM call per chapter, ever) rather than
   growing with checker cycle count, and keeps the mechanism the same shape as every other
   Brain fact in this codebase: an event, folded by the Projector, read by a pure query.

3. **Mining never invents an id for an ambiguous fact, and never auto-commits a reveal;
   both cases become `retcon_request.created` events tagged `MINED_SOURCE_TAG`, routed to
   a human/Retconner decision, exactly like M4.1's unknown-id-intent-drop rule but
   escalated instead of silently dropped.** Mined reveals escalate *regardless of
   confidence and regardless of autonomy level* — under `gated_canon`+ a Checker-committed
   `secret.revealed` would silently become a proposal rather than an event (the
   `GatingCommitter` routes gated types to `proposal.created`), and under `full_auto` it
   would mint set-once revealed canon on the strength of an LLM's after-the-fact prose
   inference; neither outcome is acceptable, so the reveal path is uniform: escalate — because a *mined* fact, unlike a *declared* intent, has no agent
   standing behind it asserting "I know this id is right"; the mining pass is inferring
   from prose after the fact, so ambiguity should surface, not resolve itself by guessing.
   This is the one place M5.1 deliberately behaves differently from M3.1/M4.1's drop-and-
   log-warning rule, and the rationale is the asymmetry between self-declaration (agent
   knows what it meant) and mining (LLM is guessing what prose implies).

4. **Dedup is log-only, against the same-poll committed-state snapshot — no new persisted
   "seen facts" cache, and no in-process ordering assumption.** The declaring agents
   (Author/Editor) commit their intents in their *own* poll cycles, asynchronously
   relative to the Checker, so mining cannot rely on any "declared path runs first"
   sequencing — it dedups purely against what the log already holds at snapshot time:
   `list_secret_references` rows (chapter-scoped) + knowledge matrix for the secret
   domain, exact `(cause, effect)` triple match on `list_causal_edges` for causal edges,
   and a raw event-log scan for `thread.*` events citing the `(thread_id, chapter_id)`
   pair (explicitly a mining-only log read — `ThreadsProjection` holds aggregate state,
   not per-chapter history, and M5.1 adds no projection for it). The residual race
   (declared intent lands between snapshot and mined commit) produces at worst a benign
   duplicate: INSERT-OR-IGNORE learns, verdict-neutral duplicate references, re-stamped
   thread touches. Cross-cycle idempotency is the `chapter.mined` marker (decision 2).

5. **M5.1's live_llm fixture engineers the previously-unreachable failure mode directly
   (withholding `known_secrets_note()` for one fixture chapter) rather than continuing to
   hope a live run produces an accidental leak.** M4's closeout note is explicit that 20+
   runs never produced one because the guardrail worked; re-running the same fixture
   unchanged would just repeat that result. This is a deliberate, documented deviation from
   "test the system exactly as a director would use it" for this one smoke test only —
   the point of the test is to prove the *catch* mechanism works when a leak occurs, and
   M5.1 needs a way to make that happen on demand rather than waiting for a sloppier
   sample, which is the same alternative M4's closeout note named as the other option.

6. **Theme identity and lifecycle mirror threads/secrets exactly (mint-at-`introduced`,
   slug, active-id-list citation, drop-unknown-with-warning) but with no terminal state**
   — themes don't get "paid off," "revealed," or "abandoned"; `introduced → developed*` is
   the whole lifecycle, because the vision doc scopes theme tracking to the Story Browser
   as an inspectable record of what the story is about, not as a Brain faculty with its
   own staleness/leak-style correctness check. This keeps M5.2 from inventing an analyzer
   the vision doc never asked for.

7. **Voice drift routes through `retcon_request.created` with a new `VOICE_SOURCE_TAG`,
   the same seam M4.2 established for leaks and paradoxes — not a new notification
   channel or a new TUI panel.** Voice enforcement was already partially built (M2.3 voice
   cards + Editor citation in free text); M5.2's contribution is making it a first-class,
   queryable, source-tagged retcon category, matching the precedent that anything the
   Continuity Checker/Editor flags becomes a retcon request, full stop — one queue, many
   source tags, distinguished by prefix, never by parallel infrastructure.

8. **The M4 deferred-backlog items are triaged, not silently dropped**: mechanical/
   correctness items (casing normalization, `_guarded_line`, settings-ify the 200-char
   window, `ctx.get`/`ctx[]` consistency, missing CLI/Literal/caplog tests, Editor
   "revise" bug, CLI/TUI `ProposalService` duplication) land in M5.3 as cleanup; the two
   items M4 explicitly named for re-evaluation (causal-graph-in-Author-prompt, and what M4
   called "configurable leak/paradox thresholds" — corrected here to configurable
   staleness/sag-spike thresholds, since `find_leaks`/`find_paradoxes` are binary
   structural checks with no parameter to configure) land in M5.3 with an explicit verdict
   recorded rather than a repeat deferral; `EmbeddingStore` gets one concrete wiring (theme-similarity
   suggestions) rather than remaining permanently unused, satisfying the "wire it for
   something real or explicitly defer" instruction — this *is* the something real, scoped
   deliberately small (a suggestion, not an auto-merge) to avoid the identity-minting
   risk a similarity-threshold auto-merge would introduce. `focus` staying context-only
   and scheduler override signals staying unconsumed are the two items that get an
   explicit non-goal treatment instead (see below), because promoting either is a design
   decision with real behavioral consequences the milestone brief did not ask for and
   the acceptance walkthrough does not need.

9. **`ctx.get(...)` vs `ctx[...]` rule (resolves the M4 backlog item)**: use `ctx[...]`
   for keys every code path in `poll()` always populates (required-shape context); reserve
   `ctx.get(...)` for keys that are genuinely conditional on story state (e.g. an empty
   list default is meaningfully different from a missing key). This is a naming/reading
   convention, not a behavior change — no test enforces it beyond the existing ones
   continuing to pass, and it is applied incrementally as agents are touched for other
   M5.3 reasons, not as a standalone repo-wide sweep.

10. **`chapter.revised` is M5.3's one new-event-domain item, scoped tightly**: payload is
    `(chapter_id, prose, editor_notes_ref)` — the id already exists, so no minting, no
    slug, no collision rules; `ChaptersProjection` folds it by replacing prose for that
    chapter id (read model = latest revision; the event log is the revision history —
    event sourcing gives history for free, no separate history table); the Author gains a
    revise branch triggered when the Editor's revise `DirectorSignal` carries the flagged
    chapter id, committing `chapter.revised` instead of `chapter.created`.
    `chapter.revised` joins `_CANON_EVENTS` (it rewrites canon prose — same gating class
    as `chapter.created`). If plan review judges this too large for the sweep, it is
    promoted to its own branch, not silently trimmed.

11. **Concurrent agent execution is a scheduler-dispatch change, not an agent change.**
    `max_concurrent_agents` (settings layer, default 2 — the user's direction is "we
    should be constantly calling inference," so the default must actually enable
    concurrency; 1 remains available as an explicit serial fallback) caps a pool of
    in-flight `run_once()` asyncio tasks. `tick()` fills free pool slots from the
    readiness-sorted eligible list (skipping agents already in flight or paused);
    `mark_ran` fires on task completion, not dispatch, preserving interval semantics.
    Agents themselves change ZERO — their poll-snapshot semantics already tolerate
    interleaving (each `poll()` reads a consistent ReadStore snapshot; commits go
    through the Committer seam; SQLite serializes appends via aiosqlite's connection
    lock, and M5.1's mining dedup was explicitly built snapshot-safe with benign-
    duplicate analysis). Done-when includes: a CI test proving two slow agents run
    overlapped (asyncio-clock instrumentation, no live LLM), a test proving the same
    agent is never double-dispatched, a test that pool size 1 reproduces today's
    serial ordering exactly, and a property test that concurrent commits from K fake
    agents produce a log whose per-aggregate event ordering is still valid (no
    interleaving corruption). Scheduler override/focus semantics unchanged.

12. **Packaging done-when is "verify and hardens," not "build from scratch"** — `uv tool
    install`-ability and a console-script entry point already exist in `pyproject.toml`;
    the setup wizard and settings TUI already exist from prior parallel work. M5.4's job is
    confirming these actually deliver the stranger experience end to end and writing the
    docs that describe it accurately, not re-architecting install or first-run flows that
    already work.

## Non-goals / deferred to later milestones

- **Promoting `focus` from context-only to a solo-override behavior** — this changes what
  autonomy/scheduling actually does (an agent could force itself to the front of the
  readiness race), which is a scheduler-semantics decision the M5 brief never asked for;
  left as a documented non-goal rather than silently dropped, available as a future
  milestone if a director workflow later needs it.
- **Consuming scheduler override signals** — same rationale as `focus`: the signals exist
  but wiring them changes scheduling behavior in a way that needs its own design pass, not
  a backlog-cleanup-sized change. Left unconsumed, explicitly, rather than half-wired.
- **Auto-merging near-duplicate themes found via `EmbeddingStore` similarity** — M5.2/M5.3
  wire embeddings for a *suggestion* only; auto-merging identity based on a similarity
  threshold is exactly the kind of silent-canon-mutation risk M4.1's secret-identity rule
  (mint only at explicit `created`, never inferred) was designed to avoid, and extending
  that risk to themes is out of scope here.
- **Mining domains beyond secrets/threads/causal-edges** (e.g. mining undeclared theme
  introductions, or mining voice-drift from prose rather than from the Editor's structured
  judgment) — M5.1's mining pass is scoped to the three domains M3/M4 already deferred
  undeclared-detection for (that's the whole reason it exists); extending mining to themes
  or voice is a natural follow-on once M5.1's pattern is proven, not a day-one requirement.
- **An in-TUI casting editor** — casting/recasting flows through `voice-scaffold` (which
  writes the voice-pack TOML for the director) plus the settings layer for selecting the
  active pack/profile; building a Mission Control screen for editing personalities would
  duplicate the settings/TOML seam for one milestone's acceptance step and is left to a
  future UX pass. The acceptance walkthrough's "no hand-editing TOML" bar is met by
  `voice-scaffold` doing the writing.
- **A fifth Brain view for themes** — per the vision doc, theme/motif tracking lives in the
  Story Browser, not as a new Brain view; M5.2 does not add a `theme_board.py` widget.
- **Configurable mining cadence or a mining on/off toggle in settings** — M5.1 ships with
  mining running once per chapter, unconditionally, matching M3.2/M4's precedent of
  shipping a fixed default first and deferring configurability (M5.3's leak/paradox
  threshold work is the configurability item that *did* get promoted this milestone;
  mining cadence did not, because chapter-once is already the minimum useful cadence, not
  an arbitrary default needing a dial).

## Standing principles (unchanged)

Event sourcing (log sole truth; only the Projector writes projections; state changes via
appended events — mined facts are no exception, they are ordinary events with a `source`
tag, not a side channel), DDD bounded contexts (Story Brain derives from canon events and
exposes read-side queries only), SOLID (extension over modification — mining reuses the
`Committer` seam and the M3.1/M4.1 identity-minting pattern rather than inventing parallel
paths for a third time), red/green TDD black-box-first with property-based tests where
invariants generalize, spec + code review as gates. Live tests marked `live_llm`, deselected
by default (`addopts = "-m 'not live_llm'"` in `pyproject.toml`), and must call
`load_effective_settings()`, never bare `EffectiveSettings()`.
