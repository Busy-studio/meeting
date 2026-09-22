"""Choose the final meeting download based on whether a receipt was attached."""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass


EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass(frozen=True)
class FinalDownload:
    data: bytes
    filename: str
    mime: str


def build_final_download(
    workbook: bytes,
    workbook_name: str,
    *,
    receipt_attached: bool,
    combined_pdf: bytes | None = None,
) -> FinalDownload:
    """Download Excel alone, or package Excel and the receipt-inclusive PDF."""
    if not workbook or not workbook_name.lower().endswith(".xlsx"):
        raise ValueError("회의록 Excel 파일이 올바르지 않습니다.")

    if not receipt_attached:
        # A stale PDF cache must never change the no-receipt download format.
        return FinalDownload(workbook, workbook_name, EXCEL_MIME)

    if not combined_pdf:
        raise ValueError("영수증이 첨부된 경우 통합 PDF가 준비되어야 합니다.")

    stem = workbook_name[:-5]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(workbook_name, workbook)
        archive.writestr(f"{stem}_영수증포함.pdf", combined_pdf)
    return FinalDownload(output.getvalue(), f"{stem}.zip", "application/zip")
