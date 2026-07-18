"""pdf-power-tools — one library and one CLI for everything PDF.

Public API is intentionally flat: import the operation modules or pull the
most common helpers straight off the package.

    from pdftools import merge, split, extract_text, watermark

Every function works on file paths and returns the path it wrote, so calls
compose without a document object model to learn.
"""

from __future__ import annotations

__version__ = "0.3.0"

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
    info,
)
from .ops_secure import encrypt, decrypt, strip_metadata
from .ops_visual import watermark, page_numbers, images_to_pdf, pdf_to_images
from .ops_forms import list_fields, fill
from .compress import compress
from .ocr import ocr

__all__ = [
    "__version__",
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
    "info",
    # secure
    "encrypt",
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
    "ocr",
]
