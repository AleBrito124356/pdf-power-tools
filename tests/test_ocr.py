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


# ---------------------------------------------------------------------------
# The whole pipeline, offline, with a fake engine (see conftest.FakeTesseract)
# ---------------------------------------------------------------------------

def _plumber_words(path, page_index=0):
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        page = pdf.pages[page_index]
        return page, {w["text"]: w for w in page.extract_words()}


def _expected_box(fraction_box, width, height):
    _text, left, top, w, h = fraction_box
    return left * width, top * height, (left + w) * width, (top + h) * height


def test_searchable_pdf_keeps_the_original_page(scanned_pdf, out_dir, fake_tesseract):
    from pypdf import PdfReader

    from conftest import FakeTesseract

    out = os.path.join(out_dir, "searchable.pdf")
    result = ocr_mod.ocr_with_stats(scanned_pdf, out, mode="pdf", dpi=150)
    assert result.pages_ocred == [1] and result.pages_skipped == [] and result.words == 3

    src, dst = PdfReader(scanned_pdf).pages[0], PdfReader(out).pages[0]
    assert [float(v) for v in dst.mediabox] == [float(v) for v in src.mediabox]
    text = dst.extract_text()
    for word, *_ in FakeTesseract.WORDS:
        assert word in text
    assert len(dst.images) == 1  # the scan itself is untouched, not re-rendered

    page, words = _plumber_words(out)
    assert len(page.images) == 1
    for box in FakeTesseract.WORDS:
        x0, top, x1, bottom = _expected_box(box, float(page.width), float(page.height))
        got = words[box[0]]
        assert abs(got["x0"] - x0) < 3 and abs(got["x1"] - x1) < 3, (box, got)
        assert abs(got["top"] - top) < 3 and abs(got["bottom"] - bottom) < 3, (box, got)

    # Tesseract is told the real render resolution (no 70-dpi fallback).
    assert fake_tesseract.calls[0][3] == "--dpi 150"


def test_invisible_layer_does_not_change_the_rendering(scanned_pdf, out_dir, fake_tesseract):
    import pypdfium2 as pdfium

    out = os.path.join(out_dir, "searchable.pdf")
    ocr_mod.ocr(scanned_pdf, out, mode="pdf", dpi=100)
    renders = []
    for path in (scanned_pdf, out):
        pdf = pdfium.PdfDocument(path)
        renders.append(pdf[0].render(scale=0.5).to_pil().convert("RGB").tobytes())
        pdf.close()
    assert renders[0] == renders[1]


def test_rotated_scan_gets_upright_words_in_place(scanned_pdf, out_dir, fake_tesseract):
    from pdftools import rotate

    from conftest import FakeTesseract

    rotated = rotate(scanned_pdf, os.path.join(out_dir, "rot.pdf"), angle=90)
    out = ocr_mod.ocr(rotated, os.path.join(out_dir, "rot_searchable.pdf"), mode="pdf", dpi=100)
    page, words = _plumber_words(out)
    assert page.rotation == 90
    assert float(page.width) > float(page.height)  # displayed landscape
    for box in FakeTesseract.WORDS:
        x0, top, x1, bottom = _expected_box(box, float(page.width), float(page.height))
        got = words[box[0]]
        assert got["upright"]
        assert abs(got["x0"] - x0) < 3 and abs(got["top"] - top) < 3, (box, got)
        assert abs(got["x1"] - x1) < 3 and abs(got["bottom"] - bottom) < 3, (box, got)


def test_pages_with_text_are_skipped(text_pdf_b, scanned_pdf, bookmarked_pdf, out_dir, fake_tesseract):
    from pypdf import PdfReader

    from pdftools import merge

    mixed = merge([text_pdf_b, scanned_pdf, bookmarked_pdf], os.path.join(out_dir, "mixed.pdf"))
    out = os.path.join(out_dir, "mixed_searchable.pdf")
    result = ocr_mod.ocr_with_stats(mixed, out, mode="pdf", dpi=100)
    assert result.pages_ocred == [4]
    assert result.pages_skipped == [1, 2, 3, 5, 6, 7, 8, 9]
    assert len(fake_tesseract.calls) == 1

    before, after = PdfReader(mixed), PdfReader(out)
    for idx in (0, 1, 2, 4):
        assert after.pages[idx].extract_text() == before.pages[idx].extract_text()
    assert "INVOICE" in after.pages[3].extract_text()
    # Bookmarks and metadata of the original survive the OCR pass.
    assert [o.title for o in after.outline if not isinstance(o, list)] == ["doc3", "scanned", "bookmarked"]


def test_force_ocrs_every_page(text_pdf_b, out_dir, fake_tesseract):
    result = ocr_mod.ocr_with_stats(
        text_pdf_b, os.path.join(out_dir, "forced.txt"), dpi=72, skip_text=False
    )
    assert result.pages_ocred == [1, 2, 3]
    assert len(fake_tesseract.calls) == 3


def test_sidecar_mixes_existing_text_and_ocr(text_pdf_b, scanned_pdf, out_dir, fake_tesseract):
    from pdftools import merge

    mixed = merge([text_pdf_b, scanned_pdf], os.path.join(out_dir, "mixed.pdf"))
    out = ocr_mod.ocr(mixed, os.path.join(out_dir, "mixed.txt"), mode="sidecar", dpi=100)
    with open(out, "rb") as fh:
        raw = fh.read()
    assert b"\r\r" not in raw  # pdfium's CRLF is normalised, never doubled
    chunks = raw.decode("utf-8").replace("\r\n", "\n").split("\f")
    assert len(chunks) == 4
    assert chunks[0].splitlines()[:2] == [
        "Memo - page 1",
        "This is body text for page 1 of 3. Searchable words: alpha bravo "
        "charlie delta echo foxtrot page1.",
    ]
    assert chunks[3] == "INVOICE TOTAL 42.00"
    assert [c[0] for c in fake_tesseract.calls] == ["string"]


def test_words_from_data_ignores_structural_rows():
    words = ocr_mod._words_from_data(
        {
            "text": ["", "Hello", " ", "world"],
            "left": [0, 10, 0, 60],
            "top": [0, 5, 0, 5],
            "width": [100, 40, 0, 40],
            "height": [50, 10, 0, 10],
            "conf": [-1, 95, -1, "88.5"],
        }
    )
    assert [w.text for w in words] == ["Hello", "world"]
    assert words[1].conf == 88.5
