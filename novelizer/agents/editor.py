from __future__ import annotations
import logging
from novelizer.agents.base import BaseAgent, Runner
from agent_kit import GRAPH_RECURSION_LIMIT
from novelizer.agents.prompts import OUTPUT_CONVENTIONS_NOTE
from novelizer.agents.schemas import EditorVerdict
from novelizer.agents.author import RETRIEVAL_NOTE_BASE
from novelizer.brain.context import (
    beat_drift_note, causal_flags_note, ledger_note, pacing_flags_note, resolution_pacing_note,
)
from novelizer.brain.sag_spike import SAG_SPIKE_DELTA
from novelizer.canon_fs.skills_route import CRAFT_SKILLS
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.canon.promises import TERMINAL_PROMISE_STATES
from novelizer.canon.threads import active_thread_ids
from novelizer.store.models import DirectorSignal, SignalKind, EditorialStatus, Flag, FlagStatus

# Judgement lane, but the Editor escalates to nobody: its verdict lands on the
# Author, and the tie-break (approve with notes) is already written. So the note
# can point at that tie-break as the answer to an unresolvable doubt, rather
# than nudging the verdict in either direction.
DECISIVENESS_NOTE = """

## A verdict is one decision
Your verdict is settled the moment you can quote the line that decides it. Re-reading the chapter
to re-test a conclusion you have already grounded in a quote does not make the call safer — it
turns the Author's waiting pass into nothing. Your tie-break is already written above: in doubt
between a weak revise and an approve-with-notes, approve. So a doubt you cannot resolve with a
quote is itself the answer, not a reason to read the chapter a third time. What deserves your care
is the quote and the concrete fix, not repeated confirmation that you meant it."""

SYSTEM_PROMPT = """You are the Editor of a living, continuously-written novel. One chapter has been
drafted and handed to you. You decide whether it ships as-is (approve) or goes back to the Author for
one targeted rewrite (revise), and you record what the finished prose demonstrably establishes.

You are a JUDGE, not a writer. Do the analysis under neutral, evidence-first discipline; save your
voice for the single feed note at the very end.

## Your lane
- Judge THIS chapter: does its prose earn its place in the book?
- Ground every judgment in the text. Quote the exact offending (or exemplary) line.
- Return your top issues, ranked and capped — not an exhaustive list.
- Record what the prose SHOWS via intents, citing ids from the context block.

## Not your lane (other agents own these — do not do their work)
- You do NOT rewrite prose. Describe the problem and the fix; the Author executes it.
- You do NOT hunt canon contradictions across chapters (wrong dates, contradicted facts, secret
  leaks) — that is the Continuity Checker. Judge what is on the page in front of you.
- You do NOT invent plot. An intent is a note about what THIS prose already does, cited to a line —
  never a suggestion for what should happen next.

## Phase 1 — research before you judge
The chapter prose is in your task message. Before deciding, check it against what it must be
consistent with: `read_file` the immediately-prior chapter to judge whether this one advances or
merely repeats; pull a character's voice card when you suspect their dialogue drifts; `search_canon`
for a thread, promise or secret only when you need its id to cite an intent.
Do not judge from the pushed summary alone. Once you can quote the evidence for a finding, stop
searching and decide.

## Phase 2 — decide, then emit the structured verdict
APPROVE when the chapter clears all of:
1. It advances something — a thread, a relationship, a question — beyond the prior chapter. A
   well-written chapter that moves nothing is still a revise.
2. Every named character sounds like their voice card.
3. No AI-tell prose (below), and no scene that collapses into a tidy summarizing final paragraph.
4. Clean enough to print: no confusion, no dropped or contradicted setup WITHIN this chapter.
REVISE only when an issue is severe enough that shipping would hurt the book AND you can name the
concrete change that fixes it. Minor polish you would merely prefer is not grounds for a revise —
say it in notes and approve. In doubt between a weak revise and an approve-with-notes, approve:
unconstrained revision homogenizes prose and every rewrite costs the Author a full pass.

### Judge against human craft, not smoothness
Your instinct will over-reward prose that reads like your own default output. That default IS the
AI tell.
- "Reads smooth / polished / evenly paced" is a YELLOW flag, not a green one. Human prose has
  sentence-length variance, deliberate roughness, asymmetry. Uniform cadence is a defect to name.
- Do NOT reward length or ornamentation. A longer or more lyrical passage is not a better one.
- Name specific tells when present: heavy em-dash use; headers or bullet lists inside prose; filler
  ("it is worth noting", "significantly", "crucially"); a reflective "and so..." wrap-up close.
- On approve, do not pad with praise. State what works in one line and move on. A correct, terse
  approval is a success.

### Cite the line, rank, cap
Every issue: quote + where it is + the specific problem + the concrete fix. No quote, no issue.
"The pacing sags" is not an issue; "the three paragraphs from 'She walked...' to '...the door' all
restate her hesitation — cut to one and let the next beat land" is. Rank by severity and include at
most the three or four that matter. A capped, ranked note gets acted on; a dump gets ignored.

Each such issue (other than a voice-drift line, which goes in `voice_drift_flags` instead) is one
`craft_flags` entry: `category` always "craft", `description` the quote + location + problem,
`proposed_resolution` the concrete fix. This is the durable, reviewable record of what you found —
file it regardless of verdict.

## Output
- `verdict`: "approve" or "revise", per the bar above.
- `notes`: on revise, the Author-facing instructions for the rewrite; on approve, the one-line
  what-works summary. The full ranked issue list belongs in `craft_flags`, not here.
- `craft_flags`: the ranked, quoted craft issues from above, capped as described.
- `thread_intents` / `theme_intents` / `knowledge_intents` / `causal_intents`: ONLY what this prose
  demonstrably enacts. Anything acting on an existing entity cites its exact id from the context
  block; a `plant` mints a new id from the title you give it, so it needs no id to cite and stays
  available when the context block lists none yet. Emit none if the prose shows none — an empty
  list is the correct and common answer.
- `knowledge_intents` specifically: a secret is withheld knowledge some characters hold and others
  do not — defined by who knows it. `plant` one when the prose establishes something a character
  conceals, or hands one character knowledge another lacks, and `learn` it for the holders in the
  same pass; `reveal`/`uses` cite an existing secret id. A fact nobody is hiding is world detail,
  not a secret, and padding this list is worse than leaving it empty.
- `promise_intents`: 'make' plants a discrete setup (a Chekhov's gun, foreshadowing, or a red
  herring), optionally with a target payoff window (window_lo/window_hi, 1-based chapter numbers);
  progress/pay/release cite an existing promise id exactly.
- `voice_drift_flags`: one per character line that violates that character's voice card. Skip lines
  already listed as filed in the context.
- `feed_note`: exactly one short line, in your editorial voice, reacting to the verdict.""" + DECISIVENESS_NOTE

logger = logging.getLogger(__name__)

# A revise returns the chapter to `draft`, straight back into this agent's
# queue. Self-refinement pays off for the first couple of rounds and then
# flattens prose, so the loop is bounded rather than left to the model's taste.
MAX_REVISIONS = 2


def _revision_budget_note(revision_count: int) -> str:
    """Tell the Editor how much of the revision budget this chapter has spent.
    Empty on a first look, so an untouched chapter's prompt is unchanged."""
    if revision_count <= 0:
        return ""
    times = "time" if revision_count == 1 else "times"
    if revision_count >= MAX_REVISIONS:
        return (
            f"\n\nThis chapter has been revised {revision_count} {times} and has spent its "
            f"revision budget: approve it. Note any remaining nits in your notes for the "
            f"record, but it ships."
        )
    return (
        f"\n\nThis chapter has already been revised {revision_count} {times}. Send it back "
        f"only for a genuine, quotable, still-unfixed defect — not for a matter of taste."
    )


class Editor(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 120,
        casting_note: str = "",
        personality: str = "",
        sag_spike_delta: float = SAG_SPIKE_DELTA,
        pull_mode: bool = False,
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="editor", personality=personality)
        self._casting_note = casting_note
        self._sag_spike_delta = sag_spike_delta
        self.pull_mode = pull_mode

    async def readiness(self) -> float:
        drafts = len(await self._read.list_chapters(status=EditorialStatus.draft))
        return min(1.0, drafts / 3)

    async def poll(self) -> dict:
        drafts = await self._read.list_chapters(status=EditorialStatus.draft)
        return {
            "target": drafts[0] if drafts else None,
            "threads": await self._read.list_threads(),
            "scores": await self._read.list_structure_scores(),
            "secrets": await self._read.list_secrets(),
            "characters": await self._read.list_characters(),
            "chapters": await self._read.list_chapters(),
            "causal_edges": await self._read.list_causal_edges(),
            "themes": await self._read.list_themes(),
            "open_retcons": await self._read.list_flags(category="contradiction", status=FlagStatus.open),
            "promises": await self._read.list_promises(),
            "blueprint": await self._read.get_active_blueprint(),
            "beats": await self._read.list_beats(),
        }

    async def _character_voices_block(self, character_ids: list[str]) -> str:
        lines = []
        for cid in character_ids:
            c = await self._read.get_character(cid)
            if c is not None and c.voice:
                lines.append(f"- {c.name}: {c.voice}")
        if not lines:
            return ""
        return "\n\nCharacter voices:\n" + "\n".join(lines)

    async def _cast_pointer_block(self, character_ids: list[str]) -> str:
        """Pull-mode replacement for _character_voices_block: the system
        prompt tells a tooled Editor to pull a voice card when it suspects
        drift, so pushing every card alongside that instruction was the
        push/pull redundancy. Names + ids stay because voice_drift_flags
        must cite a character_id."""
        lines = []
        for cid in character_ids:
            c = await self._read.get_character(cid)
            if c is not None:
                lines.append(f"{c.name} (id:{cid})")
        if not lines:
            return ""
        return (
            "\n\nCast in this chapter (pull a voice card when you suspect their "
            "dialogue drifts): " + ", ".join(lines)
        )

    async def work(self, ctx: dict) -> EditorVerdict | None:
        ch = ctx["target"]
        if ch is None:
            return None
        voice = (
            f"\n\nEnforce this prose voice: {self._casting_note}; note any drift in your feedback."
            if self._casting_note
            else ""
        )
        cast = self._guarded_line("In character", self.personality)
        if self.pull_mode:
            voices = await self._cast_pointer_block(ch.character_ids)
        else:
            voices = await self._character_voices_block(ch.character_ids)
        pacing = pacing_flags_note(ctx["scores"], delta=self._sag_spike_delta)
        chapter_order = [c.id for c in ctx["chapters"]]
        causal = causal_flags_note(ctx["causal_edges"], chapter_order)
        ledger = ledger_note(ctx.get("promises", []), ctx["chapters"])
        pacing_plan = resolution_pacing_note(ctx["threads"], ctx["secrets"], ctx["chapters"])
        beat_drift = beat_drift_note(ctx.get("blueprint"), ctx.get("beats", []), ctx["chapters"])
        # Citation aid, not knowledge-state injection (that is Author-only per
        # Locked decision #7): knowledge_intents must cite an existing secret
        # id or be dropped at commit time, so the Editor needs the id list in
        # its context to annotate what the prose shows. Empty when no secrets
        # exist -- the prompt stays byte-identical (pinned by tests).
        secret_ids = ""
        if ctx["secrets"]:
            listing = "\n".join(f"- {s.id} ('{s.title}')" for s in ctx["secrets"])
            secret_ids = (
                "\n\nActive secrets you may cite by id in knowledge_intents when "
                "the prose shows a character planting, learning, revealing, or "
                "using one:\n" + listing
            )
        # The Editor re-reviews the same draft every cycle; showing the LLM
        # which voice-drift flags are already queued keeps it from burning
        # output on repeats the commit-time dedup would drop anyway. Empty
        # when none are open (prompt stays byte-identical, pinned by tests).
        drift_filed_flags = await self._read.list_flags(category="voice_drift", status=FlagStatus.open)
        drift = ""
        if drift_filed_flags:
            listing = "\n".join(f"- {d.description}" for d in drift_filed_flags[:20])
            drift = "\n\nVoice-drift flags already filed (do not re-flag these lines):\n" + listing
        revisions = _revision_budget_note(ch.revision_count)
        rejections = await self._own_rejections_note()
        msg = (
            f"Chapter title: {ch.title}\n\nProse:\n{ch.prose}{voice}{cast}{voices}{pacing}{causal}"
            f"{secret_ids}{drift}{rejections}{ledger}{pacing_plan}{beat_drift}{revisions}"
        )
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, verdict: EditorVerdict | None, ctx: dict) -> None:
        ch = ctx["target"]
        if ch is None or verdict is None:
            return
        # The prompt asks for this, but the budget is a system invariant: a
        # model that keeps saying "revise" must not be able to loop forever.
        forced = verdict.verdict != "approve" and ch.revision_count >= MAX_REVISIONS
        if forced:
            logger.info(
                "editor: chapter %s revised %d times, forcing approve over verdict %r",
                ch.id, ch.revision_count, verdict.verdict,
            )
        if verdict.verdict == "approve" or forced:
            updated = ch.model_copy(update={"editorial_status": EditorialStatus.reviewed, "editor_notes": verdict.notes})
            await self._committer.commit(self.name, EventType.CHAPTER_STATUS_CHANGED, updated.id, updated)
        else:
            sig = DirectorSignal(kind=SignalKind.revise, body=verdict.notes, target_agent="author", target_entity=ch.id)
            await self._committer.commit(self.name, EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)
        active_ids = active_thread_ids(ctx["threads"])
        await self._commit_thread_intents(verdict.thread_intents, active_ids, chapter_id=ch.id)
        active_promise_ids = {
            p.id for p in ctx["promises"] if p.state.value not in TERMINAL_PROMISE_STATES
        }
        await self._commit_promise_intents(
            verdict.promise_intents, active_promise_ids, active_ids, chapter_id=ch.id
        )
        active_theme_ids = {t.id for t in ctx["themes"]}
        await self._commit_theme_intents(verdict.theme_intents, active_theme_ids, chapter_id=ch.id)
        active_secret_ids = {s.id for s in ctx["secrets"]}
        await self._commit_knowledge_intents(
            verdict.knowledge_intents, active_secret_ids, chapter_id=ch.id,
            character_ids={c.id for c in ctx["characters"]},
        )
        valid_chapter_ids = {c.id for c in ctx["chapters"]}
        await self._commit_causal_intents(verdict.causal_intents, valid_chapter_ids)
        if verdict.voice_drift_flags:
            # The Editor re-targets the same draft chapter every cycle until it is
            # revised, and the LLM rewords trait_violated/note on every pass, so
            # dedup must key on the stable (character, line) fragment of the
            # description — not the full reworded string — against the open queue.
            open_flags = await self._read.list_flags(category="voice_drift", status=FlagStatus.open)
            open_descriptions = [r.description for r in open_flags]
            filed_keys: set[str] = set()
            for vflag in verdict.voice_drift_flags:
                key = f"violated by {vflag.character_id}: \"{vflag.line}\""
                if key in filed_keys or any(key in d for d in open_descriptions):
                    continue
                filed_keys.add(key)
                description = (
                    f"{vflag.trait_violated} {key}"
                    + (f" — {vflag.note}" if vflag.note else "")
                )
                flag = Flag(category="voice_drift", filed_by=self.name, description=description,
                            related_entry_ids=[vflag.character_id], proposed_resolution="")
                await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, flag)
        await self._commit_flag_drafts(verdict.craft_flags, category="craft")
        await self._remark(verdict.feed_note)

    async def _run(self) -> None:
        ctx = await self.poll()
        verdict = await self.work(ctx)
        await self.commit(verdict, ctx)


def build_editor_runner(settings, callbacks=None, backend=None, tools=None, subagents=None):
    from deepagents import create_deep_agent
    from agent_kit import build_chat_model
    from agent_kit import ExcludeToolsMiddleware
    if backend is not None:
        model = build_chat_model(
            settings.agent_model, settings.llm_base_url, settings.llm_api_key,
            settings.agent_temperature, max_tokens=settings.llm_max_tokens,
            callbacks=None, streaming=callbacks is not None,
        )
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE + OUTPUT_CONVENTIONS_NOTE
        graph = create_deep_agent(
            model=model, system_prompt=system_prompt, response_format=EditorVerdict,
            backend=backend, tools=tools, skills=CRAFT_SKILLS, subagents=subagents,
            middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=EditorVerdict)


from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant, SubagentGrant


def _construct(ctx: AgentContext) -> Editor:
    enabled = ctx.settings.editor_tools_enabled
    subagent_enabled = ctx.settings.editor_subagent_enabled
    builder = ctx.tooled(build_editor_runner, enabled, subagent_enabled, "editor")
    runner = ctx.runner_for("editor", builder)
    return Editor(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.default_agent_interval,
        casting_note=ctx.casting_note,
        personality=ctx.personalities.get("editor", ""),
        sag_spike_delta=ctx.settings.sag_spike_delta,
        pull_mode=enabled,
    )


SPEC = AgentSpec(
    name="editor",
    tool_grant=ToolGrant(enabled_setting="editor_tools_enabled"),
    subagent_grant=SubagentGrant(enabled_setting="editor_subagent_enabled"),
    construct=_construct,
    rebuild_on=("agent_temperature",),
)
