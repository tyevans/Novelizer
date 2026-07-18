import os
import tempfile
import pytest
from novelizer.voices.scaffold import scaffold_prose_profile, DEFAULT_PACK_GUARD_MESSAGE
from novelizer.voices.loader import load_voice_pack

DEFAULT_PACK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "novelizer", "voices", "default.toml",
)


def test_scaffold_writes_a_new_pack_when_none_exists():
    with tempfile.TemporaryDirectory() as d:
        pack_path = os.path.join(d, "user_pack.toml")
        written = scaffold_prose_profile(pack_path, "brisk", "Fast, punchy, present-tense action prose.")
        assert written == pack_path
        pack = load_voice_pack(pack_path)
        assert pack.profile("brisk") is not None
        assert pack.profile("brisk").casting_note == "Fast, punchy, present-tense action prose."


def test_scaffold_appends_to_an_existing_user_pack_without_clobbering_other_profiles():
    with tempfile.TemporaryDirectory() as d:
        pack_path = os.path.join(d, "user_pack.toml")
        scaffold_prose_profile(pack_path, "brisk", "Fast, punchy, present-tense action prose.")
        scaffold_prose_profile(pack_path, "wistful", "Slow, nostalgic, past-tense reminiscence.")
        pack = load_voice_pack(pack_path)
        assert pack.profile("brisk").casting_note == "Fast, punchy, present-tense action prose."
        assert pack.profile("wistful").casting_note == "Slow, nostalgic, past-tense reminiscence."


def test_scaffold_is_idempotent_replacing_same_named_profile():
    with tempfile.TemporaryDirectory() as d:
        pack_path = os.path.join(d, "user_pack.toml")
        scaffold_prose_profile(pack_path, "brisk", "First description.")
        scaffold_prose_profile(pack_path, "brisk", "Revised description.")
        pack = load_voice_pack(pack_path)
        assert len(pack.prose_profiles) == 1
        assert pack.profile("brisk").casting_note == "Revised description."


def test_scaffold_refuses_to_write_the_shipped_default_pack():
    with pytest.raises(ValueError, match=DEFAULT_PACK_GUARD_MESSAGE):
        scaffold_prose_profile(DEFAULT_PACK_PATH, "brisk", "Should not land here.")
    # Confirm the shipped pack is untouched.
    pack = load_voice_pack(DEFAULT_PACK_PATH)
    assert pack.profile("brisk") is None


def test_scaffold_escapes_quotes_and_backslashes_in_description():
    with tempfile.TemporaryDirectory() as d:
        pack_path = os.path.join(d, "user_pack.toml")
        scaffold_prose_profile(pack_path, "quirky", 'She said "hello" and meant it \\ truly.')
        pack = load_voice_pack(pack_path)
        assert pack.profile("quirky").casting_note == 'She said "hello" and meant it \\ truly.'
