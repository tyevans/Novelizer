from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import EditorVerdict
from novelizer.brain.context import causal_flags_note, pacing_flags_note
from novelizer.brain.sag_spike import SAG_SPIKE_DELTA
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.canon.threads import TERMINAL_STATES
from novelizer.store.models import DirectorSignal, SignalKind, EditorialStatus, RetconRequest, RetconStatus

SYSTEM_PROMPT = """You are the Editor of a living fictional world's story. Review the given chapter
for prose quality, narrative coherence, and pacing. Return a verdict of "approve" or "revise" and
notes: if revising, specific actionable feedback; if approving, brief praise."""

VOICE_SOURCE_TAG = "[source: voice_drift]"


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
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="editor", personality=personality)
        self._casting_note = casting_note
        self._sag_spike_delta = sag_spike_delta

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
            "chapters": await self._read.list_chapters(),
            "causal_edges": await self._read.list_causal_edges(),
            "themes": await self._read.list_themes(),
            "open_retcons": await self._read.list_retcon_requests(status=RetconStatus.open),
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
        voices = await self._character_voices_block(ch.character_ids)
        pacing = pacing_flags_note(ctx["scores"], delta=self._sag_spike_delta)
        chapter_order = [c.id for c in ctx["chapters"]]
        causal = causal_flags_note(ctx["causal_edges"], chapter_order)
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
        # output on repeats the commit-time dedup would drop anyway. Only
        # VOICE_SOURCE_TAG-tagged requests belong here -- the rest of the
        # queue is other checkers' business. Empty when none are open (prompt
        # stays byte-identical, pinned by tests).
        drift_filed = [
            r.description.removeprefix(VOICE_SOURCE_TAG).strip()
            for r in ctx.get("open_retcons", [])
            if r.description.startswith(VOICE_SOURCE_TAG)
        ]
        drift = ""
        if drift_filed:
            listing = "\n".join(f"- {d}" for d in drift_filed[:20])
            drift = "\n\nVoice-drift flags already filed (do not re-flag these lines):\n" + listing
        msg = f"Chapter title: {ch.title}\n\nProse:\n{ch.prose}{voice}{cast}{voices}{pacing}{causal}{secret_ids}{drift}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, verdict: EditorVerdict | None, ctx: dict) -> None:
        ch = ctx["target"]
        if ch is None or verdict is None:
            return
        if verdict.verdict == "approve":
            updated = ch.model_copy(update={"editorial_status": EditorialStatus.reviewed, "editor_notes": verdict.notes})
            await self._committer.commit(self.name, EventType.CHAPTER_STATUS_CHANGED, updated.id, updated)
        else:
            sig = DirectorSignal(kind=SignalKind.revise, body=verdict.notes, target_agent="author", target_entity=ch.id)
            await self._committer.commit(self.name, EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)
        active_thread_ids = {
            t.id for t in ctx["threads"] if t.state.value not in TERMINAL_STATES
        }
        await self._commit_thread_intents(verdict.thread_intents, active_thread_ids, chapter_id=ch.id)
        active_theme_ids = {t.id for t in ctx["themes"]}
        await self._commit_theme_intents(verdict.theme_intents, active_theme_ids, chapter_id=ch.id)
        active_secret_ids = {s.id for s in ctx["secrets"]}
        await self._commit_knowledge_intents(verdict.knowledge_intents, active_secret_ids, chapter_id=ch.id)
        valid_chapter_ids = {c.id for c in ctx["chapters"]}
        await self._commit_causal_intents(verdict.causal_intents, valid_chapter_ids)
        if verdict.voice_drift_flags:
            # The Editor re-targets the same draft chapter every cycle until it is
            # revised, and the LLM rewords trait_violated/note on every pass, so
            # dedup must key on the stable (character, line) fragment of the
            # description — not the full reworded string — against the open queue.
            open_reqs = await self._read.list_retcon_requests(status=RetconStatus.open)
            open_descriptions = [r.description for r in open_reqs]
            filed_keys: set[str] = set()
            for flag in verdict.voice_drift_flags:
                key = f"violated by {flag.character_id}: \"{flag.line}\""
                if key in filed_keys or any(key in d for d in open_descriptions):
                    continue
                filed_keys.add(key)
                description = (
                    f"{VOICE_SOURCE_TAG} {flag.trait_violated} {key}"
                    + (f" — {flag.note}" if flag.note else "")
                )
                req = RetconRequest(description=description, conflicting_entry_ids=[flag.character_id], proposed_resolution="")
                await self._committer.commit(self.name, EventType.RETCON_REQUEST_CREATED, req.id, req)
        await self._remark(verdict.feed_note)

    async def _run(self) -> None:
        ctx = await self.poll()
        verdict = await self.work(ctx)
        await self.commit(verdict, ctx)


def build_editor_runner(settings, callbacks=None):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=EditorVerdict)
