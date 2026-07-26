from __future__ import annotations
from novelizer.agents import prompts
from novelizer.agents.base import BaseAgent, ChapterDraft, Runner
from agent_kit import GRAPH_RECURSION_LIMIT
from novelizer.agents.prompts import OUTPUT_CONVENTIONS_NOTE
from novelizer.brain.context import (
    causal_flags_note, chapter_map_note, known_secrets_note, ledger_note, resolution_pacing_note,
    stale_threads_note,
)
from novelizer.brain.context_assembly import AdvisoryEntry, assemble_advisory
from novelizer.brain.gate import author_may_draft
from novelizer.brain.staleness import STALENESS_THRESHOLD_CHAPTERS
from novelizer.canon_fs.skills_route import CRAFT_SKILLS
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, InspirationHandConsumed, ChapterBriefFulfilled
from novelizer.canon.promises import TERMINAL_PROMISE_STATES
from novelizer.canon.threads import active_thread_ids
from novelizer.canon.events import ChapterRevised
from novelizer.muse.prompts import AI_TELL_BAN_NOTE, casting_pool_note, inspiration_note
from novelizer.store.models import Chapter, SignalKind

# Generative lane. The Author already has a stopping rule for RESEARCH ("stop
# once you can say where the last chapter left off"); what it lacked is one for
# CHOOSING, which is the step that has no output of its own and so can absorb a
# whole pass invisibly.
DECISIVENESS_NOTE = """

## Committing to the chapter
Once you can say where the last chapter left off and what this chapter owes the story, the
deciding is finished and the drafting starts. Which scene to write is a choice you make once:
re-arguing it, after the brief and the canon you read already point at one, produces nothing a
reader will ever see, and a pass that ends in deliberation ends with no chapter. Weigh the
candidates in front of you — the brief, an overdue thread, a promise past its window — not every
chapter the book could theoretically have. If the draft itself proves the choice was wrong, finish
the chapter you can honestly finish and name the conflict in a `flags` entry; that is what flags
are for, and it is a better outcome than an unwritten chapter."""

AUTHOR_SYSTEM_PROMPT = """## Role
You are the Author of a living, event-sourced fictional world — the fleet's one prose
writer. You draft each new chapter and you alone own the final sentences. The Editor,
Continuity Checker, Character Keeper, Plotter and the rest advise you through structured
notes; they never write prose. Their notes are counsel, not dictation. Grading the story
and repairing canon are their jobs, not yours: yours is to write the next chapter well
and to record honestly what it did.

## Research before you write
The chapters, characters, lore, threads, promises and secrets already committed are the
ground truth. The summary in your task message is a POINTER to them, not the source — do
not write from the summary alone.
Begin every pass by calling `write_todos` with a short plan, e.g. "read end of last
chapter -> check stale threads/secrets -> draft -> set intents". Then:
- `read_file` the most recent chapter IN FULL — pass `limit=2000`, or you get only its
  first 100 lines and the scene you are continuing from is the one you never saw. You are
  continuing from its final moment, not from a gist — match its place, time and cast, and
  pick up the business it left unfinished.
- `grep` or `search_canon` for anything the task notes flag (a stale thread's id, a
  secret and who holds it, a character you'll feature) and read the relevant span before
  you rely on it.
- Stop once you can say where the last chapter left off and which threads, promises and
  secrets bear on this scene. Then write. Do not browse the whole canon.

## Check the outline before you draft
A brief is not decoration — it is the Plotter's judgment that this chapter is worth
writing right now. If the task notes carry no "Chapter brief" block, one of two things
is true: either the outline genuinely has nothing ready for this ordinal, or a real
gap exists that prose alone should not paper over. Before you draft in that situation:
- `read_file` `/outline/beats.md` and `/outline/threads-plan.md`, and `ls`
  `/outline/briefs/` (the outline is mounted read-only at `/outline/*`). Confirm there
  is really no open brief targeting the next chapter ordinal, and check whether the
  active blueprint's beat sequence even reaches this far (`/outline/blueprint.md`).
- Weigh whether the story is actually due for a chapter, using what's already in your
  task notes plus what you read:
  - A thread flagged stale (no touch in the staleness window) with an open resolution
    window closing soon is overdue material — favor it over inventing something new.
  - A promise nearing or past its `window_hi` (see the ledger note / `/outline/ledger.md`)
    is a payoff owed to the reader.
  - If a beat's ideal-position window (per the blueprint) covers the next ordinal and
    is unfulfilled, that beat is the chapter's spine.
  - No blueprint adopted: this should not normally happen — the Plotter mints
    a blueprint from the premise before you draft. If you are here, you are in
    the fallback: keep the chapter provisional (see your task note) and say in
    your feed note that the Plotter still owes a blueprint.
- You cannot mint a blueprint or a chapter brief yourself — only the Plotter emits
  those. Do not fabricate a brief in your own head and silently treat it as
  authoritative. Instead:
  - If there IS enough — a stale thread with a closing window, an unfulfilled beat
    whose window covers this ordinal, or a promise past due — write toward that
    specific thread/beat/promise, cite its exact id in your intents as you always
    would, and say in your feed note which piece of unbriefed outline material you
    chose to honor and why (e.g. "no brief for ch12; wrote to close thread
    t-widow-debt, window closes ch13").
  - If there is NOT enough — no blueprint, or nothing overdue, or recent chapters
    already exhausted the obvious material — say so explicitly in your feed note
    ("no brief for ch12 and nothing overdue in threads/beats/ledger; Plotter should
    catch up before I draft further") and keep the chapter modest and exploratory
    rather than padding toward a target you can't justify. Never invent a chapter's
    worth of new threads, promises or secrets just to have material — that debt lands
    on the Plotter and Editor to untangle later, and is worse than a quiet chapter.
A brief that IS present overrides all of the above — see "Write the chapter" below.

## Write the chapter
Write one chapter of narrative prose — scene, action and dialogue, not synopsis. Let the
beat set the length; never pad to a target.
This chapter is one movement in a continuing novel, NOT a standalone story. Do not
resolve it into a tidy mini-arc or close on a reflective "and so..." paragraph. End where
the tension is still live — on a choice made, a question opened, a consequence about to
land — so the next chapter has somewhere to go. Seeding a payoff now to cash chapters
later, or leaving a thread deliberately mid-air, is good craft.
Continuity is binding. Honor established facts, timelines and who-knows-what: if the task
notes list secrets and who holds them, never let a character act on one they have not
learned.
When a chapter brief is present it is your assignment: honor it, or deviate deliberately
and explain the deviation in your feed note.

## Craft — write like a person, not a model
- Vary sentence length and rhythm on purpose; let some run long and others land short.
  Uniform, evenly-cadenced, over-polished prose is the strongest signal a machine wrote
  it — asymmetry, and the occasional fragment or rough edge, read as human.
- Cap em-dashes at about one per 500 words; reach for a comma, a period, or the plain
  word instead.
- Keep markdown out of the prose — no section headers, no bullet lists. It is a chapter,
  not a document.
- Cut throat-clearing and filler: no "it is worth noting", "significantly", "crucially",
  "leverage", "a myriad of", "a testament to". Trust the scene.
- Show feeling through action, object and subtext; don't name the emotion.

## Record what the chapter did (structured notes)
After the prose is written, fill the intent lists with what the chapter ACTUALLY did to
the story's spine — and only that:
- thread_intents — `plant` a genuinely new through-line, or `touch`/`pay_off`/`abandon`
  an existing one by its exact id from the task notes (never invent an id). A thread is a
  load-bearing promise to the reader, not every passing mention.
- promise_intents — `make` plants a discrete setup (a Chekhov's gun, foreshadowing, or a
  red herring), optionally with a target payoff window (window_lo/window_hi, 1-based
  chapter numbers); `progress`/`pay`/`release` cite an existing promise id exactly.
- secret_plants — a NEW secret this chapter established, as a `title` and a note. Nothing
  else: the system mints the id. A secret is withheld knowledge that some characters hold
  and others do not: it is defined by who knows it, and it earns its keep by making a
  later scene land differently for the reader than for the character in the dark. A fact
  nobody is hiding is world detail, not a secret. This list does not need any secret to
  exist yet — it is how the first one comes into being.
- secret_citations — an action on a secret that ALREADY EXISTS, citing its exact id from
  the task notes: `learn` (a character comes to know it), `uses` (acts on knowledge they
  hold), `reveal` (it becomes public). `learn`/`uses` name the `character_id`; `reveal`
  leaves it blank. When this chapter both establishes a concealment AND gives it a holder,
  plant it here and cite a `learn` for the holder in the same pass — otherwise the
  asymmetry lives only in the prose, and no other agent can pace its reveal or catch a
  character acting on knowledge they never earned.
- causal_intents — link two existing chapters when one genuinely causes the other.
- theme_intents — `introduce` or `develop` a motif the chapter truly carries.
Leave a list empty rather than padding it: a marginal or invented thread is worse than
none, and every intent you declare is one another agent must reconcile.
List `character_ids` using the ids shown beside each name in the task notes.

## When something is wrong and you can't fix it by writing
You do not silently paper over a real problem, and you do not silently drop it either.
If the brief contradicts a voice card, a targeted beat has no honest way into this
chapter, or a promise's window is closing with nowhere natural to land it: write the
best chapter you honestly can, then add one `flags` entry — `category="craft"`,
`description` naming the specific conflict, `proposed_resolution` if you can see one.
This is not for ordinary craft trade-offs you resolved yourself; it's for the case
where the right fix is outside your lane (a brief the Plotter should revise, a voice
card that no longer fits the character's arc). Leave `flags` empty otherwise.

## Your feed note
Do the writing and the note-setting as a craftsperson. Then, last, write `feed_note` —
one short line in your own voice reacting to the chapter you just made.

## Marking who speaks

Wrap every line of spoken dialogue in a speaker tag, and every passage of
rendered interior thought in a thought tag:

    He stopped at the counter. <speech char="Mira">"Twenty dollars."</speech>
    <thought char="Jon">Twenty. He had four.</thought> He counted it out anyway.

Rules:
- Tag EVERY utterance, including short ones in a rapid exchange where no "she
  said" tells the reader who is speaking. That case is exactly why the tags
  exist -- nothing downstream can recover it from the prose alone.
- Use the character's canonical name or a known alias, spelled as it appears in
  canon. Never invent an id or a slug. If that name itself contains a double
  quote, write it as `&quot;` inside the attribute (e.g. `char="Bob &quot;Sly&quot;
  Jones"`) so the tag still parses.
- Leave narration untagged. Do not tag reported or summarized speech that is not
  in quotation marks.
- Tags wrap the utterance including its quotation marks, and never nest.
""" + DECISIVENESS_NOTE + AI_TELL_BAN_NOTE

# Re-exported from novelizer.agents.prompts, which is the real home: six sibling
# agents still import these through here. Migrate those imports, then drop this.
RETRIEVAL_NOTE_BASE = prompts.RETRIEVAL_NOTE_BASE
RETRIEVAL_NOTE = prompts.RETRIEVAL_NOTE
SPEECH_MARKER_NOTE = prompts.SPEECH_MARKER_NOTE


def _summarize(
    ctx: dict,
    casting_note: str = "",
    personality: str = "",
    advisory_budget: int = 2000,
    summaries: dict[str, str] | None = None,
    staleness_threshold_chapters: int = STALENESS_THRESHOLD_CHAPTERS,
    pull_mode: bool = False,
    gate_enabled: bool = True,
) -> str:
    if pull_mode:
        # Titles only: a tooled Author reads lore itself, and pushed bodies
        # are exactly the summary the retrieval note says not to write from.
        world = "\n".join(f"- {e.title}" for e in ctx["world"][:10]) or "None yet."
    else:
        world = "\n".join(f"- {e.title}: {e.body[:150]}" for e in ctx["world"][:10]) or "None yet."
    # Ids beside the names: character_ids is a required output field, and a cast
    # block of bare names leaves the Author guessing at them.
    chars = "\n".join(
        f"- {c.name} (id:{c.id}): {c.traits} | arc: {c.arc_status}" for c in ctx["characters"][:8]
    ) or "None yet."
    notes = "\n".join(f"Director: {s.body}" for s in ctx["signals"]) or "None."
    voice = BaseAgent._guarded_line("Write in this prose voice", casting_note)
    cast = BaseAgent._guarded_line("In character", personality)
    brain = stale_threads_note(ctx["threads"], ctx["chapters"], threshold=staleness_threshold_chapters)
    secrets = known_secrets_note(ctx["secrets"], ctx["characters"], ctx["knowledge_matrix"])
    causal = causal_flags_note(ctx["causal_edges"], [c.id for c in ctx["chapters"]])
    ledger = ledger_note(ctx.get("promises", []), ctx["chapters"])
    pacing_plan = resolution_pacing_note(ctx["threads"], ctx["secrets"], ctx["chapters"])
    pool = casting_pool_note(ctx.get("hand"))
    sparks = inspiration_note(ctx.get("hand"))
    if pull_mode:
        gists = {s.chapter_id: s.gist for s in ctx.get("summaries", []) if s.gist}
        chapters_block = f"Chapter index:\n{chapter_map_note(ctx['chapters'], gists=gists)}"
    else:
        # Full fidelity on the chapter being continued -- the Author needs its
        # ending to pick up from, not a gist. The chapters behind it get the
        # advisory treatment: a rolling summary when the Summarizer has caught
        # up, a labeled verbatim head (never silent) otherwise.
        previous = ctx["previous"]
        summaries_by_id = summaries or {}
        entries = [
            AdvisoryEntry(label=f"'{c.title}'", summary=summaries_by_id.get(c.id), verbatim=c.prose)
            for c in previous[:-1]
        ]
        block = assemble_advisory(entries, advisory_budget)
        lines = [block] if block else []
        if previous:
            latest = previous[-1]
            lines.append(f"- '{latest.title}' (most recent, in full):\n{latest.prose}")
        prev = "\n".join(lines) or "None yet."
        chapters_block = f"Previous chapters:\n{prev}"
    brief = ctx.get("brief")
    provisional = ""
    if gate_enabled and ctx.get("blueprint") is None and brief is None:
        provisional = (
            "\n\nNo outline exists yet — you are drafting ahead of the Plotter under a "
            "fallback. Keep this chapter provisional and exploratory; do not invent a "
            "chapter's worth of new threads/promises/secrets, and say so in your feed note."
        )
    brief_block = ""
    if brief is not None:
        brief_block = (
            "\n\nChapter brief (your assignment from the Plotter — honor it, or deviate "
            "deliberately and say why in your feed note):\n"
            f"Goal: {brief.goal}\n"
            f"POV: {brief.pov_character_id or 'your choice'}\n"
            f"Touch threads: {', '.join(brief.threads_to_touch) or 'your choice'}\n"
            f"Hit beats: {', '.join(brief.beats_to_hit) or 'none targeted'}\n"
            f"Progress promises: {', '.join(brief.promises_to_progress) or 'none targeted'}\n"
            f"Value shift: {brief.value_shift or 'unspecified'} · "
            f"Planned outcome: {brief.planned_outcome or 'unspecified'}\n"
            f"Synopsis: {brief.synopsis}"
        )
    return (
        f"World lore:\n{world}\n\nCharacters:\n{chars}\n\n"
        f"{chapters_block}\n\nDirector notes:\n{notes}{pool}{sparks}{voice}{cast}{brain}{secrets}{causal}"
        f"{ledger}{pacing_plan}{provisional}{brief_block}\n\nWrite the next chapter."
    )


def _revise_summarize(target: Chapter, revise_signal, casting_note: str = "", personality: str = "") -> str:
    voice = BaseAgent._guarded_line("Write in this prose voice", casting_note)
    cast = BaseAgent._guarded_line("In character", personality)
    return (
        f"You are revising an existing chapter. Rewrite it in full, addressing the "
        f"feedback below.\n\nChapter title: {target.title}\n\nOriginal prose:\n{target.prose}\n\n"
        f"Editor feedback: {revise_signal.body}{voice}{cast}\n\nWrite the revised chapter."
    )


def _find_revise_signal(signals):
    return next((s for s in signals if s.kind == SignalKind.revise), None)


class Author(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 300,
        casting_note: str = "",
        personality: str = "",
        provenance: dict | None = None,
        advisory_token_budget: int = 2000,
        staleness_threshold_chapters: int = STALENESS_THRESHOLD_CHAPTERS,
        pull_mode: bool = False,
        gate_enabled: bool = True,
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="author", personality=personality)
        self._casting_note = casting_note
        self.provenance = provenance
        self._advisory_token_budget = advisory_token_budget
        self._staleness_threshold_chapters = staleness_threshold_chapters
        self.pull_mode = pull_mode
        self.gate_enabled = gate_enabled

    async def readiness(self) -> float:
        # Outline-first soft gate: stand down until a first-pass blueprint
        # exists (or the genesis fallback opens). Kept in readiness so it stays
        # soft — the scheduler is untouched.
        if not await author_may_draft(self._read, gate_enabled=self.gate_enabled):
            return 0.0
        drafts = len(await self._read.list_chapters(status="draft"))
        return max(0.0, 1.0 - drafts / 3)

    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        return {
            "world": await self._read.list_world_entries(),
            "characters": await self._read.list_characters(),
            "previous": chapters[-3:],
            "chapters": chapters,
            "signals": await self._read.list_unconsumed_signals(target_agent=self.name),
            "threads": await self._read.list_threads(),
            "secrets": await self._read.list_secrets(),
            "knowledge_matrix": await self._read.knowledge_matrix(),
            "themes": await self._read.list_themes(),
            "causal_edges": await self._read.list_causal_edges(),
            "hand": await self._read.get_active_hand(),
            "promises": await self._read.list_promises(),
            "brief": await self._read.get_open_brief_for_ordinal(len(chapters) + 1),
            "blueprint": await self._read.get_active_blueprint(),
            "summaries": await self._read.list_chapter_summaries(),
        }

    async def work(self, ctx: dict) -> ChapterDraft | None:
        revise_signal = _find_revise_signal(ctx["signals"])
        target = None
        if revise_signal is not None:
            target = next((c for c in ctx["chapters"] if c.id == revise_signal.target_entity), None)
        if revise_signal is not None and target is not None:
            content = _revise_summarize(target, revise_signal, self._casting_note, self.personality)
        else:
            content = _summarize(
                ctx, self._casting_note, self.personality,
                advisory_budget=self._advisory_token_budget,
                summaries={s.chapter_id: s.summary for s in ctx["summaries"]},
                staleness_threshold_chapters=self._staleness_threshold_chapters,
                pull_mode=self.pull_mode,
                gate_enabled=self.gate_enabled,
            )
        # Both paths: the Author files craft flags whether it is drafting fresh
        # or revising, so either pass can be the one repeating a thrown-out one.
        content += await self._own_rejections_note()
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": content}]})
        return result.get("structured_response")

    async def commit(self, draft: ChapterDraft | None, ctx: dict) -> None:
        if draft is None:
            return
        revise_signal = _find_revise_signal(ctx["signals"])
        target = None
        if revise_signal is not None:
            target = next((c for c in ctx["chapters"] if c.id == revise_signal.target_entity), None)
        if revise_signal is not None and target is not None:
            chapter_id = target.id
            revised = ChapterRevised(chapter_id=chapter_id, prose=draft.prose, editor_notes_ref=revise_signal.id)
            await self._committer.commit(self.name, EventType.CHAPTER_REVISED, chapter_id, revised)
            valid_chapter_ids = {c.id for c in ctx["chapters"]}
        else:
            chapter = Chapter(
                title=draft.title, prose=draft.prose, character_ids=draft.character_ids, provenance=self.provenance
            )
            await self._committer.commit(self.name, EventType.CHAPTER_CREATED, chapter.id, chapter)
            chapter_id = chapter.id
            valid_chapter_ids = {c.id for c in ctx["chapters"]} | {chapter.id}
            brief = ctx.get("brief")
            if brief is not None:
                await self._committer.commit(
                    self.name, EventType.CHAPTER_BRIEF_FULFILLED, brief.id,
                    ChapterBriefFulfilled(brief_id=brief.id, chapter_id=chapter.id),
                )
            hand = ctx.get("hand")
            if hand is not None:
                await self._committer.commit(
                    self.name, EventType.INSPIRATION_HAND_CONSUMED, hand.id,
                    InspirationHandConsumed(hand_id=hand.id, chapter_id=chapter.id),
                )
        active_ids = active_thread_ids(ctx["threads"])
        await self._commit_thread_intents(draft.thread_intents, active_ids, chapter_id=chapter_id)
        active_promise_ids = {
            p.id for p in ctx["promises"] if p.state.value not in TERMINAL_PROMISE_STATES
        }
        await self._commit_promise_intents(
            draft.promise_intents, active_promise_ids, active_ids, chapter_id=chapter_id
        )
        active_theme_ids = {t.id for t in ctx["themes"]}
        await self._commit_theme_intents(draft.theme_intents, active_theme_ids, chapter_id=chapter_id)
        active_secret_ids = {s.id for s in ctx["secrets"]}
        await self._commit_secret_plants(draft.secret_plants, active_secret_ids, chapter_id=chapter_id)
        await self._commit_secret_citations(
            draft.secret_citations, active_secret_ids, chapter_id=chapter_id,
            character_ids={c.id for c in ctx["characters"]},
        )
        await self._commit_causal_intents(draft.causal_intents, valid_chapter_ids)
        await self._commit_flag_drafts(draft.flags, category="craft")
        await self._remark(draft.feed_note)
        await self._consume_signals(ctx["signals"])

    async def _run(self) -> None:
        ctx = await self.poll()
        draft = await self.work(ctx)
        await self.commit(draft, ctx)


def build_author_runner(settings, callbacks=None, backend=None, tools=None, subagents=None):
    from deepagents import create_deep_agent
    from agent_kit import build_chat_model
    # Tool executions run in the agent graph's ToolNode under invoke-time
    # config, not constructor callbacks on the chat model -- so telemetry
    # callbacks are bound graph-scope via with_config below (dropped from the
    # model itself to avoid double-emitting LLM events through both paths).
    model = build_chat_model(
        settings.author_model, settings.llm_base_url, settings.llm_api_key,
        settings.author_temperature, max_tokens=settings.llm_max_tokens,
        callbacks=None, streaming=callbacks is not None,
    )
    if backend is not None:
        from novelizer.agents.middleware import TodoContextMiddleware, tool_call_budget
        system_prompt = AUTHOR_SYSTEM_PROMPT + RETRIEVAL_NOTE + SPEECH_MARKER_NOTE + OUTPUT_CONVENTIONS_NOTE
        graph = create_deep_agent(
            model=model, system_prompt=system_prompt, response_format=ChapterDraft,
            backend=backend, tools=tools, skills=CRAFT_SKILLS, subagents=subagents,
            middleware=[tool_call_budget(), TodoContextMiddleware()],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    graph = create_deep_agent(model=model, system_prompt=AUTHOR_SYSTEM_PROMPT, response_format=ChapterDraft)
    if callbacks:
        return graph.with_config({"callbacks": callbacks})
    return graph


from novelizer.agents.registry_types import AgentContext, AgentSpec, AgentTier, ToolGrant, SubagentGrant


def _construct(ctx: AgentContext) -> Author:
    enabled = ctx.settings.author_tools_enabled
    subagent_enabled = ctx.settings.author_subagent_enabled
    builder = ctx.tooled(build_author_runner, enabled, subagent_enabled, "author")
    runner = ctx.runner_for("author", builder)
    return Author(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.author_interval,
        casting_note=ctx.casting_note,
        personality=ctx.personalities.get("author", ""),
        provenance=ctx.provenance,
        advisory_token_budget=ctx.settings.advisory_token_budget,
        staleness_threshold_chapters=ctx.settings.staleness_threshold_chapters,
        pull_mode=enabled,
        gate_enabled=ctx.settings.outline_gate_enabled,
    )


SPEC = AgentSpec(
    name="author",
    tool_grant=ToolGrant(enabled_setting="author_tools_enabled"),
    subagent_grant=SubagentGrant(enabled_setting="author_subagent_enabled"),
    construct=_construct,
    tier=AgentTier.FULL,
    rebuild_on=("author_temperature",),
)
