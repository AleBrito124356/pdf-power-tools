"""Content extraction and document metadata.

Text and tables lean on pdfplumber for layout-aware output; images and
metadata come straight from pypdf. Scanned pages simply yield empty text, and
unreadable, damaged or password-protected inputs raise a one-line
:class:`~pdftools._util.PdfToolsError` instead of a backend traceback. Every
function accepts ``password=`` for encrypted inputs.
"""

from __future__ import annotations

import csv
import os
from typing import Dict, List, Optional, Sequence, Union

from ._util import (
    PdfToolsError,
    ensure_parent_dir,
    open_pdfium,
    open_plumber,
    open_reader,
    open_writer,
    parse_page_ranges,
    write_pdf,
)


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def _plumber_text(
    input_path: str, indices: Sequence[int], layout: bool, password: Optional[str]
) -> List[str]:
    out: List[str] = []
    with open_plumber(input_path, password) as pdf:
        for idx in indices:
            page = pdf.pages[idx]
            out.append(page.extract_text(layout=layout) or "")
    return out


def extract_text(
    input_path: str,
    output: Optional[str] = None,
    *,
    pages: Optional[str] = None,
    joined: bool = True,
    layout: bool = False,
    password: Optional[str] = None,
) -> Union[str, List[str], None]:
    """Extract selectable text.

    * ``pages`` — a range spec; ``None`` means every page.
    * ``layout=True`` — use pdfplumber's layout engine, which keeps columns and
      spacing roughly where they were (good for statements and forms).
    * Without ``layout``, pypdf does the work; if it comes back empty (common
      for scans or bad encodings) the call falls back to pdfplumber once.

    With ``output`` set, pages are written joined by form-feed (``\\f``) and the
    path is returned. Otherwise returns the joined string (``joined=True``) or a
    list of per-page strings.
    """
    reader = open_reader(input_path, password)
    total = len(reader.pages)
    indices = parse_page_ranges(pages, total) if pages else list(range(total))

    if layout:
        texts = _plumber_text(input_path, indices, True, password)
    else:
        texts = [reader.pages[i].extract_text() or "" for i in indices]
        if not any(t.strip() for t in texts):
            fallback = _plumber_text(input_path, indices, False, password)
            if any(t.strip() for t in fallback):
                texts = fallback

    if output:
        ensure_parent_dir(output)
        with open(output, "w", encoding="utf-8") as fh:
            fh.write("\f".join(texts))
        return output
    return "\n\n".join(texts) if joined else texts


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

def _rows_to_markdown(rows: List[List[str]]) -> str:
    clean = [["" if c is None else str(c).replace("\n", " ").strip() for c in row] for row in rows]
    if not clean:
        return ""
    width = max(len(r) for r in clean)
    clean = [r + [""] * (width - len(r)) for r in clean]
    header = clean[0]
    body = clean[1:]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def extract_tables(
    input_path: str,
    output_dir: Optional[str] = None,
    *,
    pages: Optional[str] = None,
    fmt: str = "csv",
    password: Optional[str] = None,
) -> List[Dict]:
    """Detect tables with pdfplumber and optionally write them out.

    Returns a list of records, one per detected table, each with ``page``
    (1-based), ``index`` (per-page table number), ``rows`` (list of row lists)
    and ``path`` (the written file, or ``None``).

    ``fmt`` is ``"csv"`` or ``"markdown"``. When ``output_dir`` is given, files
    are named ``{stem}_p{page}_t{index}.{ext}``; without it nothing is written.
    """
    if fmt not in ("csv", "markdown", "md"):
        raise PdfToolsError("Table format must be 'csv' or 'markdown'.")
    fmt = "markdown" if fmt == "md" else fmt

    stem = os.path.splitext(os.path.basename(input_path))[0]
    records: List[Dict] = []
    with open_plumber(input_path, password) as pdf:
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        total = len(pdf.pages)
        indices = parse_page_ranges(pages, total) if pages else list(range(total))
        for idx in indices:
            page = pdf.pages[idx]
            tables = page.extract_tables() or []
            for t_index, rows in enumerate(tables, start=1):
                path = None
                if output_dir:
                    if fmt == "csv":
                        path = os.path.join(output_dir, f"{stem}_p{idx + 1}_t{t_index}.csv")
                        with open(path, "w", encoding="utf-8", newline="") as fh:
                            writer = csv.writer(fh)
                            for row in rows:
                                writer.writerow(["" if c is None else c for c in row])
                    else:
                        path = os.path.join(output_dir, f"{stem}_p{idx + 1}_t{t_index}.md")
                        with open(path, "w", encoding="utf-8") as fh:
                            fh.write(_rows_to_markdown(rows))
                records.append(
                    {"page": idx + 1, "index": t_index, "rows": rows, "path": path}
                )
    return records


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def extract_images(
    input_path: str,
    output_dir: str,
    *,
    pages: Optional[str] = None,
    password: Optional[str] = None,
) -> List[str]:
    """Save embedded raster images to ``output_dir``.

    Uses pypdf's per-page image inventory, which keeps the original encoding
    and picks a sensible extension. Returns the list of written file paths.
    """
    reader = open_reader(input_path, password)
    total = len(reader.pages)
    indices = parse_page_ranges(pages, total) if pages else list(range(total))
    os.makedirs(output_dir, exist_ok=True)

    written: List[str] = []
    counter = 0
    for idx in indices:
        page = reader.pages[idx]
        for image in page.images:
            counter += 1
            name = image.name or f"image{counter}.png"
            base, ext = os.path.splitext(os.path.basename(name))
            if not ext:
                # Detect from the decoded PIL image when pypdf gave no extension.
                ext = "." + (image.image.format or "PNG").lower()
            out_name = f"p{idx + 1}_{counter:03d}_{base}{ext}"
            out_path = os.path.join(output_dir, out_name)
            with open(out_path, "wb") as fh:
                fh.write(image.data)
            written.append(out_path)
    return written


# ---------------------------------------------------------------------------
# Metadata + info
# ---------------------------------------------------------------------------

_META_KEYS = {
    "title": "/Title",
    "author": "/Author",
    "subject": "/Subject",
    "keywords": "/Keywords",
    "creator": "/Creator",
    "producer": "/Producer",
}


def _metadata_from_reader(reader) -> Dict[str, Optional[str]]:
    meta = reader.metadata or {}
    out: Dict[str, Optional[str]] = {}
    for friendly, pdf_key in _META_KEYS.items():
        value = meta.get(pdf_key)
        out[friendly] = str(value) if value is not None else None
    # Dates are exposed as raw strings; pass them through untouched.
    out["created"] = str(meta.get("/CreationDate")) if meta.get("/CreationDate") else None
    out["modified"] = str(meta.get("/ModDate")) if meta.get("/ModDate") else None
    return out


def get_metadata(input_path: str, *, password: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Return the document information dictionary as a plain dict."""
    return _metadata_from_reader(open_reader(input_path, password))


def set_metadata(
    input_path: str,
    output: str,
    *,
    title: Optional[str] = None,
    author: Optional[str] = None,
    subject: Optional[str] = None,
    keywords: Optional[str] = None,
    password: Optional[str] = None,
) -> str:
    """Write the given metadata fields, leaving existing pages untouched.

    Only the fields you pass are changed; the rest of the info dictionary is
    preserved. Returns the output path.
    """
    writer = open_writer(input_path, password)
    updates = {}
    if title is not None:
        updates["/Title"] = title
    if author is not None:
        updates["/Author"] = author
    if subject is not None:
        updates["/Subject"] = subject
    if keywords is not None:
        updates["/Keywords"] = keywords
    if updates:
        writer.add_metadata(updates)
    return write_pdf(writer, output)


def page_count(input_path: str, *, password: Optional[str] = None) -> int:
    """Number of pages in the document."""
    return len(open_reader(input_path, password).pages)


def pages_with_text(input_path: str, *, password: Optional[str] = None) -> List[bool]:
    """One flag per page: does it carry any selectable (or invisible) text?

    Pages that come back ``False`` are image-only — scans or photos — and are
    the ones worth sending through :func:`pdftools.ocr`.
    """
    pdf = open_pdfium(input_path, password)
    try:
        flags: List[bool] = []
        for idx in range(len(pdf)):
            page = pdf[idx]
            textpage = page.get_textpage()
            try:
                flags.append(bool(textpage.get_text_range().strip()))
            finally:
                textpage.close()
                page.close()
        return flags
    finally:
        pdf.close()


def _pdf_version(reader) -> Optional[str]:
    version = None
    header = getattr(reader, "pdf_header", "") or ""
    if header.startswith("%PDF-"):
        version = header[5:].strip()
    try:
        catalog_version = reader.trailer["/Root"].get("/Version")
        if catalog_version is not None:
            cat = str(catalog_version).lstrip("/")
            if version is None or float(cat) > float(version):
                version = cat
    except Exception:
        pass
    return version


def info(input_path: str, *, password: Optional[str] = None) -> Dict:
    """A compact summary of the document.

    Includes the PDF version, page count, per-page size / rotation /
    ``has_text`` flag, a ``pages_without_text`` list (the "needs OCR" hint),
    encryption algorithm and permissions, form and bookmark presence, and the
    metadata dictionary.
    """
    from .ops_secure import _encryption_summary

    reader = open_reader(input_path, password)
    text_flags = pages_with_text(input_path, password=password)
    sizes = []
    for idx, page in enumerate(reader.pages):
        box = page.mediabox
        sizes.append(
            {
                "width_pt": round(float(box.width), 2),
                "height_pt": round(float(box.height), 2),
                "rotation": int(page.rotation or 0) % 360,
                "has_text": text_flags[idx] if idx < len(text_flags) else None,
            }
        )
    try:
        has_form = bool(reader.get_fields())
    except Exception:
        has_form = False
    try:
        has_outline = bool(reader.outline)
    except Exception:
        has_outline = False
    return {
        "path": os.path.abspath(input_path),
        "pdf_version": _pdf_version(reader),
        "page_count": len(reader.pages),
        "encrypted": bool(reader.is_encrypted),
        "encryption": _encryption_summary(reader),
        "has_form": has_form,
        "has_outline": has_outline,
        "pages_without_text": [i + 1 for i, flag in enumerate(text_flags) if not flag],
        "pages": sizes,
        "metadata": _metadata_from_reader(reader),
    }
