import unittest
from src.internal_staff import merge_participants, overlapping_staff_names, selected_staff_items


class InternalStaffTests(unittest.TestCase):
    def setUp(self):
        self.staff = [
            {"id": "first", "name": "김성근", "title": "실장"},
            {"id": "second", "name": "박성호", "title": "팀장"},
        ]

    def test_internal_first_and_existing_external_unchanged(self):
        self.assertEqual(
            merge_participants(self.staff, "KOITA 이창주 실장, 세종대학교 홍서경 팀장"),
            "부산대학교기술지주㈜ 김성근 실장, 박성호 팀장, KOITA 이창주 실장, 세종대학교 홍서경 팀장",
        )

    def test_no_internal_selection_preserves_original(self):
        self.assertEqual(merge_participants([], " KOITA 이창주 실장 "), "KOITA 이창주 실장")

    def test_only_internal_or_title_changed(self):
        self.assertEqual(merge_participants([{"name": "신성현", "title": "과장"}], ""), "부산대학교기술지주㈜ 신성현 과장")

    def test_explicit_staff_items(self):
        items = selected_staff_items(self.staff)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[1]["organization"], "부산대학교기술지주㈜")
        self.assertEqual(items[1]["raw_fragment"], "부산대학교기술지주㈜ 박성호 팀장")

    def test_internal_duplicate_flag_but_different_company_allowed(self):
        self.assertEqual(overlapping_staff_names(self.staff, [
            {"name": "김성근", "organization": "부산대학교기술지주㈜"},
            {"name": "박성호", "organization": "KOITA"},
        ]), ["김성근"])

    def test_no_staff_items_when_unselected(self):
        self.assertEqual(selected_staff_items([]), [])


if __name__ == "__main__":
    unittest.main()
