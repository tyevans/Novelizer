import os
import tempfile
import pytest
from novelizer.voices.loader import load_voice_pack

DEFAULT_PACK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "novelizer", "voices", "default.toml",
)


def test_load_default_pack_yields_expected_profiles():
    pack = load_voice_pack(DEFAULT_PACK_PATH)
    assert pack.name == "default"
    assert set(pack.prose_profiles) == {"sparse", "lush", "plain"}
    assert pack.profile("sparse").casting_note
    assert pack.profile("lush").casting_note
    assert pack.profile("sparse").casting_note != pack.profile("lush").casting_note


def test_load_default_pack_has_six_agent_personalities():
    pack = load_voice_pack(DEFAULT_PACK_PATH)
    expected_agents = {
        "author", "editor", "world_architect",
        "character_keeper", "continuity_checker", "retconner",
    }
    assert expected_agents <= set(pack.agent_personalities)
    for agent in expected_agents:
        assert pack.agent_personalities[agent].strip()


def test_profile_lookup_on_loaded_pack():
    pack = load_voice_pack(DEFAULT_PACK_PATH)
    assert pack.profile("plain") is not None
    assert pack.profile("nonexistent-profile") is None


def test_missing_file_raises_clear_error():
    with tempfile.TemporaryDirectory() as d:
        missing = os.path.join(d, "does-not-exist.toml")
        with pytest.raises(FileNotFoundError, match="Voice pack not found"):
            load_voice_pack(missing)
