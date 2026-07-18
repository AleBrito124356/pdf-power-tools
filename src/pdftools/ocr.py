"""Optical character recognition for scanned PDFs.

Wraps Tesseract (via pytesseract) with a rasterisation step from pypdfium2.
Tesseract is a separate native install, not a pip package, so this module is
careful to detect it and print actionable guidance when it is missing.
"""

from __future__ import annotations

import io
import os
from typing import List, Optional

from ._util import PdfToolsError, default_output, ensure_parent_dir, parse_page_ranges


_INSTALL_HELP = (
    "Tesseract OCR engine not found.\n"
    "  Windows : winget install UB-Mannheim.TesseractOCR   (or download the "
    "installer from github.com/UB-Mannheim/tesseract/wiki), then either add it "
    "to PATH or set TESSERACT_CMD to tesseract.exe.\n"
    "  macOS   : brew install tesseract\n"
    "  Linux   : sudo apt install tesseract-ocr\n"
    "Extra languages need their traineddata packs (e.g. tesseract-ocr-spa)."
)


def _configure_tesseract():
    import pytesseract

    override = os.environ.get("TESSERACT_CMD")
    if override:
        pytesseract.pytesseract.tesseract_cmd = override
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:  # EnvironmentError / TesseractNotFoundError
        raise PdfToolsError(_INSTALL_HELP) from exc
    return pytesseract


def _render_pages(input_path: str, dpi: int, pages: Optional[str]):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(input_path)
    try:
        total = len(pdf)
        indices = parse_page_ranges(pages, total) if pages else list(range(total))
        scale = dpi / 72.0
        images = []
        for idx in indices:
            bitmap = pdf[idx].render(scale=scale)
            images.append((idx, bitmap.to_pil()))
        return images
    finally:
        pdf.close()


def ocr(
    input_path: str,
    output: Optional[str] = None,
    *,
    mode: str = "sidecar",
    lang: str = "eng",
    dpi: int = 300,
    pages: Optional[str] = None,
) -> str:
    """Run OCR over a scanned PDF.

    ``mode``:

    * ``sidecar`` — write a plain-text file (``.ocr.txt``), one page per
      form-feed. Fast and easy to grep.
    * ``pdf`` — produce a searchable PDF that keeps the original page image with
      an invisible text layer behind it, so it looks identical but is now
      selectable and indexable.

    ``lang`` is a Tesseract language code (``eng``, ``spa``, ``eng+spa`` ...);
    the matching traineddata pack must be installed. ``dpi`` controls the
    render resolution fed to Tesseract — 300 is the recommended minimum.

    Returns the output path. Raises a friendly error, with install steps, if
    Tesseract is not available.
    """
    if mode not in ("sidecar", "pdf"):
        raise PdfToolsError("mode must be 'sidecar' or 'pdf'.")
    pytesseract = _configure_tesseract()
    images = _render_pages(input_path, dpi, pages)
    if not images:
        raise PdfToolsError("No pages to OCR.")

    if mode == "sidecar":
        output = output or default_output(input_path, "ocr", ext=".txt")
        chunks: List[str] = []
        for _idx, image in images:
            chunks.append(pytesseract.image_to_string(image, lang=lang))
        ensure_parent_dir(output)
        with open(output, "w", encoding="utf-8") as fh:
            fh.write("\f".join(chunks))
        return output

    # mode == "pdf" → searchable PDF with an invisible text layer.
    from pypdf import PdfReader, PdfWriter

    output = output or default_output(input_path, "searchable")
    writer = PdfWriter()
    for _idx, image in images:
        page_pdf = pytesseract.image_to_pdf_or_hocr(image, extension="pdf", lang=lang)
        reader = PdfReader(io.BytesIO(page_pdf))
        writer.add_page(reader.pages[0])
    ensure_parent_dir(output)
    with open(output, "wb") as fh:
        writer.write(fh)
    return output
