"""OCR tests.

Tesseract is a native install, so these tests skip cleanly when it is absent
rather than failing. The 'missing engine' guidance path is always tested.
"""

from __future__ import annotations

import importlib
import os

import pytest

from pdftools._util import PdfToolsError

# The package exports an `ocr` function, which shadows the `pdftools.ocr`
# submodule attribute; import_module fetches the real module for monkeypatching.
ocr_mod = importlib.import_module("pdftools.ocr")


def _tesseract_available() -> bool:
    try:
        ocr_mod._configure_tesseract()
        return True
    except PdfToolsError:
        return False


def test_missing_tesseract_message(image_pdf, out_dir, monkeypatch):
    def boom():
        raise PdfToolsError(ocr_mod._INSTALL_HELP)

    monkeypatch.setattr(ocr_mod, "_configure_tesseract", boom)
    with pytest.raises(PdfToolsError) as excinfo:
        ocr_mod.ocr(image_pdf, os.path.join(out_dir, "x.txt"))
    assert "Tesseract" in str(excinfo.value)


def test_ocr_bad_mode(image_pdf, out_dir):
    with pytest.raises(PdfToolsError):
        ocr_mod.ocr(image_pdf, os.path.join(out_dir, "x.txt"), mode="wrong")


@pytest.mark.skipif(not _tesseract_available(), reason="Tesseract not installed")
def test_ocr_sidecar(out_dir):
    # Build a page that is an image of text so OCR has something to read.
    from PIL import Image, ImageDraw
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    img = Image.new("RGB", (600, 200), "white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 80), "HELLO OCR WORLD", fill="black")
    img_path = os.path.join(out_dir, "scan.png")
    img.save(img_path)

    pdf_path = os.path.join(out_dir, "scan.pdf")
    c = canvas.Canvas(pdf_path, pagesize=letter)
    c.drawImage(ImageReader(img_path), 72, 500, 400, 133)
    c.showPage()
    c.save()

    out = ocr_mod.ocr(pdf_path, os.path.join(out_dir, "scan.ocr.txt"), dpi=200)
    with open(out, encoding="utf-8") as fh:
        text = fh.read().upper()
    assert "OCR" in text
