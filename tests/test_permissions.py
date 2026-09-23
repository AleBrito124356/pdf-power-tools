"""Encryption permissions: what an 'owner password' file really allows."""

from __future__ import annotations

import os

import pytest
from pypdf import PdfReader

from pdftools import ops_content, ops_secure
from pdftools._util import PdfToolsError, PdfToolsWarning


def _p(path):
    return int(PdfReader(path).trailer["/Encrypt"]["/P"]) & 0xFFFFFFFF


def _assert_reserved_bits(p):
    assert p & 0b11 == 0                 # bits 1-2 must be 0
    assert p & 0xC0 == 0xC0              # bits 7-8 must be 1
    assert p & 0xFFFFF000 == 0xFFFFF000  # bits 13-32 must be 1


def test_owner_password_only_opens_freely_but_resists_editing(text_pdf, out_dir):
    out = os.path.join(out_dir, "locked.pdf")
    ops_secure.encrypt(text_pdf, out, user_password="", owner_password="CHANGE_ME_32_CHARS")
    reader = PdfReader(out)
    assert reader.is_encrypted
    assert reader.decrypt("") != 0  # opens with no prompt
    granted = ops_secure.permissions_from_flag(_p(out))
    assert granted == ["print", "accessibility", "print-hq"]
    for denied in ("modify", "copy", "annotate", "fill-forms", "assemble"):
        assert denied not in granted
    _assert_reserved_bits(_p(out))
    # The file stays fully usable for reading, without any password.
    assert "Report - page 1" in ops_content.extract_text(out)


def test_allow_all_and_none(text_pdf, out_dir):
    everything = os.path.join(out_dir, "all.pdf")
    ops_secure.encrypt(text_pdf, everything, user_password="", owner_password="o", permissions="all")
    assert _p(everything) == 0xFFFFFFFC
    nothing = os.path.join(out_dir, "none.pdf")
    ops_secure.encrypt(text_pdf, nothing, user_password="", owner_password="o", permissions=["none"])
    assert ops_secure.permissions_from_flag(_p(nothing)) == []
    _assert_reserved_bits(_p(nothing))


def test_explicit_permissions(text_pdf, out_dir):
    out = os.path.join(out_dir, "pc.pdf")
    ops_secure.encrypt(text_pdf, out, user_password="u", owner_password="o",
                       permissions=["print", "copy"])
    assert ops_secure.permissions_from_flag(_p(out)) == ["print", "copy"]
    _assert_reserved_bits(_p(out))
    with pytest.raises(PdfToolsError, match="Unknown permission"):
        ops_secure.encrypt(text_pdf, out, user_password="u", owner_password="o",
                           permissions="print,teleport")


def test_same_passwords_keep_all_permissions_and_warn_on_restrictions(text_pdf, out_dir):
    out = os.path.join(out_dir, "same.pdf")
    ops_secure.encrypt(text_pdf, out, user_password="u")
    assert _p(out) == 0xFFFFFFFC
    with pytest.warns(PdfToolsWarning, match="owner password"):
        ops_secure.encrypt(text_pdf, out, user_password="u", permissions="print")


def test_info_reports_encryption_and_permissions(text_pdf, out_dir):
    out = os.path.join(out_dir, "locked.pdf")
    ops_secure.encrypt(text_pdf, out, user_password="", owner_password="o", algorithm="AES-128")
    data = ops_content.info(out)
    assert data["encrypted"] is True
    assert data["encryption"]["algorithm"] == "AES-128"
    assert "copy" not in data["encryption"]["permissions"]
    assert ops_secure.encryption_info(text_pdf) is None


def test_encrypt_an_already_encrypted_input(encrypted_pdf, out_dir):
    out = os.path.join(out_dir, "re.pdf")
    ops_secure.encrypt(encrypted_pdf, out, user_password="new", password="open123")
    assert PdfReader(out).decrypt("new") != 0
    assert PdfReader(out).decrypt("open123") == 0
