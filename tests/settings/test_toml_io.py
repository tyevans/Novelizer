import os

import pytest

from novelizer.settings.toml_io import TOMLFileError, load_toml_file, write_toml_file


def test_round_trip(tmp_path):
    path = tmp_path / "sub" / "config.toml"
    write_toml_file(path, {"author_model": "m1", "author_temperature": 0.5})
    assert load_toml_file(path) == {"author_model": "m1", "author_temperature": 0.5}


def test_write_with_mode_0600(tmp_path):
    path = tmp_path / "config.toml"
    write_toml_file(path, {"llm_api_key": "secret"}, mode=0o600)
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_invalid_toml_names_file_and_location(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("author_model = \n")
    with pytest.raises(TOMLFileError) as exc:
        load_toml_file(path)
    assert str(path) in str(exc.value)
    assert "line" in str(exc.value)


def test_missing_file_raises(tmp_path):
    with pytest.raises(TOMLFileError) as exc:
        load_toml_file(tmp_path / "nope.toml")
    assert "nope.toml" in str(exc.value)
