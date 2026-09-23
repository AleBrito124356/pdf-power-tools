"""CLI error handling: one clean ``error:`` line and exit 2, never a traceback."""

from __future__ import annotations

import os
import shutil

import pytest

from pdftools.cli import main


# ---------------------------------------------------------------------------
# CLI error handling
# ---------------------------------------------------------------------------

def test_cli_locked_file_names_password_option(encrypted_pdf, capsys):
    assert main(["info", encrypted_pdf]) == 2
    err = capsys.readouterr().err
    assert "--password" in err
    assert "Traceback" not in err


def test_cli_password_before_or_after_subcommand(encrypted_pdf, capsys):
    assert main(["--password", "open123", "text", encrypted_pdf]) == 0
    assert "Memo - page 1" in capsys.readouterr().out
    assert main(["text", encrypted_pdf, "--pages", "2", "--password", "owner123"]) == 0
    assert "Memo - page 2" in capsys.readouterr().out
    assert main(["text", encrypted_pdf, "--password", "wrong"]) == 2
    assert "Wrong password" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv_tail",
    [
        ["info"],
        ["text"],
        ["compress", "--mode", "lossless"],
        ["watermark", "--text", "X"],
        ["fields"],
        ["split", "--every", "1"],
    ],
)
def test_cli_non_pdf_is_a_clean_error(not_a_pdf, out_dir, capsys, argv_tail):
    argv = [argv_tail[0], not_a_pdf] + argv_tail[1:]
    if argv_tail[0] in ("compress", "watermark"):
        argv += ["-o", os.path.join(out_dir, "x.pdf")]
    if argv_tail[0] == "split":
        argv += ["-o", out_dir]
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "Traceback" not in err


def test_cli_bad_number_format(text_pdf, out_dir, capsys):
    code = main(["numbers", text_pdf, "-o", os.path.join(out_dir, "n.pdf"), "--format", "Page {page}"])
    assert code == 2
    err = capsys.readouterr().err
    assert "{page}" in err and "{n}" in err
    assert not os.path.exists(os.path.join(out_dir, "n.pdf"))


def test_cli_img2pdf_rejects_non_image(not_a_pdf, out_dir, capsys):
    assert main(["img2pdf", not_a_pdf, "-o", os.path.join(out_dir, "x.pdf")]) == 2
    assert "not a readable image" in capsys.readouterr().err


def test_cli_dry_run_tables_writes_nothing(table_pdf, tmp_path, capsys):
    target = tmp_path / "dry_tables"
    assert main(["--dry-run", "tables", table_pdf, "-o", str(target)]) == 0
    assert not target.exists()
    assert "[dry-run]" in capsys.readouterr().out


def test_cli_dry_run_after_subcommand(text_pdf, tmp_path, capsys):
    src = tmp_path / "doc.pdf"
    shutil.copy(text_pdf, src)
    assert main(["rotate", str(src), "--dry-run"]) == 0
    assert "[dry-run]" in capsys.readouterr().out
    assert sorted(os.listdir(tmp_path)) == ["doc.pdf"]


def test_cli_unexpected_error_is_one_line_unless_debug(text_pdf, out_dir, monkeypatch, capsys):
    import pdftools.cli as cli

    def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "rotate", boom)
    assert main(["rotate", text_pdf, "-o", os.path.join(out_dir, "r.pdf")]) == 1
    err = capsys.readouterr().err
    assert "unexpected RuntimeError: kaboom" in err and "--debug" in err
    assert "Traceback" not in err
    with pytest.raises(RuntimeError):
        main(["--debug", "rotate", text_pdf, "-o", os.path.join(out_dir, "r.pdf")])
