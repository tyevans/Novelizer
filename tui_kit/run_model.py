"""Pure live-view state machine and formatters for a generic "watch N
agents run" console. No Textual imports: everything here is black-box
testable. Consumes tui_kit.contracts events; a domain adapts its own
telemetry into those before calling apply_bus_item.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from rich.text import Text
from tui_kit.contracts import (
    AgentTheme, LLMCallFinished, LLMCallStarted, RunFailed, RunFinished,
    RunStarted, ToolCallFailed, ToolCallFinished, ToolCallStarted, TokenDelta,
    ToolSummaryReady,
)

TEXT_CAP = 8000


def normalize_input_summary(raw) -> str:
    """Canonical normalization for a tool call's input_summary: newlines
    swapped for a visible marker so single-line rendering stays intact, and
    truncated to 120 chars. The ToolCallStarted branch of apply_bus_item and
    a ToolSummaryReady producer MUST agree exactly on this, since
    apply_bus_item's ToolSummaryReady handler matches on this string."""
    return str(raw).replace("\n", "␤")[:120]


PREVIEW_CAP = 200


@dataclass(frozen=True)
class _BaseBlock:
    agent_name: str = ""


@dataclass(frozen=True)
class ProseBlock(_BaseBlock):
    text: str = ""


@dataclass(frozen=True)
class ThinkingBlock(_BaseBlock):
    text: str = ""


@dataclass(frozen=True)
class CallBlock(_BaseBlock):
    call_index: int = 0
    model: str = ""
    status: str = "running"  # running | done
    duration_s: float = 0.0


@dataclass(frozen=True)
class ToolBlock(_BaseBlock):
    tool_name: str = ""
    input_summary: str = ""
    status: str = "running"  # running | done | failed
    duration_s: float = 0.0
    error: str = ""
    summary: str | None = None
    repeat_count: int = 1
    delegate: str = ""  # subagent name when this tool call was dispatched by
    # a subagent rather than the parent agent itself
    preview: str = ""   # first PREVIEW_CAP chars, for the collapsed line
    sequence: int = 0   # store sequence; full output is read from there


StreamBlock = ProseBlock | ThinkingBlock | CallBlock | ToolBlock


def make_preview(raw: str) -> str:
    return str(raw).replace("\n", " ")[:PREVIEW_CAP]


def block_agent(b: StreamBlock) -> str:
    return b.agent_name


def block_key(b: StreamBlock, index: int) -> str:
    """Stable identity for widget reconciliation. Index within the run is
    enough: blocks are append-only and never reordered, and a block's kind
    never changes once opened."""
    return f"{type(b).__name__}:{index}"


@dataclass(frozen=True)
class LiveRunState:
    status: str = "idle"  # idle | running | finished | failed
    run_id: str = ""
    agent_name: str = ""
    started_at: float = 0.0  # monotonic
    ended_at: float = 0.0
    tokens: int = 0
    blocks: tuple[StreamBlock, ...] = ()
    prompt: str = ""
    model: str = ""
    call_index: int = 0
    error: str = ""
    stream_attached: bool = True
    last_kind: str = ""  # "" | "text" | "thinking" — tracks stream-segment
    # boundaries so a switch between reasoning and answer content gets a
    # visible marker instead of running together unlabeled.


def _append_text_block(state: LiveRunState, cls: type, text: str,
                       agent_name: str = "") -> LiveRunState:
    """Append to the trailing block if it's the same kind, else open a new one."""
    if state.blocks and isinstance(state.blocks[-1], cls):
        last = state.blocks[-1]
        merged = (last.text + text)[-TEXT_CAP:]
        blocks = state.blocks[:-1] + (replace(last, text=merged),)
    else:
        blocks = state.blocks + (cls(text=text[-TEXT_CAP:], agent_name=agent_name),)
    return replace(state, blocks=blocks)


def apply_bus_item(state: LiveRunState, item, now: float) -> LiveRunState:
    if isinstance(item, TokenDelta):
        if state.status != "running" or item.run_id != state.run_id:
            return state
        kind = item.kind or "text"
        block_cls = ThinkingBlock if kind == "thinking" else ProseBlock
        state = _append_text_block(state, block_cls, item.text, agent_name=item.agent_name)
        return replace(state, tokens=state.tokens + 1, last_kind=kind)
    if isinstance(item, ToolSummaryReady):
        if item.run_id != state.run_id or not state.blocks:
            return state
        for i in range(len(state.blocks) - 1, -1, -1):
            b = state.blocks[i]
            if (isinstance(b, ToolBlock) and b.tool_name == item.tool_name
                    and b.input_summary == item.input_summary
                    and b.status != "running" and b.summary is None):
                blocks = state.blocks[:i] + (replace(b, summary=item.summary),) + state.blocks[i + 1:]
                return replace(state, blocks=blocks)
        return state
    if isinstance(item, RunStarted):
        return LiveRunState(status="running", run_id=item.run_id,
                            agent_name=item.agent_name, started_at=now)
    if not isinstance(item, (RunFinished, RunFailed, LLMCallStarted, LLMCallFinished,
                             ToolCallStarted, ToolCallFinished, ToolCallFailed)):
        return state
    if item.run_id != state.run_id:
        return state
    if isinstance(item, LLMCallStarted):
        state = replace(state, prompt=item.prompt, model=item.model, call_index=item.call_index)
        blocks = state.blocks + (CallBlock(call_index=item.call_index, model=item.model,
                                           agent_name=item.agent_name),)
        return replace(state, blocks=blocks)
    if isinstance(item, LLMCallFinished):
        state = replace(state, tokens=item.output_tokens)
        if state.blocks and isinstance(state.blocks[-1], CallBlock):
            last = state.blocks[-1]
            blocks = state.blocks[:-1] + (replace(last, status="done",
                                                  duration_s=item.duration_s),)
            state = replace(state, blocks=blocks)
        return state
    if isinstance(item, RunFinished):
        return replace(state, status="finished", ended_at=now)
    if isinstance(item, RunFailed):
        error = f"{item.error_type}: {item.error_message}"
        return replace(state, status="failed", ended_at=now, error=error)
    if isinstance(item, ToolCallStarted):
        input_summary = normalize_input_summary(item.input_summary)
        if (state.blocks and isinstance(state.blocks[-1], ToolBlock)
                and state.blocks[-1].tool_name == item.tool_name
                and state.blocks[-1].input_summary == input_summary
                and state.blocks[-1].delegate == item.delegate
                and state.blocks[-1].status != "running"):
            last = state.blocks[-1]
            blocks = state.blocks[:-1] + (replace(last, status="running",
                                                  repeat_count=last.repeat_count + 1,
                                                  summary=None),)
        else:
            blocks = state.blocks + (ToolBlock(tool_name=item.tool_name,
                                               input_summary=input_summary, status="running",
                                               delegate=item.delegate,
                                               agent_name=item.agent_name),)
        return replace(state, blocks=blocks)
    if isinstance(item, (ToolCallFinished, ToolCallFailed)):
        # Prefer the running block whose input_summary matches the result's,
        # so parallel same-named calls each get their own output; fall back
        # to last-running-same-tool for producers that omit input_summary.
        wanted = normalize_input_summary(item.input_summary) if item.input_summary else ""
        candidates = [
            i for i in range(len(state.blocks) - 1, -1, -1)
            if isinstance(state.blocks[i], ToolBlock)
            and state.blocks[i].tool_name == item.tool_name
            and state.blocks[i].status == "running"
        ]
        matched = [i for i in candidates if wanted and state.blocks[i].input_summary == wanted]
        for i in matched or candidates:
            b = state.blocks[i]
            if isinstance(item, ToolCallFinished):
                updated = replace(b, status="done", duration_s=item.duration_s,
                                  preview=make_preview(item.output_summary),
                                  sequence=item.sequence)
            else:
                updated = replace(b, status="failed", duration_s=item.duration_s,
                                  error=item.error_type)
            blocks = state.blocks[:i] + (updated,) + state.blocks[i + 1:]
            return replace(state, blocks=blocks)
        return state
    return state


def seed_state(recent: list, now: float) -> LiveRunState:
    state = LiveRunState()
    for ev in recent:
        state = apply_bus_item(state, ev, now)
    if state.status == "running":
        # We rebooted mid-run: the ephemeral token stream from before the
        # restart is gone — say so instead of pretending to stream.
        state = replace(state, stream_attached=False)
    return state


def route_agent(item) -> str | None:
    """Which agent a contract event belongs to, for fanning a single stream
    out into per-agent buckets."""
    return getattr(item, "agent_name", None) or None


def seed_states(recent: list, now: float) -> dict[str, LiveRunState]:
    """Per-agent counterpart to seed_state: groups replayed events by agent
    and folds each group independently, so one agent's run doesn't clobber
    another's on replay."""
    by_agent: dict[str, list] = {}
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


def strip_line(state: LiveRunState, now: float, theme: AgentTheme, next_hint: str = "") -> str:
    if state.status == "running":
        verb = theme.verb(state.agent_name)
        elapsed = int(now - state.started_at)
        call = state.call_index or 1
        return (f"▶ {state.agent_name} · {verb} · {_fmt_tokens(state.tokens)} · "
                f"{elapsed}s · call {call}")
    if state.status == "failed":
        return (f"✗ {state.agent_name} crashed {_fmt_ago(now - state.ended_at)} ago "
                f"(see Engine Room)")
    return f"idle · {next_hint}" if next_hint else "idle"


def vitals_line(state: LiveRunState, now: float, theme: AgentTheme, hold: str = "") -> str:
    """`hold` is the scheduler's reason this agent is not producing (see
    tui_kit.widgets.roster.hold_phrase). It captions the idle line only: the
    hold is polled on a different cadence than the token stream, so a leftover
    reason must never caption a live run."""
    if state.status == "running":
        verb = theme.verb(state.agent_name)
        model = state.model or "?"
        return (f"{state.agent_name} · {verb} · {model} · call {state.call_index or 1} · "
                f"{_fmt_tokens(state.tokens)} · {int(now - state.started_at)}s")
    if state.status == "failed":
        return f"{state.agent_name} · crashed · {state.error}"
    if state.status == "finished":
        return (f"{state.agent_name} · finished · "
                f"{int(state.ended_at - state.started_at)}s · {_fmt_tokens(state.tokens)}")
    return f"idle — {hold}" if hold else "idle — waiting for the scheduler"


def live_body(state: LiveRunState) -> str:
    if state.status == "running" and not state.stream_attached:
        return "run in progress (stream not attached — restarted mid-run)"
    if state.status == "idle":
        return "no run yet"
    lines: list[str] = []
    for b in state.blocks:
        lines.append(_render_block(b))
    body = "\n".join(lines).strip("\n")
    if state.status == "failed" and body:
        return body + "\n\n✗ crashed"
    return body or "(waiting for first token…)"


def _render_block(b: StreamBlock) -> str:
    if isinstance(b, ProseBlock):
        return b.text
    if isinstance(b, ThinkingBlock):
        return f"💭 {b.text}"
    if isinstance(b, CallBlock):
        header = f"▸ call {b.call_index} ({b.model})"
        if b.status == "done":
            return header + f"\n   ↳ {b.duration_s:.1f}s"
        return header
    # ToolBlock
    suffix = f" ×{b.repeat_count}" if b.repeat_count > 1 else ""
    indent = "       " if b.delegate else "   "
    if b.delegate:
        lines = [f"    ⚒ ↳ {b.delegate}: {b.tool_name}({b.input_summary}){suffix}"]
    else:
        lines = [f"⚒ {b.tool_name}({b.input_summary}){suffix}"]
    if b.status == "done":
        lines.append(f"{indent}↳ done in {b.duration_s:.1f}s")
    elif b.status == "failed":
        lines.append(f"{indent}↳ ✗ {b.error}")
    if b.summary:
        lines.append(f"{indent}↳ {b.summary}")
    if b.preview:
        for out_line in b.preview.split("\n"):
            lines.append(f"{indent}  {out_line}")
    return "\n".join(lines)


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


# Rich styles per stream_line_kind(); "" leaves prose in the theme default so
# the agent's own accent color (applied to the vitals bar) stays the visual
# anchor rather than competing with a wall of colored prose.
_LINE_STYLES = {"tool": "bold cyan", "call": "dim", "thinking": "italic dim magenta"}


def styled_vitals(state: LiveRunState, now: float, theme: AgentTheme,
                  hold: str = "") -> Text:
    glyph = theme.glyph(state.agent_name)
    style = theme.style(state.agent_name)
    return Text(f"{glyph} {vitals_line(state, now, theme, hold)}", style=style)


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
