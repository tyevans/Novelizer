import os
from novelizer.config import Settings


def test_defaults_present():
    s = Settings()
    assert s.db_path == "stories/world.db"
    assert s.llm_base_url.endswith("/v1")
    assert s.author_model
    assert 0.0 <= s.author_temperature <= 2.0
    assert s.author_interval > 0
    assert s.projector_interval > 0


def test_env_override(monkeypatch):
    monkeypatch.setenv("NOVELIZER_AUTHOR_MODEL", "custom-model")
    monkeypatch.setenv("NOVELIZER_LLM_BASE_URL", "http://host:9000/v1")
    s = Settings()
    assert s.author_model == "custom-model"
    assert s.llm_base_url == "http://host:9000/v1"


def test_voice_pack_defaults_to_shipped_default_pack():
    s = Settings()
    assert s.voice_pack.endswith("default.toml")
    assert os.path.isfile(s.voice_pack)


def test_prose_profile_defaults_to_plain():
    s = Settings()
    assert s.prose_profile == "plain"


def test_voice_pack_env_override(monkeypatch):
    monkeypatch.setenv("NOVELIZER_VOICE_PACK", "/tmp/custom-pack.toml")
    s = Settings()
    assert s.voice_pack == "/tmp/custom-pack.toml"


def test_prose_profile_env_override(monkeypatch):
    monkeypatch.setenv("NOVELIZER_PROSE_PROFILE", "lush")
    s = Settings()
    assert s.prose_profile == "lush"
