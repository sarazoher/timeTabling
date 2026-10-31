"""Step 1: requirement cells are parsed as AND-of-OR groups."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "timetable_backend"))
from requirements import format_requirement_groups, parse_requirement_groups as parse  # noqa: E402


class ParserTests(unittest.TestCase):
    def test_empty_values(self):
        for value in (None, "", "   ", "nan", "6", "7"):
            self.assertEqual(parse(value), [], value)

    def test_single_course(self):
        self.assertEqual(parse("מבני נתונים"), [["מבני נתונים"]])

    def test_plus_is_and(self):
        self.assertEqual(parse("מבני נתונים + לינארית 1"), [["מבני נתונים"], ["לינארית 1"]])

    def test_vegam_is_and(self):
        self.assertEqual(parse("A וגם B"), [["A"], ["B"]])

    def test_slash_is_or(self):
        self.assertEqual(parse("B / C / D"), [["B", "C", "D"]])

    def test_o_is_or(self):
        self.assertEqual(parse("B או C"), [["B", "C"]])

    def test_or_binds_tighter_than_and(self):
        self.assertEqual(parse("A + B / C / D"), [["A"], ["B", "C", "D"]])
        self.assertEqual(parse("A או A2 וגם B או C"), [["A", "A2"], ["B", "C"]])

    def test_cpp_is_not_split(self):
        self.assertEqual(parse("מבוא למדעי המחשב בשפת ++C"), [["מבוא למדעי המחשב בשפת ++C"]])
        self.assertEqual(parse("מבוא למדעי המחשב בשפת ++C + מתמטיקה בדידה"),
                         [["מבוא למדעי המחשב בשפת ++C"], ["מתמטיקה בדידה"]])

    def test_duplicate_alternatives_collapse(self):
        # Real CS cell: the same course written twice around "/".
        self.assertEqual(parse("מבוא ללמידת מכונה / מבוא ללמידת מכונה"), [["מבוא ללמידת מכונה"]])

    def test_duplicate_groups_collapse(self):
        self.assertEqual(parse("A + A"), [["A"]])
        self.assertEqual(parse("B או C וגם C או B"), [["B", "C"]])

    def test_whitespace_and_trailing_period(self):
        self.assertEqual(parse("  אלגברה לינארית 1 .  +  חדו\"א 1"),
                         [["אלגברה לינארית 1"], ['חדו"א 1']])

    def test_commas_inside_names_are_kept(self):
        self.assertEqual(parse("עזרה ראשונה או עזרה ראשונה, BLS"),
                         [["עזרה ראשונה", "עזרה ראשונה, BLS"]])

    def test_real_nursing_cell(self):
        cell = ("אנטומיה ופיזיולוגיה או אנטומיה ופיזיולוגיה או אנטומיה ופיזיולוגיה וגם "
                "גנטיקה ואמבריולוגיה או גנטיקה, אמבריולוגיה ורפואה מותאמת אישית וגם "
                "פסיכולוגיה התפתחותית או פסיכולוגיה התפתחותית")
        self.assertEqual(parse(cell), [
            ["אנטומיה ופיזיולוגיה"],
            ["גנטיקה ואמבריולוגיה", "גנטיקה, אמבריולוגיה ורפואה מותאמת אישית"],
            ["פסיכולוגיה התפתחותית"],
        ])

    def test_word_containing_o_is_not_split(self):
        # "או" only separates as a standalone word.
        self.assertEqual(parse("מבוא לאותות ומערכות"), [["מבוא לאותות ומערכות"]])

    def test_aliases_are_applied(self):
        self.assertEqual(parse("לינארית 1 + X", {"לינארית 1": "אלגברה לינארית 1"}),
                         [["אלגברה לינארית 1"], ["X"]])

    def test_alias_can_merge_alternatives(self):
        self.assertEqual(parse("old או new", {"old": "new"}), [["new"]])

    def test_format_round_trip(self):
        groups = [["A"], ["B", "C"]]
        self.assertEqual(format_requirement_groups(groups), "A + B / C")
        self.assertEqual(parse(format_requirement_groups(groups)), groups)
        self.assertIsNone(format_requirement_groups([]))


if __name__ == "__main__":
    unittest.main()
