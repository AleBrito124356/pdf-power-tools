"""Honest PDF compression.

There is no magic "make it smaller" button that keeps everything. This module
is explicit about the trade in each path:

* ``images`` — recompress only the embedded raster images: downsample the
  ones stored at more detail than they are displayed (above ``max_dpi``) and
  re-encode photographic ones as JPEG. Text, vectors, fonts, links, forms and
  bookmarks are untouched. This is where scanned and photo-heavy files get
  big wins without Ghostscript.
* ``lossless`` — recompress content streams and drop duplicate objects. Safe,
  keeps everything bit-exact, but only wins on badly written files.
* ``rasterize`` — render every page to a JPEG and rebuild. Output is images:
  selectable text is gone. Opt-in only, and it warns.
* ``ghostscript`` — if Ghostscript is on PATH, its ``-dPDFSETTINGS`` pipeline
  is the best general-purpose optimiser. Detected, never required.

``auto`` uses Ghostscript when installed and ``images`` otherwise. Whatever the
mode, the result is never larger than the input unless you ask for it: when a
strategy does not shrink the file, the original bytes are written instead and
the result says so.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import warnings
import zlib
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

from pypdf.generic import IndirectObject, NameObject, NumberObject

from ._util import (
    PdfToolsError,
    binary_streams,
    PdfToolsWarning,
    default_output,
    ensure_parent_dir,
    open_pdfium,
    open_reader,
    open_writer,
    write_pdf,
)


_GS_SETTINGS = {"screen", "ebook", "printer", "prepress", "default"}
MODES = ("auto", "images", "lossless", "rasterize", "ghostscript")


@dataclass
class CompressResult:
    """What :func:`compress_with_stats` did, for reporting."""

    output: str
    mode: str
    bytes_before: int
    bytes_after: int
    kept_original: bool = False
    images_seen: int = 0
    images_recompressed: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def saved_bytes(self) -> int:
        return self.bytes_before - self.bytes_after

    @property
    def ratio(self) -> float:
        """Output size as a fraction of the input size (0.25 = 75% smaller)."""
        return self.bytes_after / max(1, self.bytes_before)


def find_ghostscript() -> Optional[str]:
    """Return the Ghostscript executable path, or ``None`` if not installed."""
    for name in ("gs", "gswin64c", "gswin32c"):
        found = shutil.which(name)
        if found:
            return found
    return None


def probe(input_path: str, password: Optional[str] = None) -> int:
    """Return the page count, raising a friendly error if the PDF is unreadable."""
    return len(open_reader(input_path, password).pages)


# ---------------------------------------------------------------------------
# lossless
# ---------------------------------------------------------------------------

def _finish_lossless(writer) -> None:
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


def _compress_lossless(input_path: str, output: str, password: Optional[str]) -> Dict:
    writer = open_writer(input_path, password)
    _finish_lossless(writer)
    write_pdf(writer, output)
    return {}


# ---------------------------------------------------------------------------
# images: recompress embedded rasters, keep everything else
# ---------------------------------------------------------------------------

_PASSTHROUGH_FILTERS = {
    "/FlateDecode", "/Fl", "/LZWDecode", "/LZW", "/ASCII85Decode", "/A85",
    "/ASCIIHexDecode", "/AHx", "/RunLengthDecode", "/RL",
}
_KEEP_KEYS = {
    "/Type", "/Subtype", "/ColorSpace", "/Intent", "/Interpolate", "/OC",
    "/Metadata", "/StructParent", "/Name", "/ID",
}
_MIN_PIXELS = 128 * 128      # smaller images are not worth touching
_MIN_BYTES = 8 * 1024


def _filters(obj) -> List[str]:
    value = obj.get("/Filter")
    if value is None:
        return []
    value = value.get_object() if hasattr(value, "get_object") else value
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _pil_mode(obj) -> Optional[str]:
    """``"RGB"``/``"L"`` for colour spaces we can round-trip, else ``None``."""
    cs = obj.get("/ColorSpace")
    if cs is None:
        return None
    cs = cs.get_object() if hasattr(cs, "get_object") else cs
    if cs in ("/DeviceRGB", "/RGB", "/CalRGB"):
        return "RGB"
    if cs in ("/DeviceGray", "/G", "/CalGray"):
        return "L"
    if isinstance(cs, list) and len(cs) == 2 and cs[0] == "/ICCBased":
        try:
            n = int(cs[1].get_object().get("/N", 0))
        except Exception:
            return None
        return {3: "RGB", 1: "L"}.get(n)
    return None  # Indexed, CMYK, Lab, Separation, DeviceN: leave alone


def _iter_images(resources, seen_forms: set) -> Iterator[Tuple[int, object]]:
    """Yield ``(idnum, image stream)`` for every image XObject, incl. in forms."""
    if resources is None:
        return
    resources = resources.get_object()
    xobjects = resources.get("/XObject")
    if xobjects is None:
        return
    for ref in xobjects.get_object().values():
        if not isinstance(ref, IndirectObject):
            continue
        obj = ref.get_object()
        subtype = obj.get("/Subtype")
        if subtype == "/Image":
            yield ref.idnum, obj
        elif subtype == "/Form" and ref.idnum not in seen_forms:
            seen_forms.add(ref.idnum)
            yield from _iter_images(obj.get("/Resources"), seen_forms)


def _placement_dpi(input_path: str, password: Optional[str]) -> List[Dict[Tuple[int, int], float]]:
    """Per page: image pixel size -> lowest effective DPI it is shown at.

    pdfium knows where each image lands on the page (its transform), which is
    what turns "4000 px wide" into "shown 3 inches wide = 1333 dpi".
    """
    import pypdfium2.raw as pdfium_c

    pdf = open_pdfium(input_path, password)
    try:
        result: List[Dict[Tuple[int, int], float]] = []
        for idx in range(len(pdf)):
            page = pdf[idx]
            seen: Dict[Tuple[int, int], float] = {}
            try:
                for img in page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,), max_depth=15):
                    try:
                        meta = img.get_metadata()
                    except Exception:
                        continue
                    dpi = min(float(meta.horizontal_dpi), float(meta.vertical_dpi))
                    if dpi <= 0:
                        continue
                    key = (int(meta.width), int(meta.height))
                    seen[key] = min(dpi, seen.get(key, dpi))
            finally:
                page.close()
            result.append(seen)
        return result
    finally:
        pdf.close()


def _looks_photographic(img) -> bool:
    """Many distinct colours = photo or scan (JPEG-friendly); few = graphic.

    Flat artwork, charts and screenshots-of-UI have a handful of colours and
    compress well (and exactly) with Flate; JPEG would smear their edges.
    Greyscale only has 256 levels, so it gets its own, lower threshold.
    """
    from PIL import Image

    sample = img if img.width * img.height <= 128 * 128 else img.resize((128, 128), Image.NEAREST)
    limit = 48 if sample.mode == "L" else 1024
    return sample.getcolors(maxcolors=limit) is None


def _decode(obj, mode: str, width: int, height: int):
    from PIL import Image

    filters = _filters(obj)
    if filters in (["/DCTDecode"], ["/DCT"]):
        img = Image.open(io.BytesIO(obj._data))  # noqa: SLF001 - raw encoded bytes
        img.load()
        if img.mode != mode or img.size != (width, height):
            return None  # CMYK / Adobe-inverted or mismatched JPEGs: leave alone
        return img
    if all(f in _PASSTHROUGH_FILTERS for f in filters):
        raw = obj.get_data()
        need = width * height * (3 if mode == "RGB" else 1)
        if len(raw) < need:
            return None
        return Image.frombytes(mode, (width, height), raw[:need])
    return None  # JPX, JBIG2, CCITT, chained DCT: not ours to touch


def _recompress(obj, dpi: float, max_dpi: int, quality: int) -> Optional[Tuple[bytes, str, int, int]]:
    from PIL import Image

    if obj.get("/ImageMask") or "/SMask" in obj or "/Mask" in obj or "/Decode" in obj:
        return None  # masked / stencil / inverted images keep their exact pixels
    if int(obj.get("/BitsPerComponent", 8)) != 8:
        return None  # 1-bit scans are already tiny with their own codecs
    mode = _pil_mode(obj)
    if mode is None:
        return None
    width, height = int(obj["/Width"]), int(obj["/Height"])
    original = len(obj._data)  # noqa: SLF001
    if width * height < _MIN_PIXELS or original < _MIN_BYTES:
        return None

    try:
        img = _decode(obj, mode, width, height)
    except Exception:
        return None
    if img is None:
        return None

    if dpi > max_dpi * 1.05:
        factor = max_dpi / dpi
        size = (max(1, round(width * factor)), max(1, round(height * factor)))
        img = img.resize(size, Image.LANCZOS)

    if _looks_photographic(img):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        data, filt = buf.getvalue(), "/DCTDecode"
    else:
        data, filt = zlib.compress(img.tobytes(), 9), "/FlateDecode"
    if len(data) >= original:
        return None  # never trade an image for a bigger one
    return data, filt, img.width, img.height


def _replace_stream(obj, data: bytes, filt: str, width: int, height: int) -> None:
    for key in list(obj.keys()):
        if key not in _KEEP_KEYS:
            del obj[key]
    obj[NameObject("/Type")] = NameObject("/XObject")
    obj[NameObject("/Subtype")] = NameObject("/Image")
    obj[NameObject("/Width")] = NumberObject(width)
    obj[NameObject("/Height")] = NumberObject(height)
    obj[NameObject("/BitsPerComponent")] = NumberObject(8)
    obj[NameObject("/Filter")] = NameObject(filt)
    obj._data = data  # noqa: SLF001 - already-encoded bytes, written verbatim
    if hasattr(obj, "decoded_self"):
        obj.decoded_self = None


def _compress_images(
    input_path: str,
    output: str,
    max_dpi: int,
    jpeg_quality: int,
    password: Optional[str],
) -> Dict:
    if max_dpi < 10:
        raise PdfToolsError("max_dpi must be at least 10.")
    writer = open_writer(input_path, password)
    dpi_by_page = _placement_dpi(input_path, password)

    images: Dict[int, Tuple[object, float]] = {}
    for idx, page in enumerate(writer.pages):
        box = page.mediabox
        longest_in = max(float(box.width), float(box.height)) / 72.0 or 1.0
        placements = dpi_by_page[idx] if idx < len(dpi_by_page) else {}
        for idnum, obj in _iter_images(page.get("/Resources"), set()):
            size = (int(obj.get("/Width", 0)), int(obj.get("/Height", 0)))
            dpi = placements.get(size)
            if dpi is None:
                # Not located on the page: assume it is at most page-sized,
                # which gives a lower bound on its DPI (the safe direction).
                dpi = max(size) / longest_in
            prev = images.get(idnum)
            images[idnum] = (obj, dpi if prev is None else min(prev[1], dpi))

    recompressed = 0
    for obj, dpi in images.values():
        result = _recompress(obj, dpi, max_dpi, jpeg_quality)
        if result is not None:
            _replace_stream(obj, *result)
            recompressed += 1

    _finish_lossless(writer)
    write_pdf(writer, output)
    return {"images_seen": len(images), "images_recompressed": recompressed}


# ---------------------------------------------------------------------------
# rasterize
# ---------------------------------------------------------------------------

def _compress_rasterize(
    input_path: str, output: str, dpi: int, jpeg_quality: int, password: Optional[str]
) -> Dict:
    warnings.warn(
        "rasterize replaces every page with a JPEG picture of it: selectable "
        "text, links, form fields and bookmarks are lost.",
        PdfToolsWarning,
        stacklevel=3,
    )
    ensure_parent_dir(output)
    pdf = open_pdfium(input_path, password)
    try:
        _rasterize_pages(pdf, output, dpi, jpeg_quality)
    finally:
        pdf.close()
    return {}


def _rasterize_pages(pdf, output: str, dpi: int, jpeg_quality: int) -> None:
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    with binary_streams():
        c = canvas.Canvas(output)
        scale = dpi / 72.0
        for idx in range(len(pdf)):
            page = pdf[idx]
            width, height = page.get_size()  # points, as displayed
            bitmap = page.render(scale=scale)
            pil_image = bitmap.to_pil().convert("RGB")
            jpg_buf = io.BytesIO()
            pil_image.save(jpg_buf, format="JPEG", quality=jpeg_quality, optimize=True)
            jpg_buf.seek(0)
            c.setPageSize((width, height))
            c.drawImage(ImageReader(jpg_buf), 0, 0, width, height)
            c.showPage()
        c.save()


# ---------------------------------------------------------------------------
# ghostscript
# ---------------------------------------------------------------------------

def _compress_ghostscript(
    input_path: str, output: str, gs_quality: str, password: Optional[str]
) -> Dict:
    if gs_quality not in _GS_SETTINGS:
        raise PdfToolsError(
            f"gs_quality must be one of {', '.join(sorted(_GS_SETTINGS))}."
        )
    gs = find_ghostscript()
    if not gs:
        raise PdfToolsError(
            "Ghostscript not found on PATH. Install it (gs / gswin64c) or use "
            "mode='images', 'lossless' or 'rasterize'."
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
    ]
    if password:
        cmd.append(f"-sPDFPassword={password}")
    cmd.append(input_path)
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:  # pragma: no cover - env specific
        raise PdfToolsError(f"Ghostscript failed with exit code {exc.returncode}.") from exc
    return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compress_with_stats(
    input_path: str,
    output: Optional[str] = None,
    *,
    mode: str = "auto",
    dpi: int = 150,
    jpeg_quality: Optional[int] = None,
    gs_quality: str = "ebook",
    max_dpi: int = 150,
    allow_larger: bool = False,
    password: Optional[str] = None,
) -> CompressResult:
    """Shrink a PDF and report what happened. See :func:`compress`."""
    if mode not in MODES:
        raise PdfToolsError(
            f"Unknown mode {mode!r}. Use {', '.join(MODES)}."
        )
    probe(input_path, password)  # clean error for missing / damaged / locked input
    output = output or default_output(input_path, "compressed")

    if mode == "auto":
        mode = "ghostscript" if find_ghostscript() else "images"
    if jpeg_quality is None:
        jpeg_quality = 70 if mode == "images" else 60
    if not 1 <= int(jpeg_quality) <= 95:
        raise PdfToolsError("jpeg_quality must be between 1 and 95.")

    ensure_parent_dir(output)
    fd, tmp = tempfile.mkstemp(
        suffix=".pdf", prefix=".pdftool-", dir=os.path.dirname(os.path.abspath(output))
    )
    os.close(fd)
    try:
        if mode == "images":
            stats = _compress_images(input_path, tmp, max_dpi, jpeg_quality, password)
        elif mode == "lossless":
            stats = _compress_lossless(input_path, tmp, password)
        elif mode == "rasterize":
            stats = _compress_rasterize(input_path, tmp, dpi, jpeg_quality, password)
        else:
            stats = _compress_ghostscript(input_path, tmp, gs_quality, password)

        before = os.path.getsize(input_path)
        produced = os.path.getsize(tmp)
        result = CompressResult(output=output, mode=mode, bytes_before=before,
                                bytes_after=produced, **stats)
        if produced >= before and not allow_larger:
            result.kept_original = True
            result.bytes_after = before
            result.notes.append(
                f"{mode} produced {produced} bytes, not smaller than the "
                f"{before}-byte input; kept the original bytes "
                "(allow_larger=True / --allow-larger writes it anyway)."
            )
            if os.path.abspath(output) != os.path.abspath(input_path):
                shutil.copyfile(input_path, output)
            os.remove(tmp)
        else:
            os.replace(tmp, output)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return result


def compress(
    input_path: str,
    output: Optional[str] = None,
    *,
    mode: str = "auto",
    dpi: int = 150,
    jpeg_quality: Optional[int] = None,
    gs_quality: str = "ebook",
    max_dpi: int = 150,
    allow_larger: bool = False,
    password: Optional[str] = None,
) -> str:
    """Shrink a PDF using the chosen strategy.

    ``mode``:

    * ``auto`` — Ghostscript if present, otherwise ``images``.
    * ``images`` — downsample embedded images shown above ``max_dpi`` and
      re-encode photographic ones as JPEG at ``jpeg_quality`` (default 70);
      masked, 1-bit,
      indexed, CMYK and tiny images are left alone, and an image is only
      replaced when the new encoding is smaller. Text and vectors are kept.
    * ``lossless`` — stream cleanup and object de-duplication only.
    * ``rasterize`` — render to JPEG pages at ``dpi`` / ``jpeg_quality``
      (default 60; loses selectable text and emits a
      :class:`~pdftools._util.PdfToolsWarning`).
    * ``ghostscript`` — force the Ghostscript path with ``gs_quality`` one of
      screen, ebook, printer, prepress, default.

    If the result would not be smaller than the input, the original bytes are
    written instead, unless ``allow_larger=True``. Use
    :func:`compress_with_stats` to learn what happened. Returns the output
    path.
    """
    return compress_with_stats(
        input_path,
        output,
        mode=mode,
        dpi=dpi,
        jpeg_quality=jpeg_quality,
        gs_quality=gs_quality,
        max_dpi=max_dpi,
        allow_larger=allow_larger,
        password=password,
    ).output
