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
import xml.etree.ElementTree as ET
from pathlib import Path

from pypdf import PdfReader, PdfWriter, Transformation
from pypdf._page import PageObject
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


def _ensure_one_page_print_setup(workbook: bytes) -> bytes:
    """Configure only the temporary PDF copy, with namespace-aware OOXML."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    def tag(name: str) -> str:
        return f"{{{ns}}}{name}"

    with zipfile.ZipFile(io.BytesIO(workbook), "r") as source:
        sheet = ET.fromstring(source.read("xl/worksheets/sheet1.xml"))
        props = sheet.find(tag("sheetPr"))
        if props is None:
            props = ET.Element(tag("sheetPr"))
            sheet.insert(0, props)
        fit = props.find(tag("pageSetUpPr"))
        if fit is None:
            fit = ET.SubElement(props, tag("pageSetUpPr"))
        fit.set("fitToPage", "1")
        fit.set("autoPageBreaks", "0")
        for name in ("rowBreaks", "colBreaks"):
            for node in list(sheet.findall(tag(name))):
                sheet.remove(node)
        setup = sheet.find(tag("pageSetup"))
        if setup is None:
            setup = ET.Element(tag("pageSetup"))
            margins = sheet.find(tag("pageMargins"))
            if margins is None:
                margins = ET.Element(tag("pageMargins"), {
                    "left": "0.7", "right": "0.7", "top": "0.75",
                    "bottom": "0.75", "header": "0.3", "footer": "0.3",
                })
                # These elements precede pageMargins in the worksheet schema.
                predecessors = {tag(n) for n in (
                    "sheetPr", "dimension", "sheetViews", "sheetFormatPr", "cols",
                    "sheetData", "sheetCalcPr", "sheetProtection", "protectedRanges",
                    "scenarios", "autoFilter", "sortState", "dataConsolidate",
                    "customSheetViews", "mergeCells", "phoneticPr",
                    "conditionalFormatting", "dataValidations", "hyperlinks", "printOptions",
                )}
                index = max((i + 1 for i, child in enumerate(sheet)
                             if child.tag in predecessors), default=0)
                sheet.insert(index, margins)
            sheet.insert(list(sheet).index(margins) + 1, setup)
        setup.attrib.pop("scale", None)
        setup.attrib.update(fitToWidth="1", fitToHeight="1", paperSize="9", orientation="portrait")

        # The template's lower horizontal rule starts at row 20. Exclude
        # the trailing rows and decorative objects outside the meeting form.
        margins = sheet.find(tag("pageMargins"))
        if margins is not None:
            margins.attrib.update(left="0.2", right="0.2", top="0.2", bottom="0.2",
                                  header="0", footer="0")
        print_options = sheet.find(tag("printOptions"))
        if print_options is None:
            print_options = ET.Element(tag("printOptions"))
            sheet.insert(list(sheet).index(margins), print_options)
        print_options.set("horizontalCentered", "1")
        print_options.set("verticalCentered", "1")

        book = ET.fromstring(source.read("xl/workbook.xml"))
        sheets = book.find(tag("sheets"))
        if sheets is None or not len(sheets):
            raise RuntimeError("회의록 Excel 시트를 찾을 수 없습니다.")
        # The exporter fills sheet1; the other sheet contains lookup data only.
        sheets[0].set("state", "visible")
        for other in list(sheets)[1:]:
            other.set("state", "hidden")
        for view in book.iter(tag("workbookView")):
            view.set("activeTab", "0")
            view.set("firstSheet", "0")

        names = book.find(tag("definedNames"))
        if names is None:
            names = ET.Element(tag("definedNames"))
            book.insert(list(book).index(sheets) + 1, names)
        for defined in list(names):
            if defined.get("name") == "_xlnm.Print_Area":
                names.remove(defined)
        print_area = ET.SubElement(names, tag("definedName"), {
            "name": "_xlnm.Print_Area", "localSheetId": "0",
        })
        sheet_name = sheets[0].get("name", "회의록").replace("'", "''")
        print_area.text = f"'{sheet_name}'!$B$1:$BK$20"

        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for entry in source.infolist():
                content = source.read(entry.filename)
                if entry.filename == "xl/worksheets/sheet1.xml":
                    content = ET.tostring(sheet, encoding="utf-8", xml_declaration=True)
                elif entry.filename == "xl/workbook.xml":
                    content = ET.tostring(book, encoding="utf-8", xml_declaration=True)
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
