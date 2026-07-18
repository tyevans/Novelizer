import os

from novelizer.settings.global_store import update_global_config, write_global_config
from novelizer.settings.toml_io import load_toml_file, write_toml_file


def test_write_global_config_0600(tmp_path):
    path = tmp_path / "cfg" / "config.toml"
    returned = write_global_config({"llm_api_key": "sk-x"}, path=path)
    assert returned == path
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert load_toml_file(path) == {"llm_api_key": "sk-x"}


def test_update_creates_missing_file(tmp_path):
    path = tmp_path / "config.toml"
    result = update_global_config(path=path, last_opened_story="/s/novel")
    assert result == {"last_opened_story": "/s/novel"}
    assert load_toml_file(path) == {"last_opened_story": "/s/novel"}
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_update_preserves_unknown_keys(tmp_path):
    path = tmp_path / "config.toml"
    write_toml_file(path, {"future_key": "kept", "author_model": "m"})
    result = update_global_config(path=path, author_model="m2")
    assert result == {"future_key": "kept", "author_model": "m2"}


def test_update_none_removes_key(tmp_path):
    path = tmp_path / "config.toml"
    write_toml_file(path, {"last_opened_story": "/gone", "author_model": "m"})
    result = update_global_config(path=path, last_opened_story=None)
    assert result == {"author_model": "m"}


def test_default_path_is_global_config_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    returned = write_global_config({"author_model": "m"})
    assert returned == tmp_path / "novelizer" / "config.toml"
