# Retconner agent prompt redesign

The Retconner is a **surgical canon-keeper**: it consumes a contradiction report + conflicting entries and emits `amended_entries` (each superseding an old entry by id) that resolve the paradox. It is accuracy-first, minimal-persona, precision-over-creativity. This proposal makes it *verify before it amends*, *change as little as possible*, *check its own blast radius*, and *decline requests it can't reproduce or that aren't in its lane* — none of which the current prompt or schema support.

---

## 1. Diagnosis

**D1 — The prompt is a 4-line "propose amendments" instruction with no verify step, no non-goals, and no tool motion.** Full current text (`novelizer/agents/retconner.py:10-13`):

> "You are the Retconner for a living fictional world. You receive a contradiction report and the conflicting world entries. Propose amended versions of the conflicting entries that resolve the contradiction. Return amended_entries, each with a title, revised body, domain, tags, and supersedes_id set to the id of the entry it replaces. Only include entries that need to change."

It treats the report as ground truth and asks for amendments in one shot. Per the tech digest §1, this prompt is *concatenated onto the deepagents base prompt* (`retconner.py:93`, `SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE`) which already documents `ls`/`read_file`/`grep`/`glob`/`search_canon`/`write_todos`. The prompt never tells the agent to *use* any of them — so it won't. The whole redesign lever is turning this from a from-scratch instruction into a role specialization that names the index-then-read motion (tech digest §1, §3).

**D2 — The contradiction is never verified against live canon; the report is trusted secondhand.** `work()` (`retconner.py:45-54`) inlines the report and the conflicting entries' bodies into the user message and asks for amendments directly. But the report was filed by another agent on an *earlier* pass — by Continuity Checker (`continuity_checker.py:321-368`), Character Keeper (`character_keeper.py:141-143`), or the Editor's voice-drift path (`editor.py:145`). Between filing and now, the Author may have revised the chapter, another Retconner pass may have already fixed it, or the report may simply be wrong. The prompt has the agent repair a paradox it never confirmed still exists. This is exactly the "don't write from the push summary alone" anti-pattern (tech digest §3): the inlined entry bodies are a *pointer*, not the source of truth, and the agent must `read_file`/`grep` the actual canon before amending.

**D3 — `response_format=RetconAmendments` triggers the constraint tax, suppressing the tool loop the fix depends on.** The runner is built with `response_format=RetconAmendments` (`retconner.py:95`). Per tech digest §5 (arXiv 2606.25605), a response schema biases the model to emit the final structure *early* and skip tools. So even after we tell the agent to verify, the schema pulls it toward answering from the inlined text. The digest's structural mitigation — **require a citation/evidence field inside the schema so the output cannot be produced without a read** — is not present: `RetconAmendments` (`schemas.py:171-174`) has only `amended_entries` + `feed_note`, no grounding field.

**D4 — There is no reject / "already consistent" / "can't reproduce" outcome; every non-None result silently marks the request RESOLVED.** `commit()` (`retconner.py:56-65`) runs `RETCON_REQUEST_RESOLVED` unconditionally whenever `out is not None`, *even if `amended_entries` is empty*. So three very different situations collapse into one:
- "I repaired it" (amendments emitted),
- "the paradox is already gone / the report was stale" (empty amendments), and
- "this request is bogus and should be rejected" (also empty amendments).

All three end as `status=resolved` with no distinguishing signal. Meanwhile `EventType.RETCON_REQUEST_REJECTED` exists end-to-end (`events.py:18`, projected at `projector.py:232-235`, status `"rejected"`) but **no agent ever emits it** — it is dead code. The schema literally does not allow the agent to decline (the task's question "does the schema even allow declining?" — answer: **no**). A verify step is toothless without a way to say "verified: no repair needed" vs "verified: repaired."

**D5 — "Minimal-diff" is asserted but undefined, and blast radius is never checked.** The prompt says "Only include entries that need to change" (`retconner.py:13`) but gives no notion of what a *good* amendment is. Behavior digest Rule 2.1 names the non-goal ("repairs but does NOT invent new plot") — the prompt states neither the non-goal nor the positive definition (the smallest edit that removes the contradiction while preserving everything else the entry asserts). Worse, an amendment can *itself* contradict other canon: if entry A says "two suns" and gets amended to "one sun," any *other* world entry or chapter that references "two suns" is now inconsistent. The prompt never asks the agent to `grep` for other mentions of the changed fact before finalizing. The agent has `grep`/`search_canon` (`runtime.py:156`, `_phase_a_toolkit`) and does not use them for this.

**D6 — Lane/scope gap: the Retconner is world-entry-only, but receives character-scoped requests.** `poll()` loads only world entries (`retconner.py:43`), and `commit()` always builds a `WorldEntry` (`retconner.py:60-61`). But `conflicting_entry_ids` can be **character ids**: the Editor files voice-drift retcons with `conflicting_entry_ids=[flag.character_id]` (`editor.py:145`). For such a request, `work()` finds no matching world entry, so `text` becomes `"(entries not found)"` (`retconner.py:50`), and the agent either (a) hallucinates a world entry from the report text, or (b) emits one whose `supersedes_id` is a character id, which supersedes *nothing* (`projector.py:204-207` no-ops on the missing id) and lands an **orphan world entry titled after a character**. The prompt never states the Retconner's lane (world-entry contradictions) or tells it to decline out-of-lane requests. This is both a correctness bug and a missing non-goal (behavior digest Rule 2.1).

**D7 — Persona handling is fine but undifferentiated.** Personality is injected as `"In character: {personality}"` into the *work message* (`retconner.py:51-52`, guarded by `_guarded_line`), and `feed_note` becomes an `AGENT_REMARKED` (`retconner.py:65`). Behavior digest Rule 5.1 says keep persona *light* on canon-keepers (accuracy-first) — which is satisfied by default (empty personality). But the prompt gives no guidance that the persona belongs *only* in the final `feed_note`, not in the analytical amendment work (Rule 5.2). Minor, but worth codifying so a future voice pack doesn't thicken the persona into the reasoning.

**Test-surface note:** no test asserts the *content* of `SYSTEM_PROMPT`. The only wording constraint is `test_build_retconner_runner_with_backend_uses_retrieval_note_base` (`tests/agents/test_retconner.py:197-206`), which asserts `(SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE).endswith(RETRIEVAL_NOTE_BASE)` — satisfied as long as `SYSTEM_PROMPT` itself doesn't trail with retrieval text and the build concatenation is unchanged. So `SYSTEM_PROMPT` is free to be rewritten wholesale.

---

## 2. Proposed system prompt

Paste-ready replacement for `SYSTEM_PROMPT` in `novelizer/agents/retconner.py:10-13`. Written as a role specialization layered on the deepagents base + `RETRIEVAL_NOTE_BASE` (which is still appended at `retconner.py:93`), so it does **not** re-explain the file tools — it references them by name. It assumes the schema change in §4 (adds `resolution`, `reason`, `evidence`); a prompt-only fallback that works within the *current* schema is given at the end of this section.

```python
SYSTEM_PROMPT = """You are the Retconner for a living fictional world — a surgical canon repair
specialist. A sibling agent (Continuity Checker, Character Keeper, or Editor) has filed a
contradiction report against one or more world entries. Your job is to VERIFY the contradiction
still exists in the live canon, and if it does, resolve it with the smallest amendment that removes
it while preserving everything else those entries truthfully assert.

## Your lane
You repair contradictions between WORLD ENTRIES by superseding them. That is the whole job.

## Not your lane (decline these — do not force a fix)
- You do NOT invent new plot, lore, or history. A retcon reconciles what already exists; it never
  adds a story development. If resolving the contradiction would require inventing new facts, the
  request is under-specified — decline it (resolution="cannot_reproduce") and say what's missing.
- You do NOT rewrite chapter prose, character sheets, threads, secrets, or themes. Those belong to
  the Author and the Keeper. If the conflicting ids are character ids or anything other than world
  entries, this request is out of your lane — decline it (resolution="out_of_lane").
- You do NOT re-litigate style or pacing. You resolve factual/logical contradictions only.

## How to work — VERIFY, then AMEND (in this order)
1. Plan with write_todos: verify the contradiction → locate other mentions → amend or decline.
2. VERIFY the contradiction reproduces. The report and the entry bodies shown to you were captured
   on an earlier pass and may be STALE — the paradox may already be fixed, or the report may be
   wrong. Use read_file / grep / search_canon to read the CURRENT world entries named by the report
   before you touch anything. Do not trust the inlined bodies as ground truth; they are a pointer.
   - If the contradiction no longer reproduces in live canon → resolution="already_consistent",
     amend nothing, cite the spans that show it's already fine.
   - If the report is incoherent or names ids you cannot find → resolution="cannot_reproduce".
   - If the conflicting ids aren't world entries → resolution="out_of_lane".
3. AMEND minimally. For each entry that genuinely must change, emit one amended version:
   - Change ONLY the sentence(s) that carry the contradiction. Keep the title, domain, tags, and
     every other true statement in the body verbatim. A good amendment is a scalpel, not a rewrite.
   - Set supersedes_id to the exact id of the entry you are replacing (copy it from the report /
     frontmatter — never invent an id).
4. CHECK YOUR BLAST RADIUS before finalizing. Your amendment can create a NEW contradiction: grep
   the canon for other mentions of the fact you changed (e.g. if you change "two suns" to "one
   sun", grep for "sun"/"suns" across world entries and chapters). If other entries assert the old
   fact, either amend them in the same pass or, if that would spill into prose you can't touch,
   decline (resolution="cannot_reproduce") and name the collision. Never leave canon in a worse
   state than you found it.
5. STOP once you can cite the evidence for your decision, and emit the structured result. Do not
   keep browsing — grounding is your stopping rule.

## Grounding
Populate `evidence` with the file:line or entry-id spans you actually read to reach your decision —
for both amendments and declines. If you cannot cite where you verified something, you have not
verified it: read first, then emit.

## Voice
Do the analysis under these neutral instructions. Put your personality ONLY in the one-line
feed_note at the end — never let it color the amendment text, which must read as plain canon."""
```

**Prompt-only fallback (if the §4 schema change is deferred).** Keep the same VERIFY-then-AMEND body but drop the `resolution`/`reason`/`evidence` references and the decline paths, and replace step 2's branch outcomes with: *"If the contradiction no longer reproduces, or the request is out of your lane (the conflicting ids are not world entries) or too under-specified to fix without inventing plot, return amended_entries empty and say so in the feed_note — do NOT invent a world entry to satisfy the request."* This is strictly better than today (it stops the D6 hallucinated-orphan failure and the D2 blind-repair), but it still marks such requests `resolved` rather than `rejected` (D4 remains). The schema change is the recommended path.

---

## 3. Context-assembly changes

`work()` currently inlines the full conflicting-entry bodies (`retconner.py:50`, `text = "\n".join(f"[{e.id}] {e.title}: {e.body}" ...)`). Under the new prompt this is a *pointer*, and inlining the full body invites writing-from-summary (D2). Recommended shape for the user message built in `work()`:

- **Push (lightweight, up front):** the contradiction `description`, the `proposed_resolution`, and a compact **index** of the conflicting entries — `id | title | domain` only, NOT full bodies. This is the Claude-Code hybrid model (tech digest §3): push identifiers, pull prose. Add a one-line freshness cue: the request's `created_at` and the current latest-chapter id, so the agent can gauge how stale the report may be (behavior digest Rule 7.4).
- **Pull (via tools, on demand):** the agent reads the current entry bodies itself via `read_file`/`search_canon`, which is what forces the D2 verification to actually happen.
- **Keep** the `"In character: {personality}"` line (`retconner.py:51-52`) exactly — `test_work_prompt_includes_personality_when_set` (`tests/agents/test_retconner.py:153-165`) asserts both the personality string and the `"In character:"` label appear in the sent message.
- **Lane pre-filter in `poll()`/`work()` (backstop for D6):** before invoking the LLM, partition `req.conflicting_entry_ids` into ids that match a loaded world entry vs those that don't. If *none* match a world entry, the request is out-of-lane at the data layer — resolve/reject it deterministically with a feed_note ("not a world-entry contradiction — routing back") without spending an LLM call. This makes the D6 fix robust even if the model ignores the non-goal. (Loading characters into `poll()` for the match check is cheap: `self._read.list_characters()`.)

Caveat: pushing an index instead of full bodies only pays off when the agent is actually **tooled** (`retconner_tools_enabled`, `runtime.py:161,204`). In the no-backend/untooled build (`retconner.py:102-103`, no `search_canon`, no fs tools) the agent *cannot* pull, so `work()` should keep inlining full bodies in that path. Gate the assembly on whether tools are wired.

---

## 4. Behavioral guardrails

**Add a first-class decline outcome to the schema (recommended, shared-surface change).** Extend `RetconAmendments` (`schemas.py:171-174`) to make abstention a labeled outcome (tech digest §6, behavior digest Rule 7.1) and to force the tool loop against the constraint tax (tech digest §5):

```python
class RetconAmendments(BaseModel):
    resolution: Literal["amend", "already_consistent", "cannot_reproduce", "out_of_lane"] = "amend"
    amended_entries: list[WorldEntryDraft] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)  # file:line / entry-id spans actually read
    reason: str = ""                                    # required when resolution != "amend"
    feed_note: str = ""
```

Keep it lean — four flat fields, no nesting (tech digest §5, "keep the schema lean"). Then update `commit()` (`retconner.py:56-65`):

- `resolution == "amend"` → current behavior: supersede each entry, emit `RETCON_REQUEST_RESOLVED`.
- `resolution == "already_consistent"` → emit `RETCON_REQUEST_RESOLVED` (paradox gone, nothing to change) with `resolved_by=self.name`.
- `resolution in ("cannot_reproduce", "out_of_lane")` → emit **`RETCON_REQUEST_REJECTED`** (finally wiring up the dead `events.py:18` path) with the `reason` recorded, so the request leaves the `open` queue instead of being retried forever. Set `status=RetconStatus.rejected` on the copied request (mirror the resolved path at `retconner.py:63`).
- Guard: if `resolution == "amend"` but `amended_entries` is empty, treat as `already_consistent` (don't emit an empty resolution as a repair).

This closes D4 and gives the verify step teeth. Note the `_deferred`/None-output machinery (`retconner.py:67-81`) is unchanged — a `None` structured_response still defers (that's a transport failure, distinct from an explicit decline).

**Constraint-tax mitigation (D3).** The `evidence` field is the structural lever: a schema that *requires* citations for its decision cannot be satisfied without reading, so it forces the tool loop the constraint tax would otherwise skip (tech digest §5). Pair it with the prompt's explicit RESEARCH-then-EMIT ordering and the "stop once you can cite" rule (§2 step 5). Log `evidence` at commit time (it need not persist to `WorldEntry`).

**Over-retrieval / turn-burning.** The recursion limit is 100 (`retconner.py:98`, `GRAPH_RECURSION_LIMIT`), raised because tool-heavy passes overran 25/50 (project memory). The blast-radius grep (§2 step 4) plus the explicit stop rule keep the Retconner from spending all 100 turns spelunking. The stopping criterion is grounding: once it can cite the spans, it emits.

**Lane boundaries (D6).** The non-goals block (§2) plus the `poll()` pre-filter (§3) keep the Retconner from acting on character-scoped or prose-scoped requests — that's the Author's / Keeper's territory (behavior digest Rule 2.1). The `out_of_lane` reject sends those requests off the queue with a reason instead of silently orphaning a world entry.

**No PASS_PROMPT_INSTRUCTION.** Unlike the idle-pass agents (`base.py:25`, used by continuity_checker/world_architect/character_keeper), the Retconner is *demand-driven*: it only runs when `readiness()` sees open retcons (`retconner.py:30-32`), and its "nothing to do" case is handled deterministically by `poll()` returning `target=None` (`retconner.py:42-43`, `work()` short-circuits at `retconner.py:46-47`). Do NOT bolt `no_action` idle-pass semantics on — the decline outcomes above are the correct abstention model here, not a per-interval pass.

---

## 5. Persona / voice

The Retconner is a canon-keeper, so persona stays **light** (behavior digest Rule 5.1 — thick personas degrade accuracy on discrimination tasks, which verifying a contradiction is). Concretely:

- **Analytical work under neutral instructions.** The system prompt (§2) carries no character voice; it's plain procedure. The persona enters only via the `"In character: {personality}"` line appended to the *work message* (`retconner.py:51-52`) and is explicitly fenced to the `feed_note` by the prompt's Voice section (behavior digest Rule 5.2).
- **`feed_note` is the one persona surface.** It becomes the `AGENT_REMARKED` shown in the feed (`retconner.py:65`, asserted by `test_commit_emits_remark_when_feed_note_present`). It should be one dry line — "Reconciled the sun-count; canon holds." — matching a surgical fixer, not a dramatic one. The prompt already steers toward this ("one-line feed_note").
- **Chat persona alignment.** The interactive persona is `"You are the Retconner — you resolve approved retcons by amending lore cleanly."` (`chat/personas.py:43`). This is consistent with the autonomous role but slightly misleading on one word: "approved" implies a gating step the *agent* sees, whereas gating is transparent to it (handled by the committer via `AutonomyPolicy`, `policy.py:5,42`). Consider "you resolve **open** retcons by amending lore cleanly, or decline the ones that don't hold up" to reflect the new decline capability. Non-blocking; flagged for consistency.

---

## 6. Risks & test hooks

**Existing tests that constrain this work** (`tests/agents/test_retconner.py`):
- `test_build_retconner_runner_with_backend_uses_retrieval_note_base` (l.197-206): asserts `(SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE).endswith(RETRIEVAL_NOTE_BASE)`. The new `SYSTEM_PROMPT` must not trail with retrieval-note text and the build concat (`retconner.py:93`) must stay `SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE`. ✅ satisfied by §2.
- `test_work_prompt_includes_personality_when_set` (l.153-165): the sent message must still contain the personality string and `"In character:"`. Keep the `_guarded_line("In character", ...)` call. ✅
- `test_commit_emits_remark_when_feed_note_present` (l.168-179): `feed_note` → one `AGENT_REMARKED`. The new `commit()` branches must still call `self._remark(out.feed_note)` on every path. ✅
- `test_resolves_retcon_and_supersedes_entry` (l.49-67) and `test_run_once_survives_llm_inventing_a_domain` (l.70-88): both send `resolution` defaulting to `"amend"` with non-empty `amended_entries`, so the default-valued new field keeps them green. The domain-coercion regression (`schemas.py:17-22`) is untouched. ✅
- `test_failing_head_request_does_not_block_the_queue` / `test_none_output_defers_head_request` / `test_deferral_resets_once_every_open_request_has_failed` (l.91-143): the `_deferred` machinery is unchanged. ✅
- `test_runtime.py:179-234`: the end-to-end scheduler test scripts `RetconAmendments(amended_entries=[...])` with default `resolution` — still valid. ✅

**Schema-pairing test:** `tests/agents/test_schemas.py:44-45` (`test_retcon_amendments_carry_supersedes`) constructs `RetconAmendments(amended_entries=[...])` positionally-by-keyword — adding fields with defaults keeps it green, but **add a new test** asserting the `resolution`/`reason`/`evidence` round-trip and that `resolution` defaults to `"amend"`.

**New tests to add** (red/green, per the house TDD rule — project memory):
1. Decline path: a request whose contradiction doesn't reproduce → `resolution="already_consistent"` → `RETCON_REQUEST_RESOLVED`, no world change.
2. Reject path: an out-of-lane request (conflicting ids are character ids, per `editor.py:145`) → `resolution="out_of_lane"` → **`RETCON_REQUEST_REJECTED`**, request leaves the open queue, no orphan world entry created. This is the D6 regression test — assert `list_world_entries()` gained nothing.
3. `poll()` lane pre-filter: a purely character-scoped request is declined deterministically without an LLM call (assert the runner was not invoked).

**Regression risks:**
- **Constraint tax may still win.** Even with the `evidence` field, a model under `response_format` can emit empty `evidence` and skip tools (tech digest §5 is a *mitigation*, not a guarantee). Mitigate by making `commit()` log a warning when `resolution="amend"` ships with empty `evidence`, so the failure is observable in the pilot run rather than silent.
- **Blast-radius grep raising false declines.** An over-cautious agent could grep, find an unrelated "sun" mention, and decline a fixable retcon (behavior digest Rule 7.1's over-abstention backlash). The prompt balances this with "either amend them in the same pass or decline" — amending is the preferred branch. Watch the resolved:rejected ratio in the day-long run; if rejects spike, the grep guidance is too trigger-happy.
- **Untooled build.** The verify-then-amend prompt assumes tools. In the no-backend path (`retconner.py:102-103`) there are none, so the agent will read "use read_file/grep" and have nothing to call. The §3 tool-gated assembly (inline full bodies when untooled) keeps that path functional; the prompt's verify language degrades gracefully to "reason over the inlined bodies" there. Confirm `retconner_tools_enabled` is on for the live run (it is wired: `runtime.py:161,204`).
- **Shared-surface blast radius of the schema change:** `RetconAmendments` is imported only by `retconner.py`, `test_retconner.py`, `test_schemas.py`, and `test_runtime.py` (grep-confirmed) — the change is well-contained. `WorldEntryDraft` (reused inside `amended_entries`) is untouched, so WorldArchitect/CharacterKeeper commit paths are unaffected.
