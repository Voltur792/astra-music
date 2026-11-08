import pytest

from src.screen_color_native import PixelSelection, PickerError, colorref_to_hex, hex_to_colorref


@pytest.mark.parametrize("color", ["#000000", "#ffffff", "#ffcc00", "#aAbBcC", "#012345"])
def test_windows_color_channel_order_round_trip(color):
    assert colorref_to_hex(hex_to_colorref(color)) == color.lower()


def test_invalid_pixel_is_not_saved_as_white():
    with pytest.raises(PickerError, match="pixel_read"):
        colorref_to_hex(0xFFFFFFFF)


def test_selection_samples_once_and_swallows_paired_click_only():
    calls = []
    def sample():
        calls.append(True)
        return 0x00332211
    selection = PixelSelection()
    assert not selection.mouse(0x200, sample)
    assert selection.mouse(0x201, sample)
    assert selection.mouse(0x201, sample)
    assert not selection.complete
    assert selection.mouse(0x202, sample)
    assert selection.complete
    assert selection.result == {"color": "#112233"}
    assert calls == [True]


def test_right_click_cancels_without_sampling():
    selection = PixelSelection()
    def sample():
        raise AssertionError("Cancellation must not read pixels")
    assert selection.mouse(0x204, sample)
    assert selection.mouse(0x205, sample)
    assert selection.complete
    assert selection.result == {"cancelled": True}
