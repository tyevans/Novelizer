from __future__ import annotations
from typing import Any
from pydantic import BaseModel


class EventType:
    WORLD_ENTRY_CREATED = "world_entry.created"
    WORLD_ENTRY_SUPERSEDED = "world_entry.superseded"
    CHARACTER_CREATED = "character.created"
    CHARACTER_UPDATED = "character.updated"
    CHAPTER_CREATED = "chapter.created"
    CHAPTER_STATUS_CHANGED = "chapter.status_changed"
    DIRECTOR_SIGNAL_CREATED = "director_signal.created"
    DIRECTOR_SIGNAL_CONSUMED = "director_signal.consumed"
    RETCON_REQUEST_CREATED = "retcon_request.created"
    RETCON_REQUEST_RESOLVED = "retcon_request.resolved"
    RETCON_REQUEST_REJECTED = "retcon_request.rejected"


class StoredEvent(BaseModel):
    sequence: int
    id: str
    event_type: str
    aggregate_id: str
    payload: dict[str, Any]
    created_at: str
