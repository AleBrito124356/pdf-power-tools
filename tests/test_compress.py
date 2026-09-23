"""Compression tests (the paths that do not need external tools)."""

from __future__ import annotations

import importlib
import os

import pytest
from pypdf import PdfReader

from pdftools._util import PdfToolsError, PdfToolsWarning

# The package exports a `compress` function, which shadows the
# `pdftools.compress` submodule attribute; import_module fetches the real
# module so we can monkeypatch find_ghostscript on it.
compress_mod = importlib.import_module("pdftools.compress")


def _count(path: str) -> int:
    return len(PdfReader(path).pages)


def test_compress_lossless_keeps_pages(image_pdf, out_dir):
    out = os.path.join(out_dir, "loss.pdf")
    compress_mod.compress(image_pdf, out, mode="lossless")
    assert os.path.isfile(out)
    assert _count(out) == _count(image_pdf)


def test_compress_rasterize_keeps_pages(image_pdf, out_dir):
    out = os.path.join(out_dir, "raster.pdf")
    with pytest.warns(PdfToolsWarning, match="selectable text"):
        compress_mod.compress(image_pdf, out, mode="rasterize", dpi=72, jpeg_quality=40)
    assert _count(out) == _count(image_pdf)


def test_compress_unknown_mode(image_pdf, out_dir):
    with pytest.raises(PdfToolsError):
        compress_mod.compress(image_pdf, os.path.join(out_dir, "x.pdf"), mode="magic")


def test_ghostscript_mode_without_binary(image_pdf, out_dir, monkeypatch):
    # Force "not installed" and confirm a friendly error, not a crash.
    monkeypatch.setattr(compress_mod, "find_ghostscript", lambda: None)
    with pytest.raises(PdfToolsError):
        compress_mod.compress(image_pdf, os.path.join(out_dir, "x.pdf"), mode="ghostscript")


def test_probe(text_pdf):
    assert compress_mod.probe(text_pdf) == 5


# ---------------------------------------------------------------------------
# images mode, auto, and the never-larger guard
# ---------------------------------------------------------------------------

def _image_dims(path):
    reader = PdfReader(path)
    dims = []
    for page in reader.pages:
        for img in page.images:
            dims.append(img.image.size)
    return dims


def test_images_mode_shrinks_photos_and_keeps_text(photo_pdf, out_dir):
    out = os.path.join(out_dir, "photos_small.pdf")
    result = compress_mod.compress_with_stats(photo_pdf, out, mode="images")
    before, after = os.path.getsize(photo_pdf), os.path.getsize(out)
    assert after <= before * 0.5, (before, after)
    assert result.mode == "images"
    assert result.images_seen == 2 and result.images_recompressed == 2
    assert result.bytes_after == after and not result.kept_original
    reader = PdfReader(out)
    assert len(reader.pages) == 2
    assert "keep this text selectable" in reader.pages[0].extract_text()
    assert "Site survey photo 2" in reader.pages[1].extract_text()


def test_images_mode_downsamples_to_max_dpi(photo_pdf, out_dir):
    out = os.path.join(out_dir, "photos_100dpi.pdf")
    compress_mod.compress(photo_pdf, out, mode="images", max_dpi=100)
    # 1600 px shown 6.5 in wide -> ~650 px at 100 dpi.
    for width, height in _image_dims(out):
        assert 600 <= width <= 700, width
        assert abs(width / height - 4 / 3) < 0.02


def test_images_mode_keeps_graphics_lossless(tmp_path, out_dir):
    """Flat-colour artwork is not JPEG'd: pixels survive exactly."""
    import io as _io

    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    art = Image.new("RGB", (400, 300), (255, 255, 255))
    for i, colour in enumerate([(200, 0, 0), (0, 150, 0), (0, 0, 200)]):
        art.paste(colour, (20 + i * 120, 40, 120 + i * 120, 260))
    buf = _io.BytesIO()
    art.save(buf, format="PNG")
    buf.seek(0)
    src = str(tmp_path / "chart.pdf")
    c = canvas.Canvas(src, pagesize=(612, 792))
    c.drawImage(ImageReader(buf), 72, 400, 400, 300)  # 72 dpi: no downsampling
    c.showPage()
    c.save()

    out = os.path.join(out_dir, "chart_small.pdf")
    compress_mod.compress(src, out, mode="images", allow_larger=True)
    images = PdfReader(out).pages[0].images
    assert len(images) == 1
    assert images[0].image.convert("RGB").tobytes() == art.tobytes()


def test_auto_without_ghostscript_uses_images_mode(photo_pdf, out_dir, monkeypatch):
    monkeypatch.setattr(compress_mod, "find_ghostscript", lambda: None)
    out = os.path.join(out_dir, "auto.pdf")
    result = compress_mod.compress_with_stats(photo_pdf, out)
    assert result.mode == "images"
    assert os.path.getsize(out) < os.path.getsize(photo_pdf) * 0.5


def test_never_larger_guard_keeps_the_original(text_pdf, out_dir):
    out = os.path.join(out_dir, "raster.pdf")
    with pytest.warns(PdfToolsWarning, match="selectable"):
        result = compress_mod.compress_with_stats(text_pdf, out, mode="rasterize", dpi=100)
    assert result.kept_original
    assert os.path.getsize(out) <= os.path.getsize(text_pdf)
    with open(out, "rb") as a, open(text_pdf, "rb") as b:
        assert a.read() == b.read()
    assert "Report - page 1" in PdfReader(out).pages[0].extract_text()


def test_allow_larger_restores_old_behaviour(text_pdf, out_dir):
    out = os.path.join(out_dir, "raster_big.pdf")
    with pytest.warns(PdfToolsWarning):
        result = compress_mod.compress_with_stats(
            text_pdf, out, mode="rasterize", dpi=100, allow_larger=True
        )
    assert not result.kept_original
    assert os.path.getsize(out) > os.path.getsize(text_pdf)
    assert (PdfReader(out).pages[0].extract_text() or "").strip() == ""


def test_compress_encrypted_input(encrypted_pdf, out_dir):
    out = os.path.join(out_dir, "enc_small.pdf")
    compress_mod.compress(encrypted_pdf, out, mode="lossless", password="open123", allow_larger=True)
    reader = PdfReader(out)
    assert not reader.is_encrypted
    assert "Memo - page 1" in reader.pages[0].extract_text()
    with pytest.raises(PdfToolsError, match="--password"):
        compress_mod.compress(encrypted_pdf, os.path.join(out_dir, "x.pdf"), mode="lossless")


def test_bad_jpeg_quality_and_max_dpi(photo_pdf, out_dir):
    with pytest.raises(PdfToolsError):
        compress_mod.compress(photo_pdf, os.path.join(out_dir, "x.pdf"), mode="images", jpeg_quality=0)
    with pytest.raises(PdfToolsError):
        compress_mod.compress(photo_pdf, os.path.join(out_dir, "x.pdf"), mode="images", max_dpi=1)


def test_images_mode_shrinks_a_300dpi_greyscale_scan(text_pdf_b, tmp_path, out_dir):
    """A typical bloated scan: 300 dpi greyscale pages stored losslessly."""
    import io as _io

    import pypdfium2 as pdfium
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    src = str(tmp_path / "scan300.pdf")
    doc = pdfium.PdfDocument(text_pdf_b)
    c = canvas.Canvas(src, pagesize=(612, 792))
    for i in range(2):
        page = doc[i].render(scale=300 / 72).to_pil().convert("L")
        page = Image.blend(page, Image.effect_noise(page.size, 12), 0.08)  # scanner grain
        buf = _io.BytesIO()
        page.save(buf, format="PNG")
        buf.seek(0)
        c.drawImage(ImageReader(buf), 0, 0, 612, 792)
        c.showPage()
    c.save()
    doc.close()

    out = os.path.join(out_dir, "scan_small.pdf")
    result = compress_mod.compress_with_stats(src, out, mode="images")
    assert result.images_recompressed == 2
    assert result.ratio < 0.2, result.ratio
    images = PdfReader(out).pages[0].images
    assert images[0].image.size == (1275, 1650)  # 300 -> 150 dpi
    assert images[0].image.mode == "L"  # stays greyscale
