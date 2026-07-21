import pytest
from novelizer.store.kg_store import KGStore


@pytest.fixture
async def store(tmp_path):
    s = KGStore(str(tmp_path / "world.db"))
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_upsert_entity_is_idempotent_by_name_and_type(store):
    first_id = await store.upsert_entity("Eldara", "character", "a potion master")
    second_id = await store.upsert_entity("eldara", "character", "updated description")

    assert first_id == second_id
    entity = await store.get_entity(first_id)
    assert entity["name"] == "Eldara"
    assert entity["description"] == "updated description"


@pytest.mark.asyncio
async def test_upsert_relation_is_idempotent(store):
    a = await store.upsert_entity("Eldara", "character")
    b = await store.upsert_entity("Grimm", "character")

    first_id = await store.upsert_relation(a, b, "friend_of")
    second_id = await store.upsert_relation(a, b, "friend_of")

    assert first_id == second_id
    relations = await store.entity_relations(a)
    assert len(relations) == 1
    assert relations[0]["relation_type"] == "friend_of"
    assert relations[0]["other_name"] == "Grimm"
    assert relations[0]["direction"] == "out"


@pytest.mark.asyncio
async def test_clear_mentions_for_fingerprint_removes_only_that_fingerprint(store):
    a = await store.upsert_entity("The Salted Gull", "location")
    await store.link_mention(a, "chapter-1-v1")
    await store.link_mention(a, "chapter-2-v1")

    cleared = await store.clear_mentions_for_fingerprint("chapter-1-v1")

    assert cleared == [a]
    # chapter-2-v1's mention survives
    cleared_again = await store.clear_mentions_for_fingerprint("chapter-1-v1")
    assert cleared_again == []
