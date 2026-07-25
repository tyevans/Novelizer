from __future__ import annotations
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from novelizer.settings import EffectiveSettings, RESTART_REQUIRED_KEYS
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import GatingCommitter
from novelizer.canon.policy import AutonomyPolicy
from novelizer.canon.proposal_service import ProposalService
from novelizer.canon.events import EventType
from agent_kit import AdaptivePool, Scheduler
from novelizer.store.models import SignalKind
from novelizer.telemetry.bus import TelemetryBus
from novelizer.telemetry.recorder import TelemetryRecorder
from novelizer.telemetry.callbacks import TelemetryCallbackHandler
# Runner builders are NOT imported here. Both start() and apply_settings() reach
# them through each agent's own AgentSpec.construct(ctx), so there is exactly one
# binding per builder -- the agent module's. The previous second binding in this
# namespace was how the rebuild path drifted from the construction path (it
# skipped agents and dropped subagent grants); a test that patches a builder now
# patches the only binding there is.
from novelizer.agents.registry import AGENT_REGISTRY
from novelizer.agents.registry_types import AgentContext
from novelizer.voices.loader import load_voice_pack
from novelizer.chat.service import ChatService
from novelizer.chat.runners import build_chat_runner
from novelizer.store.embeddings import EmbedProbe, EmbeddingStore, EmbedProbeFailure
from novelizer.store.indexer import CanonIndexer
from novelizer.store.kg_store import KGStore
from novelizer.store.kg_projector import KGProjector

logger = logging.getLogger(__name__)

# Agents pinned into Runtime._tooling_pinned. author and continuity_checker are
# deliberately excluded here even though their SPECs carry a tool_grant (needed
# internally by their own _construct(ctx) functions) -- those two track their
# own tooling state via `.pull_mode` on the agent instance instead, read
# directly in apply_settings().
_TOOLING_PINNED_NAMES = frozenset({
    "world_architect", "character_keeper", "editor",
    "retconner", "structure_analyst", "plotter",
})


def _make_override_provider(read_store):
    """agent_kit.Scheduler's override seam, carrying novelizer's Director
    override semantics: the first unconsumed override signal naming a target
    agent wins (exactly the branch the deleted novelizer/scheduler.py had
    inline)."""
    async def provider() -> str | None:
        signals = await read_store.list_unconsumed_signals()
        return next(
            (s.target_agent for s in signals
             if s.kind == SignalKind.override and s.target_agent),
            None,
        )
    return provider


def _make_gate_provider(indexer, kg_projector):
    """agent_kit.Scheduler's strict background-first gate seam, carrying
    novelizer's policy: agents dispatch only when BOTH the embedding indexer and
    the KG projector are fully caught up. The two lags count different event sets
    and are not interchangeable, so both must reach zero before agents may act.

    A None indexer means the runtime is running without it -- index_catch_up /
    kg_catch_up are themselves no-op-guarded on None -- so there is nothing to
    wait on: treat that side's lag as 0 (open)."""
    async def provider() -> bool:
        index_lag = 0 if indexer is None else await indexer.lag()
        kg_lag = 0 if kg_projector is None else await kg_projector.lag()
        return index_lag == 0 and kg_lag == 0
    return provider


# Canon events that do not count as an agent having made progress. A remark is
# an agent talking about its work, not doing it -- and BaseAgent._remark()
# commits one as a real canon event (novelizer/agents/base.py), so without this
# exclusion every agent that chatters "nothing to add this pass" would read as
# productive and the idle ladder would never engage for anyone.
#
# Signal consumption is bookkeeping for the same reason. An agent with pending
# director signals deliberately skips note_pass() even when it has decided to
# do nothing (so input is never silently stranded), then marks the signals
# consumed -- leaving a run whose only commit says "I read this and declined".
# Counting that as work keeps a converged agent at full cadence for as long as
# a director keeps trickling signals in. Excluding it costs nothing: a run that
# also did something real commits something else too, and still reads as
# progress.
NON_PROGRESS_EVENT_TYPES = frozenset({
    EventType.AGENT_REMARKED,
    EventType.DIRECTOR_SIGNAL_CONSUMED,
})


def _make_progress_probe(events):
    """agent_kit.BaseAgent's progress seam, answered from the event log rather
    than declared by the agent: GatingCommitter stamps every commit with the
    ambient run id, so "did this run make progress?" is exactly "did it commit
    anything to canon that wasn't just chatter?".

    Measured rather than self-reported, so it holds for agents added later
    without each one growing a bespoke fingerprint method."""
    async def probe(run_id: str) -> bool:
        committed = await events.events_for_run(run_id)
        return any(e.event_type not in NON_PROGRESS_EVENT_TYPES for e in committed)
    return probe


def build_embedding_store(settings) -> EmbeddingStore:
    """Construct the story's EmbeddingStore exactly as the running room does.

    Factored out so `novelizer doctor` probes the SAME endpoint, model, key and
    persist path the runtime will use. A doctor that built its own store from
    its own reading of settings could pass while the room fails, which is worse
    than no doctor at all.
    """
    return EmbeddingStore(
        str(Path(settings.db_path).with_name("embeddings")),
        embed_model=settings.embed_model,
        base_url=settings.resolved_embed_base_url,
        api_key=settings.resolved_embed_api_key,
    )


# One phrase per failure mode. Each names a DIFFERENT thing to go and do, so
# none of them may fall through to a generic "embedding failed": that sentence
# would leave the operator guessing between a host that is down, a model name
# that does not exist there, and a key that was rejected.
_PROBE_PROBLEM = {
    EmbedProbeFailure.unreachable: "is unreachable",
    EmbedProbeFailure.timeout: "did not respond in time",
    EmbedProbeFailure.unauthorized: "rejected our credentials",
    EmbedProbeFailure.http_error: "could not be reached",
    EmbedProbeFailure.no_vectors: "returned no vector for model {model!r}",
    EmbedProbeFailure.no_such_model: "has no model {model!r}",
}


def embed_probe_message(probe: EmbedProbe, settings) -> str:
    """The single operator-facing line for a failed probe, shared by the runtime
    and `novelizer doctor` so both entry points say exactly the same thing.

    Lives here rather than on EmbedProbe because the REMEDY depends on settings
    the store deliberately does not read. When embed_base_url is unset the
    embedding endpoint IS the chat endpoint -- by design, for all-local setups --
    so telling that operator to "check embed_base_url" blames them for a setting
    they never made; the fix is to set one. When they DID configure a dedicated
    endpoint, that endpoint and the model name are the things to check.

    One line, because it goes to a log and to a status readout.
    """
    problem = _PROBE_PROBLEM[probe.failure].format(model=probe.model)
    if settings.embed_base_url.strip():
        remedy = " — check embed_base_url and embed_model"
    else:
        remedy = (" — it is shared with the chat endpoint (embed_base_url is unset); "
                  "set embed_base_url if this host serves chat but not embeddings")
    return (f"embedding endpoint {probe.endpoint} {problem}; "
            f"semantic search will be unavailable{remedy} ({probe.error})")


@dataclass
class BackgroundProgress:
    """A single queryable snapshot of how far behind the two background drains
    are: the CanonIndexer's embedding lag and the KGProjector's extraction lag.

    A named type rather than a bare (index, kg) tuple because the status bar
    reads these BY NAME -- a swapped pair would be a silent bug -- and because
    the strict background gate (see _make_gate_provider) freezes EVERY agent
    while either side lags. The user accepted that a down embed/KG endpoint can
    stall the whole room ONLY on the condition that the freeze stays legible:
    the operator must be able to watch the backlog drain, otherwise a freeze is
    indistinguishable from a hang. This type is that visible signal, spanning
    BOTH indexers (the two lags count different event sets -- 24 canon event
    types vs the projector's 6 -- so watching only one would miss a freeze
    caused by the other)."""

    index_lag: int
    kg_lag: int

    @property
    def total(self) -> int:
        return self.index_lag + self.kg_lag

    @property
    def caught_up(self) -> bool:
        return self.total == 0


async def _safe_lag(indexer) -> int:
    """Read one drain's lag() without ever letting it escape into the status
    loop. A None indexer means the runtime is running without it -- nothing to
    wait on -- so it contributes 0, mirroring _make_gate_provider's None
    handling. A lag() that raises (a real "database is locked" under DB
    contention) is swallowed and read as unknown == 0, mirroring the never-raise
    contract of index_catch_up / kg_catch_up: one probe failing must not blank
    the readout for the healthy side."""
    if indexer is None:
        return 0
    try:
        return await indexer.lag()
    except Exception as e:
        logger.warning("background lag probe failed (%s: %s); reporting that side as 0",
                       type(e).__name__, e)
        return 0


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
        self.summarizer = None
        self.plotter = None
        self.muse = None
        self.scheduler: Optional[Scheduler] = None
        self.voice_pack = None
        self.active_prose_profile = None
        self.chat: Optional[ChatService] = None
        self._chat_runner_cache: dict[str, object] = {}
        self._research_runner_cache = None
        self.embeddings = embedding_store   # None => built in start()
        # Last endpoint probe (see _probe_embeddings); None until start() runs.
        self.embed_probe: Optional[EmbedProbe] = None
        self.indexer = None
        self.kg_store = None
        self.kg_projector = None

    def _runner_for(self, name: str, builder, fallback_name: str | None = None, settings=None):
        # `settings` is the settings the caller is building FOR. It matters only
        # on the apply_settings rebuild path: self.settings is still the old
        # value there (it is assigned last, so a failed apply leaves the runtime
        # describing what is actually running), so building from self.settings
        # would hand the new runner the temperature it was meant to replace.
        # During start() the two are the same object.
        settings = settings if settings is not None else self.settings
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
            return builder(settings, callbacks=self._llm_callbacks)
        if name == "author" and self._runner is not None:
            return self._runner
        return builder(settings, callbacks=self._llm_callbacks)

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
        tools = [build_search_canon_tool(self.embeddings, self.read, self.kg_store)]
        return backend, tools

    def _tooled(self, builder, enabled: bool, subagent_enabled: bool = False,
                subagent_agent_name: str = ""):
        """Wrap a runner builder so pull-mode agents keep their canon
        backend/tools on every build -- both the initial start() build and any
        later apply_settings rebuild. Returns a plain builder(settings,
        callbacks=None) callable; when `enabled` is False it's the bare
        builder unchanged.

        subagent_enabled additionally passes a `subagents=[researcher]` kwarg
        through to the builder -- a no-op when `enabled` is False, since a
        subagent with no backend/tools to read from is moot (settings guard,
        subagent-tooling design decision 6)."""
        if not enabled:
            return builder
        from novelizer.agents.subagents import build_researcher_subagent
        backend, tools = self._canon_backend, self._canon_tools
        subagents = [build_researcher_subagent(subagent_agent_name)] if subagent_enabled else None
        return lambda settings, callbacks=None: builder(
            settings, callbacks=callbacks, backend=backend, tools=tools, subagents=subagents,
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
            self.embeddings = build_embedding_store(self.settings)
        await self._probe_embeddings()
        # One shared LLM concurrency ceiling for the whole fleet, built BEFORE
        # the projectors so both the scheduler and the two background drains draw
        # permits from this single object -- the shared endpoint ceiling that does
        # not hold today (two independent LLM consumers, the 429-pileup source).
        # Created here (not after the agents) because the backfill catch_up calls
        # below already drain through it.
        self.pool = AdaptivePool(self.settings.llm_pool_size)
        self.indexer = CanonIndexer(
            self.events, self.read, self.embeddings,
            str(Path(self.settings.db_path).with_name("embed_cursor.json")),
            pool=self.pool, drain_concurrency=self.settings.background_drain_concurrency,
        )
        await self.index_catch_up()  # backfill; failure-tolerant by contract
        self.kg_store = KGStore(self.settings.db_path)
        await self.kg_store.init()
        from novelizer.agents.kg_extraction import build_kg_extraction_runner
        kg_runner = build_kg_extraction_runner(self.settings, callbacks=self._llm_callbacks)
        self.kg_projector = KGProjector(
            self.events, self.read, self.kg_store, self.embeddings, kg_runner,
            str(Path(self.settings.db_path).with_name("kg_cursor.json")),
            pool=self.pool, drain_concurrency=self.settings.background_drain_concurrency,
        )
        await self.kg_catch_up()  # backfill; failure-tolerant by contract
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
        ctx = self._agent_context(s, casting_note, personalities, provenance)
        self._tooling_pinned = {
            spec.name: spec.tool_grant.is_enabled(s)
            for spec in AGENT_REGISTRY
            if spec.tool_grant is not None and spec.name in _TOOLING_PINNED_NAMES
        }
        # Every tool/subagent grant flag, frozen at the value start() built with.
        # Tooling is inert-until-restart by design (M5 contract): a mid-session
        # flag flip must not change what a later rebuild wires up. Rebuilds
        # therefore construct against settings with these flags pinned back,
        # which lets each agent's own construct() read ctx.settings normally and
        # still honour the contract -- no second, parallel notion of "is this
        # agent tooled" for a caller to keep in sync.
        self._grant_pins = {
            grant.enabled_setting: bool(getattr(s, grant.enabled_setting))
            for spec in AGENT_REGISTRY
            for grant in (spec.tool_grant, spec.subagent_grant)
            if grant is not None
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
        self.summarizer = self.agents_by_name["summarizer"]
        # the planner ticks before the writer in a fresh room -- AGENT_REGISTRY
        # order encodes scheduling order, same as this list did before.
        self.agents = [self.agents_by_name[spec.name] for spec in AGENT_REGISTRY]
        progress_probe = _make_progress_probe(self.events)
        for agent in self.agents:
            agent.telemetry = self.telemetry
            # Injected post-construction alongside telemetry: novelizer's
            # BaseAgent subclass does not thread kit kwargs through, and
            # eleven agent constructors should not have to grow one.
            agent.progress_probe = progress_probe
        # self.pool (the one shared LLM concurrency ceiling) was built above,
        # before the projectors, so the scheduler and both background drains draw
        # permits from the same object -- one fleet-wide ceiling on a single
        # endpoint, the 429-pileup fix.
        self.scheduler = Scheduler(
            self.agents,
            max_concurrent_agents=s.max_concurrent_agents, telemetry=self.telemetry,
            override_provider=_make_override_provider(self.read),
            gate_provider=_make_gate_provider(self.indexer, self.kg_projector),
            pool=self.pool,
        )
        self.chat = ChatService(
            self.events, self.read, self.committer, self._chat_runner_for,
            lambda name: self.voice_pack.agent_personalities.get(name, ""),
            pull_mode=s.chat_tools_enabled, telemetry=self.telemetry,
            advisory_token_budget=s.advisory_token_budget,
        )
        from novelizer.research.service import ResearchService
        self.research = ResearchService(self._research_runner_for, telemetry=self.telemetry)

    def _agent_context(self, settings, casting_note: str, personalities: dict,
                       provenance: dict) -> AgentContext:
        """The construction context every AgentSpec.construct(ctx) receives.

        Shared by start() and apply_settings() so a rebuilt runner is wired
        exactly like the original -- including the agent's tooling and subagent
        grants, which live inside its own construct().
        """
        def runner_for(name: str, builder, fallback_name: str | None = None):
            # Bound to *these* settings, so a rebuild builds for the settings
            # being applied rather than the ones still recorded on self.
            return self._runner_for(name, builder, fallback_name, settings=settings)

        return AgentContext(
            read=self.read, committer=self.committer, events=self.events, settings=settings,
            casting_note=casting_note, personalities=personalities, provenance=provenance,
            tooled=self._tooled, runner_for=runner_for,
        )

    # Runner attributes an agent may hold. Rebuilding copies whichever exist
    # from a freshly constructed instance onto the live one, so the live agent
    # keeps its accumulated state (counters, backoff ladders, telemetry) and
    # only its LLM plumbing is swapped.
    _RUNNER_ATTRS = ("_runner", "_mining_runner")

    def _rebuild_runners_for(self, changed: set[str], settings) -> list[str]:
        """Re-run construct() for every agent declaring a changed rebuild_on key.

        Returns the names rebuilt. Reusing construct() is what keeps this honest:
        the previous hand-written block named seven agents and passed `_tooled`
        without the subagent flag, so a live temperature change both skipped
        agents (curator, triage) and silently dropped subagent grants from the
        ones it did rebuild.
        """
        casting_note = self.active_prose_profile.casting_note if self.active_prose_profile else ""
        personalities = self.voice_pack.agent_personalities
        provenance = {
            "model": settings.author_model, "temperature": settings.author_temperature,
            "voice_pack": self.voice_pack.name, "prose_profile": settings.prose_profile,
        }
        # Tooling is pinned at start(); everything else comes from the settings
        # being applied.
        ctx = self._agent_context(
            settings.model_copy(update=self._grant_pins),
            casting_note, personalities, provenance,
        )
        rebuilt: list[str] = []
        for spec in AGENT_REGISTRY:
            if not (set(spec.rebuild_on) & changed):
                continue
            live = self.agents_by_name.get(spec.name)
            if live is None:
                continue
            fresh = spec.construct(ctx)
            for attr in self._RUNNER_ATTRS:
                if hasattr(fresh, attr):
                    setattr(live, attr, getattr(fresh, attr))
            rebuilt.append(spec.name)
        return rebuilt

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
        # DEPRECATED (Phase 2, event-driven scheduling): these seven agent
        # *_interval keys no longer gate dispatch. ready() = now >=
        # max(_fail_until, _idle_until) -- interval is not in that expression;
        # the fail/idle backoff ladders govern cadence now. The keys are kept
        # accepted-and-inert purely for config back-compat: removing them would
        # hard-error on load for every existing story.toml / config.toml that
        # still carries one. The agent.interval writes below are a harmless
        # inert no-op (the value is stored but never read for dispatch), kept so
        # an interval change is still recorded in `applied` (never a restart)
        # and a reload of the same file stays a clean no-op. NOTE:
        # projector_interval is NOT here and is NOT deprecated -- it still paces
        # the TUI projector/scheduler/status loops.
        interval_map = {
            "author_interval": [self.author],
            "default_agent_interval": [self.world_architect, self.character_keeper, self.editor, self.retconner],
            "continuity_interval": [self.continuity_checker],
            "structure_analyst_interval": [self.structure_analyst],
            "summarizer_interval": [self.summarizer],
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
            elif key == "llm_pool_size":
                # Poke the running pool's ceiling in place -- no reconstruction,
                # no restart. AIMD keeps managing _limit under the new size from
                # here, exactly like max_concurrent_agents applies live.
                self.pool.size = new.llm_pool_size
                applied.append(key)
            elif key == "background_drain_concurrency":
                # Push the new fan-out cap onto both projectors live -- read on
                # the next catch_up pass, no reconstruction, mirroring
                # llm_pool_size. Both drains share this global-only knob.
                self.indexer._drain_concurrency = new.background_drain_concurrency
                self.kg_projector._drain_concurrency = new.background_drain_concurrency
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
        if rebuild:
            # Every agent that declares a changed key in its SPEC.rebuild_on,
            # rebuilt through its own construct(). No list here to fall out of
            # date when an agent is added or starts reading a new setting.
            if self._rebuild_runners_for(set(changed), stored):
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

    async def kg_catch_up(self) -> None:
        """Periodic-caller-safe KG catch-up: no-op without a projector, and
        never raises (KGProjector.catch_up swallows batch failures)."""
        if self.kg_projector is None:
            return
        await self.kg_projector.catch_up()

    async def background_progress(self) -> BackgroundProgress:
        """Single queryable snapshot of both background drains' lag, for the
        legibility half of the strict background gate: while either indexer
        lags, the gate freezes every agent (see _make_gate_provider), so the
        operator must be able to SEE the backlog draining or a freeze looks like
        a hang. Reads the CanonIndexer's embedding lag and the KGProjector's
        extraction lag -- they count DIFFERENT event sets and are not
        interchangeable, so both are surfaced.

        Never raises and never blocks on a missing drain: each side is read
        through _safe_lag, so a None indexer contributes 0 and a lag() that hits
        "database is locked" reads as unknown == 0 while the healthy side is
        still reported -- the same never-raise contract index_catch_up /
        kg_catch_up hold, because this drives a status loop that must not
        crash."""
        return BackgroundProgress(
            index_lag=await _safe_lag(self.indexer),
            kg_lag=await _safe_lag(self.kg_projector),
        )

    async def _probe_embeddings(self) -> None:
        """One embed round-trip, before the backfill it is there to explain.

        Deliberately NEVER fatal, in either configuration. Two reasons, and the
        second is new:

          * With embed_base_url unset the embedding endpoint is the CHAT endpoint
            by design (the supported all-local setup), where a cold server that
            is not up yet is a boot-order race, not a misconfiguration. Refusing
            to start over it would brick a legitimate config.
          * A failed probe now degrades HONESTLY. search_canon answers "Search
            unavailable ...; browse the canon filesystem with ls/glob/grep
            instead" on an empty index rather than inventing a confident miss, so
            booting with a dead index is a legible degraded mode instead of a
            silent corruption of every agent's reasoning. That is what makes
            loudness sufficient here and hard failure unnecessary; the hard,
            exit-code failure lives in `novelizer doctor`, whose job is to fail.

        Logged at ERROR because it is a real, novel-degrading fault -- and only on
        failure, since an ERROR line on every healthy boot would train the
        operator to ignore the one that matters.
        """
        self.embed_probe = await self.embeddings.probe()
        if not self.embed_probe.ok:
            logger.error("%s", embed_probe_message(self.embed_probe, self.settings))

    async def index_document_count(self) -> int | None:
        """How many documents the semantic index actually holds, or None if that
        is unknown (no store wired, or the probe failed).

        The one number that makes a DEAD index visible. Every other readout
        reports a zero-document index as healthy: lag() is 0 because the cursor
        believes it consumed the backlog, catch_up() returns success, and
        search_canon answers every query with a confident miss. Production ran
        that way for 690 consecutive search_canon calls, all of them wrong, and
        nothing said so.

        Unknown is None, NOT 0 -- the opposite of _safe_lag's convention, and
        deliberately: there, 0 means "healthy" and is the safe reading to
        default to; here 0 IS the alarm, so a locked database must not raise it.
        Never raises, because this drives the same status loop background_progress
        does.
        """
        if self.embeddings is None:
            return None
        try:
            return await self.embeddings.document_count()
        except Exception as e:
            logger.warning("semantic index size probe failed (%s: %s); reporting unknown",
                           type(e).__name__, e)
            return None

    async def close(self) -> None:
        # start() constructs the EmbeddingStore and nothing else owns it, so
        # close() owes its release. Omitting it made every caller's
        # `finally: await rt.close()` a no-op for the one resource that holds a
        # sqlite handle and worker threads (see EmbeddingStore.close).
        if self.embeddings is not None:
            self.embeddings.close()
            self.embeddings = None
        await self.read.close()
        await self.projector.close()
        await self.telemetry_store.close()
        await self.events.close()
        if self.kg_store is not None:
            await self.kg_store.close()
