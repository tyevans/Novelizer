from __future__ import annotations
from novelizer.canon.events import EventType
from novelizer.store.models import DirectorSignal, SignalKind


async def seed(events, text: str) -> None:
    sig = DirectorSignal(kind=SignalKind.seed, body=text)
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)


async def focus(events, entity: str) -> None:
    sig = DirectorSignal(kind=SignalKind.focus, body=entity)
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)


def pause(scheduler, agent_name: str) -> None:
    scheduler.pause_agent(agent_name)


def resume(scheduler, agent_name: str) -> None:
    scheduler.resume_agent(agent_name)


async def dispatch(runtime, line: str) -> str:
    parts = line.strip().split(maxsplit=1)
    if not parts:
        return "Empty command."
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    if cmd == "seed" and arg:
        await seed(runtime.events, arg)
        return f"Seed injected: {arg}"
    if cmd == "focus" and arg:
        await focus(runtime.events, arg)
        return f"Focus set: {arg}"
    if cmd == "pause" and arg:
        pause(runtime.scheduler, arg)
        return f"Paused: {arg}"
    if cmd == "resume" and arg:
        resume(runtime.scheduler, arg)
        return f"Resumed: {arg}"
    return f"Unknown command: {line.strip()}"
