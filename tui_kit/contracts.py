"""Domain-agnostic event vocabulary and theming contract for tui_kit.

A consuming domain (novelizer, a future research or coding domain) adapts
its own telemetry into these dataclasses and supplies an AgentTheme -- this
module has no knowledge of any concrete domain's event or agent shapes.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol


class AgentTheme(Protocol):
    """How a domain presents its agents: glyph/color/verb per agent name."""

    def glyph(self, agent_name: str) -> str: ...
    def label(self, agent_name: str) -> str: ...
    def style(self, agent_name: str) -> str: ...
    def verb(self, agent_name: str) -> str: ...


@dataclass(frozen=True)
class RunStarted:
    run_id: str
    agent_name: str


@dataclass(frozen=True)
class RunFinished:
    run_id: str
    agent_name: str
    duration_s: float = 0.0


@dataclass(frozen=True)
class RunFailed:
    run_id: str
    agent_name: str
    error_type: str
    error_message: str


@dataclass(frozen=True)
class LLMCallStarted:
    run_id: str
    agent_name: str
    call_index: int
    model: str
    prompt: str


@dataclass(frozen=True)
class LLMCallFinished:
    run_id: str
    agent_name: str
    call_index: int
    duration_s: float
    output_tokens: int


@dataclass(frozen=True)
class ToolCallStarted:
    run_id: str
    agent_name: str
    tool_name: str
    input_summary: str
    delegate: str = ""


@dataclass(frozen=True)
class ToolCallFinished:
    run_id: str
    agent_name: str
    tool_name: str
    duration_s: float
    output_summary: str = ""
    input_summary: str = ""  # pairs the result with the exact call when
    # several same-named tool calls run in parallel; "" falls back to
    # last-running-same-tool matching
    sequence: int = 0  # originating event's store sequence, for on-demand full-output lookup


@dataclass(frozen=True)
class ToolCallFailed:
    run_id: str
    agent_name: str
    tool_name: str
    duration_s: float
    error_type: str
    input_summary: str = ""  # same pairing contract as ToolCallFinished
    sequence: int = 0


@dataclass(frozen=True)
class TokenDelta:
    run_id: str
    agent_name: str
    text: str
    kind: str = "text"  # "text" | "thinking"


@dataclass(frozen=True)
class ToolSummaryReady:
    run_id: str
    agent_name: str
    tool_name: str
    input_summary: str
    summary: str
