"""Create the shared Astra Music store and navigation icon assets.

Run from any directory with Pillow installed: python scripts/generate-icon.py
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
SCALE = 4
SIZE = 256
SIDE = SIZE * SCALE
PNG_MAX_BYTES = 128 * 1024


def lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(image)
    for y in range(size):
        t = y / max(1, size - 1)
        color = tuple(lerp(top[i], bottom[i], t) for i in range(3)) + (255,)
        draw.line((0, y, size, y), fill=color)
    return image


def make_icon() -> Image.Image:
    s = SIDE
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((8, 8, s - 8, s - 8), radius=178, fill=255)
    canvas = gradient(s, (23, 27, 74), (92, 34, 153))
    canvas.putalpha(mask)

    # Soft cyan and magenta light keeps the central mark lively at small sizes.
    halo = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    hd.ellipse((65, 268, 610, 815), fill=(199, 54, 255, 145))
    hd.ellipse((401, 93, 940, 634), fill=(34, 226, 222, 125))
    halo = halo.filter(ImageFilter.GaussianBlur(104))
    halo.putalpha(Image.composite(halo.getchannel("A"), Image.new("L", (s, s), 0), mask))
    canvas.alpha_composite(halo)

    # Three compact spectrum bars hint at responsive playback without crowding the note.
    draw = ImageDraw.Draw(canvas)
    bar_colors = ((83, 232, 230, 230), (156, 113, 255, 230), (245, 137, 235, 230))
    for x, top, color in ((724, 454, bar_colors[0]), (794, 350, bar_colors[1]), (864, 414, bar_colors[2])):
        draw.rounded_rectangle((x, top, x + 30, 700), radius=15, fill=color)

    # A single eighth note, shaped from rounded strokes and oval notehead.
    note_mask = Image.new("L", (s, s), 0)
    nd = ImageDraw.Draw(note_mask)
    nd.line((468, 205, 468, 579), fill=255, width=62)
    nd.polygon(((454, 208), (744, 143), (744, 218), (526, 270)), fill=255)
    nd.ellipse((322, 537, 505, 656), fill=255)
    note_glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    note_glow.paste((104, 244, 255, 155), (0, 0, s, s), note_mask.filter(ImageFilter.GaussianBlur(34)))
    note_glow.putalpha(Image.composite(note_glow.getchannel("A"), Image.new("L", (s, s), 0), mask))
    canvas.alpha_composite(note_glow)
    note_fill = gradient(s, (226, 247, 255), (255, 231, 255))
    note_fill.putalpha(note_mask)
    canvas.alpha_composite(note_fill)

    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((8, 8, s - 8, s - 8), radius=178, outline=(225, 208, 255, 130), width=4)
    return canvas.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def svg_data(image: Image.Image, label: str) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" '
        f'role="img" aria-label="{label}">'
        f'<image x="0" y="0" width="{SIZE}" height="{SIZE}" '
        f'href="data:image/png;base64,{encoded}"/></svg>'
    )


def main() -> None:
    image = make_icon()
    image.save(ROOT / "icon.png", optimize=True, compress_level=9)
    icon_size = (ROOT / "icon.png").stat().st_size
    if icon_size > PNG_MAX_BYTES:
        raise SystemExit(f"icon.png is {icon_size} bytes; maximum is {PNG_MAX_BYTES} bytes")
    tab_svg = svg_data(image.resize((48, 48), Image.Resampling.LANCZOS), "Музыка")
    module = '"""Navigation icon generated from the shared Astra Music icon."""\n\nTAB_ICON_SVG = ' + repr(tab_svg) + "\n"
    (ROOT / "src" / "tab_icon.py").write_text(module, encoding="utf-8")
    print(f"Generated shared {SIZE}px icon assets; icon.png is {icon_size:,}/{PNG_MAX_BYTES:,} bytes")


if __name__ == "__main__":
    main()
