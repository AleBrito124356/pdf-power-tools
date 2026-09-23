"""Batch mode: every single-input command accepts several files or a glob."""

from __future__ import annotations

import json
import os
import shutil

from pypdf import PdfReader

from pdftools.cli import main


def test_compress_glob_writes_one_output_per_match(work_dir, tmp_path, capsys):
    outdir = str(tmp_path / "small")
    code = main(["compress", os.path.join(work_dir, "doc*.pdf"), "-o", outdir,
                 "--mode", "lossless", "--allow-larger"])
    assert code == 0
    assert sorted(os.listdir(outdir)) == ["doc3.compressed.pdf", "doc5.compressed.pdf"]
    assert len(PdfReader(os.path.join(outdir, "doc5.compressed.pdf")).pages) == 5
    assert "2 ok, 0 failed" in capsys.readouterr().out


def test_batch_keeps_going_past_a_bad_file(work_dir, not_a_pdf, tmp_path, capsys):
    shutil.copy(not_a_pdf, os.path.join(work_dir, "doc9.pdf"))
    outdir = str(tmp_path / "wm")
    code = main(["watermark", os.path.join(work_dir, "doc*.pdf"), "--text", "X", "-o", outdir])
    assert code == 2
    captured = capsys.readouterr()
    assert "doc9.pdf" in captured.err and "Traceback" not in captured.err
    assert "2 ok, 1 failed" in captured.err
    assert sorted(os.listdir(outdir)) == ["doc3.watermarked.pdf", "doc5.watermarked.pdf"]


def test_dry_run_batch_lists_targets_and_writes_nothing(work_dir, tmp_path, capsys):
    outdir = tmp_path / "planned"
    code = main(["--dry-run", "watermark", os.path.join(work_dir, "*.pdf"), "--text", "X",
                 "-o", str(outdir)])
    assert code == 0
    out = capsys.readouterr().out
    assert out.count("[dry-run] watermark") == 2
    assert os.path.join(str(outdir), "doc3.watermarked.pdf") in out
    assert not outdir.exists()
    assert sorted(os.listdir(work_dir)) == ["doc3.pdf", "doc5.pdf"]


def test_single_input_without_output_is_unchanged(work_dir, capsys):
    src = os.path.join(work_dir, "doc3.pdf")
    assert main(["watermark", src, "--text", "DRAFT"]) == 0
    assert os.path.isfile(os.path.join(work_dir, "doc3.watermarked.pdf"))
    assert "2 ok" not in capsys.readouterr().out  # no batch summary for one file


def test_batch_without_output_writes_beside_each_source(work_dir):
    assert main(["-q", "numbers", os.path.join(work_dir, "doc3.pdf"),
                 os.path.join(work_dir, "doc5.pdf")]) == 0
    assert sorted(os.listdir(work_dir)) == [
        "doc3.numbered.pdf", "doc3.pdf", "doc5.numbered.pdf", "doc5.pdf",
    ]


def test_same_file_names_from_different_folders_do_not_collide(work_dir, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    shutil.copy(os.path.join(work_dir, "doc3.pdf"), other / "doc3.pdf")
    outdir = str(tmp_path / "rot")
    assert main(["-q", "rotate", os.path.join(work_dir, "doc3.pdf"), str(other / "doc3.pdf"),
                 "-o", outdir]) == 0
    assert sorted(os.listdir(outdir)) == ["doc3-2.rotated.pdf", "doc3.rotated.pdf"]


def test_text_and_info_on_many_inputs(work_dir, tmp_path, capsys):
    pattern = os.path.join(work_dir, "doc*.pdf")
    assert main(["text", pattern, "--pages", "1"]) == 0
    out = capsys.readouterr().out
    assert "==> " in out and "Memo - page 1" in out and "Report - page 1" in out

    txt_dir = str(tmp_path / "txt")
    assert main(["-q", "text", pattern, "-o", txt_dir]) == 0
    assert sorted(os.listdir(txt_dir)) == ["doc3.txt", "doc5.txt"]

    assert main(["info", pattern]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert sorted(r["page_count"] for r in rows) == [3, 5]


def test_fan_out_commands_get_a_folder_per_input(work_dir, tmp_path):
    outdir = str(tmp_path / "parts")
    assert main(["-q", "split", os.path.join(work_dir, "doc*.pdf"), "--every", "2",
                 "-o", outdir]) == 0
    assert sorted(os.listdir(outdir)) == ["doc3", "doc5"]
    assert len(os.listdir(os.path.join(outdir, "doc5"))) == 3


def test_ocr_batch_with_fake_engine(work_dir, scanned_pdf, tmp_path, fake_tesseract, capsys):
    shutil.copy(scanned_pdf, os.path.join(work_dir, "scan.pdf"))
    outdir = str(tmp_path / "searchable")
    assert main(["ocr", os.path.join(work_dir, "*.pdf"), "--mode", "pdf", "--dpi", "72",
                 "-o", outdir]) == 0
    assert sorted(os.listdir(outdir)) == [
        "doc3.searchable.pdf", "doc5.searchable.pdf", "scan.searchable.pdf",
    ]
    assert len(fake_tesseract.calls) == 1  # only the scan needed OCR
