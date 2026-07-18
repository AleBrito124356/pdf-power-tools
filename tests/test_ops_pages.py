"""Page operation round-trips."""

from __future__ import annotations

import os

from pypdf import PdfReader

from pdftools import ops_pages
from pdftools._util import parse_page_ranges


def _count(path: str) -> int:
    return len(PdfReader(path).pages)


def test_parse_page_ranges_basic():
    assert parse_page_ranges("1-3,7", 10) == [0, 1, 2, 6]
    assert parse_page_ranges("9-", 10) == [8, 9]
    assert parse_page_ranges("-3", 10) == [0, 1, 2]
    assert parse_page_ranges("N", 4) == [3]
    assert parse_page_ranges("3-1", 5) == [2, 1, 0]


def test_merge_two_files_with_bookmarks(text_pdf, text_pdf_b, out_dir):
    out = os.path.join(out_dir, "merged.pdf")
    ops_pages.merge([text_pdf, text_pdf_b], out)
    assert _count(out) == _count(text_pdf) + _count(text_pdf_b)
    outline = PdfReader(out).outline
    # One top-level bookmark per source file.
    assert len([o for o in outline if not isinstance(o, list)]) == 2


def test_merge_glob(fixtures_dir, text_pdf, text_pdf_b, out_dir):
    out = os.path.join(out_dir, "merged_glob.pdf")
    ops_pages.merge([os.path.join(fixtures_dir, "doc*.pdf")], out)
    assert _count(out) == _count(text_pdf) + _count(text_pdf_b)


def test_split_ranges(text_pdf, out_dir):
    parts = ops_pages.split(text_pdf, out_dir, ranges="1-2,3,4-5")
    assert len(parts) == 3
    assert [_count(p) for p in parts] == [2, 1, 2]


def test_split_every_n(text_pdf, out_dir):
    parts = ops_pages.split(text_pdf, out_dir, every_n=2)
    assert len(parts) == 3  # 5 pages -> 2 + 2 + 1
    assert [_count(p) for p in parts] == [2, 2, 1]


def test_split_by_bookmark(bookmarked_pdf, out_dir):
    parts = ops_pages.split(bookmarked_pdf, out_dir, by_bookmark=True)
    # Bookmarks at pages 0, 2, 4 of a 5-page doc -> 3 chunks.
    assert len(parts) == 3
    assert sum(_count(p) for p in parts) == 5


def test_rotate(text_pdf, out_dir):
    out = os.path.join(out_dir, "rot.pdf")
    ops_pages.rotate(text_pdf, out, pages="1", angle=90)
    reader = PdfReader(out)
    assert reader.pages[0].rotation % 360 == 90
    assert reader.pages[1].rotation % 360 == 0


def test_delete_pages(text_pdf, out_dir):
    out = os.path.join(out_dir, "del.pdf")
    ops_pages.delete_pages(text_pdf, out, pages="2,4")
    assert _count(out) == 3


def test_reorder(text_pdf, out_dir):
    out = os.path.join(out_dir, "reord.pdf")
    ops_pages.reorder(text_pdf, out, order="5,1,2")
    reader = PdfReader(out)
    assert _count(out) == 3
    assert "page 5" in (reader.pages[0].extract_text() or "")


def test_nup_2(text_pdf, out_dir):
    out = os.path.join(out_dir, "2up.pdf")
    ops_pages.n_up(text_pdf, out, n=2)
    # 5 pages, 2 per sheet -> 3 sheets, landscape (width > height).
    reader = PdfReader(out)
    assert _count(out) == 3
    first = reader.pages[0]
    assert float(first.mediabox.width) > float(first.mediabox.height)


def test_nup_4(text_pdf, out_dir):
    out = os.path.join(out_dir, "4up.pdf")
    ops_pages.n_up(text_pdf, out, n=4)
    assert _count(out) == 2  # 5 pages, 4 per sheet -> 2 sheets
