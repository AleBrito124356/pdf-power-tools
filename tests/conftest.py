"""Test fixtures.

Every PDF the tests need is generated here with reportlab, so the repository
ships zero binary files. Fixtures are session-scoped and built once into a
temporary directory.
"""

from __future__ import annotations

import io
import os
import sys

import pytest

# Make the src-layout package importable without an editable install.
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _make_text_pdf(path: str, pages: int, title: str) -> str:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(path, pagesize=letter)
    c.setTitle(title)
    c.setAuthor("pdf-power-tools tests")
    c.setSubject("fixture")
    c.setKeywords(["fixture", "test", "pdf"])
    for i in range(pages):
        c.setFont("Helvetica-Bold", 24)
        c.drawString(72, 720, f"{title} - page {i + 1}")
        c.setFont("Helvetica", 12)
        c.drawString(
            72,
            680,
            f"This is body text for page {i + 1} of {pages}. Searchable words: "
            f"alpha bravo charlie delta echo foxtrot page{i + 1}.",
        )
        c.showPage()
    c.save()
    return path


def _make_image_pdf(path: str) -> str:
    from PIL import Image
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    # A generated gradient so extract-images has something real to pull out.
    img = Image.new("RGB", (240, 160))
    px = img.load()
    for y in range(160):
        for x in range(240):
            px[x, y] = (x % 256, y % 256, (x + y) % 256)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica", 14)
    c.drawString(72, 720, "Document with an embedded image")
    c.drawImage(ImageReader(buf), 72, 520, 240, 160)
    c.showPage()
    c.save()
    return path


def _make_form_pdf(path: str) -> str:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(72, 730, "Membership Application")
    c.setFont("Helvetica", 12)
    form = c.acroForm

    c.drawString(72, 690, "Full name:")
    form.textfield(
        name="full_name", tooltip="Full name",
        x=170, y=684, width=280, height=18,
        borderStyle="inset", forceBorder=True,
    )
    c.drawString(72, 655, "Email:")
    form.textfield(
        name="email", tooltip="Email address",
        x=170, y=649, width=280, height=18,
        borderStyle="inset", forceBorder=True,
    )
    c.drawString(72, 620, "Subscribe:")
    form.checkbox(
        name="subscribe", tooltip="Subscribe to newsletter",
        x=170, y=616, buttonStyle="check",
        borderStyle="solid", forceBorder=True,
    )
    c.drawString(72, 585, "Country:")
    form.choice(
        name="country", tooltip="Country",
        value="Panama",
        options=["Panama", "Mexico", "Spain", "Colombia"],
        x=170, y=579, width=160, height=20,
        borderStyle="inset", forceBorder=True,
    )
    c.save()
    return path


def _make_table_pdf(path: str) -> str:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

    data = [
        ["Date", "Description", "Amount"],
        ["2026-01-05", "Coffee shop", "4.50"],
        ["2026-01-06", "Bookstore", "23.10"],
        ["2026-01-09", "Groceries", "58.75"],
        ["2026-01-12", "Transit card", "20.00"],
    ]
    doc = SimpleDocTemplate(path, pagesize=letter)
    table = Table(data, colWidths=[100, 200, 80])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.75, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (2, 0), (2, -1), "RIGHT"),
            ]
        )
    )
    doc.build([table])
    return path


def _make_bookmarked_pdf(src_path: str, path: str) -> str:
    from pypdf import PdfWriter

    writer = PdfWriter(clone_from=src_path)
    total = len(writer.pages)
    for i in range(0, total, 2):
        writer.add_outline_item(f"Chapter {i // 2 + 1}", i)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def _make_encrypted_pdf(src_path: str, path: str, user_pw: str, owner_pw: str) -> str:
    from pypdf import PdfWriter

    writer = PdfWriter(clone_from=src_path)
    writer.encrypt(user_password=user_pw, owner_password=owner_pw, algorithm="AES-256")
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def _make_photo_pdf(path: str, pages: int = 2, size=(1600, 1200)) -> str:
    """Pages with a text line and a big, losslessly embedded noisy 'photo'."""
    from PIL import Image, ImageFilter
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(path, pagesize=letter)
    for p in range(pages):
        noise = Image.effect_noise(size, 60 + 10 * p).filter(ImageFilter.GaussianBlur(1.5))
        photo = Image.merge("RGB", (noise, noise.rotate(90, expand=False), noise.transpose(Image.FLIP_LEFT_RIGHT)))
        buf = io.BytesIO()
        photo.save(buf, format="PNG")
        buf.seek(0)
        c.setFont("Helvetica", 14)
        c.drawString(72, 740, f"Site survey photo {p + 1} - keep this text selectable")
        # 1600 px shown 468 pt (6.5 in) wide = ~246 dpi.
        c.drawImage(ImageReader(buf), 72, 300, 468, 351)
        c.showPage()
    c.save()
    return path


def _make_scanned_pdf(path: str, pagesize=(612, 792)) -> str:
    """An image-only page, like a scanner produces: no text layer at all."""
    from PIL import Image, ImageDraw
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    w, h = int(pagesize[0] * 100 / 72), int(pagesize[1] * 100 / 72)
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((w // 10, h // 10, w // 2, h // 6), fill=(90, 90, 90))
    draw.rectangle((w // 10, h // 2, 3 * w // 4, h // 2 + 30), fill=(40, 40, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    c = canvas.Canvas(path, pagesize=pagesize)
    c.drawImage(ImageReader(buf), 0, 0, pagesize[0], pagesize[1])
    c.showPage()
    c.save()
    return path


def _make_radio_form_pdf(path: str) -> str:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(path, pagesize=letter)
    c.drawString(72, 730, "T-shirt size:")
    for i, size in enumerate(("S", "M", "L")):
        c.acroForm.radio(
            name="size", value=size, selected=False, x=170 + i * 40, y=725,
            buttonStyle="circle", borderStyle="solid", shape="circle", forceBorder=True,
        )
    c.drawString(72, 690, "Quantity:")
    c.acroForm.textfield(name="qty", x=170, y=684, width=80, height=18, forceBorder=True)
    c.save()
    return path


def _make_xmp_pdf(src_path: str, path: str) -> str:
    """A copy of ``src_path`` carrying an XMP packet that names its author."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    writer = PdfWriter(clone_from=src_path)
    xmp = DecodedStreamObject()
    xmp.set_data(
        b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
        b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b'<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/">'
        b"<dc:creator>Secret Xmp Author</dc:creator></rdf:Description>"
        b'</rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
    )
    xmp[NameObject("/Type")] = NameObject("/Metadata")
    xmp[NameObject("/Subtype")] = NameObject("/XML")
    writer._root_object[NameObject("/Metadata")] = writer._add_object(xmp)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def _make_blank_pdf(path: str, pagesize=(612, 792)) -> str:
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(path, pagesize=pagesize)
    c.showPage()
    c.save()
    return path


class FakeTesseract:
    """A deterministic stand-in for pytesseract (no Tesseract binary needed).

    ``image_to_data`` "recognises" three words at fixed fractions of whatever
    image it is given, in render pixels, exactly like the real engine reports
    them; the tests then check where they land on the PDF page.
    """

    WORDS = [
        # text, left, top, width, height (fractions of the rendered image)
        ("INVOICE", 0.10, 0.10, 0.25, 0.04),
        ("TOTAL", 0.10, 0.50, 0.15, 0.03),
        ("42.00", 0.60, 0.50, 0.12, 0.03),
    ]

    class Output:
        DICT = "dict"

    def __init__(self):
        self.calls = []

    def image_to_data(self, image, lang=None, config="", output_type=None):
        self.calls.append(("data", image.size, lang, config))
        width, height = image.size
        data = {k: [] for k in ("level", "text", "left", "top", "width", "height", "conf")}
        # Tesseract also reports structural rows (page, block, line) with conf -1.
        rows = [(1, "", 0.0, 0.0, 1.0, 1.0, -1)]
        rows += [(5, t, l, tp, w, h, 91) for t, l, tp, w, h in self.WORDS]
        for level, text, l, tp, w, h, conf in rows:
            data["level"].append(level)
            data["text"].append(text)
            data["left"].append(int(round(l * width)))
            data["top"].append(int(round(tp * height)))
            data["width"].append(int(round(w * width)))
            data["height"].append(int(round(h * height)))
            data["conf"].append(conf)
        return data

    def image_to_string(self, image, lang=None, config=""):
        self.calls.append(("string", image.size, lang, config))
        return " ".join(w[0] for w in self.WORDS)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory) -> str:
    return str(tmp_path_factory.mktemp("pdf_fixtures"))


@pytest.fixture(scope="session")
def text_pdf(fixtures_dir) -> str:
    return _make_text_pdf(os.path.join(fixtures_dir, "doc5.pdf"), pages=5, title="Report")


@pytest.fixture(scope="session")
def text_pdf_b(fixtures_dir) -> str:
    return _make_text_pdf(os.path.join(fixtures_dir, "doc3.pdf"), pages=3, title="Memo")


@pytest.fixture(scope="session")
def image_pdf(fixtures_dir) -> str:
    return _make_image_pdf(os.path.join(fixtures_dir, "with_image.pdf"))


@pytest.fixture(scope="session")
def form_pdf(fixtures_dir) -> str:
    return _make_form_pdf(os.path.join(fixtures_dir, "form.pdf"))


@pytest.fixture(scope="session")
def table_pdf(fixtures_dir) -> str:
    return _make_table_pdf(os.path.join(fixtures_dir, "table.pdf"))


@pytest.fixture(scope="session")
def bookmarked_pdf(fixtures_dir, text_pdf) -> str:
    return _make_bookmarked_pdf(text_pdf, os.path.join(fixtures_dir, "bookmarked.pdf"))


@pytest.fixture(scope="session")
def encrypted_pdf(fixtures_dir, text_pdf_b) -> str:
    return _make_encrypted_pdf(
        text_pdf_b, os.path.join(fixtures_dir, "encrypted.pdf"), "open123", "owner123"
    )


@pytest.fixture(scope="session")
def photo_pdf(fixtures_dir) -> str:
    return _make_photo_pdf(os.path.join(fixtures_dir, "photos.pdf"))


@pytest.fixture(scope="session")
def scanned_pdf(fixtures_dir) -> str:
    return _make_scanned_pdf(os.path.join(fixtures_dir, "scanned.pdf"))


@pytest.fixture(scope="session")
def radio_form_pdf(fixtures_dir) -> str:
    return _make_radio_form_pdf(os.path.join(fixtures_dir, "radio_form.pdf"))


@pytest.fixture(scope="session")
def xmp_pdf(fixtures_dir, text_pdf) -> str:
    return _make_xmp_pdf(text_pdf, os.path.join(fixtures_dir, "with_xmp.pdf"))


@pytest.fixture(scope="session")
def blank_pdf(fixtures_dir) -> str:
    return _make_blank_pdf(os.path.join(fixtures_dir, "blank.pdf"))


@pytest.fixture(scope="session")
def not_a_pdf(fixtures_dir) -> str:
    path = os.path.join(fixtures_dir, "notapdf.pdf")
    with open(path, "wb") as fh:
        fh.write(b"hello!")
    return path


@pytest.fixture
def fake_tesseract(monkeypatch):
    """Patch the OCR engine seam with :class:`FakeTesseract`."""
    import importlib

    ocr_mod = importlib.import_module("pdftools.ocr")
    fake = FakeTesseract()
    monkeypatch.setattr(ocr_mod, "_configure_tesseract", lambda: fake)
    return fake


@pytest.fixture
def work_dir(tmp_path, text_pdf, text_pdf_b, not_a_pdf) -> str:
    """A private folder of inputs for batch tests (keeps fixtures_dir clean)."""
    import shutil

    d = os.path.join(str(tmp_path), "work")
    os.makedirs(d)
    shutil.copy(text_pdf, os.path.join(d, "doc5.pdf"))
    shutil.copy(text_pdf_b, os.path.join(d, "doc3.pdf"))
    return d


@pytest.fixture
def out_dir(tmp_path) -> str:
    d = os.path.join(str(tmp_path), "out")
    os.makedirs(d, exist_ok=True)
    return d
