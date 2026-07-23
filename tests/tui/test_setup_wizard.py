from textual.widgets import Button, Input, Label, Select, Static

from novelizer.settings.setup_core import ProbeResult
from novelizer.tui.setup_wizard import SetupWizardApp


async def _fake_probe_ok(base_url, api_key="not-needed", **kwargs):
    return ProbeResult(ok=True, models=["m-big", "m-fast"])


async def _fake_probe_fail(base_url, api_key="not-needed", **kwargs):
    return ProbeResult(ok=False, error="connection refused")


async def _click(pilot, selector):
    """Click a widget even if the form has scrolled it out of the viewport."""
    pilot.app.query_one(selector).scroll_visible(animate=False)
    await pilot.pause()
    await pilot.click(selector)


async def test_wizard_fields_render_at_natural_height():
    """Regression: inputs must not be crunched to height:1/border:none."""
    app = SetupWizardApp(probe=_fake_probe_ok)
    async with app.run_test(size=(80, 50)) as pilot:
        await pilot.pause()
        base_url = app.query_one("#base_url", Input)
        assert base_url.outer_size.height >= 3
        assert base_url.styles.border_top[0] != "none"
        app.exit(None)


async def test_every_field_has_a_visible_label_even_with_default_values():
    """Regression: placeholders were the only labelling, and fields with a
    default value (base_url, stories_dir) never show their placeholder."""
    app = SetupWizardApp(probe=_fake_probe_ok)
    async with app.run_test(size=(80, 50)) as pilot:
        await pilot.pause()
        labels = {str(label.renderable) for label in app.query(".field-label").results(Label)}
        assert labels == {
            "LLM base URL",
            "API key",
            "Stories directory",
            "Author model",
            "Agent model",
            "Embedding model",
        }
        app.exit(None)


async def test_every_field_has_help_text():
    app = SetupWizardApp(probe=_fake_probe_ok)
    async with app.run_test(size=(80, 50)) as pilot:
        await pilot.pause()
        helps = [str(s.renderable) for s in app.query(".field-help").results(Static)]
        assert len(helps) == 6
        joined = " ".join(helps)
        assert "Bearer token" in joined  # api_key help
        assert "expands" in joined  # stories_dir help
        assert "strongest" in joined  # author model help
        assert "embedding" in joined.lower()  # embed model help
        app.exit(None)


async def test_probe_then_save_returns_config():
    app = SetupWizardApp(probe=_fake_probe_ok)
    async with app.run_test(size=(80, 50)) as pilot:
        app.query_one("#base_url", Input).value = "http://h:1/v1"
        app.query_one("#api_key", Input).value = "sk-x"
        await _click(pilot, "#probe")
        await pilot.pause()
        assert "m-big" in str(app.query_one("#probe_result", Static).renderable)
        assert app.query_one("#save", Button).disabled is False
        assert app.query_one("#author_model", Select).value == "m-big"
        await _click(pilot, "#save")
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
        await _click(pilot, "#probe")
        await pilot.pause()
        assert "connection refused" in str(app.query_one("#probe_result", Static).renderable)
        assert app.query_one("#save", Button).disabled is True
        app.exit(None)


async def test_skip_saves_without_models():
    app = SetupWizardApp(probe=_fake_probe_fail)
    async with app.run_test(size=(80, 50)) as pilot:
        app.query_one("#base_url", Input).value = "http://h:1/v1"
        app.query_one("#stories_dir", Input).value = "~/novels"
        await _click(pilot, "#skip")
    assert app.return_value == {
        "llm_base_url": "http://h:1/v1",
        "default_stories_dir": "~/novels",
    }


async def test_skip_with_blank_base_url_shows_error_not_crash():
    app = SetupWizardApp(probe=_fake_probe_fail)
    async with app.run_test(size=(80, 50)) as pilot:
        app.query_one("#base_url", Input).value = "   "
        await _click(pilot, "#skip")
        await pilot.pause()
        assert "required" in str(app.query_one("#probe_result", Static).renderable)
        app.exit(None)
    assert app.return_value is None
