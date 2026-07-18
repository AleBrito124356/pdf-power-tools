"""Honest PDF compression.

There is no magic "make it smaller" button that keeps everything. This module
is explicit about the trade in each path:

* ``lossless`` — recompress content streams and drop duplicate objects. Safe,
  keeps text and vectors, but only wins a little (often 5-20%).
* ``rasterize`` — render every page to a JPEG and rebuild. Big wins on scanned
  or image-heavy files, but the output is images: selectable text is gone.
* ``ghostscript`` — if Ghostscript is on PATH, its ``-dPDFSETTINGS`` pipeline
  is the best general-purpose downsampler. Detected, never required.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
from typing import Optional

from pypdf import PdfReader, PdfWriter

from ._util import PdfToolsError, default_output, ensure_parent_dir


_GS_SETTINGS = {"screen", "ebook", "printer", "prepress", "default"}


def find_ghostscript() -> Optional[str]:
    """Return the Ghostscript executable path, or ``None`` if not installed."""
    for name in ("gs", "gswin64c", "gswin32c"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _compress_lossless(input_path: str, output: str) -> str:
    writer = PdfWriter(clone_from=input_path)
    for page in writer.pages:
        try:
            page.compress_content_streams()
        except Exception:  # pragma: no cover - some streams refuse; skip them
            pass
    try:
        # Defaults de-duplicate objects and drop unreferenced ones. Called with
        # no args to stay compatible across pypdf versions that renamed the
        # keyword parameters.
        writer.compress_identical_objects()
    except Exception:  # pragma: no cover - older pypdf lacks this
        pass
    ensure_parent_dir(output)
    with open(output, "wb") as fh:
        writer.write(fh)
    return output


def _compress_rasterize(input_path: str, output: str, dpi: int, jpeg_quality: int) -> str:
    import pypdfium2 as pdfium
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    ensure_parent_dir(output)
    pdf = pdfium.PdfDocument(input_path)
    try:
        c = canvas.Canvas(output)
        scale = dpi / 72.0
        for idx in range(len(pdf)):
            page = pdf[idx]
            width, height = page.get_size()  # points
            bitmap = page.render(scale=scale)
            pil_image = bitmap.to_pil().convert("RGB")
            jpg_buf = io.BytesIO()
            pil_image.save(jpg_buf, format="JPEG", quality=jpeg_quality, optimize=True)
            jpg_buf.seek(0)
            c.setPageSize((width, height))
            c.drawImage(ImageReader(jpg_buf), 0, 0, width, height)
            c.showPage()
        c.save()
    finally:
        pdf.close()
    return output


def _compress_ghostscript(input_path: str, output: str, gs_quality: str) -> str:
    if gs_quality not in _GS_SETTINGS:
        raise PdfToolsError(
            f"gs_quality must be one of {', '.join(sorted(_GS_SETTINGS))}."
        )
    gs = find_ghostscript()
    if not gs:
        raise PdfToolsError(
            "Ghostscript not found on PATH. Install it (gs / gswin64c) or use "
            "mode='lossless' / mode='rasterize'."
        )
    ensure_parent_dir(output)
    cmd = [
        gs,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.5",
        f"-dPDFSETTINGS=/{gs_quality}",
        "-dNOPAUSE",
        "-dBATCH",
        "-dQUIET",
        "-dDetectDuplicateImages=true",
        f"-sOutputFile={output}",
        input_path,
    ]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:  # pragma: no cover - env specific
        raise PdfToolsError(f"Ghostscript failed with exit code {exc.returncode}.") from exc
    return output


def compress(
    input_path: str,
    output: Optional[str] = None,
    *,
    mode: str = "auto",
    dpi: int = 150,
    jpeg_quality: int = 60,
    gs_quality: str = "ebook",
) -> str:
    """Shrink a PDF using the chosen strategy.

    ``mode``:

    * ``auto`` — use Ghostscript if present, otherwise fall back to lossless.
    * ``lossless`` — stream cleanup and object de-duplication only.
    * ``rasterize`` — render to JPEG pages at ``dpi`` / ``jpeg_quality``
      (loses selectable text).
    * ``ghostscript`` — force the Ghostscript path with ``gs_quality`` one of
      screen, ebook, printer, prepress, default.

    Returns the output path. The output is written even if it ends up larger
    than the input; the CLI reports the before/after sizes so you can decide.
    """
    if not os.path.isfile(input_path):
        raise PdfToolsError(f"Input not found: {input_path!r}")
    output = output or default_output(input_path, "compressed")

    if mode == "auto":
        mode = "ghostscript" if find_ghostscript() else "lossless"

    if mode == "lossless":
        return _compress_lossless(input_path, output)
    if mode == "rasterize":
        return _compress_rasterize(input_path, output, dpi, jpeg_quality)
    if mode == "ghostscript":
        return _compress_ghostscript(input_path, output, gs_quality)
    raise PdfToolsError(
        f"Unknown mode {mode!r}. Use auto, lossless, rasterize or ghostscript."
    )


# Keep a reference so `PdfReader` import is used even on the lossless-only path;
# it also surfaces a clear error early if the file is not a readable PDF.
def probe(input_path: str) -> int:
    """Return the page count, raising a friendly error if the PDF is unreadable."""
    try:
        return len(PdfReader(input_path).pages)
    except Exception as exc:
        raise PdfToolsError(f"Could not read PDF: {input_path!r} ({exc}).") from exc
