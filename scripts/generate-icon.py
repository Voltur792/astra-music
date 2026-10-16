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
SIZE = 128
SIDE = SIZE * SCALE
PNG_MAX_BYTES = 8192


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

    # Bold headphones and Astra's four-point star stay legible at 16 px.
    draw = ImageDraw.Draw(canvas)
    k = s / 1024
    def box(values): return tuple(round(v*k) for v in values)
    draw.arc(box((215, 210, 810, 850)), 180, 360, fill=(245, 241, 255), width=round(65*k))
    draw.rounded_rectangle(box((195, 490, 330, 785)), radius=round(52*k), fill=(245, 241, 255))
    draw.rounded_rectangle(box((695, 490, 830, 785)), radius=round(52*k), fill=(245, 241, 255))
    draw.polygon([tuple(round(v*k) for v in point) for point in
                  ((510,390),(550,510),(675,552),(550,594),(510,720),(468,594),(345,552),(468,510))],
                 fill=(91, 236, 236))
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
    image.quantize(colors=128).save(ROOT / "icon.png", optimize=True, compress_level=9)
    (ROOT / "ui" / "app-icon.png").write_bytes((ROOT / "icon.png").read_bytes())
    image.save(ROOT / "ui" / "desktop-widget.ico", format="ICO",
               sizes=[(size, size) for size in (16, 20, 24, 32, 48, 64, 128)])
    icon_size = (ROOT / "icon.png").stat().st_size
    if icon_size > PNG_MAX_BYTES:
        raise SystemExit(f"icon.png is {icon_size} bytes; maximum is {PNG_MAX_BYTES} bytes")
    tab_svg = svg_data(image.resize((48, 48), Image.Resampling.LANCZOS), "Музыка")
    module = '"""Navigation icon generated from the shared Astra Music icon."""\n\nTAB_ICON_SVG = ' + repr(tab_svg) + "\n"
    (ROOT / "src" / "tab_icon.py").write_text(module, encoding="utf-8")
    print(f"Generated shared {SIZE}px icon assets; icon.png is {icon_size:,}/{PNG_MAX_BYTES:,} bytes")


if __name__ == "__main__":
    main()
