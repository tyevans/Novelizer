from __future__ import annotations


async def browser_sections(read) -> list:
    chapters = await read.list_chapters()
    characters = await read.list_characters()
    world = await read.list_world_entries()
    retcons = await read.list_retcon_requests(status="open")
    return [
        {"key": "chapters", "label": f"Chapters ({len(chapters)})",
         "items": [{"id": c.id, "label": f"{c.title} [{c.editorial_status.value}]"} for c in chapters]},
        {"key": "characters", "label": f"Characters ({len(characters)})",
         "items": [{"id": c.id, "label": c.name} for c in characters]},
        {"key": "world", "label": f"World ({len(world)})",
         "items": [{"id": e.id, "label": f"[{e.domain.value if hasattr(e.domain,'value') else e.domain}] {e.title}"} for e in world]},
        {"key": "retcons", "label": f"Retcons ({len(retcons)})",
         "items": [{"id": r.id, "label": r.description[:40]} for r in retcons]},
    ]


async def detail_text(read, section_key: str, item_id: str) -> str:
    if section_key == "chapters":
        ch = await read.get_chapter(item_id)
        return f"{ch.title}\n\n{ch.prose}" if ch else ""
    if section_key == "characters":
        c = await read.get_character(item_id)
        if not c:
            return ""
        return f"{c.name}\nTraits: {c.traits}\nArc: {c.arc_status}\nMotivations: {c.motivations}\n\n{c.backstory}"
    if section_key == "world":
        for e in await read.list_world_entries():
            if e.id == item_id:
                return f"{e.title}\n\n{e.body}"
        return ""
    if section_key == "retcons":
        for r in await read.list_retcon_requests():
            if r.id == item_id:
                return f"{r.description}\n\nProposed: {r.proposed_resolution}\nStatus: {r.status.value if hasattr(r.status,'value') else r.status}"
        return ""
    return ""
