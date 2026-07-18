"""Visual operations: watermarks, page numbers, and image conversion.

Overlays are drawn with reportlab into an in-memory page, then merged onto the
source with pypdf. Rasterisation to and from images uses Pillow and
pypdfium2. Nothing here mutates the input file.
"""

from __future__ import annotations

import io
import os
from typing import List, Optional, Sequence

from pypdf import PdfReader, PdfWriter

from ._util import (
    PdfToolsError,
    default_output,
    ensure_parent_dir,
    expand_inputs,
    parse_color,
    parse_page_ranges,
)


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
        step_y = max(font_size * 3.0, font_size * 3)
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


def _image_overlay(
    width: float,
    height: float,
    image_path: str,
    *,
    opacity: float,
    scale: float,
    position: str,
) -> bytes:
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    with Image.open(image_path) as im:
        rgba = im.convert("RGBA")
        if opacity < 1.0:
            alpha = rgba.getchannel("A").point(lambda a: int(a * max(0.0, min(1.0, opacity))))
            rgba.putalpha(alpha)
        iw, ih = rgba.size
        png_buf = io.BytesIO()
        rgba.save(png_buf, format="PNG")
    png_buf.seek(0)

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
    c.drawImage(ImageReader(png_buf), x, y, target_w, target_h, mask="auto")
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
) -> str:
    """Stamp a text watermark or a logo image onto every page.

    Exactly one of ``text`` or ``image`` must be supplied.

    Text options: ``angle`` (degrees), ``opacity`` (0..1), ``font_size``,
    ``color`` (name or ``#RRGGBB``), and ``tiled`` to repeat across the page
    instead of a single centred stamp.

    Image options: ``opacity``, ``scale`` (fraction of page width) and
    ``position`` (center / top-left / top-right / bottom-left / bottom-right).

    Returns the output path.
    """
    if bool(text) == bool(image):
        raise PdfToolsError("Provide exactly one of text or image.")

    # clone_from attaches pages to the writer, so merging overlays in place is
    # the supported, warning-free path.
    writer = PdfWriter(clone_from=input_path)
    output = output or default_output(input_path, "watermarked")

    for page in writer.pages:
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)
        if text:
            overlay_bytes = _text_overlay(
                w, h, text, angle=angle, opacity=opacity,
                font_size=font_size, color=color, tiled=tiled,
            )
        else:
            overlay_bytes = _image_overlay(
                w, h, image, opacity=opacity, scale=scale, position=position,
            )
        overlay_page = PdfReader(io.BytesIO(overlay_bytes)).pages[0]
        page.merge_page(overlay_page)

    ensure_parent_dir(output)
    with open(output, "wb") as fh:
        writer.write(fh)
    return output


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

    if position not in _POSITIONS:
        raise PdfToolsError(
            f"position must be one of {', '.join(sorted(_POSITIONS))}."
        )
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
) -> str:
    """Stamp page numbers onto the document.

    ``fmt`` is a template with ``{n}`` (current, honouring ``start_at``) and
    ``{total}`` (page count), e.g. ``"Page {n} of {total}"``. ``skip_first``
    leaves the cover page unnumbered but still counts it. ``position`` is one
    of the six corner/edge anchors. Returns the output path.
    """
    writer = PdfWriter(clone_from=input_path)
    total = len(writer.pages)
    output = output or default_output(input_path, "numbered")

    for idx, page in enumerate(writer.pages):
        if skip_first and idx == 0:
            continue
        label = fmt.format(n=idx + start_at, total=total)
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)
        overlay_bytes = _number_overlay(
            w, h, label, position=position, font_size=font_size,
            color=color, margin=margin,
        )
        overlay_page = PdfReader(io.BytesIO(overlay_bytes)).pages[0]
        page.merge_page(overlay_page)

    ensure_parent_dir(output)
    with open(output, "wb") as fh:
        writer.write(fh)
    return output


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


def images_to_pdf(
    images: Sequence[str],
    output: str,
    *,
    page_size: str = "auto",
    fit: str = "contain",
) -> str:
    """Combine images into a single PDF, one image per page.

    ``page_size`` is ``"auto"`` (each page matches its image) or a named size
    (``a4``, ``letter``, ``legal``, ``a3``, ``a5``). ``fit`` controls placement
    on a fixed page size:

    * ``contain`` — scale to fit inside the page, preserving aspect (default).
    * ``cover`` — scale to fill the page, cropping the overflow.
    * ``stretch`` — distort to fill exactly.

    ``images`` accepts paths and globs. Returns the output path.
    """
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    if fit not in ("contain", "cover", "stretch"):
        raise PdfToolsError("fit must be contain, cover or stretch.")
    files = expand_inputs(list(images))

    ensure_parent_dir(output)
    c = canvas.Canvas(output)
    for path in files:
        with Image.open(path) as im:
            iw, ih = im.size
        if page_size == "auto":
            pw, ph = float(iw), float(ih)
        else:
            key = page_size.lower()
            if key not in _NAMED_PAGE_SIZES:
                raise PdfToolsError(
                    f"Unknown page size {page_size!r}. Use auto or "
                    + ", ".join(sorted(_NAMED_PAGE_SIZES))
                )
            pw, ph = _NAMED_PAGE_SIZES[key]

        c.setPageSize((pw, ph))
        if page_size == "auto" or fit == "stretch":
            c.drawImage(ImageReader(path), 0, 0, pw, ph, mask="auto")
        else:
            if fit == "contain":
                s = min(pw / iw, ph / ih)
            else:  # cover
                s = max(pw / iw, ph / ih)
            dw, dh = iw * s, ih * s
            c.drawImage(
                ImageReader(path), (pw - dw) / 2.0, (ph - dh) / 2.0, dw, dh, mask="auto"
            )
        c.showPage()
    c.save()
    return output


def pdf_to_images(
    input_path: str,
    output_dir: str,
    *,
    dpi: int = 150,
    fmt: str = "png",
    pages: Optional[str] = None,
) -> List[str]:
    """Render pages to raster images with pypdfium2.

    ``dpi`` trades quality for size (150 is a good screen default, 300 for
    print). ``fmt`` is any Pillow-writable format (png, jpg, tiff). ``pages``
    is a range spec. Returns the list of written image paths.
    """
    import pypdfium2 as pdfium

    fmt = fmt.lower().lstrip(".")
    ext = "jpg" if fmt in ("jpg", "jpeg") else fmt
    os.makedirs(output_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(input_path))[0]

    pdf = pdfium.PdfDocument(input_path)
    try:
        total = len(pdf)
        indices = parse_page_ranges(pages, total) if pages else list(range(total))
        scale = dpi / 72.0
        written: List[str] = []
        for idx in indices:
            page = pdf[idx]
            bitmap = page.render(scale=scale)
            pil_image = bitmap.to_pil()
            if ext in ("jpg", "jpeg"):
                pil_image = pil_image.convert("RGB")
            out_path = os.path.join(output_dir, f"{stem}_p{idx + 1:03d}.{ext}")
            pil_image.save(out_path)
            written.append(out_path)
        return written
    finally:
        pdf.close()
