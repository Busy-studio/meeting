from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf


A4_WIDTH = 595.28
A4_HEIGHT = 841.89


def tax_type_label(tax_type: object) -> str:
    text = str(tax_type or "").strip()
    if "간이과세자" in text:
        return "간이과세자"
    if "일반과세자" in text:
        return "일반과세자"
    return "과세유형 확인 필요"


def _workbook_first_page_pdf(workbook_bytes: bytes) -> bytes:
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if not executable:
        raise RuntimeError("Excel PDF 변환용 LibreOffice를 찾을 수 없습니다.")

    with tempfile.TemporaryDirectory(prefix="meeting_pdf_") as tmpdir:
        root = Path(tmpdir)
        xlsx_path = root / "meeting.xlsx"
        pdf_path = root / "meeting.pdf"
        profile_uri = (root / "lo-profile").resolve().as_uri()
        xlsx_path.write_bytes(workbook_bytes)

        completed = subprocess.run(
            [
                executable,
                f"-env:UserInstallation={profile_uri}",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(root),
                str(xlsx_path),
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if completed.returncode != 0 or not pdf_path.exists():
            detail = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(f"Excel PDF 변환에 실패했습니다. {detail[:500]}")
        return pdf_path.read_bytes()


def build_meeting_receipt_pdf(
    *,
    workbook_bytes: bytes,
    receipt_bytes: bytes,
    tax_type: object,
) -> bytes:
    meeting_pdf_bytes = _workbook_first_page_pdf(workbook_bytes)

    meeting_pdf = pymupdf.open(stream=meeting_pdf_bytes, filetype="pdf")
    receipt_pdf = pymupdf.open(stream=receipt_bytes, filetype="pdf")
    output = pymupdf.open()
    try:
        if meeting_pdf.page_count < 1:
            raise RuntimeError("회의록 PDF 출력 페이지를 만들지 못했습니다.")
        if receipt_pdf.page_count < 1:
            raise RuntimeError("영수증 PDF에 페이지가 없습니다.")

        # 1페이지: 생성된 Excel 회의록을 실제 PDF로 변환한 첫 페이지.
        output.insert_pdf(meeting_pdf, from_page=0, to_page=0)

        # 2페이지: 영수증 원본 첫 페이지를 A4 안에 비율 유지하여 배치.
        page = output.new_page(width=A4_WIDTH, height=A4_HEIGHT)
        receipt_rect = pymupdf.Rect(28, 28, A4_WIDTH - 28, A4_HEIGHT - 88)
        page.show_pdf_page(receipt_rect, receipt_pdf, 0, keep_proportion=True)

        page.draw_line(
            pymupdf.Point(36, A4_HEIGHT - 72),
            pymupdf.Point(A4_WIDTH - 36, A4_HEIGHT - 72),
            color=(0.35, 0.35, 0.35),
            width=0.8,
        )
        label = tax_type_label(tax_type)
        page.insert_textbox(
            pymupdf.Rect(36, A4_HEIGHT - 65, A4_WIDTH - 36, A4_HEIGHT - 28),
            label,
            fontsize=13,
            fontname="korea",
            align=1,
            color=(0, 0, 0),
        )

        return output.tobytes(garbage=4, deflate=True)
    finally:
        output.close()
        receipt_pdf.close()
        meeting_pdf.close()


def pdf_export_filename(xlsx_filename: str) -> str:
    stem = Path(xlsx_filename).stem or "회의록"
    return f"{stem}_영수증포함.pdf"
