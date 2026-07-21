from __future__ import annotations
from dataclasses import dataclass
from novelizer.store.models import Character, WorldEntry


@dataclass
class EntityFact:
    name: str
    entity_type: str
    description: str = ""
    canon_id: str | None = None


@dataclass
class RelationFact:
    source_name: str
    source_type: str
    target_name: str
    target_type: str
    relation_type: str


def facts_from_character(
    char: Character, character_names: dict[str, str] | None = None,
) -> tuple[list[EntityFact], list[RelationFact]]:
    """character_names maps character id -> name, needed to resolve
    relationship targets since Character.relationships only carries the
    target's id. Caller (KGProjector, Task 5) supplies this from
    ReadStore.list_characters(). A relationship whose target id isn't in
    the map (e.g. the target character was retconned away) is skipped
    rather than raising -- see the third test case above."""
    names = character_names or {}
    entities = [EntityFact(
        name=char.name, entity_type="character",
        description=char.traits, canon_id=char.id,
    )]
    relations = []
    for rel in char.relationships:
        target_name = names.get(rel.target_character_id)
        if target_name is None:
            continue
        relations.append(RelationFact(
            source_name=char.name, source_type="character",
            target_name=target_name, target_type="character",
            relation_type=rel.description,
        ))
    return entities, relations


def facts_from_world_entry(entry: WorldEntry) -> tuple[list[EntityFact], list[RelationFact]]:
    entities = [EntityFact(
        name=entry.title, entity_type="world_entry",
        description=entry.body, canon_id=entry.id,
    )]
    return entities, []
