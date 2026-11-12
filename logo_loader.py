"""Load PLG logo for tkinter."""

from __future__ import annotations

import base64
import re
import tkinter as tk
from io import BytesIO
from pathlib import Path

from plg_paths import app_dir, resource_path

PROJECT_DIR = app_dir()
LOGO_CANDIDATES = (
    resource_path("assets", "logo.png"),
    resource_path("assets", "logo.jpg"),
    resource_path("assets", "logo.svg"),
    resource_path("plug.jpg"),
)
LOGO_PNG_CACHE = app_dir() / "assets" / "logo.png"


def _load_image_file(path: Path):
    from PIL import Image

    if path.suffix.lower() == ".svg":
        text = path.read_text(encoding="utf-8")
        match = re.search(r"data:image/(?:jpeg|jpg|png);base64,([^\"']+)", text, re.I)
        if match:
            raw = base64.b64decode(match.group(1))
            return Image.open(BytesIO(raw))
        return _rasterize_brand_mark()
    return Image.open(path)


def _rasterize_brand_mark():
    """Typographic PLG mark when vector SVG cannot be rasterized."""
    from PIL import Image, ImageDraw, ImageFont

    width, height = 200, 56
    image = Image.new("RGBA", (width, height), (3, 3, 3, 255))
    draw = ImageDraw.Draw(image)

    try:
        font = ImageFont.truetype("segoeui.ttf", 34)
        font_sub = ImageFont.truetype("segoeui.ttf", 9)
    except OSError:
        font = ImageFont.load_default()
        font_sub = font

    draw.text((4, 6), "PLG", fill=(242, 242, 242, 255), font=font)
    draw.text((6, 40), "Pluggin'", fill=(122, 122, 122, 255), font=font_sub)
    draw.rectangle((0, 0, width - 1, height - 1), outline=(42, 42, 42, 255))
    return image


def ensure_logo_png() -> Path | None:
    if LOGO_PNG_CACHE.is_file():
        return LOGO_PNG_CACHE
    svg = resource_path("assets", "logo.svg")
    if not svg.is_file():
        return None
    try:
        from PIL import Image

        image = _rasterize_brand_mark()
        LOGO_PNG_CACHE.parent.mkdir(parents=True, exist_ok=True)
        image.convert("RGB").save(LOGO_PNG_CACHE, format="PNG")
        return LOGO_PNG_CACHE
    except OSError:
        return None


def load_logo_photo(master: tk.Misc, max_height: int = 52) -> tk.PhotoImage | None:
    ensure_logo_png()
    path = next((candidate for candidate in LOGO_CANDIDATES if candidate.is_file()), None)
    if path is None:
        return None

    try:
        from PIL import Image, ImageTk

        image = _load_image_file(path)
        if image is None:
            return None
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")

        width, height = image.size
        if height > max_height:
            scale = max_height / height
            image = image.resize((int(width * scale), max_height), Image.Resampling.LANCZOS)

        return ImageTk.PhotoImage(image, master=master)
    except (ImportError, OSError):
        return None
