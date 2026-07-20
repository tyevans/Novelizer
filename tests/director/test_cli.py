import asyncio
import logging
import os
import re
import tempfile
from click.testing import CliRunner
from novelizer.director.cli import cli, format_voice_report
from novelizer.settings.toml_io import load_toml_file, write_toml_file
from novelizer.voices.models import ProseProfile, VoicePack
from novelizer.store.models import Character, DirectorSignal, SignalKind
from novelizer.canon.autonomy import Proposal
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType, ThreadPlanted, SecretCreated
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.settings.story_dir import create_story


def test_config_error_shown_as_friendly_message_not_traceback(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    story_dir = tmp_path / "story"
    story_dir.mkdir()
    (story_dir / "story.toml").write_text('llm_api_key = "x"\n')
    runner = CliRunner()
    result = runner.invoke(cli, ["--story", str(story_dir), "chapters"])
    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.output
    assert "llm_api_key" in result.output


def _env(path, xdg_config_home):
    return {"NOVELIZER_DB_PATH": path, "XDG_CONFIG_HOME": str(xdg_config_home)}


def test_headless_subcommand_does_not_create_global_config_when_absent(tmp_path):
    """A fresh user's first command must not suppress the first-run wizard.

    update_global_config() creates global_config.toml as a side effect. The
    wizard only fires when that file is absent, so a headless subcommand run
    before the file exists must not create it.
    """
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    try:
        gpath = xdg / "novelizer" / "config.toml"
        assert not gpath.exists()
        r = CliRunner().invoke(cli, ["seed", "a storm is coming"], env=_env(path, xdg))
        assert r.exit_code == 0, r.output
        assert not gpath.exists()
    finally:
        os.unlink(path)


def test_headless_subcommand_still_records_last_opened_when_config_exists(tmp_path):
    """Existing regression guard: once the config exists, last_opened_story keeps updating."""
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    try:
        gpath = xdg / "novelizer" / "config.toml"
        gpath.parent.mkdir(parents=True)
        write_toml_file(gpath, {})
        r = CliRunner().invoke(cli, ["seed", "a storm is coming"], env=_env(path, xdg))
        assert r.exit_code == 0, r.output
        assert "last_opened_story" in load_toml_file(gpath)
    finally:
        os.unlink(path)


def test_seed_then_chapters_roundtrip(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    try:
        runner = CliRunner()
        r1 = runner.invoke(cli, ["seed", "a storm is coming"], env=_env(path, xdg))
        assert r1.exit_code == 0, r1.output
        assert "Seed" in r1.output
        r2 = runner.invoke(cli, ["chapters"], env=_env(path, xdg))
        assert r2.exit_code == 0, r2.output
        assert "No chapters" in r2.output  # none authored yet
    finally:
        os.unlink(path)


def test_retcons_command_empty(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    try:
        r = CliRunner().invoke(cli, ["retcons"], env=_env(path, xdg))
        assert r.exit_code == 0, r.output
        assert "No open retcon" in r.output
    finally:
        os.unlink(path)


def test_voices_lists_default_pack_profiles(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    try:
        runner = CliRunner()
        result = runner.invoke(cli, ["voices"], env=_env(path, xdg))
        assert result.exit_code == 0, result.output
        assert "sparse" in result.output
        assert "lush" in result.output
        assert "plain" in result.output
        assert "*" in result.output or "active" in result.output.lower()
    finally:
        os.unlink(path)


def test_voices_with_explicit_pack_path(tmp_path):
    fd, db_path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    try:
        fd2, custom_pack_path = tempfile.mkstemp(suffix=".toml"); os.close(fd2)
        try:
            with open(custom_pack_path, "w") as f:
                f.write('name = "custom"\n\n[prose_profiles.terse]\nname = "terse"\ncasting_note = "Very short sentences."\n')
            runner = CliRunner()
            result = runner.invoke(cli, ["voices", "--pack", str(custom_pack_path)], env=_env(db_path, xdg))
            assert result.exit_code == 0, result.output
            assert "terse" in result.output
            assert "sparse" not in result.output
        finally:
            os.unlink(custom_pack_path)
    finally:
        os.unlink(db_path)


def test_report_includes_prose_profiles_with_active_marker():
    pack = VoicePack(
        name="default",
        prose_profiles={
            "plain": ProseProfile(name="plain", casting_note="Clean and neutral."),
            "sparse": ProseProfile(name="sparse", casting_note="Spare, concrete, unadorned."),
        },
    )
    report = format_voice_report(pack, characters=[], active_profile="plain")
    assert "plain" in report and "sparse" in report
    assert "Clean and neutral." in report


def test_report_includes_agent_personalities():
    pack = VoicePack(name="default", agent_personalities={"editor": "A precise, unsentimental line editor."})
    report = format_voice_report(pack, characters=[], active_profile=None)
    assert "editor" in report
    assert "A precise, unsentimental line editor." in report


def test_report_includes_only_characters_with_nonempty_voice():
    pack = VoicePack(name="default")
    characters = [
        Character(id="c1", name="Mira", voice="Clipped sentences."),
        Character(id="c2", name="Jonas", voice=""),
    ]
    report = format_voice_report(pack, characters=characters, active_profile=None)
    assert "Mira" in report and "Clipped sentences." in report
    assert "Jonas" not in report


def _seeded_story(tmp_path):
    """A fresh, isolated story directory (not the shared cwd-relative default
    story every --story-less test falls back to) -- db_path is derived from
    this directory's root, so seeding directly at its world.db is visible to
    a CliRunner invocation that passes --story <this root>.
    """
    return create_story(tmp_path / "story", title="test").root


def _seed_proposal(story_root) -> str:
    """Append a proposal.created event directly to the story's EventStore and
    return the proposal id. Target event is a director_signal.created so the
    approve path's "target event now exists" assertion has something cheap
    and already-projected to check via ReadStore.list_unconsumed_signals.
    """
    db_path = str(story_root / "world.db")
    sig = DirectorSignal(kind=SignalKind.seed, body="approved via proposal")
    proposal = Proposal(
        proposing_agent="author",
        target_event_type=EventType.DIRECTOR_SIGNAL_CREATED,
        target_aggregate_id=sig.id,
        payload=sig.model_dump(mode="json"),
    )

    async def _write():
        events = EventStore(db_path)
        await events.init()
        try:
            await events.append(EventType.PROPOSAL_CREATED, proposal.id, proposal)
        finally:
            await events.close()

    asyncio.run(_write())
    return proposal.id


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _seed_thread(story_root, thread_id="the-locket", name="The Locket") -> str:
    db_path = str(story_root / "world.db")

    async def _write():
        events = EventStore(db_path)
        await events.init()
        try:
            await events.append(EventType.THREAD_PLANTED, thread_id, ThreadPlanted(id=thread_id, name=name))
        finally:
            await events.close()

    asyncio.run(_write())
    return thread_id


def _seed_blueprint(story_root, blueprint_id="b1", target_chapter_count=20) -> str:
    from novelizer.canon.events import BlueprintAdopted
    db_path = str(story_root / "world.db")

    async def _write():
        events = EventStore(db_path)
        await events.init()
        try:
            await events.append(
                EventType.BLUEPRINT_ADOPTED, blueprint_id,
                BlueprintAdopted(
                    blueprint_id=blueprint_id, framework="six-position",
                    target_chapter_count=target_chapter_count, beats=[],
                ),
            )
        finally:
            await events.close()

    asyncio.run(_write())
    return blueprint_id


def _seed_secret(story_root, secret_id="the-map", title="The Map") -> str:
    db_path = str(story_root / "world.db")

    async def _write():
        events = EventStore(db_path)
        await events.init()
        try:
            await events.append(EventType.SECRET_CREATED, secret_id, SecretCreated(id=secret_id, title=title))
        finally:
            await events.close()

    asyncio.run(_write())
    return secret_id


async def _read_after_catchup(db_path: str):
    """Re-open a story's stores and catch the read-model projection up to the
    latest events (a fresh CliRunner.invoke() only catches up once, at its
    own start -- events appended during that same invocation aren't
    reflected in the "proposals"/etc. tables until something projects
    again, so tests that assert on post-command read-model state must
    do this themselves)."""
    events = EventStore(db_path)
    await events.init()
    projector = Projector(events, db_path)
    await projector.init()
    try:
        await projector.catch_up()
    finally:
        # The projector holds its own aiosqlite connection and is only needed
        # for this one catch_up; leaving it to the GC trips aiosqlite's
        # Connection.__del__ complaint under `pytest -W error`.
        await projector.close()
    read = ReadStore(db_path)
    await read.init()
    return events, read


def test_autonomy_command_sets_global_level(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    story = _seeded_story(tmp_path)
    try:
        r = CliRunner().invoke(cli, ["--story", str(story), "autonomy", "gated_all"], env=_env(path, xdg))
        assert r.exit_code == 0, r.output
        assert "gated_all" in r.output
    finally:
        os.unlink(path)


def test_autonomy_command_rejects_unknown_level_with_friendly_message(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    story = _seeded_story(tmp_path)
    try:
        r = CliRunner().invoke(cli, ["--story", str(story), "autonomy", "not_a_real_level"], env=_env(path, xdg))
        assert r.exit_code == 0, r.output
        assert "Unknown autonomy level" in r.output
        assert "Traceback" not in r.output
    finally:
        os.unlink(path)


def test_autonomy_command_sets_per_agent_override(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    story = _seeded_story(tmp_path)
    try:
        r = CliRunner().invoke(cli, ["--story", str(story), "autonomy", "gated_all", "author"], env=_env(path, xdg))
        assert r.exit_code == 0, r.output
        assert "author" in r.output
        assert "gated_all" in r.output
    finally:
        os.unlink(path)


def test_proposals_command_lists_no_pending_proposals(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    story = _seeded_story(tmp_path)
    try:
        r = CliRunner().invoke(cli, ["--story", str(story), "proposals"], env=_env(path, xdg))
        assert r.exit_code == 0, r.output
        assert "No pending proposals" in r.output
    finally:
        os.unlink(path)


def test_proposals_command_lists_a_pending_proposal(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    story = _seeded_story(tmp_path)
    try:
        _seed_proposal(story)
        r = CliRunner().invoke(cli, ["--story", str(story), "proposals"], env=_env(path, xdg))
        assert r.exit_code == 0, r.output
        assert "Pending Proposals" in r.output
        assert "author" in r.output
    finally:
        os.unlink(path)


def test_approve_command_approves_and_reports(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    story = _seeded_story(tmp_path)
    try:
        proposal_id = _seed_proposal(story)
        r = CliRunner().invoke(cli, ["--story", str(story), "approve", proposal_id], env=_env(path, xdg))
        assert r.exit_code == 0, r.output
        assert "Approved proposal" in r.output
        assert proposal_id in r.output

        async def _check():
            # Note: ReadStore.get_proposal(id) round-trips the "proposals" table's
            # `data` JSON blob, which the projector's proposal.approved/rejected
            # handler never rewrites (it only updates the separate `status`
            # SQL column) -- so it still reports "open" here; that's existing
            # projector behavior, not something this test-only task changes.
            # list_proposals(status=...) filters on the SQL column instead and
            # correctly reflects the transition.
            events, read = await _read_after_catchup(str(story / "world.db"))
            try:
                approved = await read.list_proposals(status="approved")
                assert any(p.id == proposal_id for p in approved)
                still_open = await read.list_proposals(status="open")
                assert not any(p.id == proposal_id for p in still_open)
                signals = await read.list_unconsumed_signals()
                assert any(s.body == "approved via proposal" for s in signals)
            finally:
                await read.close()
                await events.close()

        asyncio.run(_check())
    finally:
        os.unlink(path)


def test_approve_command_reports_not_found(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    story = _seeded_story(tmp_path)
    try:
        r = CliRunner().invoke(cli, ["--story", str(story), "approve", "nonexistent-id"], env=_env(path, xdg))
        assert r.exit_code == 0, r.output
        assert "not found" in r.output.lower()
    finally:
        os.unlink(path)


def test_reject_command_rejects_and_reports(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    story = _seeded_story(tmp_path)
    try:
        proposal_id = _seed_proposal(story)
        r = CliRunner().invoke(cli, ["--story", str(story), "reject", proposal_id], env=_env(path, xdg))
        assert r.exit_code == 0, r.output
        assert "Rejected proposal" in r.output
        assert proposal_id in r.output

        async def _check():
            # See test_approve_command_approves_and_reports for why
            # list_proposals(status=...) is used instead of get_proposal(id).
            events, read = await _read_after_catchup(str(story / "world.db"))
            try:
                rejected = await read.list_proposals(status="rejected")
                assert any(p.id == proposal_id for p in rejected)
                still_open = await read.list_proposals(status="open")
                assert not any(p.id == proposal_id for p in still_open)
                signals = await read.list_unconsumed_signals()
                assert not any(s.body == "approved via proposal" for s in signals)
            finally:
                await read.close()
                await events.close()

        asyncio.run(_check())
    finally:
        os.unlink(path)


def test_reject_command_reports_not_found(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    story = _seeded_story(tmp_path)
    try:
        r = CliRunner().invoke(cli, ["--story", str(story), "reject", "nonexistent-id"], env=_env(path, xdg))
        assert r.exit_code == 0, r.output
        assert "not found" in r.output.lower()
    finally:
        os.unlink(path)


def test_approve_command_logs_at_info_level(tmp_path, caplog):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    story = _seeded_story(tmp_path)
    try:
        proposal_id = _seed_proposal(story)
        with caplog.at_level(logging.INFO, logger="novelizer.director.commands"):
            r = CliRunner().invoke(cli, ["--story", str(story), "approve", proposal_id], env=_env(path, xdg))
        assert r.exit_code == 0, r.output
        assert any(
            "approved proposal" in rec.message and proposal_id in rec.message
            for rec in caplog.records
        )
    finally:
        os.unlink(path)


def test_plan_resolution_valid_window_reports_success(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    story = _seeded_story(tmp_path)
    try:
        thread_id = _seed_thread(story)
        r = CliRunner().invoke(
            cli, ["--story", str(story), "plan-resolution", thread_id, "3", "5"], env=_env(path, xdg)
        )
        assert r.exit_code == 0, r.output
        assert "resolution window ch3-5 planned" in _strip_ansi(r.output)
        assert "\x1b[32m" in r.output  # green
    finally:
        os.unlink(path)


def test_plan_resolution_invalid_window_reports_rejection(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    story = _seeded_story(tmp_path)
    try:
        thread_id = _seed_thread(story)
        r = CliRunner().invoke(
            cli, ["--story", str(story), "plan-resolution", thread_id, "9", "3"], env=_env(path, xdg)
        )
        assert r.exit_code == 0, r.output
        assert "invalid window" in _strip_ansi(r.output)
        assert "\x1b[33m" in r.output  # yellow
    finally:
        os.unlink(path)


def test_retarget_command_sets_target_chapter_count(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    story = _seeded_story(tmp_path)
    try:
        _seed_blueprint(story)
        r = CliRunner().invoke(cli, ["--story", str(story), "retarget", "30"], env=_env(path, xdg))
        assert r.exit_code == 0, r.output
        assert "blueprint retargeted to 30 chapters" in _strip_ansi(r.output)
        assert "\x1b[32m" in r.output  # green
    finally:
        os.unlink(path)


def test_retarget_command_rejects_no_active_blueprint(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    story = _seeded_story(tmp_path)
    try:
        r = CliRunner().invoke(cli, ["--story", str(story), "retarget", "30"], env=_env(path, xdg))
        assert r.exit_code == 0, r.output
        assert "no active blueprint" in _strip_ansi(r.output).lower()
        assert "\x1b[33m" in r.output  # yellow
    finally:
        os.unlink(path)


def test_plan_reveal_valid_window_reports_success(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    story = _seeded_story(tmp_path)
    try:
        secret_id = _seed_secret(story)
        r = CliRunner().invoke(
            cli, ["--story", str(story), "plan-reveal", secret_id, "2", "4"], env=_env(path, xdg)
        )
        assert r.exit_code == 0, r.output
        assert "reveal window ch2-4 planned" in _strip_ansi(r.output)
        assert "\x1b[32m" in r.output  # green
    finally:
        os.unlink(path)


def test_plan_reveal_unknown_secret_reports_rejection(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    story = _seeded_story(tmp_path)
    try:
        r = CliRunner().invoke(
            cli, ["--story", str(story), "plan-reveal", "nonexistent", "2", "4"], env=_env(path, xdg)
        )
        assert r.exit_code == 0, r.output
        assert "no such secret" in _strip_ansi(r.output)
        assert "\x1b[33m" in r.output  # yellow
    finally:
        os.unlink(path)
