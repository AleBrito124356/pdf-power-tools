"""Encryption, decryption and metadata scrubbing tests."""

from __future__ import annotations

import os

import pytest
from pypdf import PdfReader

from pdftools import ops_content, ops_secure
from pdftools._util import PdfToolsError


def test_encrypt_then_decrypt_roundtrip(text_pdf_b, out_dir):
    enc = os.path.join(out_dir, "enc.pdf")
    ops_secure.encrypt(text_pdf_b, enc, user_password="open123", owner_password="owner123")

    reader = PdfReader(enc)
    assert reader.is_encrypted

    dec = os.path.join(out_dir, "dec.pdf")
    ops_secure.decrypt(enc, dec, password="open123")
    reader2 = PdfReader(dec)
    assert reader2.is_encrypted is False
    assert len(reader2.pages) == len(PdfReader(text_pdf_b).pages)


def test_decrypt_with_wrong_password(encrypted_pdf, out_dir):
    with pytest.raises(PdfToolsError):
        ops_secure.decrypt(encrypted_pdf, os.path.join(out_dir, "x.pdf"), password="nope")


def test_encrypt_rejects_bad_algorithm(text_pdf_b, out_dir):
    with pytest.raises(PdfToolsError):
        ops_secure.encrypt(
            text_pdf_b, os.path.join(out_dir, "x.pdf"),
            user_password="p", algorithm="ROT13",
        )


def test_strip_metadata(text_pdf, out_dir):
    assert ops_content.get_metadata(text_pdf)["title"] == "Report"
    out = os.path.join(out_dir, "clean.pdf")
    ops_secure.strip_metadata(text_pdf, out)
    scrubbed = ops_content.get_metadata(out)
    assert scrubbed["title"] is None
    assert scrubbed["author"] is None
    # Pages survive the scrub.
    assert len(PdfReader(out).pages) == len(PdfReader(text_pdf).pages)
