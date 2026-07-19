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
