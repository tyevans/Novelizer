# Agent prompting redesign — research & proposals (2026-07-19)

A full audit of how every novelizer agent is prompted, two research digests on what makes
deep/pull-based agents work, and one redesign proposal per agent (plus one for the shared
prompt surfaces). Produced by a fan-out of research + per-agent redesign subagents; each
proposal was written against the live source with `file:line` citations and a check of which
wording is pinned by tests.

**Status: implemented (2026-07-20).** All seven LLM agents and the shared surfaces have landed;
see "What shipped" below. The proposals remain as the design record and rationale — where the
implementation diverged from a proposal, this README says so.

## Reading order

1. `architecture-brief.md` — how prompting works today (audit of the current state).
2. `research-tech-digest.md` — technical design rules: deepagents internals, context
   engineering, agentic retrieval, structured-output "constraint tax", abstain calibration.
3. `research-behavior-digest.md` — behavioral rules: narrative coherence, lanes/non-goals,
   judge biases, persona trade-offs, AI-tell avoidance, proactive-vs-reactive gating.
4. `proposal-fleet-shared.md` — cross-cutting changes (read this before the per-agent ones;
   several per-agent proposals assume its RETRIEVAL_NOTE / PASS_PROMPT_INSTRUCTION rewrites).
5. Per-agent proposals: `proposal-author.md`, `proposal-editor.md`,
   `proposal-continuity-checker.md`, `proposal-character-keeper.md`, `proposal-retconner.md`,
   `proposal-structure-analyst.md`, `proposal-world-architect.md`.

## Cross-cutting findings (the patterns that repeat)

**1. Truncation starvation is systemic, not a one-off.** The character-discovery bug
(`prose[:300]`) was one instance of a fleet-wide pattern that is still live everywhere:

- Author sees `prose[:200]` of the previous chapter — it cannot see how the last chapter *ended* (`author.py:58`).
- Character Keeper's fix widened the cutoff to `prose[:6000]` instead of removing it; it has file tools but no `pull_mode` (`character_keeper.py:77`).
- Structure Analyst scores from `prose[:400]` — chapter openings, not arcs.
- World Architect sees `body[:100]` of 20 world entries and **zero chapters** — it cannot serve the story it's building a world for (`world_architect.py:34-39`).

The shared fix: push a lightweight *index*, require full reads via tools before emitting
(the "context-assembly protocol" the character-discovery fix anticipated).

**2. The constraint tax is unmitigated fleet-wide.** `response_format=pydantic` measurably
suppresses tool-calling (arXiv 2606.25605): the model emits the final structure early and
skips the pull loop. Every agent has pull tools; no prompt orders RESEARCH-then-EMIT, and no
schema has an evidence field. Every proposal adds phase ordering + citation grounding
("no quote → no flag") so the schema *cannot* be satisfied without reading canon.

**3. Prompts under-specify craft and over-trust push context.** The Author's only craft
guidance — "write a self-contained chapter" — literally instructs the #1 scene-level AI tell
(tidy mini-arc + wrap-up close). The Editor is told to give "brief praise" on approval,
inviting sycophantic judging on top of documented self-preference bias (smooth/polished
AI-style prose gets over-approved).

**4. Lanes exist in code but not in prompts.** No prompt names its non-goals. Research says
explicit "you do NOT do X — that's Y's job" clauses prevent role collapse. Deterministic code
already files leaks/paradoxes every cycle, yet the Continuity Checker's LLM pass is prompted
to hunt them too.

**5. no_action needs verify-then-abstain.** Over-acting is the dominant documented failure
(52–65% false-alarm base rates). PASS_PROMPT_INSTRUCTION should become a three-way decision
anchored to a concrete canon delta, with correct-silence framed as success and a
don't-miss-real-events counterweight.

**6. Persona placement is backwards in places.** Research: heavy persona helps generators
(Author, Muse), hurts fact-checkers (Continuity, Retconner, Analyst, Keeper). Do analytical
work under neutral instructions; render only the feed note in character. Chat personas and
autonomous personalities should share one identity source (voice pack).

**7. Latent bugs found along the way** (worth fixing regardless of prompts):

- **Infinite revision loop**: revise signal → Author re-draft → projector resets chapter to
  `draft` → Editor re-polls it, forever; no revision counter exists anywhere.
- **Retconner lane bug**: voice-drift retcons carry *character* ids in
  `conflicting_entry_ids`, but the Retconner only loads world entries and always emits a
  WorldEntry → orphan entry superseding nothing. `RETCON_REQUEST_REJECTED` exists but is
  dead code — there is no way to decline a bogus request.
- **Character Keeper's dead `learn` capability**: `commit()` handles knowledge intents, but
  the prompt never mentions secrets and `work()` never renders the secret ids `poll()` fetches.
- **Structure Analyst calibration drift**: absolute scores from memory feed a *delta*
  detector (`detect_sag_spike`), so between-pass scale drift manufactures false pacing flags
  that the Editor then acts on.
- **World Architect has no watermark** — it re-fires on an unchanged story.

## What shipped

**Shared surfaces.** `novelizer/agents/prompts.py` is the new home for `RETRIEVAL_NOTE`,
`RETRIEVAL_NOTE_BASE`, `PASS_PROMPT_INSTRUCTION` and `DEFAULT_PASS_REMARK`, re-exported from
`author.py`/`base.py` so the existing import sites keep resolving (migrate those, then drop the
re-export). The retrieval note now names the index-then-read loop, the grep-vs-`search_canon`
boundary, the no-write-from-summary rule and a stopping rule. Post-landing extension
(2026-07-22, beyond the proposal): the note also states the tree layout — the six top-level
directories, explicitly no `/canon` prefix — and the slug rule with a worked example
(lowercase, punctuation runs to one dash, leading articles kept), plus a never-guess-paths
rule, after a local model invented `/canon/...` paths and slugs with dropped articles,
burning a whole continuity pass on not-found reads. The pass instruction is a three-way
act / stand-aside / confirm-first decision anchored to "what changed since your last pass", with
correct silence framed as success and a don't-miss-real-events counterweight.

**Identifiers and tools.** `chapter_map_note` and `causal_flags_note` speak in `chNNN` ordinals
(matching the canon_fs `NNN-slug.md` filenames) with the raw UUID trailing only where a schema
still needs it; `known_secrets_note` is title-first. `search_canon` gained a real tool contract
(meaning vs exact string, worked example) and a 20-hit cap that announces its own truncation.

**Evidence grounding.** Thread `touch`/`pay_off`/`abandon`, knowledge `learn`/`reveal`/`uses` and
causal intents carry an `evidence` field, recorded onto the event payload as provenance.
*Divergence from the proposal:* ungrounded citing intents are logged, not dropped — losing a real
narrative beat is worse than recording an under-cited one, and the warning rate is the measurement
that would justify a stricter policy. All payload fields default to `""`, so old events replay.

**Per-agent.** Author (research-then-write phasing, anti-tell craft rules, ending guidance, cast
ids, full most-recent chapter); Editor (cite-the-line ranked judging, bias guards, revision
budget); Continuity Checker (both prompts: quote-both-sides grounding, mid-book weighting, stops
duplicating the deterministic leak/paradox checks; worked learn/uses/reveal examples in the miner);
Character Keeper (`pull_mode`, secret ids, aliases); Retconner (verify-then-amend, blast-radius
check, `resolution` outcomes, lane guard); Structure Analyst (rubric with band anchors, calibration
table of recent scores, `pull_mode`); World Architect (chapter index, watermark, survey-then-emit).

**Bugs fixed along the way.** The infinite revision loop (chapters now carry a projector-derived
`revision_count`; the Editor force-approves past `MAX_REVISIONS`); the Retconner's orphan world
entry on character-id retcons (lane guard + the previously-dead `RETCON_REQUEST_REJECTED` event);
the Character Keeper's dormant `learn` path (secret ids now reach the prompt); the World
Architect's missing watermark; the Author's 200-char prior-chapter starvation.

**Still open.** Persona weighting by lane (heavy for generators, light for fact-checkers) and the
single-source-of-truth identity refactor between `voices/default.toml` and `chat/personas.py` —
both described in `proposal-fleet-shared.md` §2.5/§5, neither implemented. The rotating crutch-word
ledger (`recently_used_note`) is also unimplemented; `AI_TELL_BAN_NOTE` stays a static list, with
the craft rules now carried in the Author's own prompt.

## Original implementation order (as proposed)

1. **Fleet-shared surfaces** (`proposal-fleet-shared.md`): new `agents/prompts.py` with
   rewritten RETRIEVAL_NOTE + PASS_PROMPT_INSTRUCTION; `chNNN` ordinals replacing raw UUIDs
   in brain notes; evidence fields on citing intents. Everything downstream builds on this.
2. **Author + Editor** — the generator/judge pair with the tightest coupling (revision-loop
   cap lands here).
3. **Continuity Checker + Retconner** — the find/fix pair (decline path + lane pre-filter).
4. **Character Keeper** (pull_mode + full-read protocol), **Structure Analyst** (calibration
   table), **World Architect** (chapter index + watermark).

Each step is independently shippable; every proposal's §6 lists the tests that pin current
wording and the (small) set its changes deliberately break.
