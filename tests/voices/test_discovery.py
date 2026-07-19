from pathlib import Path

from novelizer.voices.discovery import discover_voice_packs

_SHIPPED = Path("novelizer/voices/default.toml").resolve()

_NOIR_PACK = '''name = "noir"

[prose_profiles.hardboiled]
name = "hardboiled"
casting_note = "Short sentences. Rain on glass."
'''


def test_shipped_default_pack_is_always_first(tmp_path):
    packs = discover_voice_packs(tmp_path)
    assert len(packs) == 1
    label, path = packs[0]
    assert label == "default"
    assert Path(path).resolve() == _SHIPPED


def test_user_packs_in_stories_root_are_listed_after_shipped(tmp_path):
    (tmp_path / "noir.toml").write_text(_NOIR_PACK, encoding="utf-8")
    packs = discover_voice_packs(tmp_path)
    assert [label for label, _ in packs] == ["default", "noir (noir.toml)"]
    assert packs[1][1] == str(tmp_path / "noir.toml")


def test_unparseable_toml_in_stories_root_is_skipped(tmp_path):
    (tmp_path / "broken.toml").write_text("not = [valid", encoding="utf-8")
    (tmp_path / "notapack.toml").write_text('title = "no name key"', encoding="utf-8")
    packs = discover_voice_packs(tmp_path)
    assert [label for label, _ in packs] == ["default"]


def test_missing_stories_root_yields_only_shipped(tmp_path):
    packs = discover_voice_packs(tmp_path / "does-not-exist")
    assert [label for label, _ in packs] == ["default"]
