# Behavioral Design Digest for the Novelizer Agents

Evidence-graded. **[HIGH]** = multiple sources or strong empirical study; **[MED]** = single study/serious practitioner; **[LOW]** = informal/anecdotal but widely reported.

---

## 1. Long-horizon narrative coherence

**Rule 1.1 — Middle-of-story is the danger zone; trigger extra checking there. [HIGH]** In a 19-subtype consistency-bug taxonomy, errors cluster *around the middle of narratives* and accumulate roughly linearly with length; the two dominant failure classes are **Timeline & Plot Logic** and **Factual & Detail Consistency** (entity tracking + temporal reasoning). (Lost in Stories, arxiv.org/html/2603.05890v1) → Continuity Checker and Retconner should weight scrutiny toward mid-book chapters and toward dates/durations/causality and names/quantities/appearances specifically, not vibes.

**Rule 1.2 — Uncertainty predicts error; use it as an early-warning trigger. [MED]** Error-bearing passages show ~19% higher token entropy than clean text — models "choose incorrectly when uncertain." (Lost in Stories) → Where the Author flags its own low-confidence spans (hedged prose, vague placeholders), that's exactly where Continuity/World agents should pull canon and verify.

**Rule 1.3 — Condense the middle, keep recent + upcoming verbatim. [HIGH]** StoryWriter's ReIO mechanism *dynamically summarizes and condenses historical context, retaining only info pertinent to the current sub-event*; empirically a sliding window over `[2, k-1]` (simplify the center, preserve the edges) is optimal under ~15k tokens. (StoryWriter, ar5iv.labs.arxiv.org/html/2506.16445) → Author's context assembly should give full fidelity to the immediately-prior chapter and the near-future plan, and compressed summaries for the middle — not a flat dump.

**Rule 1.4 — Outline as an explicit event graph with character associations, not prose synopsis. [HIGH]** Coherent systems track events with timing/location/relationships and validate each new event for plausibility before it enters the outline (StoryWriter EventSeed→EventValidator; also KG-based systems combat "theme drift due to limited contextual retention"). (StoryWriter; Long Story Generation via KG, arxiv.org/html/2508.03137) → Structure Analyst should maintain threads as structured intents (open/advanced/resolved) so "forgotten thread" becomes a queryable state, not a memory feat.

**Rule 1.5 — Separate story order from event order to kill repetitive beats. [MED]** StoryWriter's Non-Linear Narration decomposes events into sub-events then *distributes them non-chronologically across chapters* while preserving causal logic — an explicit antidote to every-chapter-feels-the-same monotony. (StoryWriter) → Structure Analyst/Muse can propose deferring, interleaving, or seeding payoffs early rather than resolving each beat in place.

**Rule 1.6 — Name the drift modes in the prompt.** Documented long-form failure modes to explicitly instruct against: repetition, hallucination, topic/theme drift, and "tedious plots with incoherent logic" from lost outline memory. (StoryWriter; KG paper) Naming the failure gives the agent a concrete thing to self-check against.

---

## 2. Role differentiation (keeping agents in their lanes)

**Rule 2.1 — Give every agent explicit non-goals, not just goals. [HIGH]** Homogeneous agent pools suffer *premature convergence and unfair judging*; differentiation must be engineered via "minimal identity scaffolding" and distinct reasoning biases, or agents collapse into doing the same job. (Behavioral Differentiation, arxiv.org/pdf/2604.00026; Emergent Coordination, arxiv.org/pdf/2510.05174) → Each prompt should carry a short "you do NOT do X — that's Agent Y's job" clause (e.g., Continuity Checker *finds* contradictions but does NOT rewrite; Retconner repairs but does NOT invent new plot).

**Rule 2.2 — Draft / audit / verify must be different agents. [HIGH]** The reliable pattern is "one adapter drafts, another audits, a third independently verifies," keeping each prompt short and each verdict auditable; multi-agent separation reduces hallucination vs. a single model self-checking. (Role-specialization survey, arxiv.org/html/2402.03578v1) → Preserve the Author↔Editor↔Continuity split; never let the Author grade its own chapter inline.

**Rule 2.3 — More agents is not free; specialists can be held back by the committee. [MED]** "Multi-Agent Teams Hold Experts Back" (arxiv.org/pdf/2602.01011) — a strong specialist's output degrades when averaged into a group. → The Author (the expert generator) should own final prose; other agents advise via structured intents, they don't co-write sentences.

**Rule 2.4 — Distinct reasoning stance per lane.** Role prompts bias reasoning style ("skeptic," "experimentalist"); complementarity emerges when each agent has a genuinely different objective function. (survey) → Muse = divergent/generative (no consistency duty); Editor = convergent/evaluative; World Architect = constraint-additive. Don't let Muse start policing continuity or it stops generating.

---

## 3. Editor / critic quality

**Rule 3.1 — Force cite-the-line grounding. [HIGH]** The one method that reliably improves LLM critique is grounding each judgment in specific textual evidence (ConStory-Checker "grounds judgments in textual evidence"; RL-trained critics beat generic ones). (Lost in Stories; Teaching LMs to Critique via RL, arxiv.org/pdf/2502.03492) → Editor/Continuity output should be *quote + location + specific problem + concrete fix*, never "the pacing feels off." No quote → no flag.

**Rule 3.2 — Rank issues; cap the count. [MED]** Rubric-based, ranked critique aligns better with humans than unranked lists; ungoverned critics over-flag. (Rubric-Based Evals, medium.com/@adnanmasood) → Editor emits *top N ranked* issues per chapter, severity-tagged, rather than an exhaustive dump — over-flagging trains the Author to ignore it.

**Rule 3.3 — Budget against over-editing.** Self-refine helps in early iterations (~20% gains over 2–4 rounds) but has sharply diminishing/negative returns after; unconstrained revision homogenizes creative text. (Self-Refine findings, learnprompting.org; Pride and Prejudice: LLM Self-Bias, arxiv.org/pdf/2402.11436) → Cap Editor→Author revision loops (2–3 passes) and require the Editor to justify *why this revision improves*, not just churn.

---

## 4. LLM-as-judge biases (the Editor/Continuity agents ARE judges)

**Rule 4.1 — Self-preference bias is large and directional. [HIGH]** A judge scores its own family's / own-style outputs **10–25% higher**. (Self-Preference Bias, arxiv.org/pdf/2410.21819) → The Editor will over-approve prose that matches its own default style — which is exactly the AI-tell style. Prompt the Editor to judge *against distinctive human craft*, and treat "reads smooth/polished" as a yellow flag, not a green one.

**Rule 4.2 — Verbosity bias inflates scores for longer text. [HIGH]** Long answers get higher scores even when they don't make sense. (LLM-Judge Bias 2026, futureagi.com) → Editor must not reward chapters for length/ornamentation; anchor evaluation to a fixed rubric (thread advancement, character consistency, scene purpose).

**Rule 4.3 — Anchor to a rubric; randomize comparison order. [HIGH]** The named judge biases (position, verbosity, self-preference, format, calibration drift) are mitigated by *well-specified rubrics, randomized ordering, and golden examples*. (5 Biases, sebastiansigl.com; Deepchecks) → Give every evaluative agent an explicit rubric with a few worked examples of good vs. bad, rather than open-ended "is this good?"

---

## 5. Persona / voice prompting

**Rule 5.1 — Personas help alignment/voice, hurt factual accuracy — split the duty. [HIGH]** "A Helpful Assistant" personas do NOT improve objective task performance and can degrade it; the consistent tradeoff is *personas raise perceived expertise + structure but reduce clarity/directness/accuracy*. Personas help most on advisory/alignment/format tasks, hurt on knowledge/discrimination tasks. (Helpful Assistant, arxiv.org/html/2311.10054v3; When Does Persona Prompting Help, arxiv.org/html/2605.29420v1; PRISM, arxiv.org/pdf/2603.18507) → Load heavy persona ONLY on the Author (voice generation) and Muse. Keep persona *light* on the fact-checking agents (Continuity, Retconner, World Architect, Structure Analyst) — their job is accuracy, and a thick persona degrades it.

**Rule 5.2 — Persona for the in-character feed note, plain instruction for the task. [MED]** Since persona injection reduces instruction-following precision, structurally separate the two: do the analytical work under neutral instructions, then render the *feed note* in-character. → Prompt shape: "Analyze X per these rules. Then write one feed note in your voice." Don't let the persona bleed into the structured-intent extraction.

**Rule 5.3 — Persona effects are unpredictable per-item. [MED]** The same persona helps some questions, hurts others, with no reliable pattern. (Helpful Assistant) → Don't rely on persona to *improve* correctness; rely on it only for surface voice differentiation, which is its legitimate use here.

---

## 6. Avoiding "AI tells" in prose

**Rule 6.1 — Ban the punctuation/structure cluster explicitly. [LOW–MED]** AI prose runs ~3–5 em-dashes per 500 words vs. <1 for humans; the broader cluster includes section headers in prose, bullet lists where prose belongs, and throat-clearing. (Predictable Rhetoric, medium.com/@mgibson_99548; Why AI Loves a Dash, plaintextconverter.com) → Author prompt: hard cap em-dashes, forbid headers/bullets inside chapter prose, name banned filler ("it is worth noting," "significantly," "crucially," "leverage," "plethora").

**Rule 6.2 — Attack "too polished/symmetrical." [MED]** The strongest human-detectable tell is uniformity — prose that is too smooth, balanced, and evenly cadenced. (Predictable Rhetoric) → Instruct the Author toward deliberate sentence-length variance, occasional roughness/fragments, and asymmetry. This is also where self-preference bias (4.1) bites: the Editor's instinct rewards the polish that reads as AI.

**Rule 6.3 — Forbid the tidy summarizing final paragraph and the self-contained chapter. [MED]** A named scene-level tell is every chapter resolving into a neat mini-arc with a wrap-up closing paragraph. → Author prompt: end chapters on tension/open question where the arc demands it; ban the reflective "and so…" summary close. This dovetails with Rule 1.5 (non-linear beats) and directly attacks *premature resolution*.

**Rule 6.4 — Maintain a rotating banned-word / recently-used-phrase list. [MED]** Overused-vocabulary tells are lexical and repeat within a work. → Structure Analyst or a lightweight ledger can surface the Author's recent crutch words so the next chapter avoids them — turns "voice flattening" into a tracked, correctable signal.

---

## 7. Proactive vs. reactive (WHEN to act)

**Rule 7.1 — The dominant failure is over-acting (false alarms), not silence. [HIGH]** Across models, the primary error is *providing as much assistance as possible instead of necessary assistance*; base models sit at ~52–65% false-alarm rate, dropping to ~36–50% only with a trained gate. (Proactive Agent, arxiv.org/html/2410.12361v3) → Every agent's default should bias toward the `no_action` pass. Emitting a note must clear a bar; cadence alone is not a reason to speak. (Aligns with existing watermark/idle-pass mechanism.)

**Rule 7.2 — Act on genuine state-change signals, not the clock. [HIGH]** Proaction is a function of events + state deltas `Pt = f(Et, At, St)`, and the agent must be able to output "nothing." (Proactive Agent) → Gate each agent on canon deltas since its last action (new chapter, new intent, changed sheet) — a watermark — not on "it's my turn." "Switching software but doing nothing else" = the anti-pattern: new event, no *meaningful* change, so stay silent.

**Rule 7.3 — A separate gate beats self-judgment. [MED]** The reward/gatekeeper model (91.8% F1 vs. humans) that decides act/don't is *separate* from the agent proposing the task. (Proactive Agent) → If feasible, the "should I speak?" decision should read the same act/false-alarm/missed/silent quadrants explicitly, and the four outcomes are worth naming in the prompt so the agent optimizes for correct-silence, not activity.

**Rule 7.4 — Freshness windowing. [MED]** Recent activity (short window) captures workflow continuity while filtering stale noise; use freshness metadata. (Proactive Agent research; ProAgentBench, arxiv.org/html/2602.04482v1) → Weight triggers toward recent canon deltas; decay old unaddressed signals rather than re-firing on them.

---

### Cross-cutting synthesis for the 8 rewrites
- **Generators** (Author, Muse): heavy persona, divergence encouraged, own the final text, self-check against named drift + AI-tell lists, no consistency-policing duty.
- **Judges** (Editor, Continuity, Structure): light persona, rubric-anchored, cite-the-line, ranked + capped output, bias-aware (reject "smooth = good"), revision loops budgeted.
- **Canon-keepers** (Character Keeper, World Architect, Retconner): minimal persona (accuracy-first per Rule 5.1), structured-intent output, explicit non-goals so they don't rewrite prose.
- **Everyone**: default to `no_action`; speak only on a real canon delta past a watermark; render the in-character voice only in the final feed note, after the analytical work is done under neutral instructions.

Every agent prompt should carry three explicit blocks: **your lane** (goal), **not your lane** (non-goals, naming the sibling agent), and **when to stay silent** (the delta/watermark bar).
