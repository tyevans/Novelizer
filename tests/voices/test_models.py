from novelizer.voices.models import ProseProfile, VoicePack


def test_prose_profile_holds_name_and_casting_note():
    p = ProseProfile(name="sparse", casting_note="Spare, concrete, unadorned.")
    assert p.name == "sparse"
    assert "Spare" in p.casting_note


def test_voice_pack_defaults_are_empty():
    pack = VoicePack(name="empty-pack")
    assert pack.prose_profiles == {}
    assert pack.agent_personalities == {}
    assert pack.profile("sparse") is None


def test_voice_pack_profile_lookup():
    sparse = ProseProfile(name="sparse", casting_note="Spare, concrete, unadorned.")
    lush = ProseProfile(name="lush", casting_note="Ornate, sensory, gothic.")
    pack = VoicePack(
        name="test-pack",
        prose_profiles={"sparse": sparse, "lush": lush},
        agent_personalities={"author": "A weary chronicler."},
    )
    assert pack.profile("sparse") is sparse
    assert pack.profile("lush").casting_note == "Ornate, sensory, gothic."
    assert pack.profile("nonexistent") is None
    assert pack.agent_personalities["author"] == "A weary chronicler."
