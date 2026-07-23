# tests/research_domain/test_cli.py
import pytest
from click.testing import CliRunner

from research_domain.cli import main
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
