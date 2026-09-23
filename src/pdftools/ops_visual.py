"""Visual operations: watermarks, page numbers, and image conversion.

Overlays are drawn with reportlab into an in-memory page, then merged onto the
source with pypdf. They are laid out in the page's *displayed* frame — the
visible crop box, turned by the page's ``/Rotate`` — and mapped back into the
page's own coordinate space, so a page number lands at the bottom centre that
a reader actually sees, even on rotated or offset pages. Rasterisation to and
from images uses Pillow and pypdfium2. Nothing here mutates the input file.
"""

from __future__ import annotations

import io
import os
from typing import Dict, List, Optional, Sequence, Tuple

from pypdf import PdfReader, Transformation

from ._util import (
    PdfToolsError,
    binary_streams,
    default_output,
    ensure_parent_dir,
    expand_inputs,
    open_pdfium,
    open_writer,
    parse_color,
    parse_page_ranges,
    write_pdf,
)


# ---------------------------------------------------------------------------
# Displayed-frame geometry shared by every overlay
# ---------------------------------------------------------------------------

def _display_frame(page) -> Tuple[float, float, Tuple[float, ...]]:
    """Return ``(width, height, ctm)`` of the page as a reader sees it.

    ``width``/``height`` are the visible crop box after ``/Rotate``; ``ctm``
    maps a point drawn in that upright frame (origin bottom-left) into the
    page's unrotated user space, including the crop box origin.
    """
    box = page.cropbox
    x0, y0 = float(box.left), float(box.bottom)
    cw, ch = float(box.width), float(box.height)
    rotation = int(page.rotation or 0) % 360
    if rotation == 90:
        return ch, cw, (0.0, 1.0, -1.0, 0.0, x0 + cw, y0)
    if rotation == 180:
        return cw, ch, (-1.0, 0.0, 0.0, -1.0, x0 + cw, y0 + ch)
    if rotation == 270:
        return ch, cw, (0.0, -1.0, 1.0, 0.0, x0, y0 + ch)
    return cw, ch, (1.0, 0.0, 0.0, 1.0, x0, y0)


class _OverlayCache:
    """Parse each distinct overlay once, however many pages it lands on."""

    def __init__(self) -> None:
        self._pages: Dict[tuple, object] = {}

    def get(self, key: tuple, build) -> object:
        page = self._pages.get(key)
        if page is None:
            page = PdfReader(io.BytesIO(build())).pages[0]
            self._pages[key] = page
        return page


def _stamp(page, overlay_page, ctm: Tuple[float, ...]) -> None:
    page.merge_transformed_page(overlay_page, Transformation(ctm))


# ---------------------------------------------------------------------------
# Watermark
# ---------------------------------------------------------------------------

def _text_overlay(
    width: float,
    height: float,
    text: str,
    *,
    angle: float,
    opacity: float,
    font_size: int,
    color: str,
    tiled: bool,
) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    r, g, b = parse_color(color)
    c.setFillColorRGB(r, g, b)
    c.setFont("Helvetica-Bold", font_size)
    try:
        c.setFillAlpha(max(0.0, min(1.0, opacity)))
    except Exception:  # pragma: no cover - alpha unsupported
        pass

    def stamp(cx: float, cy: float) -> None:
        c.saveState()
        c.translate(cx, cy)
        c.rotate(angle)
        c.drawCentredString(0, 0, text)
        c.restoreState()

    if tiled:
        # Estimate the stamp footprint so tiles do not overlap badly.
        step_x = max(font_size * len(text) * 0.6, font_size * 4)
        step_y = font_size * 3.0
        y = -step_y
        while y < height + step_y:
            x = -step_x
            while x < width + step_x:
                stamp(x, y)
                x += step_x
            y += step_y
    else:
        stamp(width / 2.0, height / 2.0)

    c.showPage()
    c.save()
    return buf.getvalue()


def _load_stamp_image(image_path: str, opacity: float) -> Tuple[bytes, int, int]:
    from PIL import Image, ImageOps, UnidentifiedImageError

    if not os.path.isfile(image_path):
        raise PdfToolsError(f"Watermark image not found: {image_path!r}")
    try:
        with Image.open(image_path) as im:
            rgba = ImageOps.exif_transpose(im).convert("RGBA")
    except (UnidentifiedImageError, OSError) as exc:
        raise PdfToolsError(f"{image_path!r} is not a readable image ({exc}).") from exc
    if opacity < 1.0:
        alpha = rgba.getchannel("A").point(lambda a: int(a * max(0.0, min(1.0, opacity))))
        rgba.putalpha(alpha)
    png_buf = io.BytesIO()
    rgba.save(png_buf, format="PNG")
    return png_buf.getvalue(), rgba.size[0], rgba.size[1]


def _image_overlay(
    width: float,
    height: float,
    png: bytes,
    iw: int,
    ih: int,
    *,
    scale: float,
    position: str,
) -> bytes:
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    target_w = width * scale
    ratio = target_w / iw
    target_h = ih * ratio

    if position == "center":
        x = (width - target_w) / 2.0
        y = (height - target_h) / 2.0
    elif position == "top-left":
        x, y = 24, height - target_h - 24
    elif position == "top-right":
        x, y = width - target_w - 24, height - target_h - 24
    elif position == "bottom-left":
        x, y = 24, 24
    elif position == "bottom-right":
        x, y = width - target_w - 24, 24
    else:
        raise PdfToolsError(f"Unknown stamp position: {position!r}")

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.drawImage(ImageReader(io.BytesIO(png)), x, y, target_w, target_h, mask="auto")
    c.showPage()
    c.save()
    return buf.getvalue()


def watermark(
    input_path: str,
    output: Optional[str] = None,
    *,
    text: Optional[str] = None,
    image: Optional[str] = None,
    angle: float = 45,
    opacity: float = 0.15,
    font_size: int = 48,
    color: str = "gray",
    tiled: bool = False,
    position: str = "center",
    scale: float = 0.4,
    password: Optional[str] = None,
) -> str:
    """Stamp a text watermark or a logo image onto every page.

    Exactly one of ``text`` or ``image`` must be supplied.

    Text options: ``angle`` (degrees), ``opacity`` (0..1), ``font_size``,
    ``color`` (name or ``#RRGGBB``), and ``tiled`` to repeat across the page
    instead of a single centred stamp.

    Image options: ``opacity``, ``scale`` (fraction of page width) and
    ``position`` (center / top-left / top-right / bottom-left / bottom-right).

    Positions refer to the page as displayed, so rotated pages are stamped
    where a reader expects. Returns the output path.
    """
    if bool(text) == bool(image):
        raise PdfToolsError("Provide exactly one of text or image.")
    if position not in ("center", "top-left", "top-right", "bottom-left", "bottom-right"):
        raise PdfToolsError(f"Unknown stamp position: {position!r}")
    if text:
        parse_color(color)  # fail fast on a bad colour, before any work

    stamp_png = None
    if image:
        stamp_png = _load_stamp_image(image, opacity)

    # clone_from attaches pages to the writer, so merging overlays in place is
    # the supported, warning-free path.
    writer = open_writer(input_path, password)
    output = output or default_output(input_path, "watermarked")
    cache = _OverlayCache()

    for page in writer.pages:
        w, h, ctm = _display_frame(page)
        key = (round(w, 2), round(h, 2))
        if text:
            overlay = cache.get(key, lambda: _text_overlay(
                w, h, text, angle=angle, opacity=opacity,
                font_size=font_size, color=color, tiled=tiled,
            ))
        else:
            png, iw, ih = stamp_png
            overlay = cache.get(key, lambda: _image_overlay(
                w, h, png, iw, ih, scale=scale, position=position,
            ))
        _stamp(page, overlay, ctm)

    return write_pdf(writer, output)


# ---------------------------------------------------------------------------
# Page numbers
# ---------------------------------------------------------------------------

_POSITIONS = {
    "bottom-center": ("center", "bottom"),
    "bottom-right": ("right", "bottom"),
    "bottom-left": ("left", "bottom"),
    "top-center": ("center", "top"),
    "top-right": ("right", "top"),
    "top-left": ("left", "top"),
}


def _number_overlay(
    width: float,
    height: float,
    label: str,
    *,
    position: str,
    font_size: int,
    color: str,
    margin: float,
) -> bytes:
    from reportlab.pdfgen import canvas

    h_align, v_align = _POSITIONS[position]

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    r, g, b = parse_color(color)
    c.setFillColorRGB(r, g, b)
    c.setFont("Helvetica", font_size)

    y = margin if v_align == "bottom" else height - margin - font_size
    if h_align == "center":
        c.drawCentredString(width / 2.0, y, label)
    elif h_align == "right":
        c.drawRightString(width - margin, y, label)
    else:
        c.drawString(margin, y, label)

    c.showPage()
    c.save()
    return buf.getvalue()


def _check_number_format(fmt: str) -> None:
    try:
        fmt.format(n=1, total=1)
    except KeyError as exc:
        raise PdfToolsError(
            f"Unknown placeholder {{{exc.args[0]}}} in number format {fmt!r}; "
            "use {n} and {total}, e.g. \"Page {n} of {total}\"."
        ) from exc
    except (IndexError, ValueError, AttributeError) as exc:
        raise PdfToolsError(
            f"Invalid number format {fmt!r} ({exc}); use {{n}} and {{total}}, "
            "and write literal braces as {{ and }}."
        ) from exc


def page_numbers(
    input_path: str,
    output: Optional[str] = None,
    *,
    position: str = "bottom-center",
    fmt: str = "{n}",
    font_size: int = 10,
    color: str = "black",
    skip_first: bool = False,
    margin: float = 28.0,
    start_at: int = 1,
    password: Optional[str] = None,
) -> str:
    """Stamp page numbers onto the document.

    ``fmt`` is a template with ``{n}`` (current, honouring ``start_at``) and
    ``{total}`` (page count), e.g. ``"Page {n} of {total}"``. ``skip_first``
    leaves the cover page unnumbered but still counts it. ``position`` is one
    of the six corner/edge anchors, relative to the page as displayed (so
    rotated pages get upright numbers in the right place). Returns the output
    path.
    """
    if position not in _POSITIONS:
        raise PdfToolsError(
            f"position must be one of {', '.join(sorted(_POSITIONS))}."
        )
    _check_number_format(fmt)
    parse_color(color)

    writer = open_writer(input_path, password)
    total = len(writer.pages)
    output = output or default_output(input_path, "numbered")
    cache = _OverlayCache()

    for idx, page in enumerate(writer.pages):
        if skip_first and idx == 0:
            continue
        label = fmt.format(n=idx + start_at, total=total)
        w, h, ctm = _display_frame(page)
        overlay = cache.get((round(w, 2), round(h, 2), label), lambda: _number_overlay(
            w, h, label, position=position, font_size=font_size,
            color=color, margin=margin,
        ))
        _stamp(page, overlay, ctm)

    return write_pdf(writer, output)


# ---------------------------------------------------------------------------
# Images <-> PDF
# ---------------------------------------------------------------------------

_NAMED_PAGE_SIZES = {
    # width, height in points (72 dpi)
    "a4": (595.28, 841.89),
    "letter": (612.0, 792.0),
    "legal": (612.0, 1008.0),
    "a3": (841.89, 1190.55),
    "a5": (419.53, 595.28),
}

# Modes reportlab embeds directly; anything else is converted to RGB first.
_EMBEDDABLE_MODES = {"1", "L", "P", "RGB", "RGBA", "LA", "PA", "CMYK"}


def _orientation_transform(orientation: int, sw: float, sh: float) -> Tuple[float, ...]:
    """Canvas transform that shows a stored image the way EXIF says to.

    The stored image is drawn at ``(0, 0, sw, sh)``; the returned matrix maps
    it onto the upright displayed box ``(0, 0, dw, dh)``. Drawing the original
    pixels under a transform keeps JPEGs byte-for-byte (no re-encoding).
    """
    return {
        1: (1, 0, 0, 1, 0, 0),
        2: (-1, 0, 0, 1, sw, 0),        # mirrored
        3: (-1, 0, 0, -1, sw, sh),      # rotated 180
        4: (1, 0, 0, -1, 0, sh),        # mirrored vertically
        5: (0, -1, -1, 0, sh, sw),      # transposed
        6: (0, -1, 1, 0, 0, sw),        # needs 90 clockwise (typical portrait phone shot)
        7: (0, 1, 1, 0, 0, 0),          # transversed
        8: (0, 1, -1, 0, sh, 0),        # needs 90 counter-clockwise
    }.get(orientation, (1, 0, 0, 1, 0, 0))


def _image_facts(path: str):
    """Open ``path`` once: (reader, stored px size, EXIF orientation, dpi)."""
    from PIL import Image, UnidentifiedImageError
    from reportlab.lib.utils import ImageReader

    try:
        with Image.open(path) as im:
            size = im.size
            try:
                orientation = int(im.getexif().get(0x0112, 1) or 1)
            except Exception:
                orientation = 1
            if orientation not in range(1, 9):
                orientation = 1
            dpi = im.info.get("dpi") or (72, 72)
            try:
                xdpi, ydpi = float(dpi[0]), float(dpi[1])
            except (TypeError, ValueError, IndexError):
                xdpi = ydpi = 72.0
            if xdpi < 1 or ydpi < 1:
                xdpi = ydpi = 72.0
            if im.mode in _EMBEDDABLE_MODES:
                reader = ImageReader(path)
            else:
                buf = io.BytesIO()
                im.convert("RGB").save(buf, format="PNG")
                buf.seek(0)
                reader = ImageReader(buf)
    except (UnidentifiedImageError, OSError) as exc:
        raise PdfToolsError(f"{path!r} is not a readable image ({exc}).") from exc
    return reader, size, orientation, (xdpi, ydpi)


def images_to_pdf(
    images: Sequence[str],
    output: str,
    *,
    page_size: str = "auto",
    fit: str = "contain",
    margin: float = 0.0,
) -> str:
    """Combine images into a single PDF, one image per page.

    Images are shown the way a photo viewer shows them: the EXIF orientation
    tag is honoured, so portrait phone shots come out upright. JPEGs are
    embedded as-is (rotation is applied as a transform, never by
    re-encoding).

    ``page_size`` is ``"auto"`` (each page matches its image at the image's
    own DPI, 72 when the file does not say) or a named size (``a4``,
    ``letter``, ``legal``, ``a3``, ``a5``). ``margin`` is white space in
    points around the image. ``fit`` controls placement on a fixed page size:

    * ``contain`` — scale to fit inside the page, preserving aspect (default).
    * ``cover`` — scale to fill the page, cropping the overflow.
    * ``stretch`` — distort to fill exactly.

    ``images`` accepts paths and globs. Returns the output path.
    """
    from reportlab.pdfgen import canvas

    if fit not in ("contain", "cover", "stretch"):
        raise PdfToolsError("fit must be contain, cover or stretch.")
    if margin < 0:
        raise PdfToolsError("margin cannot be negative.")
    named = None
    if page_size != "auto":
        named = _NAMED_PAGE_SIZES.get(page_size.lower())
        if named is None:
            raise PdfToolsError(
                f"Unknown page size {page_size!r}. Use auto or "
                + ", ".join(sorted(_NAMED_PAGE_SIZES))
            )
    files = expand_inputs(list(images))
    facts = [(path,) + _image_facts(path) for path in files]  # validate first

    ensure_parent_dir(output)
    with binary_streams():
        _draw_images(canvas.Canvas(output), facts, named, page_size, fit, margin)
    return output


def _draw_images(c, facts, named, page_size, fit, margin) -> None:
    for _path, reader, (px_w, px_h), orientation, (xdpi, ydpi) in facts:
        sw, sh = px_w * 72.0 / xdpi, px_h * 72.0 / ydpi  # stored size, points
        dw, dh = (sh, sw) if orientation >= 5 else (sw, sh)  # displayed size

        if named is None:
            pw, ph = dw + 2 * margin, dh + 2 * margin
            sx = sy = 1.0
        else:
            pw, ph = named
            avail_w, avail_h = pw - 2 * margin, ph - 2 * margin
            if avail_w <= 0 or avail_h <= 0:
                raise PdfToolsError(f"margin {margin} leaves no room on a {page_size} page.")
            if fit == "stretch":
                sx, sy = avail_w / dw, avail_h / dh
            else:
                pick = min if fit == "contain" else max
                sx = sy = pick(avail_w / dw, avail_h / dh)

        x = (pw - dw * sx) / 2.0
        y = (ph - dh * sy) / 2.0
        c.setPageSize((pw, ph))
        c.saveState()
        if fit == "cover" and named is not None:
            clip = c.beginPath()
            clip.rect(margin, margin, pw - 2 * margin, ph - 2 * margin)
            c.clipPath(clip, stroke=0, fill=0)
        c.translate(x, y)
        c.scale(sx, sy)
        c.transform(*_orientation_transform(orientation, sw, sh))
        c.drawImage(reader, 0, 0, sw, sh, mask="auto")
        c.restoreState()
        c.showPage()
    c.save()


def pdf_to_images(
    input_path: str,
    output_dir: str,
    *,
    dpi: int = 150,
    fmt: str = "png",
    pages: Optional[str] = None,
    password: Optional[str] = None,
) -> List[str]:
    """Render pages to raster images with pypdfium2.

    ``dpi`` trades quality for size (150 is a good screen default, 300 for
    print). ``fmt`` is any Pillow-writable format (png, jpg, tiff). ``pages``
    is a range spec. Returns the list of written image paths.
    """
    fmt = fmt.lower().lstrip(".")
    ext = "jpg" if fmt in ("jpg", "jpeg") else fmt
    stem = os.path.splitext(os.path.basename(input_path))[0]

    pdf = open_pdfium(input_path, password)
    try:
        total = len(pdf)
        indices = parse_page_ranges(pages, total) if pages else list(range(total))
        os.makedirs(output_dir, exist_ok=True)
        scale = dpi / 72.0
        written: List[str] = []
        for idx in indices:
            page = pdf[idx]
            bitmap = page.render(scale=scale)
            pil_image = bitmap.to_pil()
            if ext in ("jpg", "jpeg"):
                pil_image = pil_image.convert("RGB")
            out_path = os.path.join(output_dir, f"{stem}_p{idx + 1:03d}.{ext}")
            try:
                pil_image.save(out_path, dpi=(dpi, dpi))
            except (KeyError, ValueError) as exc:
                raise PdfToolsError(f"Pillow cannot write {ext!r} images ({exc}).") from exc
            written.append(out_path)
        return written
    finally:
        pdf.close()
