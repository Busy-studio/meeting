from __future__ import annotations

import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

import pymupdf


A4_WIDTH = 595.28
A4_HEIGHT = 841.89
MARGIN = 32.0
LABEL_WIDTH = 108.0
FONT_NAME = "korea"


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _fit_textbox(
    page: pymupdf.Page,
    rect: pymupdf.Rect,
    text: str,
    *,
    fontsize: float = 9.0,
    min_size: float = 6.0,
    align: int = 0,
) -> None:
    value = _text(text).strip()
    size = fontsize
    while size >= min_size:
        result = page.insert_textbox(
            rect,
            value,
            fontsize=size,
            fontname=FONT_NAME,
            color=(0, 0, 0),
            align=align,
            lineheight=1.15,
        )
        if result >= 0:
            return
        size -= 0.5
    page.insert_textbox(
        rect,
        value[:800],
        fontsize=min_size,
        fontname=FONT_NAME,
        color=(0, 0, 0),
        align=align,
        lineheight=1.05,
    )


def _row(
    page: pymupdf.Page,
    y: float,
    label: str,
    value: object,
    *,
    height: float = 34.0,
    label_width: float = LABEL_WIDTH,
) -> float:
    outer = pymupdf.Rect(MARGIN, y, A4_WIDTH - MARGIN, y + height)
    page.draw_rect(outer, color=(0.25, 0.25, 0.25), width=0.6)
    page.draw_line(
        pymupdf.Point(MARGIN + label_width, y),
        pymupdf.Point(MARGIN + label_width, y + height),
        color=(0.25, 0.25, 0.25),
        width=0.6,
    )
    page.draw_rect(
        pymupdf.Rect(MARGIN, y, MARGIN + label_width, y + height),
        color=None,
        fill=(0.96, 0.96, 0.96),
        overlay=False,
    )
    _fit_textbox(
        page,
        pymupdf.Rect(MARGIN + 5, y + 5, MARGIN + label_width - 5, y + height - 4),
        label,
        fontsize=8.5,
        min_size=7,
        align=1,
    )
    _fit_textbox(
        page,
        pymupdf.Rect(MARGIN + label_width + 7, y + 5, A4_WIDTH - MARGIN - 7, y + height - 4),
        _text(value),
        fontsize=8.5,
        min_size=6.5,
    )
    return y + height


def _meeting_page(pdf: pymupdf.Document, data: dict[str, object]) -> None:
    page = pdf.new_page(width=A4_WIDTH, height=A4_HEIGHT)
    page.insert_textbox(
        pymupdf.Rect(MARGIN, 22, A4_WIDTH - MARGIN, 54),
        "회 의 록",
        fontsize=18,
        fontname=FONT_NAME,
        align=1,
        color=(0, 0, 0),
    )

    business = dict(data.get("business") or {})
    principal = " ".join(
        part
        for part in [
            _text(business.get("principal_affiliation")).strip(),
            _text(business.get("principal_title")).strip(),
            _text(business.get("principal_name")).strip(),
        ]
        if part
    )

    y = 62.0
    y = _row(page, y, "지원사업명", business.get("name", ""), height=32)
    y = _row(page, y, "연구과제명", business.get("research_project_name", ""), height=38)
    y = _row(page, y, "과제번호", business.get("project_number", ""), height=30)
    y = _row(page, y, "연구책임자", principal, height=30)

    meeting_date = data.get("meeting_date")
    date_text = meeting_date.isoformat() if isinstance(meeting_date, date) else _text(meeting_date)
    y = _row(page, y, "회의일자 / 시간", f"{date_text}  {_text(data.get('meeting_time'))}", height=32)
    y = _row(page, y, "회의장소", data.get("meeting_place", ""), height=30)
    y = _row(page, y, "작성자", data.get("author_name", ""), height=30)
    y = _row(page, y, "카드 사용처", data.get("card_merchant", ""), height=30)
    y = _row(page, y, "소요금액", data.get("amount_raw", ""), height=30)
    y = _row(page, y, "참석자", data.get("participants", ""), height=48)
    y = _row(page, y, "회의 목적", data.get("purpose", ""), height=64)

    content = _text(data.get("content_block"))
    if not content:
        from src.exporter import compose_content_block
        content = compose_content_block(
            _text(data.get("meeting_content")),
            _text(data.get("future_plan")),
        )
    remaining = max(170.0, A4_HEIGHT - MARGIN - y)
    _row(page, y, "회의 내용 및\n향후 계획", content, height=remaining)


def _convert_workbook_to_pdf(workbook_bytes: bytes) -> bytes | None:
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if not executable:
        return None

    try:
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
                return None
            return pdf_path.read_bytes()
    except Exception:
        return None


def _append_meeting_page(
    pdf: pymupdf.Document,
    *,
    workbook_bytes: bytes,
    data: dict[str, object],
) -> None:
    converted = _convert_workbook_to_pdf(workbook_bytes)
    if converted:
        source = pymupdf.open(stream=converted, filetype="pdf")
        try:
            if source.page_count:
                pdf.insert_pdf(source, from_page=0, to_page=0)
                return
        finally:
            source.close()

    _meeting_page(pdf, data)


def _receipt_page(
    pdf: pymupdf.Document,
    receipt_bytes: bytes,
    receipt_filename: str,
    tax_label: str,
) -> None:
    page = pdf.new_page(width=A4_WIDTH, height=A4_HEIGHT)
    page.insert_textbox(
        pymupdf.Rect(MARGIN, 18, A4_WIDTH - MARGIN, 45),
        "영수증",
        fontsize=14,
        fontname=FONT_NAME,
        align=1,
        color=(0, 0, 0),
    )

    content_rect = pymupdf.Rect(MARGIN, 50, A4_WIDTH - MARGIN, A4_HEIGHT - 92)
    suffix = Path(receipt_filename or "").suffix.lower()
    if suffix == ".pdf":
        source = pymupdf.open(stream=receipt_bytes, filetype="pdf")
        try:
            if source.page_count < 1:
                raise RuntimeError("영수증 PDF에 페이지가 없습니다.")
            page.show_pdf_page(content_rect, source, 0, keep_proportion=True)
        finally:
            source.close()
    else:
        page.insert_image(content_rect, stream=receipt_bytes, keep_proportion=True)

    page.draw_line(
        pymupdf.Point(MARGIN, A4_HEIGHT - 76),
        pymupdf.Point(A4_WIDTH - MARGIN, A4_HEIGHT - 76),
        color=(0.35, 0.35, 0.35),
        width=0.7,
    )
    footer = tax_label if tax_label in {"일반과세자", "간이과세자"} else "과세유형 확인 필요"
    page.insert_textbox(
        pymupdf.Rect(MARGIN, A4_HEIGHT - 68, A4_WIDTH - MARGIN, A4_HEIGHT - 30),
        footer,
        fontsize=13,
        fontname=FONT_NAME,
        align=1,
        color=(0, 0, 0),
    )


def build_meeting_receipt_pdf(
    data: dict[str, object],
    *,
    workbook_bytes: bytes,
    receipt_bytes: bytes,
    receipt_filename: str,
    tax_label: str,
) -> bytes:
    pdf = pymupdf.open()
    try:
        _append_meeting_page(pdf, workbook_bytes=workbook_bytes, data=data)
        _receipt_page(pdf, receipt_bytes, receipt_filename, tax_label)
        return pdf.tobytes(garbage=4, deflate=True)
    finally:
        pdf.close()


def pdf_export_filename(xlsx_filename: str) -> str:
    stem = Path(xlsx_filename).stem or "회의록"
    return f"{stem}_영수증포함.pdf"
