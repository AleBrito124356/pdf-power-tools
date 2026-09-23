"""Optical character recognition for scanned PDFs.

Wraps Tesseract (via pytesseract) with a rasterisation step from pypdfium2.
Tesseract is a separate native install, not a pip package, so this module is
careful to detect it and print actionable guidance when it is missing.

The searchable-PDF mode does **not** replace your pages with Tesseract's own
re-rendered raster. It asks Tesseract for word boxes only, converts them from
render pixels back into each page's coordinate space (honouring the crop box
and ``/Rotate``), and lays an invisible text layer over the *original* page.
Page size, vector art, existing text, fonts, bookmarks, links, forms and
metadata are all kept; the page looks exactly as before but becomes
selectable and searchable.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ._util import (
    PdfToolsError,
    default_output,
    ensure_parent_dir,
    open_pdfium,
    open_writer,
    parse_page_ranges,
    write_pdf,
)


_INSTALL_HELP = (
    "Tesseract OCR engine not found.\n"
    "  Windows : winget install UB-Mannheim.TesseractOCR   (or download the "
    "installer from github.com/UB-Mannheim/tesseract/wiki), then either add it "
    "to PATH or set TESSERACT_CMD to tesseract.exe.\n"
    "  macOS   : brew install tesseract\n"
    "  Linux   : sudo apt install tesseract-ocr\n"
    "Extra languages need their traineddata packs (e.g. tesseract-ocr-spa)."
)

# Helvetica's descender (AFM: -207/1000). Putting the baseline this far above
# the bottom of a word box makes the invisible glyph box match the box exactly.
_DESCENT = 0.207
_FONT = "Helvetica"


@dataclass
class OcrWord:
    """One recognised word, in render pixels (top-left origin)."""

    text: str
    left: float
    top: float
    width: float
    height: float
    conf: float = -1.0


@dataclass
class OcrResult:
    """What :func:`ocr_with_stats` did, for reporting."""

    output: str
    mode: str
    pages_ocred: List[int] = field(default_factory=list)    # 1-based
    pages_skipped: List[int] = field(default_factory=list)  # had text already
    words: int = 0


def _configure_tesseract():
    """Return the OCR engine (the pytesseract module), or raise with install help.

    This is the single seam the tests replace with a fake engine exposing
    ``image_to_data`` and ``image_to_string``.
    """
    import pytesseract

    override = os.environ.get("TESSERACT_CMD")
    if override:
        pytesseract.pytesseract.tesseract_cmd = override
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:  # EnvironmentError / TesseractNotFoundError
        raise PdfToolsError(_INSTALL_HELP) from exc
    return pytesseract


def _words_from_data(data: Dict[str, Sequence]) -> List[OcrWord]:
    """Turn pytesseract's ``image_to_data(output_type=DICT)`` into words."""
    words: List[OcrWord] = []
    texts = data.get("text", [])
    for i, raw in enumerate(texts):
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            conf = float(data.get("conf", [-1] * len(texts))[i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < 0 and "conf" in data:
            continue  # structural rows (page/block/line) carry conf -1
        width = float(data["width"][i])
        height = float(data["height"][i])
        if width <= 0 or height <= 0:
            continue
        words.append(
            OcrWord(text, float(data["left"][i]), float(data["top"][i]), width, height, conf)
        )
    return words


def _latin1(text: str) -> str:
    # The invisible layer uses a standard (WinAnsi-encoded) font. Characters
    # outside it would break the stream, so they degrade to "?".
    return text.encode("cp1252", "replace").decode("cp1252")


def _text_layer(
    disp_w: float, disp_h: float, words: Sequence[OcrWord], img_w: int, img_h: int
) -> bytes:
    """An overlay page, in the displayed frame, holding invisible words."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas

    sx = disp_w / float(img_w)
    sy = disp_h / float(img_h)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(disp_w, disp_h))
    for word in words:
        text = _latin1(word.text)
        size = word.height * sy
        if size < 1.0:
            continue
        x = word.left * sx
        box_w = word.width * sx
        bottom = disp_h - (word.top + word.height) * sy
        natural = stringWidth(text, _FONT, size)
        t = c.beginText()
        t.setTextRenderMode(3)  # invisible: selectable and searchable only
        t.setFont(_FONT, size)
        if natural > 0:
            t.setHorizScale(100.0 * box_w / natural)
        t.setTextOrigin(x, bottom + _DESCENT * size)
        t.textOut(text)
        c.drawText(t)
    c.showPage()
    c.save()
    return buf.getvalue()


def _ocr_page_image(engine, image, lang: str, dpi: int, want_boxes: bool):
    config = f"--dpi {int(dpi)}"
    if want_boxes:
        output_type = getattr(getattr(engine, "Output", None), "DICT", "dict")
        data = engine.image_to_data(image, lang=lang, config=config, output_type=output_type)
        words = _words_from_data(data)
        return words, " ".join(w.text for w in words)
    return None, engine.image_to_string(image, lang=lang, config=config)


def ocr_with_stats(
    input_path: str,
    output: Optional[str] = None,
    *,
    mode: str = "sidecar",
    lang: str = "eng",
    dpi: int = 300,
    pages: Optional[str] = None,
    skip_text: bool = True,
    password: Optional[str] = None,
) -> OcrResult:
    """Run OCR and report per-page what happened. See :func:`ocr`."""
    if mode not in ("sidecar", "pdf"):
        raise PdfToolsError("mode must be 'sidecar' or 'pdf'.")
    if dpi < 50:
        raise PdfToolsError("dpi must be at least 50 for usable OCR.")
    engine = _configure_tesseract()

    if mode == "sidecar":
        output = output or default_output(input_path, "ocr", ext=".txt")
    else:
        output = output or default_output(input_path, "searchable")
    result = OcrResult(output=output, mode=mode)

    chunks: List[str] = []
    layers: Dict[int, Tuple[List[OcrWord], int, int]] = {}
    pdf = open_pdfium(input_path, password)
    try:
        total = len(pdf)
        indices = parse_page_ranges(pages, total) if pages else list(range(total))
        if not indices:
            raise PdfToolsError("No pages to OCR.")
        for idx in indices:
            page = pdf[idx]
            try:
                textpage = page.get_textpage()
                try:
                    existing = textpage.get_text_range()
                finally:
                    textpage.close()
                existing = existing.replace("\r\n", "\n").replace("\r", "\n")
                if skip_text and existing.strip():
                    result.pages_skipped.append(idx + 1)
                    chunks.append(existing)
                    continue
                image = page.render(scale=dpi / 72.0).to_pil()
            finally:
                page.close()
            words, text = _ocr_page_image(engine, image, lang, dpi, mode == "pdf")
            result.pages_ocred.append(idx + 1)
            if words is not None:
                result.words += len(words)
                layers[idx] = (words, image.width, image.height)
            else:
                result.words += len(text.split())
            chunks.append(text)
    finally:
        pdf.close()

    if mode == "sidecar":
        ensure_parent_dir(output)
        with open(output, "w", encoding="utf-8") as fh:
            fh.write("\f".join(chunks))
        return result

    from pypdf import PdfReader

    from .ops_visual import _display_frame, _stamp

    writer = open_writer(input_path, password)
    for idx, (words, img_w, img_h) in layers.items():
        if not words:
            continue
        page = writer.pages[idx]
        disp_w, disp_h, ctm = _display_frame(page)
        overlay = PdfReader(io.BytesIO(_text_layer(disp_w, disp_h, words, img_w, img_h))).pages[0]
        _stamp(page, overlay, ctm)
    write_pdf(writer, output)
    return result


def ocr(
    input_path: str,
    output: Optional[str] = None,
    *,
    mode: str = "sidecar",
    lang: str = "eng",
    dpi: int = 300,
    pages: Optional[str] = None,
    skip_text: bool = True,
    password: Optional[str] = None,
) -> str:
    """Run OCR over a scanned PDF.

    ``mode``:

    * ``sidecar`` — write a plain-text file (``.ocr.txt``), one page per
      form-feed. Fast and easy to grep.
    * ``pdf`` — produce a searchable PDF: the original pages, untouched, with
      an invisible text layer placed over each recognised word. Page size,
      vectors, existing text, bookmarks, forms and metadata are kept.

    ``skip_text`` (default) leaves pages that already carry selectable text
    alone — they are not sent to Tesseract, and in sidecar mode their
    existing text is used. Pass ``skip_text=False`` (CLI ``--force``) to OCR
    every page.

    ``lang`` is a Tesseract language code (``eng``, ``spa``, ``eng+spa`` ...);
    the matching traineddata pack must be installed. ``dpi`` controls the
    render resolution fed to Tesseract — 300 is the recommended minimum.

    Returns the output path. Raises a friendly error, with install steps, if
    Tesseract is not available. Use :func:`ocr_with_stats` for page counts.
    """
    return ocr_with_stats(
        input_path,
        output,
        mode=mode,
        lang=lang,
        dpi=dpi,
        pages=pages,
        skip_text=skip_text,
        password=password,
    ).output
