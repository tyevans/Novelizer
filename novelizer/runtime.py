from __future__ import annotations
from typing import Optional
from novelizer.config import Settings
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
from novelizer.agents.continuity_checker import ContinuityChecker, build_continuity_checker_runner
from novelizer.agents.retconner import Retconner, build_retconner_runner
from novelizer.agents.structure_analyst import StructureAnalyst, build_structure_analyst_runner
from novelizer.voices.loader import load_voice_pack


class Runtime:
    def __init__(self, settings: Settings, runner=None, runners: Optional[dict] = None) -> None:
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
            return self._runners[name]
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
        self.author = Author(
            self._runner_for("author", build_author_runner), self.read, self.committer,
            interval=s.author_interval, casting_note=casting_note, personality=personalities.get("author", ""),
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
        )
        self.continuity_checker = ContinuityChecker(
            self._runner_for("continuity_checker", build_continuity_checker_runner), self.read, self.committer,
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
        self.scheduler = Scheduler(self.agents, self.read)

    async def close(self) -> None:
        await self.read.close()
        await self.projector.close()
        await self.events.close()
