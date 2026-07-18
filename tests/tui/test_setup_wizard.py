from textual.widgets import Button, Input, Select, Static

from novelizer.settings.setup_core import ProbeResult
from novelizer.tui.setup_wizard import SetupWizardApp


async def _fake_probe_ok(base_url, api_key="not-needed", **kwargs):
    return ProbeResult(ok=True, models=["m-big", "m-fast"])


async def _fake_probe_fail(base_url, api_key="not-needed", **kwargs):
    return ProbeResult(ok=False, error="connection refused")


async def test_wizard_fields_render_at_natural_height():
    """Regression: inputs must not be crunched to height:1/border:none."""
    app = SetupWizardApp(probe=_fake_probe_ok)
    async with app.run_test(size=(80, 50)) as pilot:
        await pilot.pause()
        base_url = app.query_one("#base_url", Input)
        assert base_url.outer_size.height >= 3
        assert base_url.styles.border_top[0] != "none"
        app.exit(None)


async def test_probe_then_save_returns_config():
    app = SetupWizardApp(probe=_fake_probe_ok)
    async with app.run_test(size=(80, 50)) as pilot:
        app.query_one("#base_url", Input).value = "http://h:1/v1"
        app.query_one("#api_key", Input).value = "sk-x"
        await pilot.click("#probe")
        await pilot.pause()
        assert "m-big" in str(app.query_one("#probe_result", Static).renderable)
        assert app.query_one("#save", Button).disabled is False
        assert app.query_one("#author_model", Select).value == "m-big"
        await pilot.click("#save")
    assert app.return_value == {
        "llm_base_url": "http://h:1/v1",
        "llm_api_key": "sk-x",
        "default_stories_dir": "stories",
        "author_model": "m-big",
        "agent_model": "m-big",
        "embed_model": "m-big",
    }


async def test_probe_failure_shows_error_and_keeps_save_disabled():
    app = SetupWizardApp(probe=_fake_probe_fail)
    async with app.run_test(size=(80, 50)) as pilot:
        app.query_one("#base_url", Input).value = "http://bad:1/v1"
        await pilot.click("#probe")
        await pilot.pause()
        assert "connection refused" in str(app.query_one("#probe_result", Static).renderable)
        assert app.query_one("#save", Button).disabled is True
        app.exit(None)


async def test_skip_saves_without_models():
    app = SetupWizardApp(probe=_fake_probe_fail)
    async with app.run_test(size=(80, 50)) as pilot:
        app.query_one("#base_url", Input).value = "http://h:1/v1"
        app.query_one("#stories_dir", Input).value = "~/novels"
        await pilot.click("#skip")
    assert app.return_value == {
        "llm_base_url": "http://h:1/v1",
        "default_stories_dir": "~/novels",
    }


async def test_skip_with_blank_base_url_shows_error_not_crash():
    app = SetupWizardApp(probe=_fake_probe_fail)
    async with app.run_test(size=(80, 50)) as pilot:
        app.query_one("#base_url", Input).value = "   "
        await pilot.click("#skip")
        await pilot.pause()
        assert "required" in str(app.query_one("#probe_result", Static).renderable)
        app.exit(None)
    assert app.return_value is None
