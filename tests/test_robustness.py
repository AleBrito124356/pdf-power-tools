"""The I/O layer: locked, damaged and odd inputs, and structure preservation.

Every command should either do its job without silently dropping forms and
bookmarks, or fail with one clean ``error:`` line (exit 2) — never a
backend traceback.
"""

from __future__ import annotations

import os

import pytest
from pypdf import PdfReader

from pdftools import ops_content, ops_pages, ops_secure
from pdftools._util import PdfToolsError, expand_inputs, natural_key, open_reader


def _outline(path):
    def walk(items):
        return [walk(i) if isinstance(i, list) else i.title for i in items]

    return walk(PdfReader(path).outline)


def _fields(path):
    return sorted((PdfReader(path).get_fields() or {}).keys())


FORM_FIELDS = ["country", "email", "full_name", "subscribe"]
CHAPTERS = ["Chapter 1", "Chapter 2", "Chapter 3"]


# ---------------------------------------------------------------------------
# Structure preservation
# ---------------------------------------------------------------------------

def test_merge_keeps_form_fields_and_nests_source_bookmarks(form_pdf, bookmarked_pdf, out_dir):
    out = os.path.join(out_dir, "m.pdf")
    ops_pages.merge([form_pdf, bookmarked_pdf], out)
    assert _fields(out) == FORM_FIELDS
    assert _outline(out) == ["form", "bookmarked", CHAPTERS]
    assert len(PdfReader(out).pages) == 1 + 5


def test_merge_without_per_source_bookmarks_keeps_originals(form_pdf, bookmarked_pdf, out_dir):
    out = os.path.join(out_dir, "m.pdf")
    ops_pages.merge([form_pdf, bookmarked_pdf], out, bookmark_per_source=False)
    assert _outline(out) == CHAPTERS
    assert _fields(out) == FORM_FIELDS


def test_merge_glob_uses_natural_order(tmp_path, out_dir):
    from conftest import _make_text_pdf

    for i in (1, 2, 10, 11):
        _make_text_pdf(str(tmp_path / f"scan{i}.pdf"), 1, f"Scan{i}")
    out = os.path.join(out_dir, "scans.pdf")
    ops_pages.merge([str(tmp_path / "scan*.pdf")], out)
    firsts = [p.extract_text().splitlines()[0] for p in PdfReader(out).pages]
    assert firsts == ["Scan1 - page 1", "Scan2 - page 1", "Scan10 - page 1", "Scan11 - page 1"]


def test_natural_key_sorts_numbers_numerically():
    names = ["scan10.pdf", "Scan2.pdf", "scan1.pdf", "scan11.pdf"]
    assert sorted(names, key=natural_key) == ["scan1.pdf", "Scan2.pdf", "scan10.pdf", "scan11.pdf"]


def test_decrypt_keeps_forms_and_bookmarks(form_pdf, bookmarked_pdf, out_dir):
    for src, check in ((form_pdf, "fields"), (bookmarked_pdf, "outline")):
        enc = os.path.join(out_dir, f"enc_{check}.pdf")
        dec = os.path.join(out_dir, f"dec_{check}.pdf")
        ops_secure.encrypt(src, enc, user_password="u", owner_password="o")
        ops_secure.decrypt(enc, dec, password="u")
        reader = PdfReader(dec)
        assert reader.is_encrypted is False
        if check == "fields":
            assert _fields(dec) == FORM_FIELDS
        else:
            assert _outline(dec) == CHAPTERS


def test_scrub_removes_info_and_xmp_but_keeps_structure(xmp_pdf, form_pdf, bookmarked_pdf, out_dir):
    clean = os.path.join(out_dir, "clean.pdf")
    ops_secure.strip_metadata(xmp_pdf, clean)
    meta = ops_content.get_metadata(clean)
    assert all(value is None for value in meta.values())
    reader = PdfReader(clean)
    assert "/Metadata" not in reader.trailer["/Root"]
    data = open(clean, "rb").read()
    assert b"Secret Xmp Author" not in data  # orphaned XMP stream is gone too
    assert b"pdf-power-tools tests" not in data  # the /Info author as well

    clean_form = os.path.join(out_dir, "clean_form.pdf")
    ops_secure.strip_metadata(form_pdf, clean_form)
    assert _fields(clean_form) == FORM_FIELDS

    clean_bm = os.path.join(out_dir, "clean_bm.pdf")
    ops_secure.strip_metadata(bookmarked_pdf, clean_bm)
    assert _outline(clean_bm) == CHAPTERS


def test_scrub_flatten_annotations_drops_the_form(form_pdf, out_dir):
    out = os.path.join(out_dir, "flat.pdf")
    ops_secure.strip_metadata(form_pdf, out, flatten_annotations=True)
    assert _fields(out) == []
    assert "/AcroForm" not in PdfReader(out).trailer["/Root"]


def test_links_survive_merge_rotate_decrypt_and_scrub(tmp_path, text_pdf, out_dir):
    from reportlab.pdfgen import canvas

    linked = str(tmp_path / "linked.pdf")
    c = canvas.Canvas(linked, pagesize=(612, 792))
    c.drawString(72, 720, "Project site")
    c.linkURL("https://example.com/project", (72, 715, 200, 735))
    c.showPage()
    c.save()

    def uris(path):
        found = []
        for page in PdfReader(path).pages:
            for annot in page.get("/Annots") or []:
                action = annot.get_object().get("/A")
                if action is not None and "/URI" in action:
                    found.append(str(action["/URI"]))
        return found

    merged = ops_pages.merge([text_pdf, linked], os.path.join(out_dir, "m.pdf"))
    rotated = ops_pages.rotate(linked, os.path.join(out_dir, "r.pdf"))
    enc = os.path.join(out_dir, "e.pdf")
    ops_secure.encrypt(linked, enc, user_password="u")
    decrypted = ops_secure.decrypt(enc, os.path.join(out_dir, "d.pdf"), password="u")
    scrubbed = ops_secure.strip_metadata(linked, os.path.join(out_dir, "s.pdf"))
    for path in (merged, rotated, decrypted, scrubbed):
        assert uris(path) == ["https://example.com/project"], path


def test_page_ops_keep_form_fields(form_pdf, bookmarked_pdf, out_dir):
    rotated = ops_pages.rotate(form_pdf, os.path.join(out_dir, "r.pdf"))
    assert _fields(rotated) == FORM_FIELDS
    reordered = ops_pages.reorder(bookmarked_pdf, os.path.join(out_dir, "k.pdf"), order="1-3")
    assert _outline(reordered)[:2] == ["Chapter 1", "Chapter 2"]


# ---------------------------------------------------------------------------
# Opening: missing, damaged, locked
# ---------------------------------------------------------------------------

def test_open_reader_errors_are_friendly(tmp_path, not_a_pdf, encrypted_pdf):
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    for bad in (str(empty), not_a_pdf, str(tmp_path / "missing.pdf"), str(tmp_path)):
        with pytest.raises(PdfToolsError):
            open_reader(bad)
    with pytest.raises(PdfToolsError, match="--password"):
        open_reader(encrypted_pdf)
    with pytest.raises(PdfToolsError, match="Wrong password"):
        open_reader(encrypted_pdf, "nope")
    assert len(open_reader(encrypted_pdf, "open123").pages) == 3
    assert len(open_reader(encrypted_pdf, "owner123").pages) == 3


def test_library_functions_accept_password(encrypted_pdf, out_dir):
    assert "Memo - page 1" in ops_content.extract_text(encrypted_pdf, password="open123")
    assert ops_content.page_count(encrypted_pdf, password="owner123") == 3
    summary = ops_content.info(encrypted_pdf, password="open123")
    assert summary["encrypted"] is True
    assert summary["encryption"]["algorithm"] == "AES-256"
    out = ops_pages.rotate(encrypted_pdf, os.path.join(out_dir, "rot.pdf"), password="open123")
    assert PdfReader(out).is_encrypted is False


def test_expand_inputs_reports_missing_literal_and_empty_glob(tmp_path):
    with pytest.raises(PdfToolsError, match="Input not found"):
        expand_inputs([str(tmp_path / "nope.pdf")])
    with pytest.raises(PdfToolsError, match="No files matched"):
        expand_inputs([str(tmp_path / "*.pdf")])
