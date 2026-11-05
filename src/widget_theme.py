"""User palette settings; text contrast is derived in the renderer."""
import re

DEFAULTS = {"mode": "provider", "background": "#18181b", "accent": "#ffcc00"}


def normalize_theme(value):
    result = dict(DEFAULTS)
    if not isinstance(value, dict):
        return result
    if value.get("mode") in {"provider", "custom"}:
        result["mode"] = value["mode"]
    for key in ("background", "accent"):
        color = value.get(key)
        if isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            result[key] = color.lower()
    return result
