"""Internal helpers shared across the operation modules.

Nothing here is part of the public API — import from the top-level package or
the ``ops_*`` modules instead. These are the small, boring pieces (opening
documents safely, page-range parsing, input globbing, colour parsing) that
every operation reuses.
"""

from __future__ import annotations

import contextlib
import glob as _glob
import os
import re
from typing import TYPE_CHECKING, Iterable, List, Optional, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pypdf import PdfReader, PdfWriter


class PdfToolsError(Exception):
    """Raised for predictable, user-facing failures.

    The CLI catches this and prints ``str(exc)`` without a traceback, so the
    message should read like something a person wants to see.
    """


class PdfToolsWarning(UserWarning):
    """Emitted for recoverable problems the caller should hear about.

    Examples: a form key that matches no field, or a compression mode that is
    about to throw away selectable text. The CLI prints these as
    ``warning: ...`` lines; library callers can filter or escalate them with
    the standard :mod:`warnings` machinery.
    """


# ---------------------------------------------------------------------------
# Opening documents. One place turns "missing / not a PDF / damaged /
# password-protected" into a clean PdfToolsError for every backend.
# ---------------------------------------------------------------------------

def _locked_message(path: str) -> str:
    return (
        f"{path!r} is password-protected. Pass --password with the user or "
        "owner password (library: password=...)."
    )


def _require_file(path) -> str:
    if not isinstance(path, (str, os.PathLike)):
        raise PdfToolsError(f"Expected a file path, got {type(path).__name__}.")
    name = os.fspath(path)
    if not os.path.exists(name):
        raise PdfToolsError(f"Input not found: {name!r}")
    if not os.path.isfile(name):
        raise PdfToolsError(f"Not a file: {name!r}")
    return name


def _describe(exc: BaseException) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    return detail.splitlines()[0]


def open_reader(path: str, password: Optional[str] = None) -> "PdfReader":
    """Open ``path`` with pypdf and return a ready-to-use, decrypted reader.

    * Missing files, directories, non-PDFs and damaged files raise
      :class:`PdfToolsError` with a one-line explanation.
    * Encrypted files are decrypted with ``password``. Without a password the
      empty user password is tried automatically, which is how
      "owner-password only" files open in every viewer.
    * A locked file without the right password raises a PdfToolsError that
      names ``--password``.
    """
    from pypdf import PdfReader

    name = _require_file(path)
    try:
        reader = PdfReader(name)
    except Exception as exc:  # pypdf raises a zoo of types on bad input
        raise PdfToolsError(
            f"Could not read {name!r}: not a PDF, or the file is damaged "
            f"({_describe(exc)})."
        ) from exc

    if reader.is_encrypted:
        try:
            result = reader.decrypt(password if password is not None else "")
        except Exception as exc:  # e.g. missing crypto backend for AES
            raise PdfToolsError(f"Could not decrypt {name!r}: {_describe(exc)}") from exc
        if not result:
            if password:
                raise PdfToolsError(f"Wrong password for {name!r}.")
            raise PdfToolsError(_locked_message(name))

    try:
        len(reader.pages)  # parse the page tree now, not halfway through an op
    except Exception as exc:
        raise PdfToolsError(
            f"Could not read {name!r}: the page tree is damaged ({_describe(exc)})."
        ) from exc
    return reader


def open_writer(path: str, password: Optional[str] = None) -> "PdfWriter":
    """Clone ``path`` into a :class:`pypdf.PdfWriter`, forms and outline included.

    The clone comes from a decrypted reader, so the result is written
    unencrypted — run ``encrypt`` again if the output must stay locked.
    """
    from pypdf import PdfWriter

    return PdfWriter(clone_from=open_reader(path, password))


def open_pdfium(path: str, password: Optional[str] = None):
    """Open ``path`` with pypdfium2, mapping failures to PdfToolsError.

    The caller owns the returned document and must ``close()`` it.
    """
    import pypdfium2 as pdfium

    name = _require_file(path)
    try:
        return pdfium.PdfDocument(name, password=password or None)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            if password:
                raise PdfToolsError(f"Wrong password for {name!r}.") from exc
            raise PdfToolsError(_locked_message(name)) from exc
        raise PdfToolsError(
            f"Could not read {name!r}: not a PDF, or the file is damaged "
            f"({_describe(exc)})."
        ) from exc


def open_plumber(path: str, password: Optional[str] = None):
    """Open ``path`` with pdfplumber, mapping failures to PdfToolsError.

    The caller owns the returned document (use it as a context manager).
    """
    import pdfplumber

    name = _require_file(path)
    try:
        pdf = pdfplumber.open(name, password=password or "")
        len(pdf.pages)
        return pdf
    except Exception as exc:
        # pdfminer reports a bad password as an empty exception, so let pypdf
        # (which knows the difference) phrase the error when it can.
        open_reader(name, password)
        raise PdfToolsError(
            f"Could not read {name!r} for layout analysis ({_describe(exc)})."
        ) from exc


@contextlib.contextmanager
def binary_streams():
    """Make reportlab write raw binary streams instead of ASCII85 text.

    reportlab ASCII85-encodes every stream by default, which inflates
    embedded JPEGs and page images by 25% for no benefit in a binary file.
    The setting is global in reportlab, so it is restored on exit.
    """
    from reportlab import rl_config

    previous = rl_config.useA85
    rl_config.useA85 = 0
    try:
        yield
    finally:
        rl_config.useA85 = previous


def write_pdf(writer: "PdfWriter", output: str) -> str:
    """Write ``writer`` to ``output`` (creating parent dirs); return the path."""
    ensure_parent_dir(output)
    with open(output, "wb") as fh:
        writer.write(fh)
    return output


# ---------------------------------------------------------------------------
# Page ranges
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Inputs and outputs
# ---------------------------------------------------------------------------

_GLOB_CHARS = re.compile(r"[*?\[]")


def is_glob(pattern: str) -> bool:
    """True when ``pattern`` will be expanded as a glob, not used literally."""
    return bool(_GLOB_CHARS.search(pattern)) and not os.path.isfile(pattern)


def natural_key(text: str) -> list:
    """Sort key that orders embedded numbers numerically.

    ``scan2.pdf`` sorts before ``scan10.pdf`` — what a person expects when
    merging numbered scans, and what plain string sorting gets wrong.
    """
    parts = re.split(r"(\d+)", text)
    return [int(p) if p.isdigit() else p.casefold() for p in parts]


def expand_inputs(patterns: Sequence[str]) -> List[str]:
    """Expand a list of paths and globs into concrete, existing file paths.

    A literal path that exists is kept as-is (so filenames containing glob
    characters still work). Anything else is treated as a glob. Order between
    patterns is preserved; matches inside one glob are sorted in natural
    (human) order, so ``scan2`` comes before ``scan10``.
    """
    out: List[str] = []
    seen = set()
    for pattern in patterns:
        if os.path.isfile(pattern):
            matches = [pattern]
        elif not _GLOB_CHARS.search(pattern):
            raise PdfToolsError(f"Input not found: {pattern!r}")
        else:
            matches = sorted(
                (m for m in _glob.glob(pattern, recursive=True) if os.path.isfile(m)),
                key=natural_key,
            )
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
