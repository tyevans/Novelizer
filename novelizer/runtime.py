from __future__ import annotations
from typing import Optional
from novelizer.settings import EffectiveSettings, RESTART_REQUIRED_KEYS
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import GatingCommitter
from novelizer.canon.policy import AutonomyPolicy
from novelizer.canon.proposal_service import ProposalService
from novelizer.scheduler import Scheduler
from novelizer.agents.author import Author, build_author_runner
from novelizer.agents.world_architect import WorldArchitect, build_world_architect_runner
from novelizer.agents.character_keeper import CharacterKeeper, build_character_keeper_runner
from novelizer.agents.editor import Editor, build_editor_runner
from novelizer.agents.continuity_checker import (
    ContinuityChecker, build_continuity_checker_runner, build_continuity_mining_runner,
)
from novelizer.agents.retconner import Retconner, build_retconner_runner
from novelizer.agents.structure_analyst import StructureAnalyst, build_structure_analyst_runner
from novelizer.voices.loader import load_voice_pack


class Runtime:
    def __init__(self, settings: EffectiveSettings, runner=None, runners: Optional[dict] = None) -> None:
        self.settings = settings
        self.events = EventStore(settings.db_path)
        self.projector = Projector(self.events, settings.db_path)
        self.read = ReadStore(settings.db_path)
        self.policy: Optional[AutonomyPolicy] = None
        self.proposals: Optional[ProposalService] = None
        self.committer = None  # constructed in start(), once self.read is initialized
        self._runner = runner          # back-compat: author-only single runner
        self._runners = runners        # full per-agent override
        self.agents: list = []
        self.author = None
        self.world_architect = None
        self.character_keeper = None
        self.editor = None
        self.continuity_checker = None
        self.retconner = None
        self.structure_analyst = None
        self.scheduler: Optional[Scheduler] = None
        self.voice_pack = None
        self.active_prose_profile = None

    def _runner_for(self, name: str, builder):
        if self._runners is not None:
            if name in self._runners:
                return self._runners[name]
            # Any name absent from an injected runners dict falls back to the real
            # builder (not just "continuity_checker_mining", which motivated this) —
            # builders construct lazily and never touch the network before ainvoke().
            return builder(self.settings)
        if name == "author" and self._runner is not None:
            return self._runner
        return builder(self.settings)

    async def start(self) -> None:
        await self.events.init()
        await self.projector.init()
        await self.read.init()
        await self.projector.catch_up()
        self.policy = AutonomyPolicy(self.read)
        self.committer = GatingCommitter(self.events, self.policy)
        self.proposals = ProposalService(self.events)
        self.voice_pack = load_voice_pack(self.settings.voice_pack)
        self.active_prose_profile = self.voice_pack.profile(self.settings.prose_profile)
        casting_note = self.active_prose_profile.casting_note if self.active_prose_profile else ""
        personalities = self.voice_pack.agent_personalities
        s = self.settings
        provenance = {
            "model": s.author_model,
            "temperature": s.author_temperature,
            "voice_pack": self.voice_pack.name,
            "prose_profile": s.prose_profile,
        }
        self.author = Author(
            self._runner_for("author", build_author_runner), self.read, self.committer,
            interval=s.author_interval, casting_note=casting_note, personality=personalities.get("author", ""),
            provenance=provenance,
            prior_chapter_summary_chars=s.prior_chapter_summary_chars,
            staleness_threshold_chapters=s.staleness_threshold_chapters,
        )
        self.world_architect = WorldArchitect(
            self._runner_for("world_architect", build_world_architect_runner), self.read, self.committer,
            interval=s.default_agent_interval, personality=personalities.get("world_architect", ""),
        )
        self.character_keeper = CharacterKeeper(
            self._runner_for("character_keeper", build_character_keeper_runner), self.read, self.committer,
            interval=s.default_agent_interval, personality=personalities.get("character_keeper", ""),
        )
        self.editor = Editor(
            self._runner_for("editor", build_editor_runner), self.read, self.committer,
            interval=s.default_agent_interval, casting_note=casting_note, personality=personalities.get("editor", ""),
            sag_spike_delta=s.sag_spike_delta,
        )
        self.continuity_checker = ContinuityChecker(
            self._runner_for("continuity_checker", build_continuity_checker_runner),
            self._runner_for("continuity_checker_mining", build_continuity_mining_runner),
            self.read, self.committer, self.events,
            interval=s.continuity_interval, personality=personalities.get("continuity_checker", ""),
        )
        self.retconner = Retconner(
            self._runner_for("retconner", build_retconner_runner), self.read, self.committer,
            interval=s.default_agent_interval, personality=personalities.get("retconner", ""),
        )
        self.structure_analyst = StructureAnalyst(
            self._runner_for("structure_analyst", build_structure_analyst_runner), self.read, self.committer,
            interval=s.structure_analyst_interval, personality=personalities.get("structure_analyst", ""),
        )
        self.agents = [
            self.world_architect, self.character_keeper, self.author,
            self.editor, self.continuity_checker, self.retconner, self.structure_analyst,
        ]
        self.scheduler = Scheduler(self.agents, self.read, max_concurrent_agents=s.max_concurrent_agents)

    def apply_settings(self, new: EffectiveSettings) -> dict:
        """Apply a freshly loaded EffectiveSettings to the running system.

        Cadence, voice, and temperatures apply live; endpoint/model changes are
        reported as restart-required and left un-applied so self.settings always
        reflects what is actually running.
        """
        old = self.settings
        changed = [k for k in EffectiveSettings.model_fields if getattr(old, k) != getattr(new, k)]
        applied: list[str] = []
        restart: list[str] = []
        interval_map = {
            "author_interval": [self.author],
            "default_agent_interval": [self.world_architect, self.character_keeper, self.editor, self.retconner],
            "continuity_interval": [self.continuity_checker],
            "structure_analyst_interval": [self.structure_analyst],
        }
        for key in changed:
            if key in RESTART_REQUIRED_KEYS:
                restart.append(key)
            elif key in interval_map:
                for agent in interval_map[key]:
                    agent.interval = getattr(new, key)
                applied.append(key)
            elif key == "max_concurrent_agents":
                # Read fresh per-tick, no cached construction to rebuild --
                # applies live, same as cadence settings.
                self.scheduler._max_concurrent = new.max_concurrent_agents
                applied.append(key)
            else:
                applied.append(key)

        errors: list[str] = []
        stored = new.model_copy(update={k: getattr(old, k) for k in restart}) if restart else new

        if "voice_pack" in changed or "prose_profile" in changed:
            try:
                new_pack = load_voice_pack(new.voice_pack)
            except Exception as e:
                errors.append(f"voice_pack: {e}")
                # Revert both voice keys — the pack and profile travel together.
                revert = {}
                if "voice_pack" in changed:
                    revert["voice_pack"] = old.voice_pack
                    applied.remove("voice_pack")
                if "prose_profile" in changed:
                    revert["prose_profile"] = old.prose_profile
                    applied.remove("prose_profile")
                stored = stored.model_copy(update=revert)
            else:
                self.voice_pack = new_pack
                self.active_prose_profile = self.voice_pack.profile(stored.prose_profile)
                casting_note = self.active_prose_profile.casting_note if self.active_prose_profile else ""
                personalities = self.voice_pack.agent_personalities
                self.author._casting_note = casting_note
                self.editor._casting_note = casting_note
                for agent in self.agents:
                    agent.personality = personalities.get(agent.name, "")

        rebuild = self._runners is None and self._runner is None
        if "author_temperature" in changed and rebuild:
            self.author._runner = build_author_runner(stored)
        if "agent_temperature" in changed and rebuild:
            self.world_architect._runner = build_world_architect_runner(stored)
            self.character_keeper._runner = build_character_keeper_runner(stored)
            self.editor._runner = build_editor_runner(stored)
            self.continuity_checker._runner = build_continuity_checker_runner(stored)
            self.continuity_checker._mining_runner = build_continuity_mining_runner(stored)
            self.retconner._runner = build_retconner_runner(stored)
            self.structure_analyst._runner = build_structure_analyst_runner(stored)

        if self.author is not None and self.author.provenance is not None:
            self.author.provenance = {
                "model": stored.author_model,
                "temperature": stored.author_temperature,
                "voice_pack": self.voice_pack.name,
                "prose_profile": stored.prose_profile,
            }

        self.settings = stored
        return {"applied": applied, "restart_required": restart, "errors": errors}

    async def close(self) -> None:
        await self.read.close()
        await self.projector.close()
        await self.events.close()
