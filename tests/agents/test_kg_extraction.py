import pytest
from novelizer.agents.kg_extraction import kg_extraction_prompt
from novelizer.agents.schemas import KGExtractedEntity, KGExtractedRelation, KGExtractionOutput


def test_kg_extraction_prompt_includes_title_and_prose():
    prompt = kg_extraction_prompt("The Salted Gull", "Eldara walked into the tavern.")

    assert "The Salted Gull" in prompt
    assert "Eldara walked into the tavern." in prompt


def test_extraction_output_schema_round_trips():
    out = KGExtractionOutput(
        entities=[KGExtractedEntity(name="Eldara", entity_type="character")],
        relations=[KGExtractedRelation(source="Eldara", target="Grimm", relation_type="friend_of")],
    )

    dumped = out.model_dump()
    restored = KGExtractionOutput.model_validate(dumped)

    assert restored.entities[0].name == "Eldara"
    assert restored.relations[0].relation_type == "friend_of"
