import asyncio
import math

from src.plugin import AstraMusic, VISUALIZER_STYLES, _visualizer_settings


def test_style_presets_survive_restart_and_do_not_overwrite_other_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    plugin = AstraMusic()
    plugin._write_settings({"playback_volume": .6, "radio_presenter": {"enabled": True, "every": 3}})
    presets = {"liquid": {"palette": "single", "color1": "#FF3010", "brightness": 1.8, "width": .7},
               "flower": {"palette": "gradient", "color1": "#12aa45", "color2": "#5412aa", "thickness": 2}}
    result = asyncio.run(plugin.ui_visualizer_set(settings={"style": "flower", "customizations": presets}))
    restarted = AstraMusic()
    assert restarted._visualizer_settings == result
    assert result["customizations"]["liquid"]["color1"] == "#ff3010"
    assert result["customizations"]["flower"]["thickness"] == 2
    assert restarted._read_settings()["playback_volume"] == .6
    assert restarted._read_settings()["radio_presenter"]["enabled"] is True
    remaining = dict(result["customizations"])
    del remaining["flower"]
    reset = asyncio.run(restarted.ui_visualizer_set(settings={"customizations": remaining}))
    assert reset["style"] == "flower" and "flower" not in reset["customizations"]
    assert reset["customizations"]["liquid"] == result["customizations"]["liquid"]


def test_presets_reject_injected_colors_and_nonfinite_values_and_bound_cost():
    result = _visualizer_settings({"customizations": {
        "liquid": {"palette": "unknown", "color1": "red;filter:url(x)", "color2": "#bad",
                   "blur": 10000, "density": 10000, "scale": -2, "brightness": math.nan, "speed": math.inf},
        "unknown": {}, "waves": None}})
    assert set(result["customizations"]) == {"liquid"}
    custom = result["customizations"]["liquid"]
    assert custom["palette"] == "original" and custom["color1"] == "#38d9ef"
    assert custom["color2"] == "#d857ff"
    assert custom["blur"] == 8 and custom["density"] == 2 and custom["scale"] == .5
    assert custom["brightness"] == 1 and custom["speed"] == 1


def test_migration_keeps_original_look_and_new_styles_are_saved():
    old = _visualizer_settings({"style": "liquid", "intensity": .8})
    assert old["customizations"] == {} and old["style"] == "liquid" and old["intensity"] == .8
    for style in ("aurora", "tunnel", "stars", "flower"):
        assert style in VISUALIZER_STYLES
        assert _visualizer_settings({"style": style})["style"] == style
