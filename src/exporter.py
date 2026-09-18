from __future__ import annotations

import base64
import io
import re
import zipfile
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape


TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "assets" / "meeting_template.b64"


def _cell_style(xml: str, address: str) -> str:
    pattern = rf'<(?P<prefix>[A-Za-z0-9_]+:)?c\b[^>]*\br="{re.escape(address)}"[^>]*(?:>.*?</(?:[A-Za-z0-9_]+:)?c>|/>)'
    match = re.search(pattern, xml, flags=re.DOTALL)
    if not match:
        raise RuntimeError(f"회의록 템플릿에서 셀을 찾을 수 없습니다: {address}")
    style = re.search(r'\bs="([^"]+)"', match.group(0))
    return style.group(1) if style else ""


def _replace_cell(xml: str, address: str, body_builder, cell_type: str | None = None) -> str:
    pattern = rf'<(?P<prefix>[A-Za-z0-9_]+:)?c\b[^>]*\br="{re.escape(address)}"[^>]*(?:>.*?</(?:[A-Za-z0-9_]+:)?c>|/>)'
    match = re.search(pattern, xml, flags=re.DOTALL)
    if not match:
        raise RuntimeError(f"회의록 템플릿에서 셀을 찾을 수 없습니다: {address}")
    original = match.group(0)
    style_match = re.search(r'\bs="([^"]+)"', original)
    style = style_match.group(1) if style_match else ""
    prefix = match.group("prefix") or ""
    attrs = f' r="{address}"'
    if style:
        attrs += f' s="{style}"'
    if cell_type:
        attrs += f' t="{cell_type}"'
    body = body_builder(prefix)
    replacement = f"<{prefix}c{attrs}>{body}</{prefix}c>"
    return xml[: match.start()] + replacement + xml[match.end() :]


def _set_text(xml: str, address: str, value: object) -> str:
    text = "" if value is None else str(value)
    safe = escape(text)
    return _replace_cell(
        xml,
        address,
        lambda p: f'<{p}is><{p}t xml:space="preserve">{safe}</{p}t></{p}is>',
        "inlineStr",
    )


def _set_number(xml: str, address: str, value: int | float) -> str:
    return _replace_cell(xml, address, lambda p: f"<{p}v>{value}</{p}v>")


def _excel_serial(value: date) -> int:
    return (value - date(1899, 12, 30)).days


def _bullet_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"^[\-•ㆍ·]\s*", "", line)
        lines.append(line)
    return lines


def compose_content_block(meeting_content: str, future_plan: str) -> str:
    content = _bullet_lines(meeting_content)
    future = _bullet_lines(future_plan)
    parts = ["1. 회의내용"]
    parts.extend(f" - {line}" for line in content)
    parts.extend(["", "2. 향후계획"])
    parts.extend(f" - {line}" for line in future)
    return "\n".join(parts).rstrip()


def build_meeting_workbook(data: dict[str, object]) -> bytes:
    if not TEMPLATE_PATH.exists():
        raise RuntimeError("회의록 Excel 템플릿 파일이 없습니다.")

    template_bytes = base64.b64decode(TEMPLATE_PATH.read_text(encoding="ascii"))
    with zipfile.ZipFile(io.BytesIO(template_bytes), "r") as zin:
        sheet_xml = zin.read("xl/worksheets/sheet1.xml").decode("utf-8")

        business = dict(data.get("business") or {})
        meeting_date = data.get("meeting_date")
        if not isinstance(meeting_date, date):
            raise RuntimeError("회의일자가 올바르지 않습니다.")

        values = {
            "R4": business.get("principal_affiliation", ""),
            "AM4": business.get("principal_title", ""),
            "BA4": business.get("principal_name", ""),
            "BH4": business.get("principal_name", ""),
            "K5": business.get("research_project_name", ""),
            "K6": business.get("project_number", ""),
            "AM6": business.get("support_organization", ""),
            "K7": business.get("total_research_period", ""),
            "AM7": business.get("round_research_period", ""),
            "K8": business.get("name", ""),
            "X12": data.get("meeting_time", ""),
            "AT12": data.get("author_name", ""),
            "J13": data.get("meeting_place", ""),
            "AT13": data.get("card_merchant", ""),
            "J14": data.get("participants", ""),
            "J16": data.get("purpose", ""),
            "J17": data.get("amount_raw", ""),
            "J18": compose_content_block(
                str(data.get("meeting_content", "")),
                str(data.get("future_plan", "")),
            ),
        }
        for address, value in values.items():
            sheet_xml = _set_text(sheet_xml, address, value)
        sheet_xml = _set_number(sheet_xml, "J12", _excel_serial(meeting_date))
        round_no = int(business.get("round_no") or 1)
        sheet_xml = _set_number(sheet_xml, "AC7", round_no)

        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                name = item.filename
                if name == "xl/calcChain.xml":
                    continue
                content = zin.read(name)
                if name == "xl/worksheets/sheet1.xml":
                    content = sheet_xml.encode("utf-8")
                elif name == "xl/_rels/workbook.xml.rels":
                    text = content.decode("utf-8")
                    text = re.sub(r'<Relationship\b[^>]*Target="calcChain\.xml"[^>]*/>', "", text)
                    content = text.encode("utf-8")
                elif name == "[Content_Types].xml":
                    text = content.decode("utf-8")
                    text = re.sub(r'<Override\b[^>]*PartName="/xl/calcChain\.xml"[^>]*/>', "", text)
                    content = text.encode("utf-8")
                zout.writestr(item, content)
        return output.getvalue()


def export_filename(business_name: str, meeting_date: date, author_name: str) -> str:
    short_business = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", business_name).strip("_")[:28] or "사업"
    author = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", author_name).strip("_")[:12] or "작성자"
    return f"회의록_{short_business}_{meeting_date:%y%m%d}_{author}.xlsx"