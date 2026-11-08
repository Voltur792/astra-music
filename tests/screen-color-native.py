"""Windows smoke check: real dialogs/hooks, automatic cancellation, no clicks/pixel capture.

Run from the repository root: python -m tests.screen-color-native is not supported;
use python tests/screen-color-native.py instead.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.screen_color_native as native


if __name__ == "__main__":
    api = native.Windows()
    owner = native.PickerWindow(api)
    handle = owner.handle
    owner.close()
    assert not api.user.IsWindow(handle)
    assert native.choose_palette("#ffcc00", api, cancel_immediately=True) == {"cancelled": True}
    times = iter([0, 61])
    original = native.time.monotonic
    try:
        native.time.monotonic = lambda: next(times)
        assert native.choose_pixel(api) == {"cancelled": True}
    finally:
        native.time.monotonic = original
    print("Native palette, mouse hook, cancellation and window cleanup passed")
