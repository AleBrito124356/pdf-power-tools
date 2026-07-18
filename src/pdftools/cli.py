"""Command-line interface for pdf-power-tools.

One ``pdftool`` binary with a subcommand per operation. Conventions:

* Inputs accept literal paths and glob patterns.
* ``-o/--output`` is a file for single-output commands, a directory for
  commands that fan out (split, tables, images, pdf2img).
* ``--dry-run`` (global) prints the plan and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List

from . import __version__
from ._util import PdfToolsError, default_output, expand_inputs
from .compress import compress, find_ghostscript
from .ocr import ocr
from .ops_content import (
    extract_images,
    extract_tables,
    extract_text,
    get_metadata,
    info,
    set_metadata,
)
from .ops_forms import fill, list_fields
from .ops_pages import delete_pages, merge, n_up, reorder, rotate, split
from .ops_secure import decrypt, encrypt, strip_metadata
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


def _size(path: str) -> str:
    try:
        n = os.path.getsize(path)
    except OSError:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def _dry(args, description: str) -> bool:
    if args.dry_run:
        _echo(args, f"[dry-run] {description}")
        return True
    return False


# ---------------------------------------------------------------------------
# Page commands
# ---------------------------------------------------------------------------

def cmd_merge(args) -> int:
    files = expand_inputs(args.inputs)
    if _dry(args, f"merge {len(files)} file(s) -> {args.output}"):
        return 0
    out = merge(files, args.output, bookmark_per_source=not args.no_bookmarks)
    _echo(args, f"Merged {len(files)} file(s) -> {out} ({_size(out)})")
    return 0


def cmd_split(args) -> int:
    out_dir = args.output or (os.path.splitext(args.input)[0] + "_parts")
    if _dry(args, f"split {args.input} -> {out_dir}/"):
        return 0
    outputs = split(
        args.input,
        out_dir,
        ranges=args.ranges,
        every_n=args.every,
        by_bookmark=args.by_bookmark,
    )
    _echo(args, f"Wrote {len(outputs)} file(s) to {out_dir}/")
    for path in outputs:
        _echo(args, f"  {os.path.basename(path)} ({_size(path)})")
    return 0


def cmd_rotate(args) -> int:
    out = args.output or default_output(args.input, "rotated")
    if _dry(args, f"rotate {args.input} by {args.angle} -> {out}"):
        return 0
    out = rotate(args.input, out, pages=args.pages, angle=args.angle)
    _echo(args, f"Rotated -> {out}")
    return 0


def cmd_delete(args) -> int:
    out = args.output or default_output(args.input, "trimmed")
    if _dry(args, f"delete pages {args.pages} from {args.input} -> {out}"):
        return 0
    out = delete_pages(args.input, out, pages=args.pages)
    _echo(args, f"Deleted pages {args.pages} -> {out}")
    return 0


def cmd_reorder(args) -> int:
    out = args.output or default_output(args.input, "reordered")
    if _dry(args, f"reorder {args.input} as {args.order} -> {out}"):
        return 0
    out = reorder(args.input, out, order=args.order)
    _echo(args, f"Reordered -> {out}")
    return 0


def cmd_nup(args) -> int:
    out = args.output or default_output(args.input, f"{args.n}up")
    if _dry(args, f"{args.n}-up impose {args.input} -> {out}"):
        return 0
    out = n_up(args.input, out, n=args.n)
    _echo(args, f"Imposed {args.n}-up -> {out}")
    return 0


# ---------------------------------------------------------------------------
# Content commands
# ---------------------------------------------------------------------------

def cmd_text(args) -> int:
    if args.output and _dry(args, f"extract text {args.input} -> {args.output}"):
        return 0
    result = extract_text(
        args.input,
        args.output,
        pages=args.pages,
        joined=not args.no_join,
        layout=args.layout,
    )
    if args.output:
        _echo(args, f"Text -> {result}")
    else:
        if isinstance(result, list):
            print(("\n\n" + "-" * 40 + "\n\n").join(result))
        else:
            print(result)
    return 0


def cmd_tables(args) -> int:
    fmt = args.format
    records = extract_tables(args.input, args.output, pages=args.pages, fmt=fmt)
    if not records:
        _echo(args, "No tables detected.")
        return 0
    if args.output:
        _echo(args, f"Extracted {len(records)} table(s) to {args.output}/")
        for rec in records:
            if rec["path"]:
                _echo(args, f"  {os.path.basename(rec['path'])}")
    else:
        from .ops_content import _rows_to_markdown

        for rec in records:
            print(f"# page {rec['page']} table {rec['index']}")
            print(_rows_to_markdown(rec["rows"]))
            print()
    return 0


def cmd_images(args) -> int:
    out_dir = args.output or (os.path.splitext(args.input)[0] + "_images")
    if _dry(args, f"extract images {args.input} -> {out_dir}/"):
        return 0
    paths = extract_images(args.input, out_dir, pages=args.pages)
    _echo(args, f"Extracted {len(paths)} image(s) to {out_dir}/")
    return 0


def cmd_meta(args) -> int:
    is_set = any(
        v is not None for v in (args.title, args.author, args.subject, args.keywords)
    )
    if not is_set:
        print(json.dumps(get_metadata(args.input), indent=2, ensure_ascii=False))
        return 0
    out = args.output or default_output(args.input, "meta")
    if _dry(args, f"set metadata on {args.input} -> {out}"):
        return 0
    out = set_metadata(
        args.input,
        out,
        title=args.title,
        author=args.author,
        subject=args.subject,
        keywords=args.keywords,
    )
    _echo(args, f"Metadata written -> {out}")
    return 0


def cmd_info(args) -> int:
    print(json.dumps(info(args.input), indent=2, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# Security commands
# ---------------------------------------------------------------------------

def cmd_encrypt(args) -> int:
    out = args.output or default_output(args.input, "encrypted")
    if _dry(args, f"encrypt {args.input} ({args.algorithm}) -> {out}"):
        return 0
    out = encrypt(
        args.input,
        out,
        user_password=args.user_password,
        owner_password=args.owner_password,
        algorithm=args.algorithm,
    )
    _echo(args, f"Encrypted ({args.algorithm}) -> {out}")
    return 0


def cmd_decrypt(args) -> int:
    out = args.output or default_output(args.input, "decrypted")
    if _dry(args, f"decrypt {args.input} -> {out}"):
        return 0
    out = decrypt(args.input, out, password=args.password)
    _echo(args, f"Decrypted -> {out}")
    return 0


def cmd_scrub(args) -> int:
    out = args.output or default_output(args.input, "clean")
    if _dry(args, f"strip metadata from {args.input} -> {out}"):
        return 0
    out = strip_metadata(args.input, out, flatten_annotations=args.flatten_annotations)
    _echo(args, f"Scrubbed metadata -> {out}")
    return 0


# ---------------------------------------------------------------------------
# Visual commands
# ---------------------------------------------------------------------------

def cmd_watermark(args) -> int:
    out = args.output or default_output(args.input, "watermarked")
    if _dry(args, f"watermark {args.input} -> {out}"):
        return 0
    out = watermark(
        args.input,
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
    )
    _echo(args, f"Watermarked -> {out}")
    return 0


def cmd_numbers(args) -> int:
    out = args.output or default_output(args.input, "numbered")
    if _dry(args, f"number pages of {args.input} -> {out}"):
        return 0
    out = page_numbers(
        args.input,
        out,
        position=args.position,
        fmt=args.format,
        font_size=args.font_size,
        color=args.color,
        skip_first=args.skip_first,
        start_at=args.start_at,
    )
    _echo(args, f"Numbered -> {out}")
    return 0


def cmd_img2pdf(args) -> int:
    files = expand_inputs(args.inputs)
    if _dry(args, f"combine {len(files)} image(s) -> {args.output}"):
        return 0
    out = images_to_pdf(files, args.output, page_size=args.page_size, fit=args.fit)
    _echo(args, f"Built PDF from {len(files)} image(s) -> {out} ({_size(out)})")
    return 0


def cmd_pdf2img(args) -> int:
    out_dir = args.output or (os.path.splitext(args.input)[0] + "_pages")
    if _dry(args, f"render {args.input} @ {args.dpi}dpi -> {out_dir}/"):
        return 0
    paths = pdf_to_images(
        args.input, out_dir, dpi=args.dpi, fmt=args.format, pages=args.pages
    )
    _echo(args, f"Rendered {len(paths)} page(s) to {out_dir}/")
    return 0


# ---------------------------------------------------------------------------
# Form commands
# ---------------------------------------------------------------------------

def cmd_fields(args) -> int:
    fields = list_fields(args.input)
    if args.json:
        print(json.dumps(fields, indent=2, ensure_ascii=False))
        return 0
    if not fields:
        _echo(args, "No form fields found.")
        return 0
    width = max(len(f["name"]) for f in fields)
    for f in fields:
        value = "" if f["value"] is None else f["value"]
        print(f"{f['name']:<{width}}  {f['type']:<9}  {value}")
    return 0


def cmd_fill(args) -> int:
    out = args.output or default_output(args.input, "filled")
    if _dry(args, f"fill form {args.input} from {args.data} -> {out}"):
        return 0
    out = fill(args.input, out, args.data, flatten=args.flatten)
    _echo(args, f"Filled{' + flattened' if args.flatten else ''} -> {out}")
    return 0


# ---------------------------------------------------------------------------
# Compress + OCR
# ---------------------------------------------------------------------------

def cmd_compress(args) -> int:
    out = args.output or default_output(args.input, "compressed")
    if _dry(args, f"compress {args.input} (mode={args.mode}) -> {out}"):
        return 0
    before = _size(args.input)
    out = compress(
        args.input,
        out,
        mode=args.mode,
        dpi=args.dpi,
        jpeg_quality=args.jpeg_quality,
        gs_quality=args.gs_quality,
    )
    try:
        ratio = os.path.getsize(out) / max(1, os.path.getsize(args.input))
        pct = f"{(1 - ratio) * 100:.0f}% smaller" if ratio < 1 else f"{(ratio - 1) * 100:.0f}% larger"
    except OSError:
        pct = "size unknown"
    _echo(args, f"Compressed {before} -> {_size(out)} ({pct}) -> {out}")
    return 0


def cmd_ocr(args) -> int:
    suffix_ext = ".txt" if args.mode == "sidecar" else ".pdf"
    default = default_output(args.input, "ocr" if args.mode == "sidecar" else "searchable", ext=suffix_ext)
    out = args.output or default
    if _dry(args, f"OCR {args.input} (mode={args.mode}, lang={args.lang}) -> {out}"):
        return 0
    out = ocr(args.input, out, mode=args.mode, lang=args.lang, dpi=args.dpi, pages=args.pages)
    _echo(args, f"OCR ({args.mode}) -> {out}")
    return 0


def cmd_doctor(args) -> int:
    """Report which optional dependencies are available."""
    print(f"pdftool {__version__}")
    checks = []

    def probe(label, fn):
        try:
            fn()
            checks.append((label, "ok"))
        except Exception as exc:  # pragma: no cover - environment dependent
            checks.append((label, f"missing ({exc.__class__.__name__})"))

    probe("pypdf", lambda: __import__("pypdf"))
    probe("pdfplumber", lambda: __import__("pdfplumber"))
    probe("Pillow", lambda: __import__("PIL"))
    probe("pypdfium2", lambda: __import__("pypdfium2"))
    probe("reportlab", lambda: __import__("reportlab"))

    gs = find_ghostscript()
    checks.append(("ghostscript", gs if gs else "not on PATH (optional)"))

    def tesseract_check():
        import pytesseract

        cmd = os.environ.get("TESSERACT_CMD")
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        pytesseract.get_tesseract_version()

    probe("tesseract (OCR)", tesseract_check)

    width = max(len(label) for label, _ in checks)
    for label, status in checks:
        print(f"  {label:<{width}}  {status}")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdftool",
        description="One CLI for everything PDF: merge, split, compress, "
        "watermark, encrypt, extract, fill forms and OCR.",
    )
    parser.add_argument("--version", action="version", version=f"pdftool {__version__}")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan; write nothing.")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress progress messages.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    # merge
    p = sub.add_parser("merge", help="Concatenate PDFs (globs allowed).")
    p.add_argument("inputs", nargs="+", help="Input PDFs or glob patterns.")
    p.add_argument("-o", "--output", required=True, help="Output PDF path.")
    p.add_argument("--no-bookmarks", action="store_true", help="Do not add a bookmark per source.")
    p.set_defaults(func=cmd_merge)

    # split
    p = sub.add_parser("split", help="Split one PDF into several.")
    p.add_argument("input", help="Input PDF.")
    p.add_argument("-o", "--output", help="Output directory (default: <name>_parts).")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--ranges", help='Comma groups, e.g. "1-3,7,9-". Each group -> one file.')
    g.add_argument("--every", type=int, metavar="N", help="Fixed chunks of N pages.")
    g.add_argument("--by-bookmark", action="store_true", help="One file per top-level bookmark.")
    p.set_defaults(func=cmd_split)

    # rotate
    p = sub.add_parser("rotate", help="Rotate pages by a multiple of 90.")
    p.add_argument("input")
    p.add_argument("-o", "--output")
    p.add_argument("--pages", help='Range spec, e.g. "1-3,7". Default: all pages.')
    p.add_argument("--angle", type=int, default=90, help="Clockwise degrees (default 90).")
    p.set_defaults(func=cmd_rotate)

    # delete
    p = sub.add_parser("delete", help="Delete pages by range spec.")
    p.add_argument("input")
    p.add_argument("-o", "--output")
    p.add_argument("--pages", required=True, help='Pages to remove, e.g. "2,5-6".')
    p.set_defaults(func=cmd_delete)

    # reorder
    p = sub.add_parser("reorder", help="Rebuild in an explicit page order.")
    p.add_argument("input")
    p.add_argument("-o", "--output")
    p.add_argument("--order", required=True, help='New order, e.g. "3,1,2,4-".')
    p.set_defaults(func=cmd_reorder)

    # nup
    p = sub.add_parser("nup", help="Impose 2 or 4 pages per sheet.")
    p.add_argument("input")
    p.add_argument("-o", "--output")
    p.add_argument("-n", type=int, choices=(2, 4), default=2, help="Pages per sheet.")
    p.set_defaults(func=cmd_nup)

    # text
    p = sub.add_parser("text", help="Extract selectable text.")
    p.add_argument("input")
    p.add_argument("-o", "--output", help="Write to a .txt file instead of stdout.")
    p.add_argument("--pages", help="Range spec. Default: all pages.")
    p.add_argument("--layout", action="store_true", help="Use pdfplumber layout mode.")
    p.add_argument("--no-join", action="store_true", help="Keep pages separate on stdout.")
    p.set_defaults(func=cmd_text)

    # tables
    p = sub.add_parser("tables", help="Extract tables to CSV or Markdown.")
    p.add_argument("input")
    p.add_argument("-o", "--output", help="Output directory. Omit to print Markdown.")
    p.add_argument("--pages", help="Range spec. Default: all pages.")
    p.add_argument("--format", choices=("csv", "markdown"), default="csv")
    p.set_defaults(func=cmd_tables)

    # images
    p = sub.add_parser("images", help="Extract embedded raster images.")
    p.add_argument("input")
    p.add_argument("-o", "--output", help="Output directory (default: <name>_images).")
    p.add_argument("--pages", help="Range spec. Default: all pages.")
    p.set_defaults(func=cmd_images)

    # meta
    p = sub.add_parser("meta", help="Get metadata (no flags) or set it (with flags).")
    p.add_argument("input")
    p.add_argument("-o", "--output", help="Output PDF when setting metadata.")
    p.add_argument("--title")
    p.add_argument("--author")
    p.add_argument("--subject")
    p.add_argument("--keywords")
    p.set_defaults(func=cmd_meta)

    # info
    p = sub.add_parser("info", help="Print a JSON summary of the document.")
    p.add_argument("input")
    p.set_defaults(func=cmd_info)

    # encrypt
    p = sub.add_parser("encrypt", help="Encrypt with a password.")
    p.add_argument("input")
    p.add_argument("-o", "--output")
    p.add_argument("--user-password", default="", help="Password to open the file.")
    p.add_argument("--owner-password", default=None, help="Password for full permissions.")
    p.add_argument(
        "--algorithm",
        default="AES-256",
        choices=("RC4-40", "RC4-128", "AES-128", "AES-256"),
    )
    p.set_defaults(func=cmd_encrypt)

    # decrypt
    p = sub.add_parser("decrypt", help="Remove encryption with a password.")
    p.add_argument("input")
    p.add_argument("-o", "--output")
    p.add_argument("--password", default="", help="User or owner password.")
    p.set_defaults(func=cmd_decrypt)

    # scrub
    p = sub.add_parser("scrub", help="Strip metadata for privacy.")
    p.add_argument("input")
    p.add_argument("-o", "--output")
    p.add_argument(
        "--flatten-annotations",
        action="store_true",
        help="Also remove annotations that may carry author names.",
    )
    p.set_defaults(func=cmd_scrub)

    # watermark
    p = sub.add_parser("watermark", help="Stamp a text or image watermark.")
    p.add_argument("input")
    p.add_argument("-o", "--output")
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
    p.set_defaults(func=cmd_watermark)

    # numbers
    p = sub.add_parser("numbers", help="Add page numbers.")
    p.add_argument("input")
    p.add_argument("-o", "--output")
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
    p.set_defaults(func=cmd_numbers)

    # img2pdf
    p = sub.add_parser("img2pdf", help="Combine images into a PDF.")
    p.add_argument("inputs", nargs="+", help="Image paths or globs.")
    p.add_argument("-o", "--output", required=True)
    p.add_argument(
        "--page-size",
        default="auto",
        help="auto or a named size (a4, letter, legal, a3, a5).",
    )
    p.add_argument("--fit", default="contain", choices=("contain", "cover", "stretch"))
    p.set_defaults(func=cmd_img2pdf)

    # pdf2img
    p = sub.add_parser("pdf2img", help="Render pages to images.")
    p.add_argument("input")
    p.add_argument("-o", "--output", help="Output directory (default: <name>_pages).")
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--format", default="png", help="png, jpg, tiff ...")
    p.add_argument("--pages", help="Range spec. Default: all pages.")
    p.set_defaults(func=cmd_pdf2img)

    # fields
    p = sub.add_parser("fields", help="List form fields.")
    p.add_argument("input")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    p.set_defaults(func=cmd_fields)

    # fill
    p = sub.add_parser("fill", help="Fill a form from a JSON file.")
    p.add_argument("input")
    p.add_argument("data", help="JSON file of field:value pairs.")
    p.add_argument("-o", "--output")
    p.add_argument("--flatten", action="store_true", help="Bake values in (needs pypdf>=5).")
    p.set_defaults(func=cmd_fill)

    # compress
    p = sub.add_parser("compress", help="Shrink a PDF (honest about the trade).")
    p.add_argument("input")
    p.add_argument("-o", "--output")
    p.add_argument(
        "--mode",
        default="auto",
        choices=("auto", "lossless", "rasterize", "ghostscript"),
    )
    p.add_argument("--dpi", type=int, default=150, help="Rasterize render dpi.")
    p.add_argument("--jpeg-quality", type=int, default=60, help="Rasterize JPEG quality.")
    p.add_argument(
        "--gs-quality",
        default="ebook",
        choices=("screen", "ebook", "printer", "prepress", "default"),
    )
    p.set_defaults(func=cmd_compress)

    # ocr
    p = sub.add_parser("ocr", help="OCR a scanned PDF.")
    p.add_argument("input")
    p.add_argument("-o", "--output")
    p.add_argument("--mode", default="sidecar", choices=("sidecar", "pdf"))
    p.add_argument("--lang", default="eng", help="Tesseract language code(s).")
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--pages", help="Range spec. Default: all pages.")
    p.set_defaults(func=cmd_ocr)

    # doctor
    p = sub.add_parser("doctor", help="Check which optional dependencies are installed.")
    p.set_defaults(func=cmd_doctor)

    return parser


def main(argv: List[str] | None = None) -> int:
    _load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PdfToolsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"error: file not found: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
