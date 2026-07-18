"""Watermark, page-number and image conversion tests."""

from __future__ import annotations

import os

from PIL import Image
from pypdf import PdfReader

from pdftools import ops_visual


def _count(path: str) -> int:
    return len(PdfReader(path).pages)


def test_watermark_text(text_pdf, out_dir):
    out = os.path.join(out_dir, "wm.pdf")
    ops_visual.watermark(text_pdf, out, text="CONFIDENTIAL", tiled=True)
    assert _count(out) == _count(text_pdf)
    assert os.path.getsize(out) > 0


def test_watermark_image(text_pdf, image_pdf, out_dir):
    logo = os.path.join(out_dir, "logo.png")
    Image.new("RGBA", (120, 120), (200, 30, 30, 255)).save(logo)
    out = os.path.join(out_dir, "wm_img.pdf")
    ops_visual.watermark(text_pdf, out, image=logo, position="bottom-right", opacity=0.5)
    assert _count(out) == _count(text_pdf)


def test_page_numbers(text_pdf, out_dir):
    out = os.path.join(out_dir, "num.pdf")
    ops_visual.page_numbers(text_pdf, out, fmt="Page {n} of {total}", skip_first=True)
    reader = PdfReader(out)
    assert _count(out) == 5
    assert "Page 2 of 5" in (reader.pages[1].extract_text() or "")


def test_images_to_pdf(out_dir):
    paths = []
    for i, color in enumerate([(255, 0, 0), (0, 128, 0), (0, 0, 255)]):
        p = os.path.join(out_dir, f"img{i}.png")
        Image.new("RGB", (200, 300), color).save(p)
        paths.append(p)
    out = os.path.join(out_dir, "images.pdf")
    ops_visual.images_to_pdf(paths, out, page_size="a4", fit="contain")
    assert _count(out) == 3


def test_pdf_to_images(text_pdf, out_dir):
    paths = ops_visual.pdf_to_images(text_pdf, out_dir, dpi=72, pages="1-2")
    assert len(paths) == 2
    for p in paths:
        with Image.open(p) as im:
            assert im.width > 0 and im.height > 0
