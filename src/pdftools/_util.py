"""Internal helpers shared across the operation modules.

Nothing here is part of the public API — import from the top-level package or
the ``ops_*`` modules instead. These are the small, boring pieces (page-range
parsing, input globbing, colour parsing) that every operation reuses.
"""

from __future__ import annotations

import glob as _glob
import os
from typing import Iterable, List, Sequence, Tuple


class PdfToolsError(Exception):
    """Raised for predictable, user-facing failures.

    The CLI catches this and prints ``str(exc)`` without a traceback, so the
    message should read like something a person wants to see.
    """


def parse_page_ranges(spec: str, page_count: int) -> List[int]:
    """Turn a human range spec into a list of 0-based page indices.

    Accepts comma-separated tokens, each one of:

    * ``"5"``      — a single page (1-based)
    * ``"2-6"``    — an inclusive range, counting up
    * ``"6-2"``    — an inclusive range, counting down
    * ``"9-"``     — from page 9 to the end
    * ``"-3"``     — from the start to page 3
    * ``"N"``      — the literal last page (also ``END`` / ``LAST``)

    Order is preserved and duplicates are kept, which is what ``reorder`` and
    ``split`` want. To avoid clashing with the open-start ``"-3"`` form, page
    numbers here are always positive; use :func:`as_index_list` when you need
    negative-from-end indexing on a programmatic list.

    >>> parse_page_ranges("1-3,7", 10)
    [0, 1, 2, 6]
    >>> parse_page_ranges("N,-2", 4)
    [3, 0, 1]
    """
    if page_count <= 0:
        raise PdfToolsError("Document has no pages.")

    def as_page(token: str) -> int:
        token = token.strip().upper()
        if token in ("N", "END", "LAST"):
            return page_count
        try:
            value = int(token)
        except ValueError as exc:
            raise PdfToolsError(f"Invalid page number: {token!r}") from exc
        if value < 1:
            raise PdfToolsError(
                f"Page numbers are 1-based and positive; got {value} in {spec!r}."
            )
        return value

    indices: List[int] = []
    for raw in spec.split(","):
        chunk = raw.strip()
        if not chunk:
            continue
        is_range = "-" in chunk and not chunk.upper() in ("N", "END", "LAST")
        if is_range:
            left, _, right = chunk.partition("-")
            start = 1 if left.strip() == "" else as_page(left)
            end = page_count if right.strip() == "" else as_page(right)
            step = 1 if end >= start else -1
            for p in range(start, end + step, step):
                _push_index(indices, p, page_count, spec)
        else:
            _push_index(indices, as_page(chunk), page_count, spec)

    if not indices:
        raise PdfToolsError(f"Page spec {spec!r} selected no pages.")
    return indices


def _push_index(indices: List[int], one_based: int, page_count: int, spec: str) -> None:
    if one_based < 1 or one_based > page_count:
        raise PdfToolsError(
            f"Page {one_based} is out of range 1-{page_count} in spec {spec!r}."
        )
    indices.append(one_based - 1)


def expand_inputs(patterns: Sequence[str]) -> List[str]:
    """Expand a list of paths and globs into concrete, existing file paths.

    A literal path that exists is kept as-is (so filenames containing glob
    characters still work). Anything else is treated as a glob. Order between
    patterns is preserved; matches inside one glob are sorted for determinism.
    """
    out: List[str] = []
    seen = set()
    for pattern in patterns:
        if os.path.isfile(pattern):
            matches = [pattern]
        else:
            matches = sorted(_glob.glob(pattern, recursive=True))
        if not matches:
            raise PdfToolsError(f"No files matched: {pattern!r}")
        for match in matches:
            resolved = os.path.abspath(match)
            if resolved not in seen:
                seen.add(resolved)
                out.append(match)
    return out


def ensure_parent_dir(path: str) -> str:
    """Create the parent directory of ``path`` if needed; return ``path``."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path


def default_output(input_path: str, suffix: str, ext: str = ".pdf") -> str:
    """Build ``name.<suffix><ext>`` next to ``input_path``.

    Used when the caller does not pass ``-o``. Keeps outputs beside their
    source so nothing lands in a surprising directory.
    """
    base, _ = os.path.splitext(input_path)
    return f"{base}.{suffix}{ext}"


# ---------------------------------------------------------------------------
# Colour parsing (shared by watermark + page numbers).
# ---------------------------------------------------------------------------

_NAMED_COLORS = {
    "black": (0.0, 0.0, 0.0),
    "white": (1.0, 1.0, 1.0),
    "red": (0.86, 0.15, 0.15),
    "green": (0.13, 0.55, 0.13),
    "blue": (0.15, 0.39, 0.92),
    "gray": (0.5, 0.5, 0.5),
    "grey": (0.5, 0.5, 0.5),
    "lightgray": (0.75, 0.75, 0.75),
    "lightgrey": (0.75, 0.75, 0.75),
    "orange": (0.95, 0.55, 0.12),
}


def parse_color(value: str) -> Tuple[float, float, float]:
    """Parse a colour name or ``#RRGGBB`` hex string into an RGB 0..1 tuple."""
    if value is None:
        return _NAMED_COLORS["gray"]
    text = value.strip().lower()
    if text in _NAMED_COLORS:
        return _NAMED_COLORS[text]
    hex_text = text.lstrip("#")
    if len(hex_text) == 3:
        hex_text = "".join(ch * 2 for ch in hex_text)
    if len(hex_text) == 6:
        try:
            r = int(hex_text[0:2], 16) / 255.0
            g = int(hex_text[2:4], 16) / 255.0
            b = int(hex_text[4:6], 16) / 255.0
            return (r, g, b)
        except ValueError:
            pass
    raise PdfToolsError(
        f"Unrecognised colour {value!r}. Use a name (red, blue, gray) or #RRGGBB."
    )


def as_index_list(pages: Iterable[int] | None, page_count: int) -> List[int]:
    """Normalise an optional page-index iterable to a concrete sorted list.

    ``None`` means "every page". Values are validated against ``page_count``.
    """
    if pages is None:
        return list(range(page_count))
    result = []
    for idx in pages:
        if idx < 0:
            idx = page_count + idx
        if idx < 0 or idx >= page_count:
            raise PdfToolsError(f"Page index {idx} out of range 0-{page_count - 1}.")
        result.append(idx)
    return sorted(set(result))
