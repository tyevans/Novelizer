import pytest
from click.testing import CliRunner
from unittest.mock import AsyncMock, MagicMock, patch
from novelizer.director.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


def test_seed_command_injects_signal(runner):
    mock_store = MagicMock()
    mock_store.init = AsyncMock()
    mock_store.close = AsyncMock()
    mock_store.save_director_signal = AsyncMock()

    with patch("novelizer.director.cli.Store", return_value=mock_store):
        result = runner.invoke(cli, ["seed", "A dark forest appears"])

    assert result.exit_code == 0
    mock_store.save_director_signal.assert_called_once()
    saved_signal = mock_store.save_director_signal.call_args[0][0]
    assert saved_signal.body == "A dark forest appears"
    assert saved_signal.kind.value == "seed"


def test_focus_command_injects_signal(runner):
    mock_store = MagicMock()
    mock_store.init = AsyncMock()
    mock_store.close = AsyncMock()
    mock_store.save_director_signal = AsyncMock()

    with patch("novelizer.director.cli.Store", return_value=mock_store):
        result = runner.invoke(cli, ["focus", "Maren's backstory"])

    assert result.exit_code == 0
    saved_signal = mock_store.save_director_signal.call_args[0][0]
    assert saved_signal.kind.value == "focus"


def test_retcons_command_lists_open(runner):
    from novelizer.store.models import RetconRequest
    req = RetconRequest(
        description="Magic contradicts itself",
        conflicting_entry_ids=["a", "b"],
        proposed_resolution="Remove one.",
    )
    mock_store = MagicMock()
    mock_store.init = AsyncMock()
    mock_store.close = AsyncMock()
    mock_store.list_retcon_requests = AsyncMock(return_value=[req])

    with patch("novelizer.director.cli.Store", return_value=mock_store):
        result = runner.invoke(cli, ["retcons"])

    assert result.exit_code == 0
    assert "Magic contradicts itself" in result.output


def test_chapters_command_lists_chapters(runner):
    from novelizer.store.models import Chapter
    ch = Chapter(title="The First Night", prose="Darkness fell.")
    mock_store = MagicMock()
    mock_store.init = AsyncMock()
    mock_store.close = AsyncMock()
    mock_store.list_chapters = AsyncMock(return_value=[ch])

    with patch("novelizer.director.cli.Store", return_value=mock_store):
        result = runner.invoke(cli, ["chapters"])

    assert result.exit_code == 0
    assert "The First Night" in result.output


def test_read_command_prints_prose(runner):
    from novelizer.store.models import Chapter
    ch = Chapter(title="The Rift", prose="The sky tore open.")

    mock_store = MagicMock()
    mock_store.init = AsyncMock()
    mock_store.close = AsyncMock()
    mock_store.get_chapter = AsyncMock(return_value=ch)

    with patch("novelizer.director.cli.Store", return_value=mock_store):
        result = runner.invoke(cli, ["read", ch.id])

    assert result.exit_code == 0
    assert "The sky tore open." in result.output


def test_finalize_command_updates_chapter(runner):
    from novelizer.store.models import Chapter, EditorialStatus
    ch = Chapter(title="The Rift", prose="The sky tore open.", editorial_status=EditorialStatus.reviewed)

    mock_store = MagicMock()
    mock_store.init = AsyncMock()
    mock_store.close = AsyncMock()
    mock_store.get_chapter = AsyncMock(return_value=ch)
    mock_store.save_chapter = AsyncMock()

    with patch("novelizer.director.cli.Store", return_value=mock_store):
        result = runner.invoke(cli, ["finalize", ch.id])

    assert result.exit_code == 0
    saved = mock_store.save_chapter.call_args[0][0]
    assert saved.editorial_status == EditorialStatus.final
