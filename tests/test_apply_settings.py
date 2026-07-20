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


async def _started_runtime(tmp_path, **settings_kwargs) -> Runtime:
    path = str(tmp_path / "world.db")
    rt = Runtime(EffectiveSettings(db_path=path, **settings_kwargs), runners=_runners())
    await rt.start()
    return rt


async def test_cadence_applies_live(tmp_path):
    rt = await _started_runtime(tmp_path, author_interval=300, default_agent_interval=120)
    result = rt.apply_settings(rt.settings.model_copy(update={"author_interval": 30, "default_agent_interval": 15}))
    assert rt.author.interval == 30
    assert rt.editor.interval == 15
    assert rt.retconner.interval == 15
    assert rt.continuity_checker.interval == rt.settings.continuity_interval
    assert set(result["applied"]) == {"author_interval", "default_agent_interval"}
    assert result["restart_required"] == []
    assert rt.settings.author_interval == 30
    await rt.close()


async def test_restart_required_not_applied(tmp_path):
    rt = await _started_runtime(tmp_path)
    old_url = rt.settings.llm_base_url
    result = rt.apply_settings(rt.settings.model_copy(update={"llm_base_url": "http://new:1/v1"}))
    assert result["restart_required"] == ["llm_base_url"]
    assert rt.settings.llm_base_url == old_url  # runtime reflects what actually runs
    await rt.close()


async def test_temperature_updates_provenance_with_injected_runners(tmp_path):
    rt = await _started_runtime(tmp_path, author_temperature=0.8)
    result = rt.apply_settings(rt.settings.model_copy(update={"author_temperature": 0.2}))
    assert "author_temperature" in result["applied"]
    assert rt.author.provenance["temperature"] == 0.2
    assert rt.settings.author_temperature == 0.2
    await rt.close()


async def test_prose_profile_change_updates_casting_and_provenance(tmp_path):
    rt = await _started_runtime(tmp_path)
    old_note = rt.author._casting_note
    result = rt.apply_settings(rt.settings.model_copy(update={"prose_profile": "__nonexistent__"}))
    assert "prose_profile" in result["applied"]
    assert rt.author.provenance["prose_profile"] == "__nonexistent__"
    assert rt.author._casting_note != old_note  # unknown profile -> empty casting note
    await rt.close()


async def test_no_changes_is_noop(tmp_path):
    rt = await _started_runtime(tmp_path)
    assert rt.apply_settings(rt.settings) == {"applied": [], "restart_required": [], "errors": []}
    await rt.close()


async def test_rebuild_uses_reverted_settings_when_restart_required_pairs_with_live_change(tmp_path, monkeypatch):
    """Fix 1: a temperature change arriving alongside a restart-required change
    (e.g. author_model) must rebuild runners from the REVERTED settings, not
    the new ones — otherwise the new model gets baked into the running runner
    while runtime.settings/provenance still claim the old model is in effect."""
    rt = await _started_runtime(tmp_path, author_temperature=0.8)
    old_settings = rt.settings
    # Switch to the real builder path so apply_settings's rebuild branch runs.
    rt._runners = None
    rt._runner = None

    seen: list[EffectiveSettings] = []

    class _FakeRunner:
        async def ainvoke(self, inputs):
            raise AssertionError("not used")

    def _spy_build_author_runner(settings, callbacks=None, backend=None, tools=None):
        seen.append(settings)
        return _FakeRunner()

    monkeypatch.setattr("novelizer.runtime.build_author_runner", _spy_build_author_runner)

    new = old_settings.model_copy(update={
        "author_temperature": 0.2,
        "author_model": "some-other-model",
    })
    result = rt.apply_settings(new)

    assert "author_model" in result["restart_required"]
    assert "author_temperature" in result["applied"]
    assert len(seen) == 1
    assert seen[0].author_model == old_settings.author_model
    assert seen[0].author_temperature == 0.2
    assert rt.settings.author_model == old_settings.author_model
    assert rt.settings.author_temperature == 0.2
    assert rt.author.provenance["model"] == old_settings.author_model
    assert rt.author.provenance["temperature"] == 0.2
    await rt.close()


async def test_rebuild_keeps_author_tooled_when_flags_on(tmp_path, monkeypatch):
    """Fix 2: apply_settings' rebuild path must reuse the runtime's canon
    backend/tools for pull-mode agents, same as start() -- a bare rebuild
    silently drops tool access while pull_mode stays True."""
    rt = await _started_runtime(tmp_path, author_temperature=0.8, author_tools_enabled=True)
    rt._runners = None
    rt._runner = None

    seen_kwargs: list[dict] = []

    def _spy_build_author_runner(settings, callbacks=None, backend=None, tools=None):
        seen_kwargs.append({"backend": backend, "tools": tools})
        return _R()

    monkeypatch.setattr("novelizer.runtime.build_author_runner", _spy_build_author_runner)

    rt.apply_settings(rt.settings.model_copy(update={"author_temperature": 0.3}))

    assert len(seen_kwargs) == 1
    assert seen_kwargs[0]["backend"] is rt._canon_backend
    assert seen_kwargs[0]["tools"] is rt._canon_tools
    await rt.close()


async def test_rebuild_keeps_checker_tooled_when_flags_on(tmp_path, monkeypatch):
    """Same as above for the continuity checker's checker_tools_enabled flag."""
    rt = await _started_runtime(tmp_path, agent_temperature=0.8, checker_tools_enabled=True)
    rt._runners = None
    rt._runner = None

    seen_kwargs: list[dict] = []

    def _spy_build_checker_runner(settings, callbacks=None, backend=None, tools=None):
        seen_kwargs.append({"backend": backend, "tools": tools})
        return _R()

    monkeypatch.setattr("novelizer.runtime.build_continuity_checker_runner", _spy_build_checker_runner)
    monkeypatch.setattr(
        "novelizer.runtime.build_continuity_mining_runner",
        lambda settings, callbacks=None: _R(),
    )

    rt.apply_settings(rt.settings.model_copy(update={"agent_temperature": 0.3}))

    assert len(seen_kwargs) == 1
    assert seen_kwargs[0]["backend"] is rt._canon_backend
    assert seen_kwargs[0]["tools"] is rt._canon_tools
    await rt.close()


async def test_rebuild_keeps_world_architect_tooled_when_flags_on(tmp_path, monkeypatch):
    """CPT-M6: apply_settings' agent_temperature rebuild path must keep the
    phase-b agents' tooling pinned at start(), same as author/checker --
    a mid-session flag flip must not change what's rebuilt (M5-documented
    inert-until-restart contract)."""
    rt = await _started_runtime(tmp_path, agent_temperature=0.8, world_architect_tools_enabled=True)
    rt._runners = None
    rt._runner = None

    seen_kwargs: list[dict] = []

    def _spy_build_world_architect_runner(settings, callbacks=None, backend=None, tools=None):
        seen_kwargs.append({"backend": backend, "tools": tools})
        return _R()

    monkeypatch.setattr("novelizer.runtime.build_world_architect_runner", _spy_build_world_architect_runner)
    monkeypatch.setattr("novelizer.runtime.build_character_keeper_runner", lambda settings, callbacks=None, backend=None, tools=None: _R())
    monkeypatch.setattr("novelizer.runtime.build_editor_runner", lambda settings, callbacks=None, backend=None, tools=None: _R())
    monkeypatch.setattr("novelizer.runtime.build_continuity_checker_runner", lambda settings, callbacks=None, backend=None, tools=None: _R())
    monkeypatch.setattr("novelizer.runtime.build_continuity_mining_runner", lambda settings, callbacks=None: _R())
    monkeypatch.setattr("novelizer.runtime.build_retconner_runner", lambda settings, callbacks=None, backend=None, tools=None: _R())
    monkeypatch.setattr("novelizer.runtime.build_structure_analyst_runner", lambda settings, callbacks=None, backend=None, tools=None: _R())

    # Simulate a live flag flip without a restart -- pinning must ignore it.
    rt.settings = rt.settings.model_copy(update={"world_architect_tools_enabled": False})
    rt.apply_settings(rt.settings.model_copy(update={"agent_temperature": 0.3}))

    assert len(seen_kwargs) == 1
    assert seen_kwargs[0]["backend"] is rt._canon_backend
    assert seen_kwargs[0]["tools"] is rt._canon_tools
    await rt.close()


async def test_invalid_voice_pack_reports_error_and_other_changes_still_apply(tmp_path):
    """Fix 2: a bad voice_pack path must not wedge apply_settings — other
    changes in the same apply should still land, and the failure should be
    reported via errors rather than raised."""
    rt = await _started_runtime(tmp_path, author_interval=300)
    old_pack = rt.settings.voice_pack
    new = rt.settings.model_copy(update={
        "voice_pack": "/nonexistent/path/pack.toml",
        "author_interval": 30,
    })
    result = rt.apply_settings(new)

    assert result["errors"]
    assert any("voice_pack" in e for e in result["errors"])
    assert rt.settings.voice_pack == old_pack
    assert rt.author.interval == 30
    assert "author_interval" in result["applied"]
    await rt.close()


async def test_apply_after_invalid_voice_pack_does_not_reraise(tmp_path):
    """A subsequent apply changing only author_interval must succeed cleanly —
    no re-raise, no lingering error — after an earlier apply had a bad
    voice_pack."""
    rt = await _started_runtime(tmp_path, author_interval=300)
    bad = rt.settings.model_copy(update={"voice_pack": "/nonexistent/path/pack.toml"})
    rt.apply_settings(bad)

    result = rt.apply_settings(rt.settings.model_copy(update={"author_interval": 45}))
    assert result["errors"] == []
    assert rt.author.interval == 45
    await rt.close()
