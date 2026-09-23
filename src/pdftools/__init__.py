"""pdf-power-tools — one library and one CLI for everything PDF.

Public API is intentionally flat: import the operation modules or pull the
most common helpers straight off the package.

    from pdftools import merge, split, extract_text, watermark

Every function works on file paths and returns the path it wrote, so calls
compose without a document object model to learn. Every function that reads
a PDF takes ``password=`` for encrypted inputs, and every predictable failure
(missing, damaged or locked file, bad option) raises :class:`PdfToolsError`
with a message meant for a person.
"""

from __future__ import annotations

__version__ = "0.4.0"

from ._util import PdfToolsError, PdfToolsWarning
from .ops_pages import (
    merge,
    split,
    rotate,
    delete_pages,
    reorder,
    n_up,
)
from .ops_content import (
    extract_text,
    extract_tables,
    extract_images,
    get_metadata,
    set_metadata,
    page_count,
    pages_with_text,
    info,
)
from .ops_secure import (
    PERMISSIONS,
    encrypt,
    encryption_info,
    decrypt,
    strip_metadata,
)
from .ops_visual import watermark, page_numbers, images_to_pdf, pdf_to_images
from .ops_forms import list_fields, fill
from .compress import CompressResult, compress, compress_with_stats
from .ocr import OcrResult, ocr, ocr_with_stats

__all__ = [
    "__version__",
    "PdfToolsError",
    "PdfToolsWarning",
    # pages
    "merge",
    "split",
    "rotate",
    "delete_pages",
    "reorder",
    "n_up",
    # content
    "extract_text",
    "extract_tables",
    "extract_images",
    "get_metadata",
    "set_metadata",
    "page_count",
    "pages_with_text",
    "info",
    # secure
    "PERMISSIONS",
    "encrypt",
    "encryption_info",
    "decrypt",
    "strip_metadata",
    # visual
    "watermark",
    "page_numbers",
    "images_to_pdf",
    "pdf_to_images",
    # forms
    "list_fields",
    "fill",
    # compress / ocr
    "compress",
    "compress_with_stats",
    "CompressResult",
    "ocr",
    "ocr_with_stats",
    "OcrResult",
]
