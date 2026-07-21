"""Pure live-view state machine and formatters for the Engine Room.

No Textual imports: everything here is black-box testable. Widgets render
what these functions return; the app folds bus items through
apply_bus_item and re-renders.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from rich.text import Text
from novelizer.canon.events import StoredEvent
from novelizer.telemetry.events import TelemetryEventType, TokenDelta
from novelizer.tui.identity import identity_for

TEXT_CAP = 8000

_VERBS = {
    "author": "drafting",
    "editor": "reviewing",
    "world_architect": "worldbuilding",
    "character_keeper": "tending characters",
    "continuity_checker": "checking continuity",
    "retconner": "retconning",
    "structure_analyst": "scoring structure",
}

# Mirrors AGENT_REGISTRY's scheduling order in novelizer/agents/registry.py --
# kept as a plain tuple (not imported) so this module stays free of the heavy
# agent-construction import chain. Keep in sync if agents are added/removed.
AGENT_NAMES = (
    "world_architect", "character_keeper", "muse", "plotter", "author",
    "editor", "continuity_checker", "retconner", "structure_analyst",
)


@dataclass(frozen=True)
class LiveRunState:
    status: str = "idle"  # idle | running | finished | failed
    run_id: str = ""
    agent_name: str = ""
    started_at: float = 0.0  # monotonic
    ended_at: float = 0.0
    tokens: int = 0
    text: str = ""
    prompt: str = ""
    model: str = ""
    call_index: int = 0
    error: str = ""
    stream_attached: bool = True
    last_kind: str = ""  # "" | "text" | "thinking" — tracks stream-segment
    # boundaries so a switch between reasoning and answer content gets a
    # visible marker instead of running together unlabeled.


def _append(state: LiveRunState, line: str) -> LiveRunState:
    text = line if not state.text else state.text + line
    return replace(state, text=text[-TEXT_CAP:])


def apply_bus_item(state: LiveRunState, item, now: float) -> LiveRunState:
    if isinstance(item, TokenDelta):
        if state.status != "running" or item.run_id != state.run_id:
            return state
        kind = item.kind or "text"
        marker = ""
        if kind != state.last_kind:
            if kind == "thinking":
                marker = "\n💭 "
            elif state.last_kind == "thinking":
                marker = "\n"
        text = (state.text + marker + item.text)[-TEXT_CAP:]
        return replace(state, text=text, tokens=state.tokens + 1, last_kind=kind)
    if not isinstance(item, StoredEvent):
        return state
    p = item.payload
    et = item.event_type
    if et == TelemetryEventType.AGENT_RUN_STARTED:
        # A fresh run always starts attached: even if the previous state was a
        # seeded not-attached run, this event arriving live means we're live.
        return LiveRunState(status="running", run_id=p.get("run_id", ""),
                            agent_name=p.get("agent_name", ""), started_at=now)
    if p.get("run_id") != state.run_id:
        return state
    if et == TelemetryEventType.LLM_CALL_STARTED:
        state = replace(state, prompt=p.get("prompt", ""), model=p.get("model", ""),
                        call_index=p.get("call_index", 0))
        line = f"\n▸ call {p.get('call_index', '?')} ({p.get('model', '?')})\n"
        return _append(state, line)
    if et == TelemetryEventType.LLM_CALL_FINISHED:
        state = replace(state, tokens=p.get("output_tokens", state.tokens))
        line = (f"   ↳ {p.get('duration_s', 0):.1f}s · {p.get('output_tokens', 0)} tok\n")
        return _append(state, line)
    if et == TelemetryEventType.AGENT_RUN_FINISHED:
        return replace(state, status="finished", ended_at=now)
    if et == TelemetryEventType.AGENT_RUN_FAILED:
        error = f"{p.get('error_type', '?')}: {p.get('error_message', '')}"
        return replace(state, status="failed", ended_at=now, error=error)
    if et == TelemetryEventType.TOOL_CALL_STARTED:
        summary = str(p.get("input_summary", "")).replace("\n", "␤")[:120]
        return _append(state, f"\n⚒ {p.get('tool_name', '?')}({summary})\n")
    if et == TelemetryEventType.TOOL_CALL_FINISHED:
        return _append(state, f"   ↳ done in {p.get('duration_s', 0):.1f}s\n")
    if et == TelemetryEventType.TOOL_CALL_FAILED:
        return _append(state, f"   ↳ ✗ {p.get('error_type', '?')}\n")
    return state


def seed_state(recent: list[StoredEvent], now: float) -> LiveRunState:
    state = LiveRunState()
    for ev in recent:
        state = apply_bus_item(state, ev, now)
    if state.status == "running":
        # We rebooted mid-run: the ephemeral token stream from before the
        # restart is gone — say so instead of pretending to stream.
        state = replace(state, stream_attached=False)
    return state


def route_agent(item) -> str | None:
    """Which agent a bus item (TokenDelta or StoredEvent) belongs to, for
    fanning a single stream out into per-agent buckets."""
    if isinstance(item, TokenDelta):
        return item.agent_name or None
    if isinstance(item, StoredEvent):
        return item.payload.get("agent_name") or None
    return None


def seed_states(recent: list[StoredEvent], now: float) -> dict[str, LiveRunState]:
    """Per-agent counterpart to seed_state: groups replayed events by agent
    and folds each group independently, so one agent's run doesn't clobber
    another's on replay."""
    by_agent: dict[str, list[StoredEvent]] = {}
    for ev in recent:
        agent = route_agent(ev)
        if agent:
            by_agent.setdefault(agent, []).append(ev)
    return {agent: seed_state(events, now) for agent, events in by_agent.items()}


def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k tok" if n >= 1000 else f"{n} tok"


def _fmt_ago(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}m" if s >= 60 else f"{s}s"


def strip_line(state: LiveRunState, now: float, next_hint: str = "") -> str:
    if state.status == "running":
        verb = _VERBS.get(state.agent_name, "working")
        elapsed = int(now - state.started_at)
        call = state.call_index or 1
        return (f"▶ {state.agent_name} · {verb} · {_fmt_tokens(state.tokens)} · "
                f"{elapsed}s · call {call}")
    if state.status == "failed":
        return (f"✗ {state.agent_name} crashed {_fmt_ago(now - state.ended_at)} ago "
                f"(see Engine Room)")
    return f"idle · {next_hint}" if next_hint else "idle"


def vitals_line(state: LiveRunState, now: float) -> str:
    if state.status == "running":
        verb = _VERBS.get(state.agent_name, "working")
        model = state.model or "?"
        return (f"{state.agent_name} · {verb} · {model} · call {state.call_index or 1} · "
                f"{_fmt_tokens(state.tokens)} · {int(now - state.started_at)}s")
    if state.status == "failed":
        return f"{state.agent_name} · crashed · {state.error}"
    if state.status == "finished":
        return (f"{state.agent_name} · finished · "
                f"{int(state.ended_at - state.started_at)}s · {_fmt_tokens(state.tokens)}")
    return "idle — waiting for the scheduler"


def live_body(state: LiveRunState) -> str:
    if state.status == "running" and not state.stream_attached:
        return "run in progress (stream not attached — restarted mid-run)"
    if state.status == "idle":
        return "no run yet"
    if state.status == "failed" and state.text:
        return state.text + "\n\n✗ crashed"
    return state.text or "(waiting for first token…)"


def stream_line_kind(line: str) -> str:
    """Classify a live_body() line for widget-level styling. Pure/text-only
    so it stays testable without Rich or Textual."""
    s = line.strip()
    if s.startswith("⚒"):
        return "tool"
    if s.startswith("▸") or s.startswith("↳"):
        return "call"
    if s.startswith("💭"):
        return "thinking"
    return "prose"


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
        summary = str(p.get('input_summary', '')).replace("\n", "␤")[:120]
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


# Rich styles per stream_line_kind(); "" leaves prose in the theme default so
# the agent's own accent color (applied to the vitals bar) stays the visual
# anchor rather than competing with a wall of colored prose.
_LINE_STYLES = {"tool": "bold cyan", "call": "dim", "thinking": "italic dim magenta"}


def styled_vitals(state: LiveRunState, now: float) -> Text:
    ident = identity_for(state.agent_name)
    return Text(f"{ident.glyph} {vitals_line(state, now)}", style=ident.style)


def styled_body(body: str) -> Text:
    # Text objects are never markup-parsed regardless of a Static's
    # markup=False setting, so untrusted stream content (tool summaries,
    # prompts) stays safe here the same way it does as a plain str.
    text = Text()
    lines = body.split("\n")
    for i, line in enumerate(lines):
        style = _LINE_STYLES.get(stream_line_kind(line), "")
        text.append(line, style=style)
        if i != len(lines) - 1:
            text.append("\n")
    return text
