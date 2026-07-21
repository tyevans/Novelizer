from novelizer.store.kg_structured import facts_from_character, facts_from_world_entry
from novelizer.store.models import Character, CharacterRelationship, WorldEntry


def test_facts_from_character_with_no_relationships():
    char = Character(id="c1", name="Eldara", traits="sharp-tongued", backstory="a potion master")

    entities, relations = facts_from_character(char)

    assert len(entities) == 1
    assert entities[0].name == "Eldara"
    assert entities[0].entity_type == "character"
    assert entities[0].canon_id == "c1"
    assert entities[0].description == "sharp-tongued"
    assert relations == []


def test_facts_from_character_with_relationships_needs_target_lookup():
    char = Character(
        id="c1", name="Eldara",
        relationships=[CharacterRelationship(target_character_id="c2", description="old friend")],
    )

    entities, relations = facts_from_character(char, character_names={"c2": "Grimm"})

    assert len(relations) == 1
    assert relations[0].source_name == "Eldara"
    assert relations[0].target_name == "Grimm"
    assert relations[0].relation_type == "old friend"


def test_facts_from_character_skips_relationship_with_unknown_target():
    char = Character(
        id="c1", name="Eldara",
        relationships=[CharacterRelationship(target_character_id="c2", description="old friend")],
    )

    entities, relations = facts_from_character(char, character_names={})

    assert relations == []


def test_facts_from_world_entry():
    entry = WorldEntry(id="w1", title="The Salted Gull", body="a dockside tavern")

    entities, relations = facts_from_world_entry(entry)

    assert len(entities) == 1
    assert entities[0].name == "The Salted Gull"
    assert entities[0].entity_type == "world_entry"
    assert entities[0].canon_id == "w1"
    assert entities[0].description == "a dockside tavern"
    assert relations == []
