# Author agent — prompt redesign proposal

Repo: `/home/ty/workspace/novelizer`. Target: the Author (`novelizer/agents/author.py`), the fleet's sole prose generator, output schema `ChapterDraft` (`novelizer/agents/base.py:32-41`).

---

## 1. Diagnosis

**D1 — The system prompt actively instructs the #1 banned AI-tell.** `AUTHOR_SYSTEM_PROMPT` (`author.py:13-17`) says *"Write a self-contained chapter with a clear narrative beat, 2-5 paragraphs."* This is three problems in one sentence: (a) "self-contained" is the exact scene-level tell behavior digest Rule 6.3 forbids ("every chapter resolving into a neat mini-arc with a wrap-up closing paragraph"); (b) "clear narrative beat" pushes the same tidy-resolution shape; (c) "2-5 paragraphs" is an arbitrary length cap and the *only* craft guidance in the entire prompt. The prompt tells the Author to do the wrong thing and gives it nothing else to steer by.

**D2 — Zero research-then-emit phasing against the heaviest schema in the fleet — the constraint-tax bullseye.** `ChapterDraft` has 8 fields including four intent lists (`author.py:32-41`) and is enforced via `response_format=ChapterDraft` (`author.py:197, 203`). Tech digest §5 names this the #1 threat: a heavy response schema biases the model to emit the final structure *early and skip the tool loop*. The current prompt gives no phase ordering, no "read before you write," no stopping rule — nothing to counteract the pull toward premature finalization. The Author is the fleet's worst-exposed victim of the constraint tax and has zero mitigation.

**D3 — Five of eight schema fields are undocumented in the prompt.** The system prompt says *"Return a title, the full prose, and the ids of characters who appear"* (`author.py:16`) — describing only `title`, `prose`, `character_ids`. `feed_note`, `thread_intents`, `knowledge_intents`, `causal_intents`, `theme_intents` are never explained. The Author learns they exist only from terse pydantic docstrings (`schemas.py:55-124`) and from conditional brain notes that appear *only when* something is stale/secret/flagged (`context.py`). On a clean pass the Author is emitting four intent lists it was never told the purpose of — so it either fires them blind or leaves them empty by accident, not judgment.

**D4 — The single most coherence-critical read is truncated to 200 characters.** In non-pull mode, `_summarize` renders the previous chapters as `c.prose[:prior_chapter_chars]` with default 200 (`author.py:58`, default `author.py:90`). The Author writes chapter N+1 from 200 characters of chapter N — it literally cannot see how the last chapter *ended*, which is exactly the state continuity most depends on. This is the same truncation-starvation class as the fixed Character Keeper `prose[:300]` bug (memory: character-discovery-fix). It also violates behavior Rule 1.3 (condense-the-middle): all three prior chapters get the *same* hard 200-char cut, so the most-recent chapter is starved identically to the oldest.

**D5 — No condense-the-middle structure; no forward plan.** `_summarize` (`author.py:37-63`) is a flat dump: world 10×150 chars, chars 8×full, previous 3×200 chars. Rule 1.3 (StoryWriter ReIO, [HIGH]) prescribes full fidelity on the immediately-prior chapter + compressed middle + verbatim *upcoming* plan. The Author gets none of this shape, and there is no forward beat/plan surfaced at all — director notes (`author.py:47`) are the only forward signal.

**D6 — `character_ids` is requested but its ids are never shown.** The prompt asks for "the ids of characters who appear" (`author.py:16`) but the pushed character block is `f"- {c.name}: {c.traits} | arc: {c.arc_status}"` (`author.py:46`) — names, no ids. The Author must guess slugs or pull to cite them; wrong guesses silently break cast linkage at commit (`author.py:152`). Tech digest §4: ids the agent must emit should be citable from what it's shown.

**D7 — Craft/AI-tell guidance is names-only.** The only anti-tell content is `AI_TELL_BAN_NOTE` (`muse/prompts.py:8-12`), which bans specific names (Elias/Elara/…) and stock figures. The behavior-digest cluster — em-dash cap, no headers/bullets in prose, banned filler ("it is worth noting," "crucially," "leverage"), anti-uniformity/anti-polish, no tidy close (§6.1-6.3) — is entirely absent.

**D8 — The revise path drops the safety notes and invites over-smoothing.** `_revise_summarize` (`author.py:66-73`) carries only the original prose + editor feedback + voice/cast lines. It does NOT carry the brain notes (`stale_threads_note`, `known_secrets_note`, `causal_flags_note`) that the main path injects — so a revision can reintroduce a secret leak the create path would have prevented. It also says *"Rewrite it in full"* with no "address the specific feedback, don't re-smooth everything" guard, inviting exactly the homogenizing over-revision behavior Rule 3.3 warns against. No AI-tell reminders reach the revise path either.

**D9 — No planning nudge.** The cheapest lever in the harness — `write_todos` (tech digest §1, "basically a no-op… context-engineering device") — is never invoked. For an open-ended, unpredictable-step task like drafting a chapter with continuity checks, a one-line plan measurably improves multi-step execution.

---

## 2. Proposed system prompt

Paste-ready. Keeps the `+ AI_TELL_BAN_NOTE` concatenation so `AUTHOR_SYSTEM_PROMPT` still contains "Elias" and "lighthouse" (required by `tests/agents/test_author_muse.py:84`). Labeled sections per tech digest §2; references harness tools by name without re-explaining them (tech digest §1).

```python
AUTHOR_SYSTEM_PROMPT = """## Role
You are the Author of a living, event-sourced fictional world — the fleet's one
prose writer. You draft each new chapter and you alone own the final sentences.
The Editor, Continuity Checker, Character Keeper, and the rest advise you through
structured notes; they never write prose. Their notes are counsel, not dictation,
and grading or repairing the story is their job, not yours — your job is to write
the next chapter well and to record honestly what it did.

## Research before you write
The chapters, characters, lore, threads, and secrets already committed are the
ground truth. The summary in your task message is a POINTER to them, not the
source — do not write from the summary alone.
Begin every pass by calling `write_todos` with a short plan, e.g. "read end of
last chapter -> check stale threads/secrets -> draft -> set intents". Then:
- `read_file` the most recent chapter IN FULL. You are continuing from its final
  moment, not from a gist — match its place, time, and cast, and pick up the
  business it left unfinished.
- `grep` or `search_canon` for anything the task notes flag (a stale thread's id,
  a secret and who knows it, a character you'll feature) and read the relevant
  span before you rely on it.
- Stop once you can say where the last chapter left off and which threads and
  secrets bear on this scene. Then write. Do not browse the whole canon.

## Write the chapter
Write one chapter of narrative prose — scene, action, and dialogue, not synopsis.
Let the beat set the length; never pad to a target.
This chapter is one movement in a continuing novel, NOT a standalone story. Do
not resolve it into a tidy mini-arc or close on a reflective "and so…" paragraph.
End where the tension is still live — on a choice made, a question opened, a
consequence about to land — so the next chapter has somewhere to go. Seeding a
payoff now to cash chapters later, or leaving a thread deliberately mid-air, is
good craft.
Continuity is binding. Honor established facts, timelines, and who-knows-what:
if the task notes list secrets and who holds them, never let a character act on
one they have not learned.

## Craft — write like a person, not a model
- Vary sentence length and rhythm on purpose; let some run long and others land
  short. Uniform, evenly-cadenced, over-polished prose is the strongest signal a
  machine wrote it — asymmetry, and the occasional fragment or rough edge, read
  as human.
- Cap em-dashes at about one per 500 words; reach for a comma, a period, or the
  plain word instead.
- Keep markdown out of the prose — no section headers, no bullet lists. It is a
  chapter, not a document.
- Cut throat-clearing and filler: no "it is worth noting," "significantly,"
  "crucially," "leverage," "a myriad of," "a testament to." Trust the scene.
- Show feeling through action, object, and subtext; don't name the emotion.

## Record what the chapter did (structured notes)
After the prose is written, fill the intent lists with what the chapter ACTUALLY
did to the story's spine — and only that:
- thread_intents — `plant` a genuinely new through-line, or `touch`/`pay_off`/
  `abandon` an existing one by its exact id from the task notes (never invent an
  id). A thread is a load-bearing promise to the reader, not every passing mention.
- knowledge_intents — `plant` a real secret, or mark a character `learn`/`uses`/
  `reveal` on an existing secret id.
- causal_intents — link two existing chapter ids when one genuinely causes the other.
- theme_intents — `introduce` or `develop` a motif the chapter truly carries.
Leave a list empty rather than padding it: a marginal or invented thread is worse
than none, and every intent you declare is one another agent must reconcile.
List `character_ids` using the ids shown beside each name in the task notes.

## Your feed note
Do the writing and the note-setting as a craftsperson. Then, last, write
`feed_note` — one short line in your own voice reacting to the chapter you just
made.
""" + AI_TELL_BAN_NOTE
```

Rationale mapping: Role/non-goals → behavior Rules 2.1-2.3 (Author owns final prose, others advise). Research-before-write + stop rule → tech §3, §5 (constraint-tax mitigation via phase ordering + stopping criterion). "Write the chapter" ending guidance → behavior Rules 6.3, 1.5. Craft → behavior §6.1-6.2. "Record what the chapter did" → D3 fix + verify-then-emit (behavior 7.1). feed_note-last → persona Rule 5.2. `write_todos` nudge → tech §1.

**Shared-surface note (RETRIEVAL_NOTE):** `build_author_runner` appends `RETRIEVAL_NOTE` after the system prompt (`author.py:194`). Its text is pinned across six test files (`test_author.py:668`, `test_editor.py:719`, `test_retconner.py:199`, `test_structure_analyst.py:161`, `test_character_keeper.py:435`, `test_world_architect.py:140`) — **do not edit its text.** My "Research before you write" section now covers the index-then-read motion more richly than `RETRIEVAL_NOTE`'s one map-sentence, so the clean option is to switch *the Author's* build to `RETRIEVAL_NOTE_BASE` (`author.py:194` → `AUTHOR_SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE`), removing the redundant sentence. No test pins *which* constant the Author uses, so this is safe. If you'd rather not touch the runner, leaving `RETRIEVAL_NOTE` in place costs only one duplicated sentence — acceptable.

---

## 3. Context-assembly changes (`_summarize`, `author.py:37-63`)

Goal: implement condense-the-middle (Rule 1.3) and close the ACI gaps, while preserving the empty-case byte shape the three byte-identical tests assert.

**C1 — Make pull_mode the Author's default AND inline the full last chapter.** Today pull_mode replaces prose with the index and pushes *no prose at all* (`author.py:55-56`), which under-provisions the one read that matters most; non-pull mode truncates it to 200 chars (D4). The correct shape is a hybrid: **full prose of the most recent chapter inline + one-line index of all earlier chapters + pull-on-demand for the middle.** Concretely, in pull_mode:
```
Last chapter (full) — '<title>':
<c.prose in full>

Earlier chapters (index — read_file any you need):
<chapter_map_note(earlier)>
```
This gives full fidelity on the edge that continuity depends on, compresses the middle to citable pointers, and leaves the Author's tools to recover any middle chapter. It directly fixes D4/D5 and makes the "read the last chapter in full" instruction cheap (it's already in front of the model) while still forcing pulls for older material.

**C2 — Show character ids in the pushed cast block (`author.py:46`).** Change to `f"- {c.name} (id: {c.id}): {c.traits} | arc: {c.arc_status}"` so `character_ids` can be cited from what's shown (fixes D6). Empty case stays "None yet.", so byte-identical tests are unaffected.

**C3 — Label a forward plan when one exists.** If any planned/upcoming beats exist (director signals already flow in at `author.py:47`; Muse story sparks at `inspiration_note`), render them under an explicit "Upcoming / planned:" heading so the Author can write *toward* something (Rule 1.3 "keep upcoming verbatim"). Keep it optional/empty-safe like the other brain notes.

**C4 — Keep the volatile context in the user message, keep the system prompt stable (tech §1).** The redesigned system prompt is per-instance-constant → maximizes Anthropic prompt-cache hits across interval passes. Do NOT migrate the per-cycle brain notes into it. (Optional micro-opt: `personality`/`casting_note` are also per-instance-constant and could move to the system prompt for cache stability, but that breaks the `"In character:"` / `"Write in this prose voice:"` user-message tests — low value, skip.)

**C5 — Fix the revise path (`_revise_summarize`, `author.py:66-73`).** Carry the same `known_secrets_note` + `stale_threads_note` + `causal_flags_note` blocks the create path injects, so a revision can't reintroduce a leak (D8). Reframe the instruction from "Rewrite it in full" to "address the editor's specific points; preserve what works and do not re-smooth prose that isn't flagged" (Rule 3.3), and append the craft/AI-tell reminders. The revise path emits `ChapterDraft` but `commit()` only uses `draft.prose` for `ChapterRevised` (`author.py:147`) while still running the intent-commit block — so either tell the reviser it may leave intents empty (recommended) or explicitly scope revision to prose only.

---

## 4. Behavioral guardrails

**No pass/no_action path — and that's correct.** The Author has no `no_action` field and `readiness()` gates on draft backlog (`author.py:101-103`); it always produces a chapter. The idle-pass machinery (`base.py:25-29` `PASS_PROMPT_INSTRUCTION`) is for the auditor/keeper agents, not the generator. Do not add abstention at the pass level.

**Abstention lives at the intent level instead (behavior 7.1, over-acting is the dominant failure).** The Author's version of "don't over-act" is per-intent: emit a thread/knowledge/causal/theme intent only on a genuine development, leave the list empty otherwise. The prompt's "Record what the chapter did… leave a list empty rather than padding it" is the verify-then-emit guard. This is the calibration that matters for a generator.

**Constraint tax is the primary structured-output pitfall (tech §5).** Mitigations, in order of leverage: (1) research-first phase ordering + "read the last chapter before writing" (prompt-only, in §2); (2) `write_todos` plan at pass start; (3) *optional, higher-effort:* add a lightweight evidence hook to the schema — e.g. `continuity_basis: str = ""` on `ChapterDraft` ("one line: where the last chapter left off, which you read") — a field that cannot be filled without a real read structurally forces the tool loop the constraint tax would skip. Flag as a schema change (touches `base.py:32-41`, `commit()`, and `ChapterDraft` tests); recommend prompt-only first, add the field only if live runs show the Author still writing from the summary.

**Over-retrieval guard.** The explicit stop rule ("Stop once you can say where the last chapter left off and which threads/secrets bear on this scene… do not browse the whole canon") curbs turn-burning against `GRAPH_RECURSION_LIMIT=100` (`base.py:19`).

**Lane boundaries (behavior 2.1-2.3).** Encoded in Role: Author owns final prose; does not grade its own chapter (Editor's job), does not police continuity beyond honoring known facts (Continuity Checker's job), does not repair contradictions (Retconner's job). Intents are the Author declaring what *its own* chapter did — not auditing anyone else.

---

## 5. Persona / voice

Three distinct voice layers must not blur:

- **`casting_note`** (prose profile — sparse/lush/plain, `voices/default.toml:3-35`) is the NOVEL's voice and the Author's *core craft instruction*. It legitimately steers the prose and belongs prominently in the task message as today ("Write in this prose voice: …", `author.py:48`). This is voice generation — the one place heavy stylistic steering is appropriate (Rule 5.1: personas help voice tasks).
- **`personality`** (agent_personalities.author, "a restless, slightly romantic chronicler…", `voices/default.toml:38`) is the AGENT's flavor. Per Rule 5.2, persona injection degrades instruction-following precision, so it must be scoped to the `feed_note` ONLY — not bled into prose craft or intent extraction. The proposed prompt's "Your feed note" section does exactly this ("do the writing… as a craftsperson. Then, last, write feed_note in your own voice"). Keep `personality` in the task message ("In character: …", `author.py:49`) but let the system prompt confine its scope.
- **Chat persona** (`chat/personas.py:24-25`, "You are the Author — you think in scenes, beats, and consequences") is a thin @-mention surface with full intent permissions. It intentionally shares no text with the autonomous prompt and needs no alignment with it — leave as-is.

Net: `casting_note` = how the *book* sounds (prose); `personality` = how the *Author* sounds (feed note only). They're already separate constants — the redesign keeps them separate and pins each to its lane.

---

## 6. Risks & test hooks

**Pinned wording that MUST be preserved:**
- `AUTHOR_SYSTEM_PROMPT` must contain "Elias" and "lighthouse" — satisfied by keeping `+ AI_TELL_BAN_NOTE` (`test_author_muse.py:84`).
- `RETRIEVAL_NOTE` / `RETRIEVAL_NOTE_BASE` text is pinned in 6 files (§2 shared-surface note) — do not edit the constants' text. Switching the Author to `RETRIEVAL_NOTE_BASE` is safe; editing either string is not.
- The user-message trailer **"Write the next chapter."** is locked by three byte-identical tests (`test_author.py:305, 470, 565`). Keep it verbatim as the trailer.
- `_guarded_line` "In character" / "Write in this prose voice" labels are asserted (`test_author.py:130, 170`; `test_guarded_line_adoption.py:11-12`). Keep both labels.

**Tests my recommended changes WILL break (update these):**
- `test_summarize_default_prior_chapter_chars_is_200` (`test_author.py:488`), `test_summarize_uses_configured_prior_chapter_chars` (`:475`), `test_author_constructor_threads_prior_chapter_summary_chars_through` (`:500`) — all pin the 200-char previous-chapter truncation, which C1 replaces with full-last-chapter. Rewrite to assert the new shape (last chapter full; earlier chapters indexed).
- `test_author_pull_mode_true_replaces_prose_with_chapter_map` (`:584`) asserts pull mode contains NO prose ("secret prose text" not in sent). C1 deliberately inlines the *last* chapter's prose. Rewrite it to assert: earlier chapters are indexed (their prose absent) while the most-recent chapter's prose IS present.

**Tests that STAY green (verify, don't rewrite):** the three "byte_identical … when brain silent" tests (`test_author.py:296, 461, 557`) assert only the empty case — my changes touch non-empty formatting only (full last chapter, char ids, forward plan), so the "None yet." / "Write the next chapter." shape is unchanged. C2/C3 are empty-safe by construction.

**Regression risks:**
- *Under-retrieval persists* if the constraint tax overpowers the prompt-only mitigation. Mitigation: land the optional `continuity_basis` field (§4) if live runs show summary-only writing. Watch: chapters that contradict the prior chapter's ending.
- *Over-retrieval / turn-burn* against `GRAPH_RECURSION_LIMIT=100`. Mitigated by the stop rule; watch pass durations.
- *Length inflation.* Removing "2-5 paragraphs" plus "let the beat set the length" could drift long. If chapters bloat, add a soft ceiling ("usually a few hundred to ~1200 words") rather than restoring the hard paragraph cap.
- *Prompt length vs attention budget* (tech §2). The new prompt is ~5× longer. Justified for the expert generator (behavior 5.1), but if craft adherence drops, trim the Craft bullets first — they're the most compressible.
```
