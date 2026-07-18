"""Compression tests (the paths that do not need external tools)."""

from __future__ import annotations

import importlib
import os

import pytest
from pypdf import PdfReader

from pdftools._util import PdfToolsError

# The package exports a `compress` function, which shadows the
# `pdftools.compress` submodule attribute; import_module fetches the real
# module so we can monkeypatch find_ghostscript on it.
compress_mod = importlib.import_module("pdftools.compress")


def _count(path: str) -> int:
    return len(PdfReader(path).pages)


def test_compress_lossless_keeps_pages(image_pdf, out_dir):
    out = os.path.join(out_dir, "loss.pdf")
    compress_mod.compress(image_pdf, out, mode="lossless")
    assert os.path.isfile(out)
    assert _count(out) == _count(image_pdf)


def test_compress_rasterize_keeps_pages(image_pdf, out_dir):
    out = os.path.join(out_dir, "raster.pdf")
    compress_mod.compress(image_pdf, out, mode="rasterize", dpi=72, jpeg_quality=40)
    assert _count(out) == _count(image_pdf)


def test_compress_unknown_mode(image_pdf, out_dir):
    with pytest.raises(PdfToolsError):
        compress_mod.compress(image_pdf, os.path.join(out_dir, "x.pdf"), mode="magic")


def test_ghostscript_mode_without_binary(image_pdf, out_dir, monkeypatch):
    # Force "not installed" and confirm a friendly error, not a crash.
    monkeypatch.setattr(compress_mod, "find_ghostscript", lambda: None)
    with pytest.raises(PdfToolsError):
        compress_mod.compress(image_pdf, os.path.join(out_dir, "x.pdf"), mode="ghostscript")


def test_probe(text_pdf):
    assert compress_mod.probe(text_pdf) == 5
