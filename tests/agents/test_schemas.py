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


def test_secret_plant_defaults():
    from novelizer.agents.schemas import SecretPlant
    plant = SecretPlant(title="The Heir Lives")
    assert plant.note == ""


def test_secret_citation_learn_roundtrips():
    from novelizer.agents.schemas import SecretCitation
    cite = SecretCitation(action="learn", id="the-heir-lives", character_id="mara", note="found the letter")
    again = SecretCitation.model_validate_json(cite.model_dump_json())
    assert again == cite


def test_causal_intent_roundtrips():
    from novelizer.agents.schemas import CausalIntent
    intent = CausalIntent(cause_chapter_id="c1", effect_chapter_id="c3", note="the fire forces the move")
    again = CausalIntent.model_validate_json(intent.model_dump_json())
    assert again == intent


def test_editor_verdict_default_secret_and_causal_intents_empty():
    assert EditorVerdict().secret_plants == []
    assert EditorVerdict().secret_citations == []
    assert EditorVerdict().causal_intents == []


def test_editor_verdict_carries_both_secret_halves_and_causal_intents():
    from novelizer.agents.schemas import SecretPlant, SecretCitation, CausalIntent
    v = EditorVerdict(
        verdict="approve",
        secret_plants=[SecretPlant(title="The Heir Lives")],
        secret_citations=[SecretCitation(action="learn", id="the-heir-lives", character_id="mara")],
        causal_intents=[CausalIntent(cause_chapter_id="c1", effect_chapter_id="c2")],
    )
    assert v.secret_plants[0].title == "The Heir Lives"
    assert v.secret_citations[0].character_id == "mara"
    assert v.causal_intents[0].effect_chapter_id == "c2"


def test_keeper_output_default_secret_citations_empty():
    from novelizer.agents.schemas import KeeperOutput
    assert KeeperOutput().secret_citations == []


def test_keeper_output_carries_secret_citations():
    from novelizer.agents.schemas import KeeperOutput, SecretCitation
    out = KeeperOutput(secret_citations=[SecretCitation(action="learn", id="the-heir-lives", character_id="mara")])
    assert out.secret_citations[0].id == "the-heir-lives"


def test_chapter_draft_default_secret_and_causal_intents_empty():
    from novelizer.agents.base import ChapterDraft
    d = ChapterDraft(title="T", prose="P")
    assert d.secret_plants == [] and d.secret_citations == [] and d.causal_intents == []


def test_chapter_draft_carries_both_secret_halves_and_causal_intents():
    from novelizer.agents.base import ChapterDraft
    from novelizer.agents.schemas import SecretPlant, SecretCitation, CausalIntent
    d = ChapterDraft(
        title="T", prose="P",
        secret_plants=[SecretPlant(title="The Heir Lives")],
        secret_citations=[SecretCitation(action="reveal", id="the-heir-lives")],
        causal_intents=[CausalIntent(cause_chapter_id="c1", effect_chapter_id="c2")],
    )
    assert d.secret_plants[0].title == "The Heir Lives"
    assert d.secret_citations[0].character_id == ""
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


# -- F6: minting and citing are separate slots ---------------------------------
#
# KnowledgeIntent was ONE slot whose action Literal chose between minting
# (`plant`, needs title) and citing (`learn`/`reveal`/`uses`, need an existing
# id, and learn/uses a character_id). Requiredness therefore depended on the
# action -- a shape pydantic cannot express -- so the model got no structural
# signal about what to fill in. Worse, all three citing actions need a secret
# that already exists, so in a story with ZERO secrets three of four actions
# were unreachable by construction and the only reachable one was a 1-in-4
# branch inside one of seven optional lists. Measured: offered 641 times, fired
# zero times, and that was still true after the prompt causes were fixed.


def test_secret_plant_needs_only_a_title():
    from novelizer.agents.schemas import SecretPlant

    plant = SecretPlant(title="The Heir Lives")
    assert plant.title == "The Heir Lives" and plant.note == ""
    assert not hasattr(plant, "id"), "a plant mints its own id; carrying one invites a citation"
    assert not hasattr(plant, "character_id")


def test_secret_citation_carries_id_and_action_and_cannot_plant():
    from novelizer.agents.schemas import SecretCitation

    import pydantic

    cite = SecretCitation(action="learn", id="the-heir-lives", character_id="mara")
    assert cite.evidence == ""
    with pytest.raises(pydantic.ValidationError):
        SecretCitation(action="plant", id="x")


def test_author_and_editor_carry_both_halves():
    from novelizer.agents.base import ChapterDraft
    from novelizer.agents.schemas import EditorVerdict

    for cls in (ChapterDraft, EditorVerdict):
        fields = cls.model_fields
        assert "secret_plants" in fields and "secret_citations" in fields, cls.__name__
        assert "knowledge_intents" not in fields, f"{cls.__name__} still has the merged slot"


def test_keeper_cannot_plant_because_it_has_no_slot_to_plant_in():
    """Locked decision #1 -- minting is reserved to Author/Editor -- was a
    runtime allowed_actions check the model could still be asked to violate.
    Denying the Keeper the field makes it unrepresentable instead."""
    from novelizer.agents.schemas import KeeperOutput

    fields = KeeperOutput.model_fields
    assert "secret_citations" in fields
    assert "secret_plants" not in fields
    assert "knowledge_intents" not in fields
