import asyncio

from src.plugin import AstraMusic
from src.widget_theme import DEFAULTS, normalize_theme


def test_theme_rejects_css_injection_and_normalizes_hex():
    assert normalize_theme({"background": "red;position:fixed", "accent": "#ABCDEF", "mode": "custom"}) == {
        "mode": "custom", "background": DEFAULTS["background"], "accent": "#abcdef"}
    assert normalize_theme(None) == DEFAULTS


def test_theme_persists_without_changing_accounts_or_widget_size(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    music = AstraMusic()
    music._write_settings({"yandex": {"token": "private"}, "desktop_widget": {"enabled": False, "x": 20}})
    custom = {"mode": "custom", "background": "#ffffff", "accent": "#0044aa"}
    assert asyncio.run(music.ui_widget_theme_set(settings=custom)) == custom
    restarted = AstraMusic()
    assert asyncio.run(restarted.ui_widget_theme_get()) == custom
    assert restarted._read_settings()["yandex"]["token"] == "private"
    assert restarted._read_settings()["desktop_widget"] == {"enabled": False, "x": 20}
    switched = asyncio.run(restarted.ui_widget_theme_set(settings={"mode": "provider"}))
    assert switched == {**custom, "mode": "provider"}


def test_remote_and_queue_share_palette_without_extra_http_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    music = AstraMusic()
    music._playback_state["service"] = "vk"
    remote = asyncio.run(music.ui_remote_state())
    queue = asyncio.run(music.ui_queue())
    state = asyncio.run(music.ui_playback_state())
    assert queue["service"] == "vk"
    assert remote["widget_theme"] == queue["widget_theme"] == state["widget_theme"] == DEFAULTS
