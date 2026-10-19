"""Image conversions and tools via Pillow (+ pillow-heif for HEIC input; AVIF is built into Pillow 11.2+)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont, ImageOps

from shiftdial.engines import JobContext
from shiftdial.registry import CAMERA_RAW
from shiftdial.utils.paths import Output

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # HEIC input just won't open
    pass

PIL_FORMAT = {"png": "PNG", "jpg": "JPEG", "webp": "WEBP", "avif": "AVIF", "ico": "ICO", "bmp": "BMP",
              "gif": "GIF", "tif": "TIFF", "tiff": "TIFF"}
SAVE_OPTS: dict[str, dict[str, Any]] = {
    "jpg": {"quality": 92, "optimize": True},
    "webp": {"quality": 90, "method": 4},
    "avif": {"quality": 80},
    "png": {"optimize": True},
}
LOSSY = ("jpg", "webp", "avif")


def load(src: Path) -> Image.Image:
    """Open, apply EXIF rotation, normalise to RGB/RGBA. First frame only."""
    if src.suffix.lower().lstrip(".") in CAMERA_RAW.split():
        import rawpy  # camera RAW: develop with the camera's white balance

        with rawpy.imread(str(src)) as raw:
            return Image.fromarray(raw.postprocess(use_camera_wb=True))
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        has_alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
        return im.convert("RGBA" if has_alpha else "RGB")


def flatten(im: Image.Image) -> Image.Image:
    """Composite transparency onto white for formats without alpha."""
    if im.mode != "RGBA":
        return im
    bg = Image.new("RGB", im.size, "white")
    bg.paste(im, mask=im.getchannel("A"))
    return bg


def _square_icon(im: Image.Image) -> Image.Image:
    side = max(im.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
    return canvas.resize((256, 256), Image.LANCZOS) if side > 256 else canvas


def same_ext(src: Path) -> str:
    """Output extension for tools: keep the source format; HEIC etc. become JPG."""
    ext = src.suffix.lower().lstrip(".")
    ext = "jpg" if ext == "jpeg" else ext
    return ext if ext in PIL_FORMAT else "jpg"


def save(im: Image.Image, path: Path, ext: str, quality: int | None = None) -> None:
    opts = dict(SAVE_OPTS.get(ext, {}))
    if quality is not None and ext in LOSSY:
        opts["quality"] = quality
    if ext in ("jpg", "bmp"):
        im = flatten(im)
    elif ext == "ico":
        im = _square_icon(im)
        opts["sizes"] = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256) if s <= im.width]
    im.save(path, PIL_FORMAT[ext], **opts)


def each(paths: list[Path], ctx: JobContext, label: str, edit: Callable[[Image.Image], Image.Image],
         ext_for: Callable[[Path], str] = same_ext, quality: int | None = None) -> list[Path]:
    """Load each image, ``edit`` it, save next to the source."""
    outputs: list[Path] = []
    for i, src in enumerate(paths):
        ctx.check()
        n = len(paths)
        ctx.progress(i / n, f"{src.name}: {label}")
        im = load(src)
        ctx.progress((i + 0.35) / n, f"{src.name}: {label}")
        im = edit(im)
        ctx.progress((i + 0.6) / n, f"{src.name}: saving")
        ext = ext_for(src)
        with Output.next_to(src, ext) as out:
            save(im, out.tmp, ext, quality)
        outputs.append(out.final)
    ctx.progress(1.0, "Done")
    return outputs


def convert(paths: list[Path], fmt: str, ctx: JobContext) -> list[Path]:
    if fmt == "pdf":
        return [_to_pdf(paths, ctx)]
    if fmt == "svg":
        return [_trace(src, ctx) for src in paths]
    return each(paths, ctx, f"→ {fmt.upper()}", lambda im: im, ext_for=lambda _: fmt)


TRACE_MAX = 1600  # px; tracing a 24 MP photo takes minutes and yields a huge SVG


def _trace(src: Path, ctx: JobContext) -> Path:
    """Raster → SVG: colour regions traced into vector paths (vtracer)."""
    import tempfile

    import vtracer

    ctx.check()
    ctx.progress(0.1, f"{src.name}: tracing")
    im = load(src)
    im.thumbnail((TRACE_MAX, TRACE_MAX))
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "in.png"
        im.save(png)
        with Output.next_to(src, "svg") as out:
            vtracer.convert_image_to_svg_py(str(png), str(out.tmp), colormode="color")
    return out.final


def _to_pdf(paths: list[Path], ctx: JobContext) -> Path:
    """All images → one PDF, one page each, named after the first image."""
    pages: list[Image.Image] = []
    for i, src in enumerate(paths):
        ctx.check()
        ctx.progress(i / len(paths), f"Adding {src.name}")
        pages.append(flatten(load(src)))
    with Output.next_to(paths[0], "pdf") as out:
        pages[0].save(out.tmp, "PDF", save_all=True, append_images=pages[1:], resolution=72.0)
    ctx.progress(1.0, "Done")
    return out.final


def tool_resize(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    """mode 'percent' (value = %) or 'max_side' (value = longest side in px)."""
    mode, value = p.get("mode", "percent"), float(p.get("value", 50))
    if value <= 0:
        raise ValueError("Size must be positive")

    def edit(im: Image.Image) -> Image.Image:
        scale = value / 100 if mode == "percent" else value / max(im.size)
        return im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)

    return each(paths, ctx, "Resize", edit)


def tool_compress(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    """JPG/WEBP/AVIF re-encode at ``quality``; PNG drops to a 256-colour palette; others become JPG."""
    quality = int(p.get("quality", 75))

    def ext_for(src: Path) -> str:
        ext = same_ext(src)
        return ext if ext in (*LOSSY, "png") else "jpg"

    def quantize(im: Image.Image) -> Image.Image:
        method = Image.Quantize.FASTOCTREE if im.mode == "RGBA" else Image.Quantize.MEDIANCUT
        return im.quantize(256, method=method)

    outputs: list[Path] = []
    for src in paths:
        palette = ext_for(src) == "png" and quality < 100
        outputs += each([src], ctx, "Compress", quantize if palette else (lambda im: im), ext_for, quality)
    return outputs


ROTATIONS = {
    "right": Image.Transpose.ROTATE_270,  # Pillow's ROTATE_* are counter-clockwise
    "left": Image.Transpose.ROTATE_90,
    "180": Image.Transpose.ROTATE_180,
    "flip_h": Image.Transpose.FLIP_LEFT_RIGHT,
    "flip_v": Image.Transpose.FLIP_TOP_BOTTOM,
}


def tool_grayscale(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    def edit(im: Image.Image) -> Image.Image:
        grey = im.convert("L")
        return Image.merge("RGBA", (grey, grey, grey, im.getchannel("A"))) if im.mode == "RGBA" else grey

    return each(paths, ctx, "Grayscale", edit)


def tool_rotate(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    op = ROTATIONS[p.get("op", "right")]
    return each(paths, ctx, "Rotate", lambda im: im.transpose(op))


def crop_box(box: list[float] | tuple[float, ...], w: int, h: int) -> tuple[int, int, int, int]:
    """Fractions (x0, y0, x1, y1) of the image → pixel box, at least 1x1."""
    x0, y0, x1, y1 = (float(v) for v in box)
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError(f"Bad crop box {box}")
    l, t = round(x0 * w), round(y0 * h)
    return l, t, max(l + 1, round(x1 * w)), max(t + 1, round(y1 * h))


def tool_crop(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    """box = fractions [x0, y0, x1, y1], so one frame applies to images of any size."""
    box = p["box"]
    return each(paths, ctx, "Crop", lambda im: im.crop(crop_box(box, *im.size)))


def tool_strip_exif(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    """Remove EXIF/GPS. Upright JPEGs reuse their own quantization tables (no visible loss)."""
    outputs: list[Path] = []
    for i, src in enumerate(paths):
        ctx.check()
        ctx.progress(i / len(paths), f"{src.name}: Strip EXIF")
        with Image.open(src) as raw:
            upright = raw.getexif().get(0x0112, 1) == 1  # 0x0112 = Orientation tag
            if raw.format == "JPEG" and upright:
                with Output.next_to(src, "jpg") as out:
                    raw.save(out.tmp, "JPEG", quality="keep", subsampling="keep",
                             icc_profile=raw.info.get("icc_profile"))
                outputs.append(out.final)
                continue
        ext = same_ext(src)
        with Output.next_to(src, ext) as out:
            save(load(src), out.tmp, ext, quality=95)
        outputs.append(out.final)
    ctx.progress(1.0, "Done")
    return outputs


POSITIONS = ("bottom-right", "bottom-left", "top-right", "top-left", "center")


def watermark(im: Image.Image, text: str, size_pct: float = 5, position: str = "bottom-right",
              opacity: float = 60) -> Image.Image:
    im = im.convert("RGBA")
    px = max(8, round(min(im.size) * size_pct / 100))
    try:
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.truetype("arial.ttf", px)
    except OSError:
        font = ImageFont.load_default(px)
    stroke = max(1, px // 15)
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    tw, th, margin = right - left, bottom - top, px // 2
    vert, _, horiz = position.partition("-") if position != "center" else ("middle", "", "center")
    x = {"left": margin, "center": (im.width - tw) // 2, "right": im.width - tw - margin}[horiz]
    y = {"top": margin, "middle": (im.height - th) // 2, "bottom": im.height - th - margin}[vert]
    alpha = round(255 * opacity / 100)
    draw.text((x - left, y - top), text, font=font, fill=(255, 255, 255, alpha),
              stroke_width=stroke, stroke_fill=(0, 0, 0, alpha))
    return Image.alpha_composite(im, layer)


def tool_watermark(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    text = str(p.get("text", "")).strip()
    if not text:
        raise ValueError("Watermark text is empty")
    size, pos, opacity = float(p.get("size", 5)), p.get("position", "bottom-right"), float(p.get("opacity", 60))
    return each(paths, ctx, "Watermark", lambda im: watermark(im, text, size, pos, opacity))
