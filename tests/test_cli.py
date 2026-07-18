"""CLI smoke tests through the argparse entry point."""

from __future__ import annotations

import json
import os

from pdftools.cli import main


def test_cli_merge(text_pdf, text_pdf_b, out_dir, capsys):
    out = os.path.join(out_dir, "cli_merged.pdf")
    code = main(["merge", text_pdf, text_pdf_b, "-o", out])
    assert code == 0
    assert os.path.isfile(out)


def test_cli_dry_run_writes_nothing(text_pdf, out_dir, capsys):
    out = os.path.join(out_dir, "should_not_exist.pdf")
    code = main(["--dry-run", "rotate", text_pdf, "-o", out, "--angle", "90"])
    assert code == 0
    assert not os.path.exists(out)
    assert "[dry-run]" in capsys.readouterr().out


def test_cli_info_json(text_pdf, capsys):
    code = main(["info", text_pdf])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["page_count"] == 5


def test_cli_fields_table(form_pdf, capsys):
    code = main(["fields", form_pdf])
    assert code == 0
    out = capsys.readouterr().out
    assert "full_name" in out


def test_cli_bad_input_returns_error(out_dir, capsys):
    code = main(["info", os.path.join(out_dir, "nope.pdf")])
    assert code == 2


def test_cli_doctor(capsys):
    code = main(["doctor"])
    assert code == 0
    assert "pypdf" in capsys.readouterr().out
