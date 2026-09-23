"""Command-line interface for pdf-power-tools.

One ``pdftool`` binary with a subcommand per operation. Conventions:

* Every input accepts literal paths and glob patterns. ``merge`` and
  ``img2pdf`` combine their inputs into one file; every other command runs
  once per input (batch mode) when given several files or a glob.
* ``-o/--output`` is a file for single-output commands, a directory for
  commands that fan out (split, tables, images, pdf2img) and for batches.
* ``--dry-run`` prints the plan and writes nothing; ``--password`` opens
  encrypted inputs; ``-q`` silences progress; ``--debug`` shows tracebacks.
  These work before or after the subcommand.
* Exit codes: 0 success, 2 bad input or a failed file in a batch, 1 an
  unexpected internal error, 130 interrupted.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import warnings
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from . import __version__
from ._util import (
    PdfToolsError,
    PdfToolsWarning,
    default_output,
    expand_inputs,
    is_glob,
)
from .compress import MODES, compress_with_stats, find_ghostscript
from .ocr import ocr_with_stats
from .ops_content import (
    _rows_to_markdown,
    extract_images,
    extract_tables,
    extract_text,
    get_metadata,
    info,
    set_metadata,
)
from .ops_forms import fill, list_fields
from .ops_pages import delete_pages, merge, n_up, reorder, rotate, split
from .ops_secure import PERMISSIONS, decrypt, encrypt, strip_metadata
from .ops_visual import images_to_pdf, page_numbers, pdf_to_images, watermark


def _load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from a local .env into os.environ if present.

    Dependency-free on purpose: this tool needs at most one optional variable
    (TESSERACT_CMD), so pulling in python-dotenv would be overkill. Values
    already set in the real environment always win.
    """
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:  # pragma: no cover - unreadable .env is non-fatal
        pass


def _echo(args, message: str) -> None:
    if not getattr(args, "quiet", False):
        print(message)


def _size(path_or_bytes) -> str:
    if isinstance(path_or_bytes, int):
        n = float(path_or_bytes)
    else:
        try:
            n = float(os.path.getsize(path_or_bytes))
        except OSError:
            return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"  # pragma: no cover


def _dry(args, description: str) -> bool:
    if args.dry_run:
        _echo(args, f"[dry-run] {description}")
        return True
    return False


def _dir(path: Optional[str]) -> str:
    """Show a directory with exactly one trailing slash."""
    return (path or "").rstrip("/\\") + "/"


# ---------------------------------------------------------------------------
# Error reporting shared by single runs and batches
# ---------------------------------------------------------------------------

def _error_message(exc: BaseException) -> Optional[str]:
    """A one-line message for errors caused by the input, else ``None``."""
    if isinstance(exc, PdfToolsError):
        return str(exc)
    try:
        from pypdf.errors import FileNotDecryptedError, PdfReadError, WrongPasswordError

        if isinstance(exc, WrongPasswordError):
            return "wrong password for an encrypted input."
        if isinstance(exc, FileNotDecryptedError):
            return "an input is password-protected; pass --password."
        if isinstance(exc, PdfReadError):
            return f"could not read PDF ({exc})."
    except ImportError:  # pragma: no cover
        pass
    try:
        from PIL import UnidentifiedImageError

        if isinstance(exc, UnidentifiedImageError):
            return f"not a readable image ({exc})."
    except ImportError:  # pragma: no cover
        pass
    try:
        from pypdfium2 import PdfiumError

        if isinstance(exc, PdfiumError):
            return f"could not read PDF ({exc})."
    except ImportError:  # pragma: no cover
        pass
    if isinstance(exc, FileNotFoundError):
        return f"file not found: {exc.filename or exc}"
    if isinstance(exc, json.JSONDecodeError):
        return f"invalid JSON ({exc})."
    if isinstance(exc, OSError):
        where = f": {exc.filename}" if getattr(exc, "filename", None) else ""
        return f"{exc.strerror or exc}{where}"
    return None


# ---------------------------------------------------------------------------
# Per-input commands and the batch runner
# ---------------------------------------------------------------------------

@dataclass
class Spec:
    """How a single-input command names its output and does its work.

    ``kind`` is ``"file"`` (one output file per input), ``"dir"`` (a folder
    per input), ``"stdout"`` (prints; ``-o`` optionally writes a file) or
    ``"report"`` (prints only).
    """

    kind: str
    run: Callable
    describe: Callable
    suffix: str = ""
    ext: str = ".pdf"
    dir_suffix: str = ""


def _output_for(args, spec: Spec, src: str, batch: bool, used: Dict[str, int]) -> Optional[str]:
    base = os.path.splitext(src)[0]
    out = getattr(args, "output", None)
    suffix = spec.suffix(args) if callable(spec.suffix) else spec.suffix
    ext = spec.ext(args) if callable(spec.ext) else spec.ext
    if spec.kind == "report":
        return None
    if not batch:
        if spec.kind == "file":
            return out or default_output(src, suffix, ext)
        if spec.kind == "dir":
            return out or (base + spec.dir_suffix if spec.dir_suffix else None)
        return out  # stdout kind: a file only when -o is given
    if not out:
        if spec.kind == "file":
            return default_output(src, suffix, ext)
        if spec.kind == "dir":
            return base + spec.dir_suffix if spec.dir_suffix else None
        return None

    # Several inputs into one -o directory. Two inputs with the same file name
    # (from different folders) must not overwrite each other.
    stem = os.path.splitext(os.path.basename(src))[0]
    key = stem.casefold()
    used[key] = used.get(key, 0) + 1
    if used[key] > 1:
        stem = f"{stem}-{used[key]}"
    if spec.kind == "file":
        name = f"{stem}.{suffix}{ext}"
    elif spec.kind == "dir":
        name = stem
    else:
        name = f"{stem}.txt"
    return os.path.join(out, name)


def _run_inputs(args, spec: Spec) -> int:
    files = expand_inputs(args.inputs)
    batch = len(files) > 1 or any(is_glob(p) for p in args.inputs)
    used: Dict[str, int] = {}
    jobs = [(src, _output_for(args, spec, src, batch, used)) for src in files]

    # Only commands that write files have a plan to show; printing ones
    # (info, fields, text/tables without -o) just run.
    writes = spec.kind != "report" and any(out is not None for _src, out in jobs)

    if not batch:
        src, out = jobs[0]
        if writes and _dry(args, spec.describe(args, src, out)):
            return 0
        spec.run(args, src, out, batch=False)
        return 0

    if writes and args.dry_run:
        for src, out in jobs:
            _dry(args, spec.describe(args, src, out))
        _echo(args, f"[dry-run] {len(jobs)} file(s) planned; nothing written.")
        return 0

    failed: List[str] = []
    for src, out in jobs:
        try:
            spec.run(args, src, out, batch=True)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # report and keep going with the batch
            if args.debug:
                raise
            message = _error_message(exc) or f"unexpected {exc.__class__.__name__}: {exc}"
            sys.stdout.flush()  # keep progress and errors in order on a terminal
            print(f"error: {src}: {message}", file=sys.stderr)
            failed.append(src)
    ok = len(jobs) - len(failed)
    summary = f"{ok} ok, {len(failed)} failed"
    if failed:
        print(summary, file=sys.stderr)
        return 2
    if spec.kind != "report":
        _echo(args, summary)
    return 0


# -- page commands ----------------------------------------------------------

def _run_rotate(args, src, out, batch):
    out = rotate(src, out, pages=args.pages, angle=args.angle, password=args.password)
    _echo(args, f"Rotated -> {out}")


def _run_delete(args, src, out, batch):
    out = delete_pages(src, out, pages=args.pages, password=args.password)
    _echo(args, f"Deleted pages {args.pages} -> {out}")


def _run_reorder(args, src, out, batch):
    out = reorder(src, out, order=args.order, password=args.password)
    _echo(args, f"Reordered -> {out}")


def _run_nup(args, src, out, batch):
    out = n_up(src, out, n=args.n, password=args.password)
    _echo(args, f"Imposed {args.n}-up -> {out}")


def _run_split(args, src, out, batch):
    outputs = split(
        src,
        out,
        ranges=args.ranges,
        every_n=args.every,
        by_bookmark=args.by_bookmark,
        password=args.password,
    )
    _echo(args, f"Wrote {len(outputs)} file(s) to {_dir(out)}")
    for path in outputs:
        _echo(args, f"  {os.path.basename(path)} ({_size(path)})")


# -- content commands -------------------------------------------------------

def _run_text(args, src, out, batch):
    result = extract_text(
        src,
        out,
        pages=args.pages,
        joined=not args.no_join,
        layout=args.layout,
        password=args.password,
    )
    if out:
        _echo(args, f"Text -> {result}")
        return
    if batch:
        print(f"==> {src} <==")
    if isinstance(result, list):
        print(("\n\n" + "-" * 40 + "\n\n").join(result))
    else:
        print(result)


def _run_tables(args, src, out, batch):
    records = extract_tables(src, out, pages=args.pages, fmt=args.format, password=args.password)
    if batch and not out:
        print(f"==> {src} <==")
    if not records:
        _echo(args, "No tables detected.")
        return
    if out:
        _echo(args, f"Extracted {len(records)} table(s) to {_dir(out)}")
        for rec in records:
            if rec["path"]:
                _echo(args, f"  {os.path.basename(rec['path'])}")
        return
    for rec in records:
        print(f"# page {rec['page']} table {rec['index']}")
        print(_rows_to_markdown(rec["rows"]))
        print()


def _run_images(args, src, out, batch):
    paths = extract_images(src, out, pages=args.pages, password=args.password)
    _echo(args, f"Extracted {len(paths)} image(s) to {_dir(out)}")


def _meta_is_set(args) -> bool:
    return any(v is not None for v in (args.title, args.author, args.subject, args.keywords))


def _run_meta_set(args, src, out, batch):
    out = set_metadata(
        src,
        out,
        title=args.title,
        author=args.author,
        subject=args.subject,
        keywords=args.keywords,
        password=args.password,
    )
    _echo(args, f"Metadata written -> {out}")


def _run_meta_get(args, src, out, batch):
    meta = get_metadata(src, password=args.password)
    if batch:
        print(json.dumps({"path": src, **meta}, ensure_ascii=False))
    else:
        print(json.dumps(meta, indent=2, ensure_ascii=False))


def _run_info(args, src, out, batch):
    payload = info(src, password=args.password)
    if batch:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))


# -- security commands ------------------------------------------------------

def _permissions_arg(args) -> Optional[List[str]]:
    if not args.allow:
        return None
    names: List[str] = []
    for chunk in args.allow:
        names.extend(p for p in chunk.split(",") if p.strip())
    return names


def _run_encrypt(args, src, out, batch):
    out = encrypt(
        src,
        out,
        user_password=args.user_password,
        owner_password=args.owner_password,
        algorithm=args.algorithm,
        permissions=_permissions_arg(args),
        password=args.password,
    )
    _echo(args, f"Encrypted ({args.algorithm}) -> {out}")


def _run_decrypt(args, src, out, batch):
    out = decrypt(src, out, password=args.password or "")
    _echo(args, f"Decrypted -> {out}")


def _run_scrub(args, src, out, batch):
    out = strip_metadata(
        src, out, flatten_annotations=args.flatten_annotations, password=args.password
    )
    _echo(args, f"Scrubbed metadata -> {out}")


# -- visual commands --------------------------------------------------------

def _run_watermark(args, src, out, batch):
    out = watermark(
        src,
        out,
        text=args.text,
        image=args.image,
        angle=args.angle,
        opacity=args.opacity,
        font_size=args.font_size,
        color=args.color,
        tiled=args.tiled,
        position=args.position,
        scale=args.scale,
        password=args.password,
    )
    _echo(args, f"Watermarked -> {out}")


def _run_numbers(args, src, out, batch):
    out = page_numbers(
        src,
        out,
        position=args.position,
        fmt=args.format,
        font_size=args.font_size,
        color=args.color,
        skip_first=args.skip_first,
        start_at=args.start_at,
        password=args.password,
    )
    _echo(args, f"Numbered -> {out}")


def _run_pdf2img(args, src, out, batch):
    paths = pdf_to_images(
        src, out, dpi=args.dpi, fmt=args.format, pages=args.pages, password=args.password
    )
    _echo(args, f"Rendered {len(paths)} page(s) to {_dir(out)}")


# -- forms ------------------------------------------------------------------

def _run_fields(args, src, out, batch):
    fields = list_fields(src, password=args.password)
    if args.json:
        payload = {"path": src, "fields": fields} if batch else fields
        print(json.dumps(payload, indent=None if batch else 2, ensure_ascii=False))
        return
    if batch:
        print(f"==> {src} <==")
    if not fields:
        _echo(args, "No form fields found.")
        return
    width = max(len(f["name"]) for f in fields)
    for f in fields:
        value = "" if f["value"] is None else f["value"]
        print(f"{f['name']:<{width}}  {f['type']:<9}  {value}")


def _run_fill(args, src, out, batch):
    out = fill(src, out, args.data, flatten=args.flatten, strict=args.strict, password=args.password)
    _echo(args, f"Filled{' + flattened' if args.flatten else ''} -> {out}")


# -- compress + OCR ---------------------------------------------------------

def _run_compress(args, src, out, batch):
    result = compress_with_stats(
        src,
        out,
        mode=args.mode,
        dpi=args.dpi,
        jpeg_quality=args.jpeg_quality,
        gs_quality=args.gs_quality,
        max_dpi=args.max_dpi,
        allow_larger=args.allow_larger,
        password=args.password,
    )
    details = [f"mode={result.mode}"]
    if result.mode == "images":
        details.append(f"{result.images_recompressed}/{result.images_seen} images recompressed")
    if result.kept_original:
        _echo(
            args,
            f"Kept the original {_size(result.bytes_before)}: {result.mode} did not "
            f"make it smaller ({', '.join(details)}; --allow-larger writes it anyway) "
            f"-> {result.output}",
        )
        return
    ratio = result.ratio
    pct = f"{(1 - ratio) * 100:.0f}% smaller" if ratio <= 1 else f"{(ratio - 1) * 100:.0f}% larger"
    _echo(
        args,
        f"Compressed {_size(result.bytes_before)} -> {_size(result.bytes_after)} "
        f"({pct}, saved {_size(max(0, result.saved_bytes))}; {', '.join(details)}) "
        f"-> {result.output}",
    )


def _run_ocr(args, src, out, batch):
    result = ocr_with_stats(
        src,
        out,
        mode=args.mode,
        lang=args.lang,
        dpi=args.dpi,
        pages=args.pages,
        skip_text=not args.force,
        password=args.password,
    )
    skipped = (
        f", skipped {len(result.pages_skipped)} page(s) that already had text"
        if result.pages_skipped else ""
    )
    _echo(
        args,
        f"OCR ({result.mode}): {len(result.pages_ocred)} page(s), {result.words} "
        f"word(s){skipped} -> {result.output}",
    )


SPECS: Dict[str, Spec] = {
    "rotate": Spec("file", _run_rotate, lambda a, s, o: f"rotate {s} by {a.angle} -> {o}", "rotated"),
    "delete": Spec("file", _run_delete, lambda a, s, o: f"delete pages {a.pages} from {s} -> {o}", "trimmed"),
    "reorder": Spec("file", _run_reorder, lambda a, s, o: f"reorder {s} as {a.order} -> {o}", "reordered"),
    "nup": Spec("file", _run_nup, lambda a, s, o: f"{a.n}-up impose {s} -> {o}", lambda a: f"{a.n}up"),
    "split": Spec("dir", _run_split, lambda a, s, o: f"split {s} -> {_dir(o)}", dir_suffix="_parts"),
    "text": Spec("stdout", _run_text, lambda a, s, o: f"extract text {s} -> {o}"),
    "tables": Spec("dir", _run_tables, lambda a, s, o: f"extract tables {s} -> {_dir(o)}"),
    "images": Spec("dir", _run_images, lambda a, s, o: f"extract images {s} -> {_dir(o)}", dir_suffix="_images"),
    "meta-set": Spec("file", _run_meta_set, lambda a, s, o: f"set metadata on {s} -> {o}", "meta"),
    "meta-get": Spec("report", _run_meta_get, lambda a, s, o: ""),
    "info": Spec("report", _run_info, lambda a, s, o: ""),
    "encrypt": Spec("file", _run_encrypt, lambda a, s, o: f"encrypt {s} ({a.algorithm}) -> {o}", "encrypted"),
    "decrypt": Spec("file", _run_decrypt, lambda a, s, o: f"decrypt {s} -> {o}", "decrypted"),
    "scrub": Spec("file", _run_scrub, lambda a, s, o: f"strip metadata from {s} -> {o}", "clean"),
    "watermark": Spec("file", _run_watermark, lambda a, s, o: f"watermark {s} -> {o}", "watermarked"),
    "numbers": Spec("file", _run_numbers, lambda a, s, o: f"number pages of {s} -> {o}", "numbered"),
    "pdf2img": Spec("dir", _run_pdf2img, lambda a, s, o: f"render {s} @ {a.dpi}dpi -> {_dir(o)}", dir_suffix="_pages"),
    "fields": Spec("report", _run_fields, lambda a, s, o: ""),
    "fill": Spec("file", _run_fill, lambda a, s, o: f"fill form {s} from {a.data} -> {o}", "filled"),
    "compress": Spec(
        "file", _run_compress,
        lambda a, s, o: f"compress {s} (mode={a.mode}) -> {o}", "compressed",
    ),
    "ocr": Spec(
        "file", _run_ocr,
        lambda a, s, o: f"OCR {s} (mode={a.mode}, lang={a.lang}) -> {o}",
        lambda a: "ocr" if a.mode == "sidecar" else "searchable",
        lambda a: ".txt" if a.mode == "sidecar" else ".pdf",
    ),
}


def _per_input(name: str) -> Callable:
    def handler(args) -> int:
        return _run_inputs(args, SPECS[name])

    handler.__name__ = f"cmd_{name.replace('-', '_')}"
    return handler


def cmd_meta(args) -> int:
    return _run_inputs(args, SPECS["meta-set" if _meta_is_set(args) else "meta-get"])


# ---------------------------------------------------------------------------
# Many-inputs-to-one-output commands
# ---------------------------------------------------------------------------

def cmd_merge(args) -> int:
    files = expand_inputs(args.inputs)
    if _dry(args, f"merge {len(files)} file(s) -> {args.output}"):
        for path in files:
            _echo(args, f"  {path}")
        return 0
    out = merge(
        files, args.output, bookmark_per_source=not args.no_bookmarks, password=args.password
    )
    _echo(args, f"Merged {len(files)} file(s) -> {out} ({_size(out)})")
    return 0


def cmd_img2pdf(args) -> int:
    files = expand_inputs(args.inputs)
    if _dry(args, f"combine {len(files)} image(s) -> {args.output}"):
        return 0
    out = images_to_pdf(
        files, args.output, page_size=args.page_size, fit=args.fit, margin=args.margin
    )
    _echo(args, f"Built PDF from {len(files)} image(s) -> {out} ({_size(out)})")
    return 0


def cmd_doctor(args) -> int:
    """Report which optional dependencies are available."""
    print(f"pdftool {__version__}")
    checks = []

    def probe(label, module):
        try:
            mod = __import__(module)
            version = getattr(mod, "__version__", None) or getattr(mod, "PYPDFIUM_INFO", None)
            if version is None and module == "reportlab":
                version = getattr(mod, "Version", None)
            checks.append((label, f"ok {version}" if version else "ok"))
        except Exception as exc:  # pragma: no cover - environment dependent
            checks.append((label, f"missing ({exc.__class__.__name__})"))

    probe("pypdf", "pypdf")
    probe("pdfplumber", "pdfplumber")
    probe("Pillow", "PIL")
    probe("pypdfium2", "pypdfium2")
    probe("reportlab", "reportlab")

    gs = find_ghostscript()
    checks.append(("ghostscript", gs if gs else "not on PATH (optional; compress uses mode=images)"))

    def tesseract_check():
        import pytesseract

        cmd = os.environ.get("TESSERACT_CMD")
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        return pytesseract.get_tesseract_version()

    try:
        checks.append(("tesseract (OCR)", f"ok {tesseract_check()}"))
    except Exception as exc:  # pragma: no cover - environment dependent
        checks.append(("tesseract (OCR)", f"missing ({exc.__class__.__name__}; only `ocr` needs it)"))

    width = max(len(label) for label, _ in checks)
    for label, status in checks:
        print(f"  {label:<{width}}  {status}")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _global_options(parser: argparse.ArgumentParser, suppress: bool) -> None:
    """Options accepted both before and after the subcommand.

    On subcommands their default is SUPPRESS, so an option given before the
    subcommand is not reset by the subcommand's parser.
    """
    def default(value):
        return argparse.SUPPRESS if suppress else value

    parser.add_argument("--dry-run", action="store_true", default=default(False),
                        help="Print the plan; write nothing.")
    parser.add_argument("-q", "--quiet", action="store_true", default=default(False),
                        help="Suppress progress messages.")
    parser.add_argument("--password", default=default(None),
                        help="User or owner password for encrypted inputs.")
    parser.add_argument("--debug", action="store_true", default=default(False),
                        help="Show full tracebacks and library log messages.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdftool",
        description="One CLI for everything PDF: merge, split, compress, "
        "watermark, encrypt, extract, fill forms and OCR. Every input accepts "
        "globs; several inputs run as a batch.",
    )
    parser.add_argument("--version", action="version", version=f"pdftool {__version__}")
    _global_options(parser, suppress=False)

    common = argparse.ArgumentParser(add_help=False)
    _global_options(common, suppress=True)

    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    def add(name: str, help_text: str, func) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, parents=[common], description=help_text)
        p.set_defaults(func=func)
        return p

    def inputs(p, what="Input PDF(s) or glob patterns."):
        p.add_argument("inputs", nargs="+", metavar="input", help=what)

    batch_out = "Output file (one input) or directory (several inputs / a glob)."

    # merge
    p = add("merge", "Concatenate PDFs, keeping forms and bookmarks (globs allowed).", cmd_merge)
    p.add_argument("inputs", nargs="+", metavar="input", help="Input PDFs or glob patterns.")
    p.add_argument("-o", "--output", required=True, help="Output PDF path.")
    p.add_argument("--no-bookmarks", action="store_true",
                   help="Do not add a bookmark per source (source bookmarks are kept).")

    # split
    p = add("split", "Split PDF(s) into several files.", _per_input("split"))
    inputs(p)
    p.add_argument("-o", "--output", help="Output directory (default: <name>_parts).")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--ranges", help='Comma groups, e.g. "1-3,7,9-". Each group -> one file.')
    g.add_argument("--every", type=int, metavar="N", help="Fixed chunks of N pages.")
    g.add_argument("--by-bookmark", action="store_true", help="One file per top-level bookmark.")

    # rotate
    p = add("rotate", "Rotate pages by a multiple of 90.", _per_input("rotate"))
    inputs(p)
    p.add_argument("-o", "--output", help=batch_out)
    p.add_argument("--pages", help='Range spec, e.g. "1-3,7". Default: all pages.')
    p.add_argument("--angle", type=int, default=90, help="Clockwise degrees (default 90).")

    # delete
    p = add("delete", "Delete pages by range spec.", _per_input("delete"))
    inputs(p)
    p.add_argument("-o", "--output", help=batch_out)
    p.add_argument("--pages", required=True, help='Pages to remove, e.g. "2,5-6".')

    # reorder
    p = add("reorder", "Rebuild in an explicit page order (or keep only some pages).",
            _per_input("reorder"))
    inputs(p)
    p.add_argument("-o", "--output", help=batch_out)
    p.add_argument("--order", required=True, help='New order, e.g. "3,1,2,4-" or "1-3,7".')

    # nup
    p = add("nup", "Impose 2 or 4 pages per sheet.", _per_input("nup"))
    inputs(p)
    p.add_argument("-o", "--output", help=batch_out)
    p.add_argument("-n", type=int, choices=(2, 4), default=2, help="Pages per sheet.")

    # text
    p = add("text", "Extract selectable text.", _per_input("text"))
    inputs(p)
    p.add_argument("-o", "--output",
                   help="Write to a .txt file (one input) or a directory (batch) "
                   "instead of stdout.")
    p.add_argument("--pages", help="Range spec. Default: all pages.")
    p.add_argument("--layout", action="store_true", help="Use pdfplumber layout mode.")
    p.add_argument("--no-join", action="store_true", help="Keep pages separate on stdout.")

    # tables
    p = add("tables", "Extract tables to CSV or Markdown.", _per_input("tables"))
    inputs(p)
    p.add_argument("-o", "--output", help="Output directory. Omit to print Markdown.")
    p.add_argument("--pages", help="Range spec. Default: all pages.")
    p.add_argument("--format", choices=("csv", "markdown"), default="csv")

    # images
    p = add("images", "Extract embedded raster images.", _per_input("images"))
    inputs(p)
    p.add_argument("-o", "--output", help="Output directory (default: <name>_images).")
    p.add_argument("--pages", help="Range spec. Default: all pages.")

    # meta
    p = add("meta", "Get metadata (no flags) or set it (with flags).", cmd_meta)
    inputs(p)
    p.add_argument("-o", "--output", help="Output PDF (or directory) when setting metadata.")
    p.add_argument("--title")
    p.add_argument("--author")
    p.add_argument("--subject")
    p.add_argument("--keywords")

    # info
    p = add("info", "Print a JSON summary (version, pages, text/OCR hint, encryption).",
            _per_input("info"))
    inputs(p)

    # encrypt
    p = add("encrypt", "Encrypt with a password and permission flags.", _per_input("encrypt"))
    inputs(p)
    p.add_argument("-o", "--output", help=batch_out)
    p.add_argument("--user-password", default="", help="Password to open the file.")
    p.add_argument("--owner-password", default=None,
                   help="Password that lifts the restrictions (full permissions).")
    p.add_argument(
        "--allow", action="append", metavar="PERMS",
        help="What the user password allows: all, none, or a comma list of "
        + ", ".join(PERMISSIONS)
        + ". Default: print,print-hq,accessibility when the owner password "
        "differs from the user password, otherwise all.",
    )
    p.add_argument(
        "--algorithm",
        default="AES-256",
        choices=("RC4-40", "RC4-128", "AES-128", "AES-256"),
    )

    # decrypt
    p = add("decrypt", "Remove encryption (give --password).", _per_input("decrypt"))
    inputs(p)
    p.add_argument("-o", "--output", help=batch_out)

    # scrub
    p = add("scrub", "Strip metadata (info dict, XMP) for privacy.", _per_input("scrub"))
    inputs(p)
    p.add_argument("-o", "--output", help=batch_out)
    p.add_argument(
        "--flatten-annotations",
        action="store_true",
        help="Also remove annotations and form widgets that may carry names.",
    )

    # watermark
    p = add("watermark", "Stamp a text or image watermark.", _per_input("watermark"))
    inputs(p)
    p.add_argument("-o", "--output", help=batch_out)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--text", help="Watermark text.")
    g.add_argument("--image", help="Logo/stamp image path.")
    p.add_argument("--angle", type=float, default=45.0)
    p.add_argument("--opacity", type=float, default=0.15)
    p.add_argument("--font-size", type=int, default=48)
    p.add_argument("--color", default="gray", help="Name or #RRGGBB.")
    p.add_argument("--tiled", action="store_true", help="Repeat across the page (text).")
    p.add_argument(
        "--position",
        default="center",
        choices=("center", "top-left", "top-right", "bottom-left", "bottom-right"),
        help="Stamp position (image).",
    )
    p.add_argument("--scale", type=float, default=0.4, help="Image width as fraction of page.")

    # numbers
    p = add("numbers", "Add page numbers.", _per_input("numbers"))
    inputs(p)
    p.add_argument("-o", "--output", help=batch_out)
    p.add_argument(
        "--position",
        default="bottom-center",
        choices=(
            "bottom-center", "bottom-right", "bottom-left",
            "top-center", "top-right", "top-left",
        ),
    )
    p.add_argument("--format", default="{n}", help='Template with {n} and {total}.')
    p.add_argument("--font-size", type=int, default=10)
    p.add_argument("--color", default="black")
    p.add_argument("--skip-first", action="store_true", help="Leave the cover unnumbered.")
    p.add_argument("--start-at", type=int, default=1)

    # img2pdf
    p = add("img2pdf", "Combine images into a PDF (EXIF-rotation and DPI aware).", cmd_img2pdf)
    p.add_argument("inputs", nargs="+", metavar="input", help="Image paths or globs.")
    p.add_argument("-o", "--output", required=True)
    p.add_argument(
        "--page-size",
        default="auto",
        help="auto (image size at its DPI) or a named size (a4, letter, legal, a3, a5).",
    )
    p.add_argument("--fit", default="contain", choices=("contain", "cover", "stretch"))
    p.add_argument("--margin", type=float, default=0.0, help="White margin in points.")

    # pdf2img
    p = add("pdf2img", "Render pages to images.", _per_input("pdf2img"))
    inputs(p)
    p.add_argument("-o", "--output", help="Output directory (default: <name>_pages).")
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--format", default="png", help="png, jpg, tiff ...")
    p.add_argument("--pages", help="Range spec. Default: all pages.")

    # fields
    p = add("fields", "List form fields.", _per_input("fields"))
    inputs(p)
    p.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")

    # fill
    p = add("fill", "Fill a form from a JSON file.", _per_input("fill"))
    p.add_argument("inputs", nargs="+", metavar="input", help="Form PDF(s) or glob patterns.")
    p.add_argument("data", help="JSON file of field:value pairs.")
    p.add_argument("-o", "--output", help=batch_out)
    p.add_argument("--flatten", action="store_true", help="Bake values in (needs pypdf>=5).")
    p.add_argument("--strict", action="store_true",
                   help="Fail (exit 2) on unknown fields or invalid values instead of warning.")

    # compress
    p = add("compress", "Shrink a PDF (honest about the trade).", _per_input("compress"))
    inputs(p)
    p.add_argument("-o", "--output", help=batch_out)
    p.add_argument("--mode", default="auto", choices=MODES,
                   help="auto = ghostscript if installed, else images.")
    p.add_argument("--max-dpi", type=int, default=150,
                   help="images mode: downsample images shown above this DPI (default 150).")
    p.add_argument("--dpi", type=int, default=150, help="rasterize mode: render DPI.")
    p.add_argument("--jpeg-quality", type=int, default=None,
                   help="JPEG quality 1-95 (default 70 for images, 60 for rasterize).")
    p.add_argument(
        "--gs-quality",
        default="ebook",
        choices=("screen", "ebook", "printer", "prepress", "default"),
    )
    p.add_argument("--allow-larger", action="store_true",
                   help="Write the result even when it is not smaller than the input.")

    # ocr
    p = add("ocr", "OCR scanned pages (sidecar text or searchable PDF).", _per_input("ocr"))
    inputs(p)
    p.add_argument("-o", "--output", help=batch_out)
    p.add_argument("--mode", default="sidecar", choices=("sidecar", "pdf"))
    p.add_argument("--lang", default="eng", help="Tesseract language code(s).")
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--pages", help="Range spec. Default: all pages.")
    p.add_argument("--force", action="store_true",
                   help="OCR pages that already have selectable text too.")

    # doctor
    add("doctor", "Check which optional dependencies are installed.", cmd_doctor)

    return parser


def _show_warning(message, category, filename, lineno, file=None, line=None):
    if issubclass(category, PdfToolsWarning):
        print(f"warning: {message}", file=sys.stderr)
    else:  # pragma: no cover - third-party warnings keep their usual format
        sys.stderr.write(warnings.formatwarning(message, category, filename, lineno, line))


def main(argv: List[str] | None = None) -> int:
    _load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.debug:
        # pypdf logs recoverable oddities ("EOF marker not found") that are
        # noise for a CLI user; --debug brings them back.
        logging.getLogger("pypdf").setLevel(logging.ERROR)
    with warnings.catch_warnings():
        warnings.simplefilter("always", PdfToolsWarning)
        warnings.showwarning = _show_warning
        try:
            return args.func(args)
        except KeyboardInterrupt:  # pragma: no cover
            print("interrupted", file=sys.stderr)
            return 130
        except Exception as exc:
            if args.debug:
                raise
            message = _error_message(exc)
            if message is not None:
                print(f"error: {message}", file=sys.stderr)
                return 2
            print(
                f"error: unexpected {exc.__class__.__name__}: {exc}\n"
                "(re-run with --debug for the full traceback, and please report it)",
                file=sys.stderr,
            )
            return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
