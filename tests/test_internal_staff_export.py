import io
import unittest
import zipfile
from datetime import date
from xml.etree import ElementTree as ET

from src.exporter import build_meeting_workbook
from src.final_download import build_final_download
from src.internal_staff import merge_participants


def cell_text(xml_bytes: bytes, address: str) -> str:
    root = ET.fromstring(xml_bytes)
    for node in root.iter():
        if node.tag.split("}")[-1] == "c" and node.get("r") == address:
            return "".join((el.text or "") for el in node.iter() if el.tag.split("}")[-1] == "t")
    raise AssertionError(f"Missing workbook cell {address}")


class StaffExcelIntegrationTest(unittest.TestCase):
    def test_checked_staff_and_external_names_are_written_in_existing_template(self):
        participants = merge_participants(
            [{"name": "김성근", "title": "실장"}, {"name": "박성호", "title": "팀장"}],
            "KOITA 이창주 실장, 세종대학교 홍서경 팀장",
        )
        workbook = build_meeting_workbook({
            "business": {"name": "테스트 지원사업", "round_no": 1},
            "meeting_date": date(2026, 9, 23),
            "participants": participants,
            "purpose": "참석자 병합 테스트",
            "meeting_content": "회의 내용",
            "future_plan": "향후 계획",
        })
        with zipfile.ZipFile(io.BytesIO(workbook)) as archive:
            self.assertEqual(cell_text(archive.read("xl/worksheets/sheet1.xml"), "J14"), participants)

        # No receipt: retain the existing single Excel download.
        download = build_final_download(workbook, "회의록.xlsx", receipt_attached=False)
        self.assertEqual(download.data, workbook)
        self.assertEqual(download.filename, "회의록.xlsx")

    def test_no_checked_staff_keeps_external_names(self):
        self.assertEqual(
            merge_participants([], "KOITA 이창주 실장, 세종대학교 홍서경 팀장"),
            "KOITA 이창주 실장, 세종대학교 홍서경 팀장",
        )


if __name__ == "__main__":
    unittest.main()
