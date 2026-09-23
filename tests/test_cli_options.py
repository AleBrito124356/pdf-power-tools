"""CLI options added in 0.4: --allow, --strict, ocr --force and friends."""

from __future__ import annotations

import json
import os

from pypdf import PdfReader

from pdftools import ops_forms, ops_secure
from pdftools.cli import main


def _p(path):
    return int(PdfReader(path).trailer["/Encrypt"]["/P"]) & 0xFFFFFFFF


def _values(path):
    return {f["name"]: f["value"] for f in ops_forms.list_fields(path)}


def test_cli_encrypt_allow(text_pdf, out_dir, capsys):
    out = os.path.join(out_dir, "cli.pdf")
    assert main(["encrypt", text_pdf, "-o", out, "--owner-password", "o",
                 "--allow", "print", "--allow", "fill-forms,copy"]) == 0
    assert ops_secure.permissions_from_flag(_p(out)) == ["print", "copy", "fill-forms"]
    assert main(["encrypt", text_pdf, "-o", out, "--owner-password", "o", "--allow", "bogus"]) == 2
    assert "Unknown permission" in capsys.readouterr().err


def test_cli_fill_warns_and_strict_exits_2(form_pdf, out_dir, capsys):
    data = os.path.join(out_dir, "data.json")
    with open(data, "w", encoding="utf-8") as fh:
        json.dump({"full_name": "Ada Lovelace", "subscribe": True, "country": "Mexico",
                   "emial": "typo@example.com"}, fh)
    out = os.path.join(out_dir, "filled.pdf")
    assert main(["fill", form_pdf, data, "-o", out]) == 0
    assert "warning: no form field named 'emial'" in capsys.readouterr().err
    assert _values(out)["subscribe"] == "/Yes"
    strict_out = os.path.join(out_dir, "strict.pdf")
    assert main(["fill", form_pdf, data, "-o", strict_out, "--strict"]) == 2
    assert not os.path.exists(strict_out)


def test_ocr_cli_reports_pages(scanned_pdf, text_pdf_b, out_dir, fake_tesseract, capsys):
    out = os.path.join(out_dir, "s.pdf")
    assert main(["ocr", scanned_pdf, "--mode", "pdf", "--dpi", "100", "-o", out]) == 0
    assert "1 page(s), 3 word(s)" in capsys.readouterr().out
    assert main(["ocr", text_pdf_b, "-o", os.path.join(out_dir, "t.txt")]) == 0
    assert "skipped 3 page(s)" in capsys.readouterr().out
    assert main(["ocr", text_pdf_b, "--force", "-o", os.path.join(out_dir, "t2.txt")]) == 0
    assert "3 page(s), 9 word(s)" in capsys.readouterr().out
