from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class AgentTier(Enum):
    """How much machinery an agent's work actually needs.

    FULL: judgment. Reads context, may choose tools, iterates -- a deepagents
    graph earns its overhead here.

    LIGHT: deterministic shaping of text someone else wrote (labelling,
    one-line summaries). One structured call, no tools to choose between, and
    no reason to open a thinking block. A graph around this is a state machine
    wrapped around a single request.

    The distinction is about the *work*, not the output length: a short answer
    that required reading prose and deciding what matters is still FULL.
    """

    FULL = "full"
    LIGHT = "light"


@dataclass(frozen=True)
class ToolGrant:
    """Declares which Settings field gates an agent's canon-fs tooling."""
    enabled_setting: str

    def is_enabled(self, settings: Any) -> bool:
        return bool(getattr(settings, self.enabled_setting))


@dataclass(frozen=True)
class SubagentGrant:
    """Declares which Settings field gates an agent's subagent-dispatch access."""
    enabled_setting: str

    def is_enabled(self, settings: Any) -> bool:
        return bool(getattr(settings, self.enabled_setting))


@dataclass
class AgentContext:
    """Shared construction state passed to every AgentSpec.construct(ctx).

    `tooled` and `runner_for` are Runtime._tooled / Runtime._runner_for bound
    methods, passed through unchanged so each agent's construct() builds its
    runner(s) exactly the way runtime.py did before this registry existed.
    """
    read: Any
    committer: Any
    events: Any
    settings: Any
    casting_note: str
    personalities: dict
    provenance: dict
    tooled: Callable
    runner_for: Callable


@dataclass(frozen=True)
class AgentSpec:
    """One fiction-domain agent's declaration: its name, whether it can be
    tooled, and how to build it. `construct` owns full responsibility for
    that agent's actual (possibly non-uniform) constructor signature."""
    name: str
    tool_grant: ToolGrant | None
    construct: Callable[[AgentContext], Any]
    # Deliberately has no default: a new agent must state which tier it is.
    # A default would let agent fourteen inherit whichever tier was cheaper to
    # leave out, which is precisely the silent drift this field exists to stop.
    tier: AgentTier
    subagent_grant: SubagentGrant | None = None
    # Settings fields whose live change invalidates this agent's runner(s).
    # The host rebuilds by calling construct() again, so the agent's own
    # tooling/subagent wiring is reused rather than restated by the caller.
    # Declared next to the builder that reads the field: an agent that starts
    # depending on a new setting updates one line, in its own module.
    rebuild_on: tuple[str, ...] = ()
