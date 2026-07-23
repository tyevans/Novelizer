# tests/research_domain/test_cli.py
import click
import pytest
from click.testing import CliRunner

from research_domain.cli import _resolve_dsn, main
from tests.substrate.postgres_fixture import postgres_dsn


def test_append_then_show_reflects_the_appended_event(postgres_dsn):
    runner = CliRunner()

    append_result = runner.invoke(
        main,
        [
            "append",
            "claim.proposed",
            '{"claim_id": "claim-1", "source_id": "source-a", "text": "the sky is green"}',
            "--dsn", postgres_dsn,
            "--stream", "cli-test-stream",
        ],
    )
    assert append_result.exit_code == 0, append_result.output
    assert "Appended" in append_result.output

    show_result = runner.invoke(
        main,
        ["show", "source_coverage", "--dsn", postgres_dsn, "--stream", "cli-test-stream"],
    )
    assert show_result.exit_code == 0, show_result.output
    assert "source-a" in show_result.output


def test_append_rejects_invalid_json_payload(postgres_dsn):
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["append", "claim.proposed", "not-json", "--dsn", postgres_dsn, "--stream", "cli-test-stream-2"],
    )
    assert result.exit_code != 0
    assert "Invalid JSON" in result.output


def test_resolve_dsn_prefers_explicit_dsn_over_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://env-host/env-db")
    assert _resolve_dsn("postgresql://explicit-host/explicit-db") == (
        "postgresql://explicit-host/explicit-db"
    )


def test_resolve_dsn_falls_back_to_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://env-host/env-db")
    assert _resolve_dsn(None) == "postgresql://env-host/env-db"


def test_resolve_dsn_raises_when_no_dsn_and_no_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(click.ClickException, match="No --dsn given and DATABASE_URL is not set."):
        _resolve_dsn(None)


def test_cli_reports_error_when_dsn_unresolvable(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    runner = CliRunner()

    result = runner.invoke(main, ["show", "source_coverage", "--stream", "cli-test-stream-3"])

    assert result.exit_code != 0
    assert "No --dsn given and DATABASE_URL is not set." in result.output


def test_cli_uses_database_url_when_dsn_omitted(postgres_dsn, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", postgres_dsn)
    runner = CliRunner()

    append_result = runner.invoke(
        main,
        [
            "append",
            "claim.proposed",
            '{"claim_id": "claim-env", "source_id": "source-env", "text": "via env dsn"}',
            "--stream", "cli-test-stream-env",
        ],
    )
    assert append_result.exit_code == 0, append_result.output

    show_result = runner.invoke(
        main, ["show", "source_coverage", "--stream", "cli-test-stream-env"]
    )
    assert show_result.exit_code == 0, show_result.output
    assert "source-env" in show_result.output


def test_stream_option_defaults_to_research_stream(postgres_dsn):
    """Append with the documented default stream id spelled out explicitly,
    then show WITHOUT --stream: the read only sees the event if the option's
    default really is 'research-stream'."""
    runner = CliRunner()

    append_result = runner.invoke(
        main,
        [
            "append",
            "claim.proposed",
            '{"claim_id": "claim-default", "source_id": "source-default", "text": "default stream"}',
            "--dsn", postgres_dsn,
            "--stream", "research-stream",
        ],
    )
    assert append_result.exit_code == 0, append_result.output

    show_result = runner.invoke(main, ["show", "source_coverage", "--dsn", postgres_dsn])
    assert show_result.exit_code == 0, show_result.output
    assert "source-default" in show_result.output


def test_show_unknown_projection_prints_empty_table_and_exits_zero(postgres_dsn):
    """An unregistered projection name is not an error: RuntimeBase.get_projection
    returns {} for unknown names, so `show` renders an empty table and exits 0."""
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["show", "nonesuch", "--dsn", postgres_dsn, "--stream", "cli-test-stream-4"],
    )

    assert result.exit_code == 0, result.output
    assert "nonesuch" in result.output  # table title is the requested name
    assert "Key" in result.output and "Value" in result.output  # headers, no rows


def test_append_accepts_event_type_not_in_research_registry(postgres_dsn):
    """The registry gates autonomy tiers, not append validity: appending an
    EVENT_TYPE absent from build_research_registry() still succeeds."""
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "append",
            "totally.unregistered",
            '{"anything": "goes"}',
            "--dsn", postgres_dsn,
            "--stream", "cli-test-stream-5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Appended" in result.output
