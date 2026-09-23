"""Watermark, page-number and image conversion tests."""

from __future__ import annotations

import os

import pytest
from PIL import Image, ImageOps
from pypdf import PdfReader

from pdftools import ops_pages, ops_visual
from pdftools._util import PdfToolsError


def _render(path, page=0, scale=0.5):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(path)
    try:
        return pdf[page].render(scale=scale).to_pil().convert("RGB")
    finally:
        pdf.close()


def _ink(image, dark=128):
    """(centre x, centre y, width, height) of the dark pixels, as fractions."""
    gray = image.convert("L")
    bbox = gray.point(lambda v: 255 if v < dark else 0).getbbox()
    assert bbox, "nothing was drawn"
    w, h = image.size
    return (
        (bbox[0] + bbox[2]) / 2 / w,
        (bbox[1] + bbox[3]) / 2 / h,
        bbox[2] - bbox[0],
        bbox[3] - bbox[1],
    )


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


def test_pdf_to_images_encrypted_input(encrypted_pdf, out_dir):
    with pytest.raises(PdfToolsError, match="--password"):
        ops_visual.pdf_to_images(encrypted_pdf, out_dir, dpi=36)
    paths = ops_visual.pdf_to_images(encrypted_pdf, out_dir, dpi=36, pages="1", password="open123")
    assert len(paths) == 1
    with Image.open(paths[0]) as im:
        assert im.info.get("dpi") and round(im.info["dpi"][0]) == 36


# ---------------------------------------------------------------------------
# Overlays follow the page as displayed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_page_numbers_land_bottom_centre_on_rotated_pages(blank_pdf, out_dir, angle):
    src = blank_pdf
    if angle:
        src = ops_pages.rotate(blank_pdf, os.path.join(out_dir, f"rot{angle}.pdf"), angle=angle)
    out = ops_visual.page_numbers(src, os.path.join(out_dir, f"num{angle}.pdf"),
                                  fmt="PAGE {n}", font_size=24)
    cx, cy, w, h = _ink(_render(out))
    assert abs(cx - 0.50) < 0.03, cx
    assert 0.90 < cy < 0.98, cy
    assert w > h  # horizontal text, not turned sideways


def test_page_numbers_respect_an_offset_crop_box(blank_pdf, out_dir):
    from pypdf import PdfWriter
    from pypdf.generic import RectangleObject

    writer = PdfWriter(clone_from=blank_pdf)
    writer.pages[0].cropbox = RectangleObject([100, 200, 400, 600])
    cropped = os.path.join(out_dir, "cropped.pdf")
    with open(cropped, "wb") as fh:
        writer.write(fh)
    out = ops_visual.page_numbers(cropped, os.path.join(out_dir, "c_num.pdf"),
                                  fmt="PAGE {n}", font_size=20, position="top-right")
    image = _render(out, scale=1.0)
    assert image.size == (300, 400)  # pdfium shows only the crop box
    cx, cy, _w, _h = _ink(image)
    assert cx > 0.6 and cy < 0.2  # visible top-right corner


def test_image_watermark_bottom_right_on_rotated_page(blank_pdf, out_dir):
    logo = os.path.join(out_dir, "logo.png")
    Image.new("RGB", (100, 100), (0, 0, 0)).save(logo)
    rotated = ops_pages.rotate(blank_pdf, os.path.join(out_dir, "rot.pdf"), angle=90)
    out = ops_visual.watermark(rotated, os.path.join(out_dir, "wm.pdf"), image=logo,
                               position="bottom-right", opacity=1.0, scale=0.2)
    cx, cy, w, h = _ink(_render(out))
    assert cx > 0.75 and cy > 0.65, (cx, cy)
    assert abs(w - h) <= 2  # the square logo is not stretched


def test_bad_watermark_and_number_options(text_pdf, out_dir):
    with pytest.raises(PdfToolsError, match="placeholder"):
        ops_visual.page_numbers(text_pdf, os.path.join(out_dir, "x.pdf"), fmt="{page}")
    with pytest.raises(PdfToolsError):
        ops_visual.page_numbers(text_pdf, os.path.join(out_dir, "x.pdf"), fmt="{n")
    with pytest.raises(PdfToolsError):
        ops_visual.watermark(text_pdf, os.path.join(out_dir, "x.pdf"), text="X", color="nocolour")
    with pytest.raises(PdfToolsError, match="not found"):
        ops_visual.watermark(text_pdf, os.path.join(out_dir, "x.pdf"), image="missing.png")
    assert not os.path.exists(os.path.join(out_dir, "x.pdf"))


# ---------------------------------------------------------------------------
# img2pdf: EXIF orientation and DPI
# ---------------------------------------------------------------------------

def _quadrant_image():
    base = Image.new("RGB", (160, 100))
    colours = [(230, 30, 30), (30, 200, 30), (30, 30, 230), (240, 220, 20)]
    for i, colour in enumerate(colours):
        x, y = (i % 2) * 80, (i // 2) * 50
        base.paste(colour, (x, y, x + 80, y + 50))
    return base


def _quadrants(image):
    w, h = image.size
    return [image.getpixel((int(w * fx), int(h * fy)))
            for fx, fy in ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75))]


@pytest.mark.parametrize("orientation", range(1, 9))
@pytest.mark.parametrize("fmt", ["jpg", "png"])
def test_img2pdf_honours_every_exif_orientation(out_dir, orientation, fmt):
    exif = Image.Exif()
    exif[0x0112] = orientation
    path = os.path.join(out_dir, f"o{orientation}.{fmt}")
    extra = {"quality": 95} if fmt == "jpg" else {}
    _quadrant_image().save(path, exif=exif.tobytes(), **extra)
    with Image.open(path) as im:
        expected = ImageOps.exif_transpose(im).convert("RGB")

    out = ops_visual.images_to_pdf([path], os.path.join(out_dir, f"o{orientation}.pdf"))
    got = _render(out, scale=1.0)
    assert got.size == expected.size
    for a, b in zip(_quadrants(got), _quadrants(expected)):
        assert all(abs(x - y) < 40 for x, y in zip(a, b)), (orientation, a, b)


def test_img2pdf_phone_photo_is_upright(out_dir):
    path = os.path.join(out_dir, "phone.jpg")
    exif = Image.Exif()
    exif[0x0112] = 6  # stored landscape, displayed portrait
    Image.new("RGB", (400, 300), (200, 50, 50)).save(path, exif=exif.tobytes())
    out = ops_visual.images_to_pdf([path], os.path.join(out_dir, "phone.pdf"))
    box = PdfReader(out).pages[0].mediabox
    assert (float(box.width), float(box.height)) == (300.0, 400.0)


def test_img2pdf_auto_page_uses_image_dpi(out_dir):
    path = os.path.join(out_dir, "camera.jpg")
    Image.new("RGB", (4032, 3024), (20, 120, 200)).save(path, dpi=(300, 300), quality=70)
    out = ops_visual.images_to_pdf([path], os.path.join(out_dir, "camera.pdf"))
    box = PdfReader(out).pages[0].mediabox
    assert abs(float(box.width) / 72 - 13.44) < 0.01
    assert abs(float(box.height) / 72 - 10.08) < 0.01
    # The JPEG is embedded as-is: no re-encode and no ASCII85 inflation.
    assert os.path.getsize(out) < os.path.getsize(path) * 1.05
    reader_image = PdfReader(out).pages[0].images[0]
    assert reader_image.image.size == (4032, 3024)


def test_img2pdf_margin(out_dir):
    path = os.path.join(out_dir, "sq.png")
    Image.new("RGB", (100, 100), (0, 0, 0)).save(path)
    auto = ops_visual.images_to_pdf([path], os.path.join(out_dir, "m_auto.pdf"), margin=10)
    box = PdfReader(auto).pages[0].mediabox
    assert (float(box.width), float(box.height)) == (120.0, 120.0)
    a4 = ops_visual.images_to_pdf([path], os.path.join(out_dir, "m_a4.pdf"),
                                  page_size="a4", margin=72)
    cx, cy, w, h = _ink(_render(a4, scale=1.0))
    assert abs(cx - 0.5) < 0.01 and abs(cy - 0.5) < 0.01
    assert abs(w - (595.28 - 144)) < 3  # contained inside the 1-inch margins
    with pytest.raises(PdfToolsError):
        ops_visual.images_to_pdf([path], os.path.join(out_dir, "x.pdf"), page_size="a4", margin=400)
