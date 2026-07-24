import pytest
from novelizer.agents.base import ChapterDraft
from novelizer.agents.schemas import (
    WorldEntryDraft, WorldEntriesDraft, CharacterUpdate, FlagDraft,
    KeeperOutput, EditorVerdict, ContinuityOutput, RetconAmendments,
)


def test_world_entries_draft_roundtrip():
    d = WorldEntriesDraft(entries=[WorldEntryDraft(title="Brinemarsh", body="salt flats")])
    again = WorldEntriesDraft.model_validate_json(d.model_dump_json())
    assert again.entries[0].domain == "physical"


def test_world_entry_draft_coerces_unknown_domain_to_other():
    # Regression: the live retconner looped forever because the LLM answered
    # domain="character", which passed this draft schema and then blew up the
    # store-side Domain enum in Retconner.commit.
    assert WorldEntryDraft(title="T", body="B", domain="character").domain == "other"


def test_world_entry_draft_preserves_known_domains():
    for d in ("physical", "social", "metaphysical", "historical", "other"):
        assert WorldEntryDraft(title="T", body="B", domain=d).domain == d


def test_world_entry_draft_domains_match_store_domain_enum():
    # The LLM-facing schema must enumerate exactly the store's legal domains,
    # so the model is told the choices instead of guessing free text.
    from novelizer.store.models import Domain
    schema = WorldEntryDraft.model_json_schema()
    assert set(schema["properties"]["domain"]["enum"]) == {d.value for d in Domain}


def test_keeper_output_defaults_empty():
    k = KeeperOutput()
    assert k.updated_characters == [] and k.flags == []


def test_editor_verdict_literal():
    assert EditorVerdict(verdict="revise", notes="tighten act two").verdict == "revise"


def test_retcon_amendments_carry_supersedes():
    a = RetconAmendments(amended_entries=[WorldEntryDraft(title="North", body="v2", supersedes_id="old1")])
    assert a.amended_entries[0].supersedes_id == "old1"


def test_continuity_and_character_shapes():
    ContinuityOutput(flags=[FlagDraft(category="contradiction", description="x", proposed_resolution="y")])
    assert CharacterUpdate(id="c1", arc_status="wary").traits is None


def test_feed_note_defaults_empty_on_all_response_schemas():
    assert ChapterDraft(title="T", prose="P").feed_note == ""
    assert WorldEntriesDraft().feed_note == ""
    assert KeeperOutput().feed_note == ""
    assert EditorVerdict().feed_note == ""
    assert ContinuityOutput().feed_note == ""
    assert RetconAmendments().feed_note == ""


def test_feed_note_roundtrips_when_set():
    draft = ChapterDraft(title="T", prose="P", feed_note="Another storm, another chapter.")
    assert draft.model_validate_json(draft.model_dump_json()).feed_note == "Another storm, another chapter."
    verdict = EditorVerdict(verdict="approve", notes="clean", feed_note="Finally, a clean draft.")
    assert verdict.feed_note == "Finally, a clean draft."


def test_thread_intent_plant_defaults():
    from novelizer.agents.schemas import ThreadIntent
    intent = ThreadIntent(action="plant", name="The Locket")
    assert intent.id == "" and intent.note == ""


def test_thread_intent_touch_roundtrips():
    from novelizer.agents.schemas import ThreadIntent
    intent = ThreadIntent(action="touch", id="the-locket", note="reappears")
    again = ThreadIntent.model_validate_json(intent.model_dump_json())
    assert again == intent


def test_editor_verdict_default_thread_intents_empty():
    assert EditorVerdict().thread_intents == []


def test_editor_verdict_carries_thread_intents():
    from novelizer.agents.schemas import ThreadIntent
    v = EditorVerdict(verdict="approve", thread_intents=[ThreadIntent(action="touch", id="the-locket")])
    assert v.thread_intents[0].id == "the-locket"


def test_chapter_draft_default_thread_intents_empty():
    from novelizer.agents.base import ChapterDraft
    assert ChapterDraft(title="T", prose="P").thread_intents == []


def test_chapter_draft_carries_thread_intents():
    from novelizer.agents.base import ChapterDraft
    from novelizer.agents.schemas import ThreadIntent
    d = ChapterDraft(title="T", prose="P", thread_intents=[ThreadIntent(action="plant", name="The Locket")])
    assert d.thread_intents[0].name == "The Locket"


def test_knowledge_intent_plant_defaults():
    from novelizer.agents.schemas import KnowledgeIntent
    intent = KnowledgeIntent(action="plant", title="The Heir Lives")
    assert intent.id == "" and intent.character_id == "" and intent.note == ""


def test_knowledge_intent_learn_roundtrips():
    from novelizer.agents.schemas import KnowledgeIntent
    intent = KnowledgeIntent(action="learn", id="the-heir-lives", character_id="mara", note="found the letter")
    again = KnowledgeIntent.model_validate_json(intent.model_dump_json())
    assert again == intent


def test_causal_intent_roundtrips():
    from novelizer.agents.schemas import CausalIntent
    intent = CausalIntent(cause_chapter_id="c1", effect_chapter_id="c3", note="the fire forces the move")
    again = CausalIntent.model_validate_json(intent.model_dump_json())
    assert again == intent


def test_editor_verdict_default_knowledge_and_causal_intents_empty():
    assert EditorVerdict().knowledge_intents == []
    assert EditorVerdict().causal_intents == []


def test_editor_verdict_carries_knowledge_and_causal_intents():
    from novelizer.agents.schemas import KnowledgeIntent, CausalIntent
    v = EditorVerdict(
        verdict="approve",
        knowledge_intents=[KnowledgeIntent(action="learn", id="the-heir-lives", character_id="mara")],
        causal_intents=[CausalIntent(cause_chapter_id="c1", effect_chapter_id="c2")],
    )
    assert v.knowledge_intents[0].character_id == "mara"
    assert v.causal_intents[0].effect_chapter_id == "c2"


def test_keeper_output_default_knowledge_intents_empty():
    from novelizer.agents.schemas import KeeperOutput
    assert KeeperOutput().knowledge_intents == []


def test_keeper_output_carries_knowledge_intents():
    from novelizer.agents.schemas import KeeperOutput, KnowledgeIntent
    out = KeeperOutput(knowledge_intents=[KnowledgeIntent(action="learn", id="the-heir-lives", character_id="mara")])
    assert out.knowledge_intents[0].id == "the-heir-lives"


def test_chapter_draft_default_knowledge_and_causal_intents_empty():
    from novelizer.agents.base import ChapterDraft
    d = ChapterDraft(title="T", prose="P")
    assert d.knowledge_intents == [] and d.causal_intents == []


def test_chapter_draft_carries_knowledge_and_causal_intents():
    from novelizer.agents.base import ChapterDraft
    from novelizer.agents.schemas import KnowledgeIntent, CausalIntent
    d = ChapterDraft(
        title="T", prose="P",
        knowledge_intents=[KnowledgeIntent(action="plant", title="The Heir Lives")],
        causal_intents=[CausalIntent(cause_chapter_id="c1", effect_chapter_id="c2")],
    )
    assert d.knowledge_intents[0].title == "The Heir Lives"
    assert d.causal_intents[0].cause_chapter_id == "c1"


def test_mined_facts_output_defaults_to_empty():
    from novelizer.agents.schemas import MinedFactsOutput
    out = MinedFactsOutput()
    assert out.secret_facts == [] and out.reveal_facts == [] and out.thread_facts == [] and out.causal_facts == []


def test_mined_secret_fact_defaults_known_id_true():
    from novelizer.agents.schemas import MinedSecretFact
    f = MinedSecretFact(action="uses", id="s1", character_id="mara", chapter_id="c1")
    assert f.known_id is True


def test_mined_secret_fact_can_declare_unknown_id():
    from novelizer.agents.schemas import MinedSecretFact
    f = MinedSecretFact(action="uses", id="s-guessed", character_id="mara", chapter_id="c1", known_id=False)
    assert f.known_id is False


def test_mined_reveal_fact_shape():
    from novelizer.agents.schemas import MinedRevealFact
    f = MinedRevealFact(id="s1", chapter_id="c1")
    assert f.note == "" and f.known_id is True


def test_mined_thread_fact_action_is_restricted():
    from novelizer.agents.schemas import MinedThreadFact
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        MinedThreadFact(action="plant", id="t1", chapter_id="c1")


def test_mined_causal_fact_has_no_known_id_field():
    from novelizer.agents.schemas import MinedCausalFact
    f = MinedCausalFact(cause_chapter_id="c1", effect_chapter_id="c2")
    assert not hasattr(f, "known_id")


def test_theme_intent_introduce_action():
    from novelizer.agents.schemas import ThemeIntent
    intent = ThemeIntent(action="introduce", title="The Weight of Secrets")
    assert intent.action == "introduce" and intent.id == ""


def test_theme_intent_develop_action_cites_id():
    from novelizer.agents.schemas import ThemeIntent
    intent = ThemeIntent(action="develop", id="the-weight-of-secrets", note="revisited in ch3")
    assert intent.action == "develop" and intent.id == "the-weight-of-secrets"


def test_theme_intent_rejects_terminal_actions():
    import pydantic
    from novelizer.agents.schemas import ThemeIntent
    with pytest.raises(pydantic.ValidationError):
        ThemeIntent(action="pay_off", id="t1")


def test_no_action_defaults_false_on_pass_capable_outputs():
    assert KeeperOutput().no_action is False
    assert WorldEntriesDraft().no_action is False
    assert ContinuityOutput().no_action is False


def test_curation_decision_shapes():
    from novelizer.agents.schemas import CurationDecision, WorldEntryDraft

    # default is the safe no-op
    assert CurationDecision().action == "reject"

    revise = CurationDecision(
        action="revise",
        entry=WorldEntryDraft(title="Tavern", body="Tighter prose.", supersedes_id="w1"),
    )
    assert revise.entry.supersedes_id == "w1"

    merge = CurationDecision(
        action="merge",
        entry=WorldEntryDraft(title="The Tavern", body="Consolidated.", supersedes_id="w1"),
        retire_ids=["w2", "w3"],
    )
    assert merge.retire_ids == ["w2", "w3"]

    retire = CurationDecision(action="retire", retire_ids=["w9"], reason="no longer serves the story")
    assert retire.retire_ids == ["w9"]

    # unknown domain on the carried entry is coerced, never raised
    assert CurationDecision(
        action="revise",
        entry=WorldEntryDraft(title="X", body="y", domain="nonsense"),
    ).entry.domain == "other"
