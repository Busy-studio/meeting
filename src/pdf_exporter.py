"""Create a two-page meeting PDF: existing Excel form, then stamped receipt.

The receipt remains in session memory; this module uses temporary files only for
the spreadsheet-to-PDF conversion. No raw receipt is uploaded to any database.
"""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter, Transformation
from pypdf._page import PageObject
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


def _ensure_one_page_print_setup(workbook: bytes) -> bytes:
    """Set Calc's fit-to-one-page print flags without modifying the Excel export."""
    with zipfile.ZipFile(io.BytesIO(workbook), "r") as source:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as target:
            for entry in source.infolist():
                content = source.read(entry.filename)
                if entry.filename == "xl/worksheets/sheet1.xml":
                    xml = content.decode("utf-8")
                    if re.search(r"<(?:\w+:)?sheetPr\b", xml):
                        if not re.search(r"<(?:\w+:)?pageSetUpPr\b", xml):
                            xml = re.sub(
                                r"(<(?:\w+:)?sheetPr\b[^>]*>)",
                                r'\1<pageSetUpPr fitToPage="1"/>',
                                xml,
                                count=1,
                            )
                        else:
                            xml = re.sub(
                                r"<((?:\w+:)?pageSetUpPr)\b[^>]*/>",
                                r'<\1 fitToPage="1"/>',
                                xml,
                                count=1,
                            )
                    else:
                        xml = re.sub(
                            r"(<(?:\w+:)?worksheet\b[^>]*>)",
                            r'\1<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>',
                            xml,
                            count=1,
                        )

                    setup_match = re.search(r"<((?:\w+:)?pageSetup)\b[^>]*/>", xml)
                    if setup_match:
                        tag = setup_match.group(1)
                        original = setup_match.group(0)
                        updated = original
                        for name in ("fitToWidth", "fitToHeight"):
                            if re.search(rf'\b{name}="[^"]*"', updated):
                                updated = re.sub(rf'\b{name}="[^"]*"', f'{name}="1"', updated)
                            else:
                                updated = updated.replace("/>", f' {name}="1"/>')
                        xml = xml.replace(original, updated, 1)
                    else:
                        # pageSetup belongs after pageMargins and before headerFooter.
                        margin = re.search(r"<(?:\w+:)?pageMargins\b[^>]*/>", xml)
                        if margin:
                            i = margin.end()
                            xml = xml[:i] + '<pageSetup fitToWidth="1" fitToHeight="1"/>' + xml[i:]
                        else:
                            footer = re.search(r"<(?:\w+:)?headerFooter\b", xml)
                            if footer:
                                i = footer.start()
                            else:
                                i = xml.rfind("</")
                            xml = xml[:i] + '<pageSetup fitToWidth="1" fitToHeight="1"/>' + xml[i:]
                    content = xml.encode("utf-8")
                target.writestr(entry, content)
        return output.getvalue()


def _excel_page(workbook: bytes) -> PageObject:
    office = shutil.which("libreoffice") or shutil.which("soffice")
    if not office:
        raise RuntimeError(
            "회의록 PDF 변환에 LibreOffice가 필요합니다. "
            "Streamlit 배포 환경에 packages.txt의 libreoffice-calc를 설치해 주세요."
        )

    with tempfile.TemporaryDirectory(prefix="meeting-pdf-") as folder:
        root = Path(folder)
        xlsx_path = root / "meeting.xlsx"
        xlsx_path.write_bytes(_ensure_one_page_print_setup(workbook))
        profile = (root / "lo-profile").as_uri()
        command = [
            office,
            f"-env:UserInstallation={profile}",
            "--headless",
            "--convert-to",
            "pdf:calc_pdf_Export",
            "--outdir",
            str(root),
            str(xlsx_path),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=90,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("회의록 PDF 변환 시간이 초과되었습니다.") from exc

        pdf_path = root / "meeting.pdf"
        if result.returncode != 0 or not pdf_path.exists():
            detail = (result.stderr or result.stdout or "").strip()[:300]
            raise RuntimeError(f"회의록 PDF 변환에 실패했습니다. {detail}")
        document = PdfReader(io.BytesIO(pdf_path.read_bytes()), strict=False)
        if len(document.pages) != 1:
            raise RuntimeError(
                "회의록 엑셀을 1페이지 PDF로 변환하지 못했습니다. "
                f"현재 {len(document.pages)}페이지로 출력됩니다."
            )
        page = document.pages[0]
        page.transfer_rotation_to_content()
        return page


def _register_korean_font() -> str:
    name = "ReceiptNanumGothic"
    if name in pdfmetrics.getRegisteredFontNames():
        return name
    candidates = [
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothicCoding.ttf"),
    ]
    for font_path in candidates:
        if font_path.is_file():
            pdfmetrics.registerFont(TTFont(name, str(font_path)))
            return name
    raise RuntimeError(
        "영수증에 한글 과세유형을 표시할 글꼴이 없습니다. "
        "배포 환경에 packages.txt의 fonts-nanum을 설치해 주세요."
    )


def _receipt_page(receipt_pdf: bytes, tax_type: str) -> PageObject:
    try:
        document = PdfReader(io.BytesIO(receipt_pdf), strict=False)
        if document.is_encrypted and not document.decrypt(""):
            raise RuntimeError("암호화된 영수증 PDF는 처리할 수 없습니다.")
        if len(document.pages) != 1:
            raise RuntimeError(
                "2페이지 통합 PDF는 1페이지 영수증을 기준으로 합니다. "
                f"현재 영수증이 {len(document.pages)}페이지입니다."
            )
        original = document.pages[0]
        original.transfer_rotation_to_content()
        width = float(original.mediabox.width)
        height = float(original.mediabox.height)
        if width <= 0 or height <= 0:
            raise RuntimeError("영수증 PDF의 페이지 크기가 올바르지 않습니다.")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("영수증 PDF를 읽지 못했습니다. 파일을 확인해 주세요.") from exc

    # Keep the complete receipt on a single page and reserve a clear footer.
    footer_height = 44.0
    margin = 8.0
    available_h = max(1.0, height - footer_height - margin)
    scale = min(1.0, (width - 2 * margin) / width, available_h / height)
    receipt_width = width * scale
    receipt_height = height * scale
    x = (width - receipt_width) / 2
    y = footer_height + (height - footer_height - receipt_height) / 2
    stamped = PageObject.create_blank_page(width=width, height=height)
    stamped.merge_transformed_page(
        original,
        Transformation().scale(scale).translate(x, y),
        expand=False,
    )

    font_name = _register_korean_font()
    label = f"과세유형: {tax_type}"
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    pdf.setFillColorRGB(1, 1, 1)
    pdf.rect(0, 0, width, footer_height, fill=1, stroke=0)
    pdf.setStrokeColorRGB(0.3, 0.3, 0.3)
    pdf.line(margin, footer_height - 2, width - margin, footer_height - 2)
    size = min(14.0, max(8.0, width / 25.0))
    while size > 7 and pdfmetrics.stringWidth(label, font_name, size) > width - 2 * margin:
        size -= 0.5
    pdf.setFont(font_name, size)
    pdf.setFillColorRGB(0, 0, 0)
    pdf.drawCentredString(width / 2, 15, label)
    pdf.save()
    buffer.seek(0)
    footer = PdfReader(buffer).pages[0]
    stamped.merge_page(footer)
    return stamped


def tax_type_label(status: dict | None) -> str:
    """Only report a tax category actually returned by the NTS lookup."""
    status = status or {}
    if status.get("error"):
        return "미확인 (국세청 조회 필요)"
    raw = str(status.get("tax_type") or "").strip()
    if "일반과세" in raw:
        return "일반과세자"
    if "간이과세" in raw:
        return "간이과세자"
    if "면세" in raw:
        return "면세사업자"
    return "미확인 (국세청 조회 필요)"


def build_combined_meeting_pdf(
    workbook: bytes,
    *,
    receipt_pdf: bytes | None = None,
    tax_type: str = "",
) -> bytes:
    """Return a 1-page meeting PDF, or meeting + receipt in exactly 2 pages."""
    writer = PdfWriter()
    writer.add_page(_excel_page(workbook))
    if receipt_pdf:
        writer.add_page(_receipt_page(receipt_pdf, tax_type or "미확인 (국세청 조회 필요)"))
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
