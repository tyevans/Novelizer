# Structure Analyst — prompt & context redesign proposal

The Structure Analyst is the fleet's hardest calibration problem: a numeric JUDGE whose per-chapter scores feed a **delta detector** (`detect_sag_spike`) that fires the Editor's pacing flags. Its current prompt gives a bare 0.0–1.0 scale with no rubric, scores from a 400-char excerpt, and never lets the agent see its own prior scores — so scale drift between passes silently manufactures false sag/spike alarms downstream.

---

## 1. Diagnosis

**D1 — No rubric anchors; the scale is undefined.** `SYSTEM_PROMPT` (structure_analyst.py:12-16) offers only `tension score from 0.0 (slack) to 1.0 (peak intensity)` plus four example labels. There is no description of what a 0.2 vs 0.5 vs 0.9 chapter *looks like*. A judge with an unanchored scale drifts with mood and context — exactly the calibration failure behavior digest Rule 4.3 warns against ("well-specified rubrics + golden examples").

**D2 — Anchorless absolute scorer feeding a global-relative delta detector (the core bug).** `work()` (structure_analyst.py:48-56) shows the LLM only the current batch of ≤5 *unscored* chapters; it never sees the scores it assigned on prior passes. But `detect_sag_spike` (sag_spike.py:16-24) flags any chapter that deviates ≥0.3 **from the mean of the entire score list**, and the Editor consumes those flags over *all* scores (`"scores": await self._read.list_structure_scores()`, editor.py:44; `pacing_flags_note(ctx["scores"], …)`, editor.py:73). So the Analyst is asked for a per-batch judgment on an absolute scale it can't see, and a global-mean-relative detector turns any between-pass scale drift into phantom "sag"/"spike" alarms. This is THE highest-impact defect: consistency *across chapters and across passes* is a hard requirement, and nothing in the current design supports it.

**D3 — `prose[:400]` truncation: it scores openings, not arcs.** `listing = "\n\n".join(f"Chapter id:{c.id} '{c.title}': {c.prose[:400]}" …)` (structure_analyst.py:52). Tension is a whole-chapter property — where the peak sits, whether the chapter ends on a hook or a lull. Judging it from the first ~400 characters (the opening beat) is structurally wrong, and it repeats the Character Keeper `prose[:300]` truncation-starvation bug. The agent now *has* canon file tools (the backend branch, structure_analyst.py:83-93) but the prompt never tells it to read a chapter in full.

**D4 — Constraint tax: tools are attached but dead.** The runner passes `response_format=StructureAnalystOutput` with `tools` and `RETRIEVAL_NOTE_BASE` appended (structure_analyst.py:89-93), yet `SYSTEM_PROMPT` frames the work as `You read recent unscored chapters` as if they were fully inline. With a response schema present and no instruction to research first, the model is biased to emit the structure immediately and skip the tool loop (tech digest Rule 5, constraint tax). Result: it scores off the truncated blurbs and the pull tools go unused.

**D5 — Verbosity bias unguarded.** Nothing tells the agent not to score longer/ornate chapters as tenser (behavior Rule 4.2). Latent today because `prose[:400]` hides length — but the moment D3 is fixed (read full chapters), verbosity bias goes live and must be pre-empted in the prompt.

**D6 — `pacing_label` is unconstrained free text.** `pacing_label: str = ""` (schemas.py:179, mirrored in models.py:166 and the event payload). The four labels ("rising"/"climax"/"lull"/"steady") exist only as prose examples; the LLM can emit "moderately brisk, downbeat" and every consumer (TUI shape tab, feed) just renders whatever string arrives. Not aggregatable, not filterable, synonym-drifts over a long book.

**D7 — Minimal-but-unstructured prompt.** The prompt is a 5-line prose blob with no labeled sections (tech digest Rule 2.2 wants `## Role / ## Rubric / ## How to work / ## Output`). It's short, but not *high-signal* short — it omits the guidance that actually changes behavior (rubric, calibration, read-in-full) while spending lines restating the schema the `response_format` already enforces.

**D8 — Lane is under-scoped for its native axis.** The Analyst owns the tension *curve*, but its remit stops at per-chapter numbers. Cross-chapter shape pathologies — repetitive beat shapes, premature resolution (behavior Rules 1.5/1.6) — are currently detected by *no one*: the Editor judges a single draft in isolation (editor.py:62-65), and staleness is a separate deterministic id-based path (`stale_threads_note`, context.py:11-26) routed to the Author. The sequence's *shape* is exactly the Analyst's lane and explicitly not the Editor's. See §4 for the position I take.

**Non-issues (leave alone):** persona is already minimal (empty by default; injected only in tests), correct for a judge per behavior Rule 5.1. The idle-pass / `no_action` mechanism (base.py:25-29) is deliberately absent and *should stay* absent — see §4.

---

## 2. Proposed system prompt

Paste-ready. Replaces `SYSTEM_PROMPT` in structure_analyst.py:12-16. `RETRIEVAL_NOTE_BASE` continues to be appended by the runner (structure_analyst.py:89), so tool mechanics (ls/read_file/grep/glob/search_canon, cite-ids) are not re-explained here.

```python
SYSTEM_PROMPT = """You are the Structure Analyst for a living, continuously-written novel. You are a JUDGE: for each chapter you are handed, you assign one tension score and one pacing label. You do not rewrite prose, flag craft problems, or manage threads — that work belongs to the Editor and the story brain.

## Your one job
Score narrative tension on a fixed 0.0-1.0 scale and name the chapter's pacing. Tension is a property of the WHOLE chapter's arc -- where its peak sits, what pressure it opens and closes on -- not of its first paragraph and not of its length. A long, ornate chapter is not tenser than a short, spare one; score the pressure, not the word count.

## Tension rubric -- anchor every score to this
- 0.0-0.2  slack / lull: reflection, transition, downtime. No active want is pressed, nothing escalates; the scene could be cut with little plot loss.
- 0.3-0.4  rising / low: a want or question is on the table; mild friction, setup, small complications. Stakes named but not yet pressing.
- 0.5-0.6  steady / mid: active conflict in motion; an obstacle meaningfully resists; consequences accumulate. The reader is pulled, but nothing ruptures.
- 0.7-0.8  high: a decisive confrontation, reversal, or revelation lands; something changes that cannot be undone; real cost is paid.
- 0.9-1.0  climax / peak: the central pressure the arc was building toward finally breaks. Maximum, irreversible stakes.
Pick the band matching the chapter's strongest SUSTAINED pressure, then place it within that band.

## Calibrate across chapters -- the hard part
Your scores are compared against the running average of ALL earlier scores; any chapter sitting 0.3 or more from that average is flagged "sag" or "spike" for the Editor. So a 0.6 in chapter 30 must mean the same intensity as a 0.6 in chapter 5. Before scoring, study the chapters you have ALREADY scored (their scores are listed for you) and re-read one or two of them in full, then rate the new chapters against them on the SAME scale. Drift between passes manufactures false sag/spike alarms -- inconsistent scaling is a real failure, not rounding noise.

## How to work -- research first, then score
1. Write a short todo list of the chapters to score.
2. For EACH chapter, read it IN FULL with read_file (use grep/glob to locate it) -- never score from a title or an excerpt.
3. Re-read the nearest already-scored chapters as calibration anchors.
4. Only after you have read the chapters, emit the structured scores.
Score EXACTLY the chapters you were given: one entry each, chapter_id matching exactly, no more and no fewer. Every chapter gets a score even when slack -- a thin or short chapter scores LOW, it is never skipped.

## Pacing label
Choose the single label that fits the chapter's motion: lull, rising, climax, falling, or steady.

## Feed note
After scoring, write one short feed_note in your own voice summarizing the SHAPE you saw this pass -- a run of steady chapters, a spike, a stretch that sags. If the SEQUENCE is going wrong (the same beat shape repeating chapter after chapter, or a thread resolving before it earned its payoff), name it here as an observation for the team. Stay on the curve and the shape of the sequence; do not critique individual sentences or ask for revisions -- that is the Editor's lane."""
```

Rationale keyed to research: labeled sections (tech Rule 2.2); rubric with concrete visual anchors (behavior Rule 4.3); explicit cross-pass calibration tied to the *actual* detector mechanics (fixes D2); research-then-emit ordering + read-in-full to defeat the constraint tax and D3/D4 (tech Rule 5.2, 3.x); verbosity guard inline (behavior Rule 4.2); "score, don't skip" so no chapter is left permanently un-scored and re-batched; `write_todos` planning nudge (tech Rule 1.9); lane fence naming the Editor (behavior Rule 2.1); shape observations routed to `feed_note` only (§4).

---

## 3. Context-assembly changes

The fix for D2/D3 lives mostly in `work()` (structure_analyst.py:48-56). Change the user message from *"truncated prose of the new batch"* to *"an index of the new batch + a calibration table of recent scored chapters,"* and let the agent pull full prose via tools (index-then-read, tech Rule 3.x).

**Push (small, high-signal, in the user message):**
- The unscored batch as an INDEX only: `id` + `title` (+ optional length in words). No prose — the agent reads it in full via `read_file`.
- A **calibration table**: the last ~5 already-scored chapters as `id · title · tension · pacing_label`. This is the anchor that makes cross-pass consistency possible; it is small and stable enough to be cache-friendly.

**Pull (via tools, on demand):** the full prose of each batch chapter, and re-reads of one or two calibration anchors.

Sketch (`poll()` gains the scored anchors; `work()` rebuilds the message):

```python
_CALIBRATION_ANCHORS = 5

async def _recent_scored_anchors(self) -> list:
    scores = {s.chapter_id: s for s in await self._read.list_structure_scores()}
    chapters = await self._read.list_chapters()
    scored = [c for c in chapters if c.id in scores]
    return [(c, scores[c.id]) for c in scored[-_CALIBRATION_ANCHORS:]]

async def poll(self) -> dict:
    return {
        "unscored": await self._unscored_recent_chapters(),
        "anchors": await self._recent_scored_anchors(),
    }

async def work(self, ctx: dict) -> StructureAnalystOutput | None:
    chapters = ctx["unscored"]
    if not chapters:
        return None
    index = "\n".join(f"- id:{c.id} '{c.title}'" for c in chapters)
    if ctx["anchors"]:
        table = "\n".join(
            f"- id:{c.id} '{c.title}': tension {s.tension:.2f} ({s.pacing_label})"
            for c, s in ctx["anchors"]
        )
        anchor_block = f"\n\nAlready scored (your calibration anchors):\n{table}"
    else:
        anchor_block = ""
    cast = self._guarded_line("In character", self.personality)
    msg = (
        f"Score these chapters. Read each one IN FULL with your file tools before scoring:\n{index}"
        f"{anchor_block}{cast}"
    )
    result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
    return result.get("structured_response")
```

Notes: keeps the `"unscored"` key and the `_guarded_line("In character", …)` line (both pinned by tests — see §6). `commit()` is unchanged; its existing extra-id drop (structure_analyst.py:61-66) still guards against the agent scoring an anchor by mistake. The batch/readiness logic (`_BATCH_SIZE=5`, `_READINESS_DIVISOR=3`) is unchanged.

**Shared-surface note:** no change to `RETRIEVAL_NOTE_BASE` needed — it already names the tools and the cite-ids rule, which is all this agent requires.

---

## 4. Behavioral guardrails

**Constraint tax (top risk).** The `response_format` schema biases the model to finalize early and skip tools (D4). Mitigations, all in the proposed prompt: explicit RESEARCH-then-EMIT step order; "read each chapter IN FULL … never score from a title or an excerpt"; the `write_todos` planning nudge. I deliberately do **not** add a per-score `evidence: list[str]` citation field: tension is a diffuse whole-chapter property, not a single quotable line, so a citation field would add constraint-tax weight (tech Rule 5, "keep the schema lean") for little grounding value. The grounding mechanism here is the *mandatory full read + the calibration table*, not a line cite.

**Verbosity bias.** Pre-empted inline ("A long, ornate chapter is not tenser than a short, spare one"; "score the pressure, not the word count"). Behavior Rule 4.2.

**Scale-drift / self-consistency.** Named as a first-class failure in the prompt and made actionable by the calibration table (§3). This is the guardrail that most directly protects the downstream detector.

**Over-retrieval / turn burn.** Stop rule is implicit in "read the batch + one or two anchors, then emit"; the calibration re-read is capped ("one or two"), not "all prior chapters." Under `recursion_limit=100` a 5-chapter batch plus a couple anchor reads is comfortable.

**No idle-pass — deliberate.** Unlike the keeper/architect/checker (base.py:25-29 `PASS_PROMPT_INSTRUCTION`), the Analyst should NOT get a `no_action` path. Its trigger is already a concrete, checkable delta — `readiness()` only wakes it when unscored chapters exist (structure_analyst.py:39-43) — and its unit of work is deterministic: score exactly these N. Abstention framing (tech Rule 6) doesn't apply per-chapter; a chapter that "feels unscoreable" must still get a (low) score or it stays unscored and is re-batched forever. The prompt says exactly this ("Every chapter gets a score … never skipped").

**Lane boundaries — position on remit expansion (D8).** I take the position: **expand modestly along the sequence/shape axis, advisory-only, through `feed_note` — do NOT expand the numeric schema or per-chapter flagging.**
- *In lane:* the tension curve and the *shape of the sequence* (repetitive beat shapes, premature resolution). This is the Analyst's native axis and explicitly not the Editor's — the Editor judges one draft's craft in isolation (editor.py:62-65); nobody currently watches the shape *across* chapters. Routing this to `feed_note` gives the team the signal with zero schema churn and zero constraint-tax cost.
- *Out of lane (named as non-goals in the prompt):* rewriting prose or judging sentences (Editor); requesting revisions / emitting revise signals (Editor); hunting stale threads by id (deterministic `stale_threads`/brain → Author, context.py:11-26); retcons. This respects behavior Rules 2.1 (explicit non-goals), 2.3 (don't let the specialist co-write), 2.4 (distinct reasoning stance).
- *Future, not now:* if shape observations prove useful, graduate them from free-text `feed_note` to a structured `pacing_shape_note` brain surface analogous to `pacing_flags_note` (context.py:29-37), consumed by the Editor/Author. Flagged as a follow-up so this redesign stays schema-stable and low-risk.

**Output hygiene.** "one entry each, chapter_id matching exactly, no more and no fewer" reinforces the commit-time guard (structure_analyst.py:61-66) at the source.

---

## 5. Persona / voice

Keep persona **light** — correct for a judge (behavior Rule 5.1: heavy persona degrades discrimination/accuracy). Concretely:
- The analytical scoring runs under **neutral instructions** (the rubric); personality enters only as the injected `In character:` line in the user message (structure_analyst.py:53) and is expressed **only in the one `feed_note`** (behavior Rule 5.2 — do the work plainly, render the voice at the end). The prompt already isolates it: "write one short feed_note in your own voice."
- The chat persona (personas.py:45-47, `"you read the manuscript's tension curve and pacing"`) is thin and consistent with this — leave it. It correctly shares no analytical text with the autonomous prompt (architecture brief §chat surface).
- Do not let personality bleed into the numeric judgment; a "clinical pacing critic" and a "breathless dramaturg" must produce the *same* score for the same chapter. The rubric is the invariant; the voice is cosmetic.

---

## 6. Risks & test hooks

**Tests that pin wording/shape (grepped `tests/agents/test_structure_analyst.py`, `test_llm.py`):**
- `test_build_structure_analyst_runner_with_backend_uses_retrieval_note_base` (test_structure_analyst.py:159-168): asserts `"chapter list below" not in RETRIEVAL_NOTE_BASE` and `(SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE).endswith(RETRIEVAL_NOTE_BASE)`. **Safe** — proposal keeps appending `RETRIEVAL_NOTE_BASE` and puts no such phrase in `SYSTEM_PROMPT`. No test asserts any substring of `SYSTEM_PROMPT` itself, so the rewrite is unconstrained in content.
- `test_work_prompt_includes_personality_when_set` (test_structure_analyst.py:109-118): asserts the personality string appears in the sent message. **Safe** — the new `work()` retains `self._guarded_line("In character", self.personality)`.
- `test_work_returns_none_and_commit_is_noop_when_no_unscored_chapters` (83-91): reads `ctx["unscored"]`. **Safe** — `poll()` keeps the `"unscored"` key; adding `"anchors"` is additive.
- `test_run_once_emits_a_structure_scored_event_per_chapter`, `_drops_score_for_unrequested_chapter_id`, `_emits_agent_remarked…`, `_propagates_validation_error…` (56-141): all use `FakeRunner`, which ignores message content and returns canned output. **Safe** — the message-assembly change is invisible to them; `commit()` is untouched.
- `test_readiness_*` (31-53): unchanged readiness math. **Safe** — `_unscored_recent_chapters`, `_BATCH_SIZE`, `_READINESS_DIVISOR` all preserved.

**`pacing_label` enum (D6) — recommended, with a replay caveat.** Constrain the **output** schema only:
```python
from typing import Literal
PacingLabel = Literal["lull", "rising", "climax", "falling", "steady"]
# schemas.py ChapterScore.pacing_label: PacingLabel = "steady"
```
This covers every label used in tests ("lull", "climax", "steady", "rising" — test_structure_analyst.py:50,62-63,76,99) plus "falling". **Do NOT** make `StructureScore` (models.py:166) or the `AnnotationStructureScored` event payload a `Literal`: this is an event-sourced store (the projector replays historical `annotation.structure_scored` events, structure_analyst.py:68-71), and a strict type would reject any pre-existing free-text label on replay. Keep the read/event models as `str`; validate the enum at emit time on the LLM output only. If you adopt the enum, update the prompt's label list to match exactly (already aligned).

**Regressions to watch:**
- *Turn/latency cost:* reading 5 chapters in full + 1-2 anchors per pass is heavier than the old inline blurb. Bounded by `recursion_limit=100` and the "one or two anchors" cap; acceptable, but worth a telemetry eye on Analyst pass duration after rollout.
- *Cold start:* with no prior scores the calibration table is empty (`anchor_block == ""`); the first pass rates on the rubric alone. Expected and correct — anchors accrue as the book grows.
- *Enum brittleness:* if a future prompt tweak adds a label not in the `Literal`, the LLM output fails validation. Keep the prompt's label list and the `Literal` in lockstep; the existing `test_commit_propagates_validation_error…` (121-141) already proves the fail-fast path works.

**Recommended new test coverage (not blocking):** assert the calibration table appears in the message when scored chapters exist; assert an anchor chapter id is never re-emitted as a score; a property test that a fixed chapter's score is invariant to `personality` (persona must not move the number).
