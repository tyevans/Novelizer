from __future__ import annotations
from pathlib import Path
from typing import Optional
from novelizer.settings import EffectiveSettings, RESTART_REQUIRED_KEYS
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import GatingCommitter
from novelizer.canon.policy import AutonomyPolicy
from novelizer.canon.proposal_service import ProposalService
from novelizer.scheduler import Scheduler
from novelizer.telemetry.bus import TelemetryBus
from novelizer.telemetry.recorder import TelemetryRecorder
from novelizer.telemetry.callbacks import TelemetryCallbackHandler
from novelizer.agents.author import build_author_runner
from novelizer.agents.world_architect import build_world_architect_runner
from novelizer.agents.character_keeper import build_character_keeper_runner
from novelizer.agents.editor import build_editor_runner
from novelizer.agents.continuity_checker import (
    build_continuity_checker_runner, build_continuity_mining_runner,
)
from novelizer.agents.retconner import build_retconner_runner
from novelizer.agents.structure_analyst import build_structure_analyst_runner
from novelizer.agents.plotter import build_plotter_runner
# NOTE: start() builds runners via each agent module's own build_X_runner binding
# (reached through AgentSpec.construct/_construct in registry.py), while
# apply_settings() below calls the build_X_runner names imported directly into
# this module's namespace -- two separate bindings to the same underlying
# functions. This split is intentional (a prior fix in this branch corrected a
# test that monkeypatched the wrong binding); keep both in sync if you touch either.
from novelizer.agents.registry import AGENT_REGISTRY
from novelizer.agents.registry_types import AgentContext
from novelizer.voices.loader import load_voice_pack
from novelizer.chat.service import ChatService
from novelizer.chat.runners import build_chat_runner
from novelizer.store.embeddings import EmbeddingStore
from novelizer.store.indexer import CanonIndexer

# Agents pinned into Runtime._tooling_pinned. author and continuity_checker are
# deliberately excluded here even though their SPECs carry a tool_grant (needed
# internally by their own _construct(ctx) functions) -- those two track their
# own tooling state via `.pull_mode` on the agent instance instead, read
# directly in apply_settings().
_TOOLING_PINNED_NAMES = frozenset({
    "world_architect", "character_keeper", "editor",
    "retconner", "structure_analyst", "plotter",
})


class Runtime:
    def __init__(
        self, settings: EffectiveSettings, runner=None, runners: Optional[dict] = None,
        embedding_store=None,
    ) -> None:
        self.settings = settings
        self.events = EventStore(settings.db_path)
        self.projector = Projector(self.events, settings.db_path)
        self.read = ReadStore(settings.db_path)
        self.telemetry_store = EventStore(str(Path(settings.db_path).with_name("telemetry.db")))
        self.telemetry_bus = TelemetryBus()
        self.telemetry = TelemetryRecorder(self.telemetry_store, self.telemetry_bus)
        self._llm_callbacks = [TelemetryCallbackHandler(self.telemetry)]
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
        self.plotter = None
        self.muse = None
        self.scheduler: Optional[Scheduler] = None
        self.voice_pack = None
        self.active_prose_profile = None
        self.chat: Optional[ChatService] = None
        self._chat_runner_cache: dict[str, object] = {}
        self._research_runner_cache = None
        self.embeddings = embedding_store   # None => built in start()
        self.indexer = None

    def _runner_for(self, name: str, builder, fallback_name: str | None = None):
        if self._runners is not None:
            if name in self._runners:
                return self._runners[name]
            # A derived role (e.g. the checker's mining runner) defaults to its
            # parent agent's injected fake: building the REAL runner here made
            # TUI tests hang on live connection attempts whenever the parent
            # agent actually ran (post-M5.3-merge test_app_layout failure).
            # The checker's isinstance guard treats a wrong-type response as
            # malformed, so sharing the parent fake is safe.
            if fallback_name is not None and fallback_name in self._runners:
                return self._runners[fallback_name]
            # Any other absent name falls back to the real builder — builders
            # construct lazily and never touch the network before ainvoke(),
            # and partial-roster fixtures only omit agents that never dispatch.
            return builder(self.settings, callbacks=self._llm_callbacks)
        if name == "author" and self._runner is not None:
            return self._runner
        return builder(self.settings, callbacks=self._llm_callbacks)

    def _phase_a_toolkit(self):
        """Build the (backend, tools) pair pull-mode agents use for canon file
        access + semantic search. Built once per start() and cached on
        self._canon_backend / self._canon_tools so later rebuilds (e.g.
        apply_settings) can reuse the same toolkit instead of losing it."""
        from deepagents.backends import CompositeBackend, StateBackend
        from novelizer.canon_fs.backend import CanonBackend
        from novelizer.canon_fs.outline import OutlineBackend
        from novelizer.canon_fs.search import build_search_canon_tool
        from novelizer.canon_fs.skills_route import build_skills_backend
        # deepagents' CompositeBackend fans unrouted-path globs (e.g. a bare
        # "**/*") out to every routed backend as well as default, so a
        # canon-scoped glob can surface /outline/ files alongside canon
        # files -- upstream fan-out behavior, not a bug in our routing.
        backend = CompositeBackend(
            default=CanonBackend(self.read),
            routes={
                "/outline/": OutlineBackend(self.read),
                "/skills/": build_skills_backend(),
                "/workspace/": StateBackend(),
            },
        )
        tools = [build_search_canon_tool(self.embeddings, self.read)]
        return backend, tools

    def _tooled(self, builder, enabled: bool):
        """Wrap a runner builder so pull-mode agents keep their canon
        backend/tools on every build -- both the initial start() build and any
        later apply_settings rebuild. Returns a plain builder(settings,
        callbacks=None) callable; when `enabled` is False it's the bare
        builder unchanged."""
        if not enabled:
            return builder
        backend, tools = self._canon_backend, self._canon_tools
        return lambda settings, callbacks=None: builder(
            settings, callbacks=callbacks, backend=backend, tools=tools,
        )

    def _chat_runner_for(self, agent_name: str):
        """Lazy per-agent chat runner. Injected fakes use key 'chat_<name>' in
        the runners dict; real runners are built on first use and cached."""
        key = f"chat_{agent_name}"
        if self._runners is not None and key in self._runners:
            return self._runners[key]
        if key not in self._chat_runner_cache:
            backend = getattr(self, "_canon_backend", None)
            tools = getattr(self, "_canon_tools", None)
            pull_mode = self.chat is not None and self.chat.pull_mode
            if pull_mode and backend is not None and tools is not None:
                self._chat_runner_cache[key] = build_chat_runner(
                    self.settings, agent_name, callbacks=self._llm_callbacks,
                    backend=backend, tools=tools,
                )
            else:
                self._chat_runner_cache[key] = build_chat_runner(self.settings, agent_name)
        return self._chat_runner_cache[key]

    def _research_runner_for(self):
        """Lazy research runner, built once and cached. Injected fakes use
        key 'research' in the runners dict."""
        if self._runners is not None and "research" in self._runners:
            return self._runners["research"]
        if self._research_runner_cache is None:
            from novelizer.research.runner import build_research_runner
            self._research_runner_cache = build_research_runner(
                self.settings, callbacks=self._llm_callbacks,
                backend=self._canon_backend, tools=self._canon_tools,
                read_store=self.read,
            )
        return self._research_runner_cache

    async def start(self) -> None:
        await self.events.init()
        await self.projector.init()
        await self.read.init()
        await self.telemetry_store.init()
        await self.projector.catch_up()
        if self.embeddings is None:
            self.embeddings = EmbeddingStore(
                str(Path(self.settings.db_path).with_name("embeddings")),
                embed_model=self.settings.embed_model,
                base_url=self.settings.llm_base_url,
                api_key=self.settings.llm_api_key,
            )
        self.indexer = CanonIndexer(
            self.events, self.read, self.embeddings,
            str(Path(self.settings.db_path).with_name("embed_cursor.json")),
        )
        await self.index_catch_up()  # backfill; failure-tolerant by contract
        self.policy = AutonomyPolicy(self.read)
        self.committer = GatingCommitter(self.events, self.policy)
        self.proposals = ProposalService(self.events)
        self.voice_pack = load_voice_pack(self.settings.voice_pack)
        self.active_prose_profile = self.voice_pack.profile(self.settings.prose_profile)
        casting_note = self.active_prose_profile.casting_note if self.active_prose_profile else ""
        personalities = self.voice_pack.agent_personalities
        s = self.settings
        self._canon_backend, self._canon_tools = self._phase_a_toolkit()
        provenance = {
            "model": s.author_model,
            "temperature": s.author_temperature,
            "voice_pack": self.voice_pack.name,
            "prose_profile": s.prose_profile,
        }
        ctx = AgentContext(
            read=self.read, committer=self.committer, events=self.events, settings=s,
            casting_note=casting_note, personalities=personalities, provenance=provenance,
            tooled=self._tooled, runner_for=self._runner_for,
        )
        self._tooling_pinned = {
            spec.name: spec.tool_grant.is_enabled(s)
            for spec in AGENT_REGISTRY
            if spec.tool_grant is not None and spec.name in _TOOLING_PINNED_NAMES
        }
        self.agents_by_name = {spec.name: spec.construct(ctx) for spec in AGENT_REGISTRY}
        self.world_architect = self.agents_by_name["world_architect"]
        self.character_keeper = self.agents_by_name["character_keeper"]
        self.muse = self.agents_by_name["muse"]
        self.plotter = self.agents_by_name["plotter"]
        self.author = self.agents_by_name["author"]
        self.editor = self.agents_by_name["editor"]
        self.continuity_checker = self.agents_by_name["continuity_checker"]
        self.retconner = self.agents_by_name["retconner"]
        self.structure_analyst = self.agents_by_name["structure_analyst"]
        # the planner ticks before the writer in a fresh room -- AGENT_REGISTRY
        # order encodes scheduling order, same as this list did before.
        self.agents = [self.agents_by_name[spec.name] for spec in AGENT_REGISTRY]
        for agent in self.agents:
            agent.telemetry = self.telemetry
        self.scheduler = Scheduler(
            self.agents, self.read,
            max_concurrent_agents=s.max_concurrent_agents, telemetry=self.telemetry,
        )
        self.chat = ChatService(
            self.events, self.read, self.committer, self._chat_runner_for,
            lambda name: self.voice_pack.agent_personalities.get(name, ""),
            pull_mode=s.chat_tools_enabled, telemetry=self.telemetry,
        )
        from novelizer.research.service import ResearchService
        self.research = ResearchService(self._research_runner_for)

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
            "plotter_interval": [self.plotter],
            "muse_interval": [self.muse],
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
            elif key == "muse_era":
                self.muse._era = new.muse_era
                applied.append(key)
            elif key == "muse_exclusion_hands":
                self.muse._exclusion_hands = new.muse_exclusion_hands
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
            author_builder = self._tooled(build_author_runner, self.author.pull_mode)
            self.author._runner = author_builder(stored, callbacks=self._llm_callbacks)
        if "agent_temperature" in changed and rebuild:
            world_architect_builder = self._tooled(
                build_world_architect_runner, self._tooling_pinned["world_architect"])
            self.world_architect._runner = world_architect_builder(stored, callbacks=self._llm_callbacks)
            character_keeper_builder = self._tooled(
                build_character_keeper_runner, self._tooling_pinned["character_keeper"])
            self.character_keeper._runner = character_keeper_builder(stored, callbacks=self._llm_callbacks)
            editor_builder = self._tooled(build_editor_runner, self._tooling_pinned["editor"])
            self.editor._runner = editor_builder(stored, callbacks=self._llm_callbacks)
            checker_builder = self._tooled(build_continuity_checker_runner, self.continuity_checker.pull_mode)
            self.continuity_checker._runner = checker_builder(stored, callbacks=self._llm_callbacks)
            self.continuity_checker._mining_runner = build_continuity_mining_runner(stored, callbacks=self._llm_callbacks)
            retconner_builder = self._tooled(build_retconner_runner, self._tooling_pinned["retconner"])
            self.retconner._runner = retconner_builder(stored, callbacks=self._llm_callbacks)
            structure_analyst_builder = self._tooled(
                build_structure_analyst_runner, self._tooling_pinned["structure_analyst"])
            self.structure_analyst._runner = structure_analyst_builder(stored, callbacks=self._llm_callbacks)
            plotter_builder = self._tooled(build_plotter_runner, self._tooling_pinned["plotter"])
            self.plotter._runner = plotter_builder(stored, callbacks=self._llm_callbacks)
        if ("agent_temperature" in changed or "author_temperature" in changed) and rebuild:
            self._chat_runner_cache.clear()

        if self.author is not None and self.author.provenance is not None:
            self.author.provenance = {
                "model": stored.author_model,
                "temperature": stored.author_temperature,
                "voice_pack": self.voice_pack.name,
                "prose_profile": stored.prose_profile,
            }

        self.settings = stored
        return {"applied": applied, "restart_required": restart, "errors": errors}

    async def index_catch_up(self) -> None:
        """Periodic-caller-safe embedding catch-up: no-op without an indexer,
        and never raises (CanonIndexer.catch_up swallows batch failures)."""
        if self.indexer is None:
            return
        await self.indexer.catch_up()

    async def close(self) -> None:
        await self.read.close()
        await self.projector.close()
        await self.telemetry_store.close()
        await self.events.close()
