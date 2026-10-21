from pathlib import Path
from types import SimpleNamespace
from src import playback_host
from src import platform_support as paths


def test_linux_browser_discovery_never_uses_exe(tmp_path, monkeypatch):
    browser = tmp_path / "google-chrome"
    browser.write_text("mock executable")
    monkeypatch.setattr(playback_host.sys, "platform", "linux")
    monkeypatch.setattr(playback_host.shutil, "which", lambda name: str(browser) if name == "google-chrome" else None)
    assert playback_host.find_edge_browser() == browser.resolve()
    plugin = SimpleNamespace()
    monkeypatch.delenv("ASTRA_MUSIC_NATIVE_PLAYER", raising=False)
    assert playback_host.PlaybackHost(plugin).enabled


def test_mac_browser_application_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(playback_host.sys, "platform", "darwin")
    monkeypatch.setattr(playback_host.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(playback_host.shutil, "which", lambda name: None)
    browser = tmp_path / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    browser.parent.mkdir(parents=True)
    browser.touch()
    assert playback_host.find_edge_browser() == browser.resolve()


def test_native_wayland_gui_and_relative_xdg_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", "relative/path")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.delenv("PYWEBVIEW_GUI", raising=False)
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    assert paths.user_data() == tmp_path / ".local/share"
    assert paths.webview_gui() == "qt"
    assert paths.os.environ["PYWEBVIEW_GUI"] == "qt"
    assert paths.os.environ["QT_QPA_PLATFORM"] == "wayland"
