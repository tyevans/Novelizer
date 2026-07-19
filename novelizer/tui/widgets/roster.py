from __future__ import annotations

_ERROR_SNIPPET_LEN = 40


def roster_summary(status: list) -> str:
    """One-line agent summary for the status bar. Idle agents collapse to a
    count; only running/paused/errored agents are named."""
    if not status:
        return "no agents"
    parts: list[str] = []
    running = [s["name"] for s in status if s.get("running")]
    if running:
        parts.append(f"● {', '.join(running)}")
    else:
        # Fast agents finish between status polls; fall back to the sticky
        # last-completed marker so the bar always names the active agent.
        last = [s["name"] for s in status if s.get("last_completed")]
        if last:
            parts.append(f"◦ {last[0]}")
    paused = [s["name"] for s in status if s.get("paused")]
    if paused:
        parts.append(f"⏸ {', '.join(paused)}")
    for s in status:
        err = s.get("last_error")
        if err:
            parts.append(f"⚠ {s['name']}: {err[:_ERROR_SNIPPET_LEN]}")
    if not parts:
        return f"{len(status)} agents idle"
    return "  ".join(parts)
