from __future__ import annotations


def _enum_val(v):
    """Safely extract enum value, handling both enum and non-enum types."""
    return v.value if hasattr(v, "value") else v


async def browser_sections(read) -> list:
    chapters = await read.list_chapters()
    characters = await read.list_characters()
    world = await read.list_world_entries()
    retcons = await read.list_retcon_requests(status="open")
    themes = await read.list_themes()
    return [
        {"key": "chapters", "label": f"Chapters ({len(chapters)})",
         "items": [{"id": c.id, "label": f"{c.title} [{_enum_val(c.editorial_status)}]"} for c in chapters]},
        {"key": "characters", "label": f"Characters ({len(characters)})",
         "items": [{"id": c.id, "label": c.name} for c in characters]},
        {"key": "world", "label": f"World ({len(world)})",
         "items": [{"id": e.id, "label": f"[{_enum_val(e.domain)}] {e.title}"} for e in world]},
        {"key": "retcons", "label": f"Retcons ({len(retcons)})",
         "items": [{"id": r.id, "label": r.description[:40]} for r in retcons]},
        {"key": "themes", "label": f"Themes ({len(themes)})",
         "items": [{"id": t.id, "label": t.title} for t in themes]},
    ]


async def detail_text(read, section_key: str, item_id: str) -> str:
    if section_key == "chapters":
        ch = await read.get_chapter(item_id)
        return f"{ch.title}\n\n{ch.prose}" if ch else ""
    if section_key == "characters":
        c = await read.get_character(item_id)
        if not c:
            return ""
        detail = f"{c.name}\nTraits: {c.traits}\nArc: {c.arc_status}\nMotivations: {c.motivations}"
        if c.voice:
            detail += f"\nVoice: {c.voice}"
        return f"{detail}\n\n{c.backstory}"
    if section_key == "world":
        for e in await read.list_world_entries():
            if e.id == item_id:
                return f"{e.title}\n\n{e.body}"
        return ""
    if section_key == "retcons":
        for r in await read.list_retcon_requests():
            if r.id == item_id:
                return f"{r.description}\n\nProposed: {r.proposed_resolution}\nStatus: {_enum_val(r.status)}"
        return ""
    if section_key == "themes":
        theme = await read.get_theme(item_id)
        if not theme:
            return ""
        return f"{theme.title}\n\nTouched {theme.touch_count}x. Last note: {theme.last_note}"
    return ""
