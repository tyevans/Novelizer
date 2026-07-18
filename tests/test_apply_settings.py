import os
import tempfile

from novelizer.runtime import Runtime
from novelizer.settings import EffectiveSettings


class _R:
    async def ainvoke(self, inputs):
        raise AssertionError("not used")


def _runners():
    names = [
        "author", "world_architect", "character_keeper", "editor",
        "continuity_checker", "retconner", "structure_analyst",
    ]
    return {n: _R() for n in names}


async def _started_runtime(**settings_kwargs) -> Runtime:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    rt = Runtime(EffectiveSettings(db_path=path, **settings_kwargs), runners=_runners())
    await rt.start()
    return rt


async def test_cadence_applies_live():
    rt = await _started_runtime(author_interval=300, default_agent_interval=120)
    result = rt.apply_settings(rt.settings.model_copy(update={"author_interval": 30, "default_agent_interval": 15}))
    assert rt.author.interval == 30
    assert rt.editor.interval == 15
    assert rt.retconner.interval == 15
    assert rt.continuity_checker.interval == rt.settings.continuity_interval
    assert set(result["applied"]) == {"author_interval", "default_agent_interval"}
    assert result["restart_required"] == []
    assert rt.settings.author_interval == 30
    await rt.close()


async def test_restart_required_not_applied():
    rt = await _started_runtime()
    old_url = rt.settings.llm_base_url
    result = rt.apply_settings(rt.settings.model_copy(update={"llm_base_url": "http://new:1/v1"}))
    assert result["restart_required"] == ["llm_base_url"]
    assert rt.settings.llm_base_url == old_url  # runtime reflects what actually runs
    await rt.close()


async def test_temperature_updates_provenance_with_injected_runners():
    rt = await _started_runtime(author_temperature=0.8)
    result = rt.apply_settings(rt.settings.model_copy(update={"author_temperature": 0.2}))
    assert "author_temperature" in result["applied"]
    assert rt.author.provenance["temperature"] == 0.2
    assert rt.settings.author_temperature == 0.2
    await rt.close()


async def test_prose_profile_change_updates_casting_and_provenance():
    rt = await _started_runtime()
    old_note = rt.author._casting_note
    result = rt.apply_settings(rt.settings.model_copy(update={"prose_profile": "__nonexistent__"}))
    assert "prose_profile" in result["applied"]
    assert rt.author.provenance["prose_profile"] == "__nonexistent__"
    assert rt.author._casting_note != old_note  # unknown profile -> empty casting note
    await rt.close()


async def test_no_changes_is_noop():
    rt = await _started_runtime()
    assert rt.apply_settings(rt.settings) == {"applied": [], "restart_required": []}
    await rt.close()
