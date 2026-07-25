"""Translates novelizer's real telemetry vocabulary (StoredEvent +
TelemetryEventType, plus the bus-only TokenDelta/ToolSummaryReady) into
tui_kit.contracts events, and formats the durable machinery trace.

trace_line/trace_detail stay here rather than in tui_kit: they render
*domain* events ("produced: chapter.created ch-12"), which is inherently
novelizer-specific, not part of the generic agent-run vocabulary.
"""
from __future__ import annotations
from novelizer.canon.events import StoredEvent
from novelizer.telemetry.events import TelemetryEventType
from novelizer.telemetry.events import TokenDelta as NovelizerTokenDelta
from novelizer.telemetry.events import ToolSummaryReady as NovelizerToolSummaryReady
from tui_kit import contracts
from tui_kit.run_model import normalize_input_summary


def to_contract_event(item):
    """Translate one bus item into a tui_kit.contracts event, or None if it
    carries nothing the generic run model renders (scheduler events, or any
    unrecognized shape)."""
    if isinstance(item, NovelizerTokenDelta):
        return contracts.TokenDelta(run_id=item.run_id, agent_name=item.agent_name,
                                    text=item.text, kind=item.kind)
    if isinstance(item, NovelizerToolSummaryReady):
        return contracts.ToolSummaryReady(run_id=item.run_id, agent_name=item.agent_name,
                                          tool_name=item.tool_name,
                                          input_summary=item.input_summary, summary=item.summary)
    if not isinstance(item, StoredEvent):
        return None
    p = item.payload
    et = item.event_type
    if et == TelemetryEventType.AGENT_RUN_STARTED:
        return contracts.RunStarted(run_id=p.get("run_id", ""), agent_name=p.get("agent_name", ""))
    if et == TelemetryEventType.AGENT_RUN_FINISHED:
        return contracts.RunFinished(run_id=p.get("run_id", ""), agent_name=p.get("agent_name", ""),
                                     duration_s=p.get("duration_s", 0.0))
    if et == TelemetryEventType.AGENT_RUN_FAILED:
        return contracts.RunFailed(run_id=p.get("run_id", ""), agent_name=p.get("agent_name", ""),
                                   error_type=p.get("error_type", "?"),
                                   error_message=p.get("error_message", ""))
    if et == TelemetryEventType.AGENT_RUN_CANCELLED:
        # Terminal, so the live block must close -- an unmapped terminal event
        # would leave the agent reading as running forever. tui_kit's run model
        # has only finished/failed, and "cancelled" is the nearer of the two
        # (it did not complete); the error_type carries the distinction the
        # generic model has no status for, and the durable trace below keeps it
        # spelled out.
        return contracts.RunFailed(run_id=p.get("run_id", ""), agent_name=p.get("agent_name", ""),
                                   error_type="CancelledError", error_message="run cancelled")
    if et == TelemetryEventType.LLM_CALL_STARTED:
        return contracts.LLMCallStarted(run_id=p.get("run_id", ""), agent_name=p.get("agent_name", ""),
                                        call_index=p.get("call_index", 0), model=p.get("model", ""),
                                        prompt=p.get("prompt", ""))
    if et == TelemetryEventType.LLM_CALL_FINISHED:
        return contracts.LLMCallFinished(run_id=p.get("run_id", ""),
                                         agent_name=p.get("agent_name", ""),
                                         call_index=p.get("call_index", 0),
                                         duration_s=p.get("duration_s", 0.0),
                                         output_tokens=p.get("output_tokens", 0))
    if et == TelemetryEventType.TOOL_CALL_STARTED:
        return contracts.ToolCallStarted(run_id=p.get("run_id", ""), agent_name=p.get("agent_name", ""),
                                         tool_name=p.get("tool_name", "?"),
                                         input_summary=p.get("input_summary", ""),
                                         delegate=p.get("delegate", ""))
    if et == TelemetryEventType.TOOL_CALL_FINISHED:
        return contracts.ToolCallFinished(run_id=p.get("run_id", ""),
                                          agent_name=p.get("agent_name", ""),
                                          tool_name=p.get("tool_name", "?"),
                                          duration_s=p.get("duration_s", 0.0),
                                          output_summary=p.get("output_summary", ""),
                                          input_summary=p.get("input_summary", ""))
    if et == TelemetryEventType.TOOL_CALL_FAILED:
        return contracts.ToolCallFailed(run_id=p.get("run_id", ""), agent_name=p.get("agent_name", ""),
                                        tool_name=p.get("tool_name", "?"),
                                        duration_s=p.get("duration_s", 0.0),
                                        error_type=p.get("error_type", "?"),
                                        input_summary=p.get("input_summary", ""))
    return None


def _t(ev: StoredEvent) -> str:
    return ev.created_at[11:19]


def trace_line(ev: StoredEvent) -> str:
    p = ev.payload
    et = ev.event_type
    if et == TelemetryEventType.AGENT_RUN_STARTED:
        return f"{_t(ev)} {p.get('agent_name', '?')} run started"
    if et == TelemetryEventType.AGENT_RUN_FINISHED:
        return f"{_t(ev)} {p.get('agent_name', '?')} run ✓ {p.get('duration_s', 0):.0f}s"
    if et == TelemetryEventType.AGENT_RUN_FAILED:
        return (f"{_t(ev)} {p.get('agent_name', '?')} run ✗ {p.get('error_type', '?')} "
                f"({p.get('phase', '?')})")
    if et == TelemetryEventType.AGENT_RUN_CANCELLED:
        return (f"{_t(ev)} {p.get('agent_name', '?')} run ⊘ cancelled "
                f"({p.get('phase', '?')})")
    if et == TelemetryEventType.LLM_CALL_STARTED:
        return (f"{_t(ev)} {p.get('agent_name', '?')} llm call {p.get('call_index', '?')} "
                f"started ({p.get('model', '?')})")
    if et == TelemetryEventType.LLM_CALL_FINISHED:
        return (f"{_t(ev)} {p.get('agent_name', '?')} llm call {p.get('call_index', '?')} "
                f"✓ {p.get('duration_s', 0):.0f}s · {p.get('output_tokens', 0)} tok")
    if et == TelemetryEventType.LLM_CALL_FAILED:
        return (f"{_t(ev)} {p.get('agent_name', '?')} llm call {p.get('call_index', '?')} "
                f"✗ {p.get('error_type', '?')}")
    if et == TelemetryEventType.TOOL_CALL_STARTED:
        summary = normalize_input_summary(p.get('input_summary', ''))
        return (f"{_t(ev)} ⚒ {p.get('agent_name', '?')} → "
                f"{p.get('tool_name', '?')}({summary})")
    if et == TelemetryEventType.TOOL_CALL_FINISHED:
        return (f"{_t(ev)} ⚒ {p.get('agent_name', '?')} ← {p.get('tool_name', '?')} "
                f"({p.get('duration_s', 0):.1f}s)")
    if et == TelemetryEventType.TOOL_CALL_FAILED:
        return (f"{_t(ev)} ⚒ {p.get('agent_name', '?')} ✗ {p.get('tool_name', '?')}: "
                f"{p.get('error_type', '?')}")
    if et == TelemetryEventType.SCHEDULER_PICKED:
        return f"{_t(ev)} scheduler picked {p.get('agent_name', '?')}"
    if et == TelemetryEventType.SCHEDULER_ELIGIBILITY_CHANGED:
        flag = "eligible" if p.get("eligible") else "ineligible"
        return f"{_t(ev)} {p.get('agent_name', '?')} {flag}: {p.get('reason', '?')}"
    return f"{_t(ev)} {et}"


def trace_detail(ev: StoredEvent, produced: list[StoredEvent]) -> str:
    lines = [trace_line(ev), ""]
    p = dict(ev.payload)
    prompt = p.pop("prompt", None)
    for k, v in p.items():
        lines.append(f"{k}: {v}")
    for d in produced:
        lines.append(f"produced: {d.event_type} {d.aggregate_id}")
    if prompt is not None:
        lines += ["", "─ prompt ─", prompt]
    return "\n".join(lines)
