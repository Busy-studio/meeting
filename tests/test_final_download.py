import io
import unittest
import zipfile

from src.final_download import EXCEL_MIME, build_final_download


class FinalDownloadTest(unittest.TestCase):
    def test_no_receipt_downloads_only_original_excel(self):
        result = build_final_download(b"workbook", "회의록.xlsx", receipt_attached=False)
        self.assertEqual(result.data, b"workbook")
        self.assertEqual(result.filename, "회의록.xlsx")
        self.assertEqual(result.mime, EXCEL_MIME)

    def test_receipt_downloads_zip_with_excel_and_combined_pdf(self):
        result = build_final_download(
            b"workbook", "회의록.xlsx", receipt_attached=True, combined_pdf=b"combined-pdf"
        )
        self.assertEqual(result.filename, "회의록.zip")
        self.assertEqual(result.mime, "application/zip")
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            self.assertEqual(archive.namelist(), ["회의록.xlsx", "회의록_영수증포함.pdf"])
            self.assertEqual(archive.read("회의록.xlsx"), b"workbook")
            self.assertEqual(archive.read("회의록_영수증포함.pdf"), b"combined-pdf")

    def test_receipt_does_not_fall_back_to_excel_if_pdf_fails(self):
        with self.assertRaisesRegex(ValueError, "통합 PDF"):
            build_final_download(b"workbook", "회의록.xlsx", receipt_attached=True)

    def test_no_receipt_ignores_stale_pdf(self):
        result = build_final_download(
            b"workbook", "회의록.xlsx", receipt_attached=False, combined_pdf=b"stale-pdf"
        )
        self.assertEqual(result.data, b"workbook")
        self.assertEqual(result.filename, "회의록.xlsx")

    def test_invalid_workbook_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Excel"):
            build_final_download(b"", "회의록.xlsx", receipt_attached=False)


if __name__ == "__main__":
    unittest.main()
