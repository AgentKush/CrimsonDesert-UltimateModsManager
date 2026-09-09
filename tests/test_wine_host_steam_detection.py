"""GitHub #386 (NonDScript, Bazzite): CDUMM.exe running inside a Proton
prefix never found the host's Steam install — the Windows scan probes
drive roots (Z:/Steam) and Windows default paths, but host Steam lives
at /home/<user>/.local/share/Steam, reachable only as Z:/home/... via
Proton's always-created dosdevices/z: -> / symlink."""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import cdumm.storage.game_finder as gf


def _fake_z(tmp_path: Path, monkeypatch) -> Path:
    """Redirect Path('Z:/home') to a temp tree."""
    zhome = tmp_path / "zhome"
    zhome.mkdir()
    orig = gf.Path

    def fake_path(*args):
        if args and str(args[0]).replace("\\", "/").rstrip("/") == "Z:/home":
            return zhome
        return orig(*args)

    monkeypatch.setattr(gf, "Path", fake_path)
    return zhome


def test_wine_host_steam_roots_probes_z_home(tmp_path, monkeypatch):
    zhome = _fake_z(tmp_path, monkeypatch)
    steam = zhome / "deck" / ".local" / "share" / "Steam"
    steam.mkdir(parents=True)

    with mock.patch("cdumm.platform.is_wine", return_value=True):
        roots = gf._wine_host_steam_roots()
    assert steam in roots


def test_wine_host_steam_roots_empty_when_not_wine(tmp_path, monkeypatch):
    zhome = _fake_z(tmp_path, monkeypatch)
    (zhome / "deck" / ".local" / "share" / "Steam").mkdir(parents=True)
    with mock.patch("cdumm.platform.is_wine", return_value=False):
        assert gf._wine_host_steam_roots() == []


def test_map_host_library_path():
    assert gf._map_host_library_path(Path("/run/media/deck/SSD")) == Path(
        "Z:/run/media/deck/SSD")
    win = Path("D:/SteamLibrary")
    assert gf._map_host_library_path(win) == win


def test_pickers_use_wine_aware_dialog():
    """Source-level check (repo convention): every game/CDMods folder
    picker goes through pick_directory, not the native-only static."""
    base = Path(__file__).parent.parent / "src" / "cdumm" / "gui"
    for rel in ("setup_dialog.py", "welcome_wizard.py",
                "pages/settings_page.py"):
        src = (base / rel).read_text(encoding="utf-8")
        assert "pick_directory" in src, rel
        assert "QFileDialog.getExistingDirectory" not in src, rel
