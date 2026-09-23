"""Page-level operations: merge, split, rotate, delete, reorder, n-up.

These never touch page content — they move whole pages around. Text, images,
links, form fields and bookmarks ride along: documents are cloned or appended
with pypdf's structure-aware APIs instead of being rebuilt page by page.

Every function accepts ``password=`` for encrypted inputs; outputs are
written unencrypted.
"""

from __future__ import annotations

import os
from typing import List, Optional, Sequence

from pypdf import PdfReader, PdfWriter, Transformation

from ._util import (
    PdfToolsError,
    default_output,
    expand_inputs,
    open_reader,
    parse_page_ranges,
    write_pdf,
)


def _bookmark_title(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def merge(
    inputs: Sequence[str],
    output: str,
    *,
    bookmark_per_source: bool = True,
    password: Optional[str] = None,
) -> str:
    """Concatenate PDFs in order into ``output``.

    ``inputs`` may contain literal paths and glob patterns; they are expanded
    (globs in natural order, so ``scan2`` precedes ``scan10``) and
    de-duplicated while preserving order.

    Sources are appended structurally: interactive form fields, internal
    links and each source's own bookmarks survive. When
    ``bookmark_per_source`` is true, every source gets a top-level bookmark
    named after its file with its original bookmarks nested beneath it;
    otherwise the original bookmarks stay at the top level.

    ``password`` is tried on every encrypted input. Returns the output path.
    """
    files = expand_inputs(list(inputs))
    if len(files) < 1:
        raise PdfToolsError("merge needs at least one input file.")

    writer = PdfWriter()
    for path in files:
        reader = open_reader(path, password)
        if len(reader.pages) == 0:
            continue
        writer.append(
            reader,
            outline_item=_bookmark_title(path) if bookmark_per_source else None,
        )
    return write_pdf(writer, output)


def _write_indices(reader: PdfReader, indices: Sequence[int], output: str) -> str:
    writer = PdfWriter()
    if len(set(indices)) == len(indices):
        # Structure-aware copy: keeps form fields, links and the bookmarks
        # that point at the pages being kept.
        writer.append(reader, pages=list(indices))
    else:
        # Repeated pages (e.g. reorder "1,1,2") need plain page copies.
        for idx in indices:
            writer.add_page(reader.pages[idx])
    return write_pdf(writer, output)


def split(
    input_path: str,
    output_dir: str,
    *,
    ranges: Optional[str] = None,
    every_n: Optional[int] = None,
    by_bookmark: bool = False,
    password: Optional[str] = None,
) -> List[str]:
    """Split one PDF into several. Choose exactly one mode.

    * ``ranges="1-3,7,9-"`` — each comma group becomes its own file (to keep
      several groups in *one* file, use :func:`reorder`).
    * ``every_n=5`` — fixed-size chunks of N pages.
    * ``by_bookmark=True`` — a file per top-level outline entry, cut at each
      bookmark's page.

    Files are written into ``output_dir`` (created if missing) and the list of
    written paths is returned.
    """
    modes = [ranges is not None, every_n is not None, by_bookmark]
    if sum(1 for m in modes if m) != 1:
        raise PdfToolsError(
            "split needs exactly one of: ranges, every_n, by_bookmark."
        )

    reader = open_reader(input_path, password)
    total = len(reader.pages)
    stem = os.path.splitext(os.path.basename(input_path))[0]
    os.makedirs(output_dir, exist_ok=True)
    outputs: List[str] = []

    if ranges is not None:
        groups = [g.strip() for g in ranges.split(",") if g.strip()]
        for n, group in enumerate(groups, start=1):
            indices = parse_page_ranges(group, total)
            out = os.path.join(output_dir, f"{stem}_part{n:02d}.pdf")
            outputs.append(_write_indices(reader, indices, out))
        return outputs

    if every_n is not None:
        if every_n < 1:
            raise PdfToolsError("every_n must be at least 1.")
        for n, start in enumerate(range(0, total, every_n), start=1):
            indices = list(range(start, min(start + every_n, total)))
            out = os.path.join(output_dir, f"{stem}_part{n:02d}.pdf")
            outputs.append(_write_indices(reader, indices, out))
        return outputs

    # by_bookmark
    boundaries = _top_level_bookmark_pages(reader)
    if not boundaries:
        raise PdfToolsError(
            "No top-level bookmarks found; cannot split by bookmark."
        )
    # Ensure the first chunk starts at page 0 even if the first bookmark does not.
    starts = sorted(set([0] + boundaries))
    for n in range(len(starts)):
        start = starts[n]
        end = starts[n + 1] if n + 1 < len(starts) else total
        if end <= start:
            continue
        indices = list(range(start, end))
        out = os.path.join(output_dir, f"{stem}_part{n + 1:02d}.pdf")
        outputs.append(_write_indices(reader, indices, out))
    return outputs


def _top_level_bookmark_pages(reader: PdfReader) -> List[int]:
    """Return the 0-based page index of each top-level outline entry."""
    pages: List[int] = []
    try:
        outline = reader.outline
    except Exception:  # pragma: no cover - malformed outlines
        return pages
    for item in outline:
        if isinstance(item, list):
            # Nested children; we only cut on top-level entries.
            continue
        try:
            page_number = reader.get_destination_page_number(item)
        except Exception:  # pragma: no cover
            continue
        if page_number is not None and page_number >= 0:
            pages.append(int(page_number))
    return sorted(set(pages))


def rotate(
    input_path: str,
    output: Optional[str] = None,
    *,
    pages: Optional[str] = None,
    angle: int = 90,
    password: Optional[str] = None,
) -> str:
    """Rotate pages clockwise by a multiple of 90 degrees.

    ``pages`` is a range spec (``"1-3,7"``); ``None`` rotates every page. The
    document is cloned, so forms, links and bookmarks are untouched.
    """
    if angle % 90 != 0:
        raise PdfToolsError("Rotation angle must be a multiple of 90.")
    writer = PdfWriter(clone_from=open_reader(input_path, password))
    total = len(writer.pages)
    target = set(parse_page_ranges(pages, total)) if pages else set(range(total))
    output = output or default_output(input_path, "rotated")

    for idx in sorted(target):
        writer.pages[idx].rotate(angle)
    return write_pdf(writer, output)


def delete_pages(
    input_path: str,
    output: Optional[str] = None,
    *,
    pages: str,
    password: Optional[str] = None,
) -> str:
    """Drop the pages named by the ``pages`` spec; keep the rest in order."""
    reader = open_reader(input_path, password)
    total = len(reader.pages)
    drop = set(parse_page_ranges(pages, total))
    keep = [i for i in range(total) if i not in drop]
    if not keep:
        raise PdfToolsError("Refusing to delete every page.")
    output = output or default_output(input_path, "trimmed")
    return _write_indices(reader, keep, output)


def reorder(
    input_path: str,
    output: Optional[str] = None,
    *,
    order: str,
    password: Optional[str] = None,
) -> str:
    """Rebuild the document in the page order given by ``order``.

    ``order`` is a range spec whose sequence is respected literally, so
    ``"3,1,2"`` really moves page 3 to the front and duplicates are allowed.
    It doubles as "keep only these pages": ``order="1-3,7"`` writes one file
    holding pages 1, 2, 3 and 7.
    """
    reader = open_reader(input_path, password)
    total = len(reader.pages)
    indices = parse_page_ranges(order, total)
    output = output or default_output(input_path, "reordered")
    return _write_indices(reader, indices, output)


def n_up(
    input_path: str,
    output: Optional[str] = None,
    *,
    n: int = 2,
    password: Optional[str] = None,
) -> str:
    """Impose 2 or 4 source pages onto each output sheet.

    2-up rotates the sheet to landscape and places pages side by side; 4-up
    keeps the orientation and tiles a 2x2 grid. Pages are scaled to fit their
    cell while preserving aspect ratio and centred within it. Source content
    is embedded as vector graphics, so text stays selectable and searchable;
    interactive form fields are not carried onto the handout sheets.
    """
    if n not in (2, 4):
        raise PdfToolsError("n_up supports n=2 or n=4.")
    reader = open_reader(input_path, password)
    if len(reader.pages) == 0:
        raise PdfToolsError("Document has no pages.")
    first = reader.pages[0]
    w = float(first.mediabox.width)
    h = float(first.mediabox.height)

    if n == 2:
        sheet_w, sheet_h = h, w  # swap → long side becomes the split axis
        cols, rows = (2, 1) if h >= w else (1, 2)
    else:  # n == 4
        sheet_w, sheet_h = w, h
        cols, rows = 2, 2

    per_sheet = cols * rows
    output = output or default_output(input_path, f"{n}up")
    writer = PdfWriter()
    pages = list(reader.pages)
    cell_w = sheet_w / cols
    cell_h = sheet_h / rows

    for base in range(0, len(pages), per_sheet):
        # add_blank_page attaches the sheet to the writer so merging is the
        # supported, warning-free path.
        sheet = writer.add_blank_page(width=sheet_w, height=sheet_h)
        for slot, page in enumerate(pages[base : base + per_sheet]):
            col = slot % cols
            row = slot // cols
            pw = float(page.mediabox.width)
            ph = float(page.mediabox.height)
            scale = min(cell_w / pw, cell_h / ph)
            # PDF origin is bottom-left; fill the grid top row first.
            tx = col * cell_w + (cell_w - pw * scale) / 2 - float(page.mediabox.left) * scale
            ty = (rows - 1 - row) * cell_h + (cell_h - ph * scale) / 2 - float(page.mediabox.bottom) * scale
            transform = Transformation().scale(scale, scale).translate(tx, ty)
            sheet.merge_transformed_page(page, transform)

    return write_pdf(writer, output)
