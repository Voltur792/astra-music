import asyncio
from pathlib import Path

from src.plugin import AstraMusic


def test_dom_access_uses_javascript_entries_for_page_and_background(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    contributions = asyncio.run(AstraMusic().get_ui_contributions())
    urls = {item.id: item.url for item in contributions}
    assert urls["music"] == "host-page.js"
    assert urls["music-visualizer"] == "host-visualizer.js"
    assert urls["music-queue-overlay"] == "queue-controller.js"
    ui = Path(__file__).resolve().parents[1] / "ui"
    for name in ("host-page.js", "host-visualizer.js", "host-frames.js", "page-bridge.js"):
        assert (ui / name).is_file()
    assert 'src="page-bridge.js"' in (ui / "index.html").read_text(encoding="utf-8")
    assert 'src="page-bridge.js"' in (ui / "visualizer.html").read_text(encoding="utf-8")
