from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolGrant:
    """Declares which Settings field gates an agent's canon-fs tooling."""
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
