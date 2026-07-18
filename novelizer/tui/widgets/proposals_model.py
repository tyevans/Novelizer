from __future__ import annotations


def proposal_line(p) -> str:
    return f"◇ {p.id[:8]}  {p.proposing_agent} → {p.target_event_type}"


async def pending_lines(read) -> list[str]:
    props = await read.list_proposals(status="open")
    return [proposal_line(p) for p in props]
