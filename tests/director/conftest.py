"""Blanket isolation for every CLI test in this package.

Without this, any test that reaches load_effective_settings() without its own
env override reads the DEVELOPER'S real ~/.config/novelizer/config.toml -- and
with last_opened_story set there, opens (and write-locks) the developer's real
story database. Observed live 2026-07-19: a full-suite run in a shared checkout
locked the user's active story out from under their running session.

Per-test fixtures may still layer their own XDG/story setup on top; this only
guarantees the *default* is a throwaway config dir and a throwaway cwd, so no
CLI test can ever touch a real config, a real story, or the repo's stories/.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_cli_environment(monkeypatch, tmp_path_factory):
    sandbox = tmp_path_factory.mktemp("cli-isolation")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(sandbox / "xdg"))
    monkeypatch.chdir(sandbox)
