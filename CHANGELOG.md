# Changelog

## 0.4.0

This release makes the README true. Every claim it makes is now backed by the code and by a test, and several silent data-loss bugs are fixed.

### Added

- **Encrypted inputs everywhere.** Every library function that reads a PDF takes `password=`. The CLI has a global `--password` that works before or after the subcommand. Owner-password-only files open without one.
- **One-line errors.** Missing, damaged, non-PDF and locked files, wrong passwords, unreadable images and bad `--format` templates now print `error: ...` and exit 2. Unexpected internal errors print one line and exit 1. `--debug` shows the full traceback and pypdf's log messages.
- **Batch mode.** Every single-input command accepts several files or a glob. With several inputs, `-o` is a directory. One bad file is reported, the rest are still processed, and the run exits 2. `--dry-run` lists every planned output.
- **`compress --mode images`**, the new default when Ghostscript is absent. It downsamples embedded images shown above `--max-dpi` (default 150) and re-encodes photos and scans as JPEG, while text, vectors, forms and bookmarks stay as they were. Measured: a 9.6 MB 300-dpi scan becomes 80 KB, and 9.5 MB of lossless photos becomes 182 KB with the text still selectable.
- **Never-larger guard for `compress`.** If a strategy does not shrink the file, the original bytes are written and the result says so. `--allow-larger` / `allow_larger=True` writes the result anyway. `compress_with_stats()` returns a `CompressResult` with byte counts and image counts.
- **OCR rebuilt.** `--mode pdf` now lays an invisible, correctly placed text layer over the *original* page (crop box and `/Rotate` aware) instead of replacing each page with Tesseract's re-rendered raster. Pages that already have text are skipped; `--force` / `skip_text=False` OCRs them anyway. Tesseract is told the real render DPI. `ocr_with_stats()` reports the pages OCR'd and skipped and the word count.
- **Encryption permissions.** `encrypt(permissions=...)` / `--allow print,copy,...` accepts `print`, `print-hq`, `modify`, `copy`, `annotate`, `fill-forms`, `accessibility`, `assemble`, `all` and `none`, and writes a spec-correct `/P` value with the reserved bits set.
- **`info`** now reports the PDF version, the encryption algorithm and permissions, `has_outline`, a per-page `has_text` flag, and `pages_without_text` (the "needs OCR" hint). `pages_with_text()` and `encryption_info()` are public.
- **Strict, coercing form fill.** Checkboxes accept `True`/`"yes"`/`"on"` and are switched to the field's real on-state. Radio groups take an export value. Choice values are checked against the field's options. Unknown keys raise a `PdfToolsWarning` with a "did you mean" suggestion. `strict=True` / `--strict` turns these into an error and writes nothing. `list_fields()` records gain a `states` key.
- **`img2pdf --margin`**, plus EXIF orientation support: phone photos come out upright, and JPEGs are still embedded byte-for-byte.
- `PdfToolsError`, `PdfToolsWarning`, `PERMISSIONS`, `CompressResult`, `OcrResult` are exported from the package.

### Fixed

- `merge` dropped every AcroForm field and flattened the sources' bookmarks. It now appends structurally: fields, links and bookmarks survive, and each source's bookmarks are nested under its file's bookmark.
- `decrypt` and `scrub` rebuilt documents page by page, which silently destroyed forms and outlines. Both now clone the document. `rotate`, `delete`, `reorder` and `split` keep fields and the bookmarks of the pages they keep.
- `scrub` now removes XMP `/Metadata` packets and `/PieceInfo` everywhere, deletes the info dictionary, and drops the orphaned objects so the data is really gone from the file.
- Glob expansion sorted `scan10` before `scan2`. It now uses natural order.
- The global `--dry-run` was ignored by `tables`, which wrote files anyway. `--dry-run` also works after the subcommand now.
- Page numbers and watermarks landed sideways at the page edge on rotated pages and ignored the crop box. Overlays are now laid out in the displayed frame.
- `img2pdf` ignored EXIF orientation, and `--page-size auto` treated pixels as points (a 4032×3024 photo at 300 dpi became a 56-inch page; it is now 13.4 × 10.1 in).
- reportlab's ASCII85 encoding inflated embedded JPEGs by 25% in `img2pdf` and `rasterize` output.
- `.env.example` shipped an active Windows `TESSERACT_CMD`, which broke OCR on macOS and Linux after `cp .env.example .env`. It is now commented out.
- Docstrings that claimed `n_up` output is not selectable (it is), that lossless compression is "typically 5-20% smaller", and similar statements are corrected.

### Changed behaviour (check before upgrading)

- `encrypt` with an owner password that differs from the user password now allows only `print`, `print-hq` and `accessibility` by default. Before, it granted every permission, despite the docs. Pass `permissions="all"` / `--allow all` for the old behaviour.
- `compress(mode="auto")` without Ghostscript now uses `images` instead of `lossless`. `compress` never writes a file larger than its input unless `allow_larger=True`. `jpeg_quality` now defaults to 70 for `images` and 60 for `rasterize`.
- `ocr(mode="pdf")` output now keeps the original pages, and pages with existing text are skipped by default.
- `scrub` removes the info dictionary entirely instead of blanking `/Producer` and `/Creator`.
- `merge` bookmarks now include each source's own outline, nested under the per-file entry. With `--no-bookmarks`, the sources' outlines are kept at the top level.
- `img2pdf --page-size auto` sizes pages from the image's DPI (72 when the file does not say, which matches the old result).
- The `decrypt --password` option is now the global `--password` (still accepted after the subcommand).
- CLI outputs processed from an encrypted input are written unencrypted, as before with `decrypt`. Run `encrypt` again if needed.

## 0.3.0

- Initial public release: merge, split, rotate, delete, reorder, n-up, text/tables/images extraction, metadata, encrypt/decrypt/scrub, watermark, page numbers, img2pdf/pdf2img, forms, compression and OCR.
