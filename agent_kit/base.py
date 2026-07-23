from __future__ import annotations
import time
import uuid
from typing import Protocol

from agent_kit.run_context import current_agent_name, current_run_id
from agent_kit.telemetry import (
    AgentRunFailed,
    AgentRunFinished,
    AgentRunStarted,
    TelemetryEventType,
)

# An agent that ran on fresh material but explicitly chose not to act steps
# back for this many intervals instead of one, freeing dispatch slots.
PASS_BACKOFF_MULTIPLIER = 3


class Runner(Protocol):
    async def ainvoke(self, inputs: dict) -> dict: ...


class BaseAgent:
    """Generic poll/work/commit loop chassis, extracted from novelizer's
    BaseAgent (its fiction-specific commit helpers stay behind). Behavior is
    verbatim; the constructor drops the read_store/committer the generic
    half never used, and telemetry is an injected TelemetryEmitter."""

    name: str = "agent"

    def __init__(
        self,
        runner,
        interval: int,
        name: str | None = None,
        personality: str = "",
        clock=time.monotonic,
    ) -> None:
        self._runner = runner
        self.interval = interval
        if name is not None:
            self.name = name
        self.personality = personality
        self.paused = False
        self._last_run = 0.0
        self._backoff_until = 0.0
        self._last_fingerprint: tuple | None = None
        self.telemetry = None  # TelemetryEmitter; injected post-construction
        self._clock = clock

    @staticmethod
    def _guarded_line(label: str, value: str) -> str:
        """Return an optional "\\n\\n{label}: {value}" line, or "" if value is falsy."""
        return f"\n\n{label}: {value}" if value else ""

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def ready_for_interval(self, now: float) -> bool:
        return (now - self._last_run) >= self.interval and now >= self._backoff_until

    def mark_ran(self, now: float) -> None:
        self._last_run = now

    def seconds_until_ready(self, now: float) -> float:
        return max(0.0, self.interval - (now - self._last_run), self._backoff_until - now)

    def note_pass(self, now: float | None = None) -> None:
        """Record an explicit "nothing to do" verdict: back off for
        PASS_BACKOFF_MULTIPLIER intervals instead of one. Same clock family
        as the scheduler's default (time.monotonic); inject `clock` at
        construction to keep agent backoff and a scheduler's injected clock
        in the same timeline."""
        if now is None:
            now = self._clock()
        self._backoff_until = now + self.interval * PASS_BACKOFF_MULTIPLIER

    async def _fingerprint(self) -> tuple | None:
        """External state this agent's work depends on. None (default)
        disables watermarking. Subclasses return a small tuple; captured
        AFTER the agent's own commits, so its own writes never re-trigger it."""
        return None

    async def _gate_on_watermark(self, score: float) -> float:
        fp = await self._fingerprint()
        if fp is not None and fp == self._last_fingerprint:
            return 0.0
        return score

    async def _record_watermark(self) -> None:
        self._last_fingerprint = await self._fingerprint()

    def _clear_watermark(self) -> None:
        self._last_fingerprint = None

    async def readiness(self) -> float:
        return 0.0

    async def _run(self) -> None:
        """Subclasses put their poll/work/commit body here; run_once brackets
        it with machinery telemetry and ambient run context."""

    async def run_once(self) -> None:
        run_id = str(uuid.uuid4())
        started = time.monotonic()
        rid_token = current_run_id.set(run_id)
        name_token = current_agent_name.set(self.name)
        await self._emit_telemetry(
            TelemetryEventType.AGENT_RUN_STARTED, run_id,
            AgentRunStarted(run_id=run_id, agent_name=self.name),
        )
        try:
            await self._run()
        except Exception as e:
            phase = "llm_call" if (self.telemetry and self.telemetry.in_llm_call(run_id)) else "agent"
            await self._emit_telemetry(
                TelemetryEventType.AGENT_RUN_FAILED, run_id,
                AgentRunFailed(run_id=run_id, agent_name=self.name,
                               error_type=type(e).__name__, error_message=str(e),
                               phase=phase, duration_s=time.monotonic() - started),
            )
            raise
        else:
            await self._emit_telemetry(
                TelemetryEventType.AGENT_RUN_FINISHED, run_id,
                AgentRunFinished(run_id=run_id, agent_name=self.name,
                                 duration_s=time.monotonic() - started),
            )
        finally:
            current_run_id.reset(rid_token)
            current_agent_name.reset(name_token)

    async def _emit_telemetry(self, event_type: str, aggregate_id: str, payload) -> None:
        if self.telemetry is None:
            return
        await self.telemetry.emit(event_type, aggregate_id, payload)
