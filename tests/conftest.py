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


@pytest.fixture
def out_dir(tmp_path) -> str:
    d = os.path.join(str(tmp_path), "out")
    os.makedirs(d, exist_ok=True)
    return d
