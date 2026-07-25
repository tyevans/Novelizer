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
            "Embedding base URL (optional)",
            "Embedding API key",
            "Embedding model",
        }
        app.exit(None)


async def test_every_field_has_help_text():
    app = SetupWizardApp(probe=_fake_probe_ok)
    async with app.run_test(size=(80, 50)) as pilot:
        await pilot.pause()
        helps = [str(s.renderable) for s in app.query(".field-help").results(Static)]
        assert len(helps) == 8
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


async def test_separate_embedding_endpoint_probe_fills_only_embed_model():
    """The OpenRouter case: chat models come from one endpoint, embedding models
    from another, and the embedding pick must not be a chat model."""

    async def probe(base_url, api_key="not-needed", **kwargs):
        if "11434" in base_url:
            assert api_key == "sk-embed"  # chat key must not be forwarded
            return ProbeResult(ok=True, models=["nomic-embed-text"])
        return ProbeResult(ok=True, models=["m-big", "m-fast"])

    app = SetupWizardApp(probe=probe)
    async with app.run_test(size=(80, 60)) as pilot:
        app.query_one("#base_url", Input).value = "http://openrouter/api/v1"
        app.query_one("#api_key", Input).value = "sk-or"
        app.query_one("#embed_base_url", Input).value = "http://localhost:11434/v1"
        app.query_one("#embed_api_key", Input).value = "sk-embed"
        await _click(pilot, "#probe")
        await pilot.pause()
        await _click(pilot, "#probe_embed")
        await pilot.pause()
        assert "nomic-embed-text" in str(app.query_one("#probe_embed_result", Static).renderable)
        assert app.query_one("#author_model", Select).value == "m-big"
        assert app.query_one("#embed_model", Select).value == "nomic-embed-text"
        await _click(pilot, "#save")
    assert app.return_value == {
        "llm_base_url": "http://openrouter/api/v1",
        "llm_api_key": "sk-or",
        "default_stories_dir": "stories",
        "author_model": "m-big",
        "agent_model": "m-big",
        "embed_model": "nomic-embed-text",
        "embed_base_url": "http://localhost:11434/v1",
        "embed_api_key": "sk-embed",
    }


async def test_chat_probe_does_not_clobber_a_successful_embedding_pick():
    """Re-testing the chat endpoint after picking an embedding model must leave
    the embedding pick alone — otherwise it silently becomes a chat model."""

    async def probe(base_url, api_key="not-needed", **kwargs):
        if "11434" in base_url:
            return ProbeResult(ok=True, models=["nomic-embed-text"])
        return ProbeResult(ok=True, models=["m-big"])

    app = SetupWizardApp(probe=probe)
    async with app.run_test(size=(80, 60)) as pilot:
        app.query_one("#base_url", Input).value = "http://openrouter/api/v1"
        app.query_one("#embed_base_url", Input).value = "http://localhost:11434/v1"
        await _click(pilot, "#probe_embed")
        await pilot.pause()
        await _click(pilot, "#probe")
        await pilot.pause()
        assert app.query_one("#embed_model", Select).value == "nomic-embed-text"
        app.exit(None)


async def test_embed_probe_without_url_asks_for_one():
    app = SetupWizardApp(probe=_fake_probe_ok)
    async with app.run_test(size=(80, 60)) as pilot:
        await _click(pilot, "#probe_embed")
        await pilot.pause()
        assert "base URL" in str(app.query_one("#probe_embed_result", Static).renderable)
        assert app.query_one("#embed_model", Select).disabled is True
        app.exit(None)


async def test_single_endpoint_setup_still_fills_all_three_picks():
    """Back-compat: one endpoint serving chat and embeddings needs one probe."""
    app = SetupWizardApp(probe=_fake_probe_ok)
    async with app.run_test(size=(80, 60)) as pilot:
        app.query_one("#base_url", Input).value = "http://localhost:11434/v1"
        await _click(pilot, "#probe")
        await pilot.pause()
        assert app.query_one("#embed_model", Select).value == "m-big"
        app.exit(None)


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
