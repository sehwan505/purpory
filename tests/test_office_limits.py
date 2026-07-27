"""Resource-cap guards for parsing untrusted office/PDF files (F2).

.docx/.xlsx are zip+XML containers; a few-KB zip-bomb can decompress to
gigabytes and OOM-kill the process during a corpus scan. These tests verify the
pre-parse screen rejects bombs before openpyxl/python-docx ever decompress them.
"""
import zipfile
import sys
import types

import pytest
from purpory import detect


def _write_zip(path, name, payload):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, payload)


def test_file_within_size_cap(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"x" * 1024)
    assert detect._file_within_size_cap(f) is True          # within default cap
    assert detect._file_within_size_cap(f, cap=512) is False  # over an explicit small cap
    assert detect._file_within_size_cap(tmp_path / "missing") is False


def test_zip_ratio_bomb_rejected(tmp_path):
    """A tiny file that expands far past the ratio threshold is rejected."""
    bomb = tmp_path / "bomb.xlsx"
    _write_zip(bomb, "xl/worksheets/sheet1.xml", b"0" * (5 * 1024 * 1024))  # 5 MiB of zeros -> tiny zip
    assert bomb.stat().st_size < 100 * 1024  # compressed to well under 100 KiB
    assert detect._zip_within_caps(bomb) is False


def test_legit_zip_passes(tmp_path):
    ok = tmp_path / "ok.docx"
    _write_zip(ok, "word/document.xml", b"<xml>hello world</xml>" * 20)
    assert detect._zip_within_caps(ok) is True


def test_non_zip_rejected(tmp_path):
    notzip = tmp_path / "fake.xlsx"
    notzip.write_bytes(b"this is not a zip file")
    assert detect._zip_within_caps(notzip) is False


def test_converters_return_empty_for_bomb(tmp_path):
    """The live converters bail out (return "") on a bomb before parsing."""
    for ext in (".docx", ".xlsx"):
        bomb = tmp_path / f"bomb{ext}"
        _write_zip(bomb, "x.xml", b"0" * (5 * 1024 * 1024))
        assert detect.docx_to_markdown(bomb) == ""
        assert detect.xlsx_to_markdown(bomb) == ""


def test_legit_multi_member_passes_streaming(tmp_path):
    """A normal multi-member office zip passes the streaming-ceiling pass."""
    ok = tmp_path / "ok.xlsx"
    with zipfile.ZipFile(ok, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", b"<types/>")
        zf.writestr("xl/workbook.xml", b"<workbook/>" * 100)
        zf.writestr("xl/worksheets/sheet1.xml", b"<sheetData>rows</sheetData>" * 500)
    assert detect._zip_within_caps(ok) is True


def test_streaming_ceiling_rejects_oversized_actual(tmp_path, monkeypatch):
    """With a low decompressed cap, content whose actual bytes exceed it is rejected.

    This exercises the authoritative bounded-decompression pass: the function
    reads real decompressed bytes (not the attacker-declared central-directory
    sizes) and stops once the ceiling is crossed.
    """
    monkeypatch.setattr(detect, "_OFFICE_MAX_DECOMPRESSED_BYTES", 64 * 1024)  # 64 KiB
    f = tmp_path / "big.xlsx"
    # ~512 KiB of incompressible data: low ratio (passes the ratio pre-filter),
    # but real decompressed size far exceeds the 64 KiB ceiling.
    import os as _os
    with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/x.xml", _os.urandom(512 * 1024))
    assert detect._zip_within_caps(f) is False


def test_pdf_over_cap_returns_empty(tmp_path, monkeypatch):
    """A PDF larger than the raw cap is skipped before pypdf opens it."""
    big = tmp_path / "big.pdf"
    big.write_bytes(b"%PDF-1.4\n" + b"x" * 4096)
    # shrink the cap via the helper's default by patching the module constant and
    # calling through a wrapper that reads it fresh
    monkeypatch.setattr(detect, "_OFFICE_MAX_RAW_BYTES", 100)
    monkeypatch.setattr(detect, "_file_within_size_cap",
                        lambda p, cap=100: p.stat().st_size <= cap if p.exists() else False)
    assert detect.extract_pdf_text(big) == ""


@pytest.mark.parametrize(
    ("function_name", "module_name", "message"),
    [
        ("extract_pdf_text", "pypdf", "PDF extraction requires pypdf"),
        ("docx_to_markdown", "docx", "DOCX extraction requires python-docx"),
        ("xlsx_to_markdown", "openpyxl", "XLSX extraction requires openpyxl"),
    ],
)
def test_missing_document_dependency_fails_explicitly(
    tmp_path,
    monkeypatch,
    function_name,
    module_name,
    message,
):
    source = tmp_path / {
        "extract_pdf_text": "doc.pdf",
        "docx_to_markdown": "doc.docx",
        "xlsx_to_markdown": "doc.xlsx",
    }[function_name]
    source.write_bytes(b"placeholder")
    monkeypatch.setattr(detect, "_file_within_size_cap", lambda _path: True)
    monkeypatch.setattr(detect, "_zip_within_caps", lambda _path: True)
    monkeypatch.setitem(sys.modules, module_name, None)

    with pytest.raises(RuntimeError, match=message):
        getattr(detect, function_name)(source)


def test_pdf_parser_failure_is_not_an_empty_document(tmp_path, monkeypatch):
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"%PDF")
    fake = types.ModuleType("pypdf")

    def broken_reader(_path):
        raise ValueError("corrupt xref")

    fake.PdfReader = broken_reader
    monkeypatch.setitem(sys.modules, "pypdf", fake)

    with pytest.raises(RuntimeError, match="could not extract PDF text"):
        detect.extract_pdf_text(source)


def test_count_words_read_failure_propagates(tmp_path):
    with pytest.raises(FileNotFoundError):
        detect.count_words(tmp_path / "missing.md")
