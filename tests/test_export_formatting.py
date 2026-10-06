import io
import shutil
import unittest
import zipfile
from datetime import date, timedelta
from xml.etree import ElementTree as ET

from src.business_values import business_text
from src.exporter import build_meeting_workbook
from src.pdf_exporter import _ensure_one_page_print_setup, build_combined_meeting_pdf
import pymupdf

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
BUSINESS_CELLS = ('R4', 'AM4', 'BA4', 'BH4', 'K5', 'K6', 'AM6', 'K7', 'AM7', 'K8')


def sheet(workbook):
    with zipfile.ZipFile(io.BytesIO(workbook)) as archive:
        return ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))


def cell(root, address):
    return root.find(f'.//m:c[@r="{address}"]', NS)


def text(root, address):
    return ''.join(node.text or '' for node in cell(root, address).findall('.//m:t', NS))


class ExportFormattingTest(unittest.TestCase):
    def test_optional_business_values(self):
        for value in (None, '', '  ', 'None', ' None ', 'null'):
            with self.subTest(value=value):
                self.assertEqual(business_text(value), '')
                workbook = build_meeting_workbook({
                    'meeting_date': date(2026, 9, 29),
                    'business': {'project_number': value},
                })
                root = sheet(workbook)
                for address in BUSINESS_CELLS:
                    self.assertEqual(text(root, address), '-')
        self.assertEqual(business_text(' PR-2026-01 '), 'PR-2026-01')
        self.assertEqual(business_text(0), '0')

    def test_pdf_dates_are_korean_text_and_excel_remains_numeric(self):
        dates = [date(2026, 9, 28) + timedelta(days=i) for i in range(7)]
        dates += [date(2024, 2, 29), date(2026, 12, 31), date(2027, 1, 1)]
        for day in dates:
            with self.subTest(day=day):
                workbook = build_meeting_workbook({
                    'meeting_date': day,
                    'business': {'project_number': 'PR-2026-01'},
                })
                original = sheet(workbook)
                prepared = sheet(_ensure_one_page_print_setup(workbook))
                self.assertEqual(cell(original, 'J12').find('m:v', NS).text,
                                 str((day - date(1899, 12, 30)).days))
                self.assertEqual(text(prepared, 'J12'),
                                 f'{day:%Y/%m/%d}({"월화수목금토일"[day.weekday()]})')
                self.assertEqual(cell(original, 'J12').get('s'), cell(prepared, 'J12').get('s'))
                self.assertEqual(text(prepared, 'K6'), 'PR-2026-01')
                self.assertEqual(text(sheet(workbook), 'J12'), '')

    @unittest.skipUnless(shutil.which('libreoffice') or shutil.which('soffice'),
                         'LibreOffice is required for actual PDF conversion')
    def test_actual_pdf_contains_correct_date_and_no_none(self):
        workbook = build_meeting_workbook({
            'meeting_date': date(2026, 9, 29),
            'business': {'name': '테스트 사업', 'project_number': None},
        })
        pdf = pymupdf.open(stream=build_combined_meeting_pdf(workbook), filetype='pdf')
        self.assertEqual(len(pdf), 1)
        content = ''.join(pdf[0].get_text().split())
        self.assertIn('2026/09/29(화)', content)
        self.assertNotIn('None', content)
        self.assertNotIn('Tue', content)
        self.assertIn('과제번호-', content)


if __name__ == '__main__':
    unittest.main()
