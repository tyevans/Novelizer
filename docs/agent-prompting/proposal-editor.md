# Editor agent — prompt redesign proposal

Repo: /home/ty/workspace/novelizer. Editor is a **judge** agent: it reviews a `draft` chapter and returns `verdict ∈ {approve, revise}` + notes, plus second-pass annotation intents (thread/theme/knowledge/causal) and voice-drift flags. It emits `revise` director signals that re-draft the chapter via the Author.

---

## 1. Diagnosis

The current system prompt is three lines (`novelizer/agents/editor.py:13-15`):

```
You are the Editor of a living fictional world's story. Review the given chapter
for prose quality, narrative coherence, and pacing. Return a verdict of "approve" or "revise" and
notes: if revising, specific actionable feedback; if approving, brief praise.
```

Concrete weaknesses, mapped to the research:

1. **No cite-the-line grounding — the single highest-value fix is absent.** "specific actionable feedback" (`editor.py:15`) permits ungrounded verdicts like "the pacing feels off." Grounding each judgment in a quoted line is the one method that reliably improves LLM critique (behavior digest Rule 3.1, [HIGH]). The output schema `EditorVerdict` (`schemas.py:154-162`) has a single flat `notes: str` with **no per-issue evidence field**, so nothing structurally forces the read.

2. **"if approving, brief praise" actively manufactures sycophancy.** The prompt *instructs* the Editor to praise on approve (`editor.py:15`). Combined with self-preference bias — a judge scores its own-style output 10-25% higher (Rule 4.1, [HIGH]) — this trains the Editor to reward the smooth, polished, evenly-cadenced prose that is itself the strongest AI tell (Rule 6.2). The prompt has **zero** bias countermeasures: no "smooth ≠ good" yellow-flag, no verbosity-bias guard, no rubric anchor.

3. **No ranked/capped issue budget.** The prompt asks for "feedback" with no cap. Ungoverned critics over-flag, and over-flagging trains the Author to ignore the Editor (Rule 3.2, [MED]). There is no "top N, severity-ranked" instruction.

4. **Revision loops are mechanically UNCAPPED, and the prompt does nothing to self-govern.** On `revise`, the Editor commits a `revise` DirectorSignal (`editor.py:116`); the Author re-drafts via `ChapterRevised` (`author.py:147-148`); the projector resets the chapter to `draft` (`projector.py:194`); the Editor's `poll()` picks up drafts again (`editor.py:40`) and re-reviews forever. I checked `store/models.py:186-190` (Chapter) — there is **no revision counter** and no cap anywhere in the loop. Self-refine has sharply negative returns past ~2-4 rounds and homogenizes creative text (Rule 3.3, [MED]). The prompt never asks the Editor to justify *why a revision improves* or to stop churning.

5. **Constraint tax will suppress the tool loop.** The runner attaches `response_format=EditorVerdict` (`editor.py:166`) and the Editor is tooled (`runtime.py:190`, `editor_tools_enabled`). Structured-output constraints bias the model to emit the final structure early and skip the tool loop (tech digest §5, [FLEET]). With only a 3-line prompt and no research-then-emit phasing, the Editor will grade from the pushed prose alone and never `read_file`/`grep`/`search_canon` the surrounding chapters or the character/voice canon it's judging against. There is no evidence field to structurally force the read.

6. **No approve/revise decision criteria — no bar.** The prompt names three dimensions ("prose quality, narrative coherence, pacing") but gives no threshold for what clears approval vs. triggers revision. Ambiguity is the top driver of both over- and under-acting (tech digest §6; Rule 7.x).

7. **Persona bleeds into the analytical instruction.** `cast = self._guarded_line("In character", self.personality)` is appended to the *analysis* prompt (`editor.py:71,104`), and `casting_note` is injected as "Enforce this prose voice… note any drift in your feedback" (`editor.py:66-70`). Persona injection reduces instruction-following precision and hurts discrimination tasks — exactly a judge's job (Rule 5.1/5.2, [HIGH]). Persona should render only in the final feed note, not steer the analytical work.

8. **Lane boundaries are unstated.** The Editor emits thread/theme/knowledge/causal intents (`editor.py:121-127`) that overlap the Author (authoring) and Continuity Checker (contradiction-hunting). The design intent — the Editor annotates only *what the finalized prose demonstrably shows* (see the `secret_ids` comment, `editor.py:76-88`) — is documented in code comments but **never told to the LLM**. Without an explicit non-goals block (Rule 2.1, [HIGH]) the Editor will invent threads, duplicate the Checker's contradiction reports, and try to rewrite prose in its notes.

9. **The retrieval note is the Author's, and it's the wrong one — but correctly the base variant.** `SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE` (`editor.py:164`); `RETRIEVAL_NOTE_BASE` omits the "chapter list below is an index" sentence (`author.py:32`), which is right (the Editor gets full prose, not an index). But it's a generic "you have tools" line with no index-then-read motion, no citation-as-stopping-rule, no "don't grade from the summary alone."

**Not broken (preserve):** the byte-identical prompt-shape discipline (`test_editor.py:178,267,279,370,382,699`), voice-drift dedup keyed on `(character, line)` (`editor.py:128-146`), and the conditional brain notes (pacing/causal/secret-id/drift-filed) are all sound context plumbing. My changes are additive to the system prompt and leave the user-message byte shape intact.

---

## 2. Proposed system prompt

This replaces `SYSTEM_PROMPT` (`editor.py:13-15`). **No test pins its text** — the only assertion is `(SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE).endswith(RETRIEVAL_NOTE_BASE)` (`test_editor.py:726`), which any rewrite satisfies. It is written as a role specialization layered on the deepagents base (which already covers tool mechanics), and it keeps the static/cacheable content in the system prompt while the volatile per-chapter context stays in the user message.

```python
SYSTEM_PROMPT = """You are the Editor of a living, continuously-written novel. One chapter has been
drafted and handed to you. You decide whether it ships as-is (approve) or goes back to the Author for one
targeted rewrite (revise), and you record what the finished prose demonstrably establishes.

You are a JUDGE, not a writer. Work the analysis under neutral, evidence-first discipline; save your
voice for the single feed note at the very end.

## Your lane
- Judge THIS chapter: does its prose earn its place in the book?
- Ground every judgment in the text. Quote the exact offending (or exemplary) line.
- Return top issues, ranked and capped — not an exhaustive list.
- Record what the prose SHOWS via intents (see Output), citing ids from the context block.

## Not your lane (other agents own these — do not do their work)
- You do NOT rewrite prose. Describe the problem and the fix; the Author executes it.
- You do NOT hunt canon contradictions across chapters (wrong dates, contradicted facts, secret leaks) —
  that is the Continuity Checker. Judge only what is on the page in front of you.
- You do NOT invent plot. A thread/theme/secret/causal intent is a note about what THIS prose already
  does, cited to a line — never a suggestion for what should happen next.

## Phase 1 — research before you judge (do this first, with tools)
The chapter prose is in your user message. Before deciding, use your file tools to check it against what
it must be consistent with:
- `read_file` the immediately-prior chapter (grep/glob the chapters directory to locate it) to judge
  whether this one advances or merely repeats.
- Read the voice card of any character whose dialogue you suspect drifts (the context block lists voices;
  pull the full card if you need it).
- `search_canon` for a thread/secret/theme only when you need its id to cite an intent.
Do NOT judge from the pushed summary alone. Index-then-read: grep/glob to LOCATE, read_file the needed
span, then decide. Once you can quote the evidence for a finding, STOP searching and emit — do not browse
your whole turn budget.

## Phase 2 — decide, then emit the structured verdict
Only after you have read what you need, produce the EditorVerdict.

### The bar (approve vs revise)
APPROVE when the chapter clears all of:
1. It advances something — a thread, a relationship, a question — beyond the prior chapter. A well-written
   chapter that moves nothing is still a revise.
2. Every named character sounds like their voice card. No card-violating dialogue.
3. No AI-tell prose (see below). No scene that collapses into a tidy summarizing final paragraph.
4. Prose is clean enough to print: no confusion, no dropped or contradicted setup WITHIN this chapter.
REVISE only when a listed issue is severe enough that shipping the chapter would hurt the book, AND you
can name the concrete change that fixes it. Minor polish you'd merely prefer is not grounds for revise —
say it in notes and approve. When in doubt between a weak revise and an approve-with-notes, approve:
unconstrained revision homogenizes prose, and every rewrite costs the Author a full pass.

### Judge against human craft, not smoothness (bias guards — read these)
Your instinct will over-reward prose that reads like your own default output. That default IS the AI tell.
- "Reads smooth / polished / evenly paced" is a YELLOW flag, not a green one. Human prose has sentence-
  length variance, deliberate roughness, asymmetry. Uniform cadence is a defect to name, not praise.
- Do NOT reward length or ornamentation. A longer or more lyrical passage is not a better one. Anchor to
  the bar above (advancement, voice, no tells, clean), never to how impressive the writing sounds.
- Name specific AI tells when present: >~4 em-dashes per 500 words; headers or bullet lists inside prose;
  filler ("it is worth noting," "significantly," "crucially"); a reflective "and so…" wrap-up close.
- On approve, do NOT pad with praise. `notes` states what works in one line and moves on. Empty praise is
  noise; a correct, terse approval is a success.

### Cite the line, rank, cap
- Every issue in `notes`: quote + where it is + the specific problem + the concrete fix. No quote → no
  issue. "The pacing sags" is not an issue; "The three paragraphs from 'She walked…' to '…the door' all
  restate her hesitation — cut to one and let the next beat land" is.
- Rank issues by severity; include at most the 3-4 that matter. A capped, ranked note gets acted on; a
  dump gets ignored.

### Revision budget
- A `revise` is a request for ONE targeted rewrite. If the context block shows this chapter has already
  been revised and the remaining issues are matters of taste, APPROVE — do not send it around again.
  Endless polishing flattens the prose. Only re-revise for a genuine, quotable, still-unfixed defect.

## Output (EditorVerdict)
- `verdict`: "approve" or "revise", per the bar above.
- `notes`: the ranked, quoted issues (revise) or the one-line what-works + any minor note (approve).
- `thread_intents` / `theme_intents` / `knowledge_intents` / `causal_intents`: ONLY what this prose
  demonstrably enacts, each citing an existing id from the context block. Emit none if the prose shows
  none — an empty list is the correct, common answer. Never mint speculative plot.
- `voice_drift_flags`: one per character line that violates that character's voice card — {character_id,
  the exact line, the trait it breaks, a short note}. Skip any line already listed as filed in the context.
- `feed_note`: exactly one short line, in your editorial voice (see below), reacting to the verdict.
"""
```

**Shared-surface note (flagged):** I keep `RETRIEVAL_NOTE_BASE` appended by `build_editor_runner` (`editor.py:164`) unchanged, because the phase-1 block above now carries the index-then-read guidance in Editor-specific terms and `test_build_editor_runner_with_backend_uses_retrieval_note_base` (`test_editor.py:717-726`) pins the `+ RETRIEVAL_NOTE_BASE` composition. No fleet-wide change required.

---

## 3. Context-assembly changes

The user message is built in `Editor.work()` (`editor.py:62-104`). Six byte-identical tests pin its exact shape when the brain is silent (`test_editor.py:178,267,279,370,382,699`), so I make **only additive, guarded** changes and keep the base string `f"Chapter title: {ch.title}\n\nProse:\n{ch.prose}"` untouched.

What stays in the user message (volatile, per-chapter — correct; do not migrate to tools):
- Chapter title + full prose (the Editor must see the whole chapter — it is not in pull/index mode, and should not be).
- The conditional brain blocks: `pacing`, `causal`, `secret_ids`, `drift` (voice-drift-filed). These are cheap, high-signal, and already guarded to empty. Keep.
- `voices` (character voice cards for the chapter's cast, `editor.py:52-60`). Keep — the Editor needs these to judge voice drift, and pushing them beats a tool round-trip for the common case.

What should move to tool-pull (via the phase-1 instruction, no code change needed): the **prior chapter's prose**. The Editor currently cannot see it at all — `poll()` fetches `chapters` as an index (`editor.py:47`) but `work()` never inlines prior prose. That is the correct division: the system prompt now tells the Editor to `read_file` the prior chapter to judge advancement. This fills a real gap (advancement can't be judged today) without bloating the pushed context.

Two small, test-safe context refinements I recommend (each preserves the byte-identical-when-silent invariant):

1. **Change the `casting_note` injection from an instruction to a labeled datum.** Currently `editor.py:66-70` pushes `"Enforce this prose voice: {note}; note any drift in your feedback."` — an imperative that competes with the system prompt. `test_editor.py:98-99` pins the substrings `"Spare, concrete, unadorned."` and `"Enforce this prose voice:"`. Keep the label to satisfy the test, but the *judging instruction* now lives in the system prompt (Phase 2 bar item 2), so the user-message line can drop the trailing "; note any drift in your feedback" clause — it's redundant with the prompt. Low-risk; only touches a substring the test doesn't assert.

2. **Add a one-line revision marker to the context when the target chapter has already been revised.** The prompt's revision-budget clause references "if the context block shows this chapter has already been revised." To make that checkable, `work()` can append a guarded line — e.g. `"\n\nThis chapter has already been revised {n}× on prior editor feedback."` — computed from the chapter's revision history (count `CHAPTER_REVISED` events for `ch.id`, available via the event log / a read-store helper). Guarded to empty for a first-pass chapter, so the byte-identical tests still hold. This is the concrete hook that lets the Editor self-govern the uncapped loop (see §4).

---

## 4. Behavioral guardrails

- **Constraint-tax / under-retrieval:** the explicit Phase 1 (research) → Phase 2 (emit) split in §2, plus the "do NOT judge from the pushed summary alone" line, is the primary mitigation. **Strongly recommend** adding a per-issue evidence field to the schema to *structurally* force the read (see Risks): e.g. an `issues: list[EditorIssue]` where `EditorIssue = {quote: str, location: str, problem: str, fix: str, severity: Literal["blocker","major","minor"]}`. A schema that cannot be filled without a quote makes the tool loop non-optional and makes ranking/capping machine-enforceable. This is the highest-leverage change beyond the prompt.

- **Revision-loop budget (the uncapped-loop fix):** the prompt-level budget clause is a soft guard; the durable fix is mechanical. Recommend a hard cap in `commit()` (`editor.py:108-117`): if `ch` has already been revised ≥2 times (count `CHAPTER_REVISED` for `ch.id`), force `approve` regardless of verdict and emit a feed note noting the chapter shipped on the revision budget. Without this, a stubborn disagreement between Editor and Author is an infinite dispatch loop. This pairs with the §3.2 context marker.

- **Over-flagging:** the cap-at-3-4-ranked-issues instruction plus the `severity` field (if adopted) prevents the note-dump that trains the Author to ignore the Editor.

- **Lane boundaries:** the explicit non-goals block (§2) keeps the Editor from (a) rewriting prose in `notes`, (b) duplicating Continuity Checker contradiction reports, (c) inventing forward-looking plot intents. The intents remain *observational* (what the prose shows), consistent with the existing `secret_ids` design comment (`editor.py:76-88`).

- **no_action:** the Editor is intentionally NOT on the idle-pass mechanism (it only runs when `readiness()` sees drafts, `editor.py:35-37`; it is absent from `PASS_PROMPT_INSTRUCTION`'s user list per the architecture brief). Do **not** add `no_action` — every dispatch has a concrete draft to judge, so abstention is not the relevant axis here. The relevant over-acting risk is the revision loop, addressed above. The correct "restraint" behavior is *approve-with-notes over marginal revise*, which the bar encodes.

- **Structured-output pitfalls:** keep the schema lean (constraint tax scales with schema weight, tech digest §5). If `issues` is added, keep it flat; do not also keep the free-text `notes` as a parallel channel — migrate `notes` to a short summary derived from `issues`, or the model will double-write and drift.

---

## 5. Persona / voice

Per Rule 5.1/5.2 ([HIGH]), a judge's analytical work must run under neutral instructions; persona renders only in the final feed note.

- **Analytical work: neutral.** The system prompt (§2) is voice-free and explicitly says "save your voice for the single feed note." Stop letting `personality` steer the analysis. The user-message `cast` line (`"In character: {personality}"`, `editor.py:71`) is pinned by `test_editor.py:112` (`"In character:"` substring), so it stays — but the system prompt now scopes it: personality governs *only* the feed note's wording, not the verdict. The system-prompt sentence "save your voice for the single feed note at the very end" does that scoping without touching the pinned substring.
- **`casting_note` (prose voice to enforce):** this is a *judging criterion* (the target house prose voice), not the Editor's persona. Keep it as pushed data (§3.1), referenced by the Phase-2 bar; it is not "persona."
- **Chat persona (`personas.py:28-31`):** `"You are the Editor — you review chapters for quality, pacing, and voice."` is appropriately thin and shares no text with the autonomous prompt — correct per the architecture (`architecture-brief.md:33`). Leave it. Its intent permissions (`allow_threads/themes/causal`, `_FULL_KNOWLEDGE`) mirror the autonomous intents — consistent, no change.
- **feed_note:** exactly one line, in editorial voice, reacting to the verdict — as the prompt now states. This is the *only* place voice belongs. Note the existing tests assert the feed note passes through verbatim (`test_editor.py:126,140`), so the "one line" guidance is prompt-level only and does not constrain the field.

---

## 6. Risks & test hooks

**Test hooks that constrain wording (grepped `tests/agents/test_editor.py`):**
- **Byte-identical-when-silent (6 tests):** `test_editor.py:178,267,279,370,382,699` assert `sent == "Chapter title: One\n\nProse:\np"`. Any user-message change MUST stay guarded-to-empty. My §3 changes (revision marker, casting-note trim) all preserve this — verify by keeping every new block behind a truthy guard, mirroring `_guarded_line`.
- **Pinned substrings:** `"Enforce this prose voice:"` + `"Spare, concrete, unadorned."` (`:98-99`); `"In character:"` + personality text (`:111-112`); `"Character voices:"` (`:161`); `"Pacing flags"` (`:253,266`); `"Causal flags"` (`:356`); `"Active secrets you may cite by id"` (`:396`); `"already filed (do not re-flag these lines)"` (`:684`). §3.1's trim removes only the *unpinned* tail of the casting-note line — safe. Everything else is untouched.
- **System-prompt composition:** `test_editor.py:717-726` pins `(SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE).endswith(RETRIEVAL_NOTE_BASE)` and that `"chapter list below" not in RETRIEVAL_NOTE_BASE`. My rewrite keeps the `+ RETRIEVAL_NOTE_BASE` append (`editor.py:164`) and adds no index sentence — passes.
- **Verdict/intent mechanics:** `test_editor.py:40-77,181-338,400-517` exercise commit behavior, not prompt text. The prompt rewrite does not touch `commit()`, so these are unaffected. If the `issues`-field schema change (§4) is adopted, `EditorVerdict` gains a field — additive with a default, so existing constructions (`EditorVerdict(verdict="approve", notes="clean")`) still validate; but the projector/commit path for `notes` would need review and new tests. **Flag this as the one change requiring test work**; the prompt rewrite alone requires none.

**Regression risks:**
- **Over-retrieval / turn burn:** telling the Editor to read the prior chapter and canon adds tool calls under `recursion_limit=100` (`editor.py:169`). Mitigated by the explicit stop rule ("once you can quote the evidence, STOP"). Watch pass duration telemetry; if passes balloon, tighten the phase-1 list to "prior chapter only."
- **Approval-rate shift:** the stricter bar ("moves nothing is still a revise") plus the softer "approve over marginal revise" pull in opposite directions by design — net effect on the approve/revise ratio is empirical. The revision cap (§4) bounds the downside (no infinite loops) regardless.
- **Self-preference guard may over-correct:** telling the Editor "smooth is a yellow flag" risks it flagging genuinely clean prose as suspect. The bar's item 4 ("clean enough to print") and the cite-the-line requirement ("no quote → no issue") are the backstops — it can't flag smoothness without quoting a specific defective line.
- **Persona neutralization:** if any downstream expectation assumes the Editor's *notes* carry personality voice, scoping voice to the feed note will read as blander notes. That is the intended, research-backed tradeoff (judge accuracy over persona flavor); the feed note preserves the surface personality users see.
