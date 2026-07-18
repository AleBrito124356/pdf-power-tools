"""Content extraction and metadata tests."""

from __future__ import annotations

import os

from pdftools import ops_content


def test_extract_text_joined(text_pdf):
    text = ops_content.extract_text(text_pdf)
    assert "Report - page 1" in text
    assert "Report - page 5" in text


def test_extract_text_list(text_pdf):
    pages = ops_content.extract_text(text_pdf, joined=False)
    assert isinstance(pages, list)
    assert len(pages) == 5
    assert "page 3" in pages[2]


def test_extract_text_to_file(text_pdf, out_dir):
    out = os.path.join(out_dir, "text.txt")
    ops_content.extract_text(text_pdf, out, pages="1-2")
    with open(out, encoding="utf-8") as fh:
        content = fh.read()
    assert "page 1" in content
    assert "\f" in content  # form-feed page separator


def test_extract_tables_csv(table_pdf, out_dir):
    records = ops_content.extract_tables(table_pdf, out_dir, fmt="csv")
    assert records
    flat = [str(cell) for rec in records for row in rec["rows"] for cell in row]
    assert any("Coffee shop" in cell for cell in flat)
    assert os.path.isfile(records[0]["path"])


def test_extract_tables_markdown(table_pdf, out_dir):
    records = ops_content.extract_tables(table_pdf, out_dir, fmt="markdown")
    assert records
    with open(records[0]["path"], encoding="utf-8") as fh:
        md = fh.read()
    assert "| Date" in md
    assert "---" in md


def test_extract_images(image_pdf, out_dir):
    paths = ops_content.extract_images(image_pdf, out_dir)
    assert len(paths) >= 1
    assert all(os.path.getsize(p) > 0 for p in paths)


def test_get_and_set_metadata(text_pdf, out_dir):
    meta = ops_content.get_metadata(text_pdf)
    assert meta["title"] == "Report"

    out = os.path.join(out_dir, "meta.pdf")
    ops_content.set_metadata(text_pdf, out, title="New Title", author="Grace Hopper")
    updated = ops_content.get_metadata(out)
    assert updated["title"] == "New Title"
    assert updated["author"] == "Grace Hopper"


def test_page_count_and_info(text_pdf):
    assert ops_content.page_count(text_pdf) == 5
    data = ops_content.info(text_pdf)
    assert data["page_count"] == 5
    assert data["encrypted"] is False
    assert len(data["pages"]) == 5
