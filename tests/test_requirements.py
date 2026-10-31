"""Prerequisite parsing (AND of OR-groups) and resolution against a catalog."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "timetable_backend"))
from models import Course  # noqa: E402
from requirements import parse_requirement_groups as parse, resolve_requirements  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_empty_values(self):
        for value in (None, "", "   ", "nan", "6", "7"):
            self.assertEqual(parse(value), [], value)

    def test_single_course(self):
        self.assertEqual(parse("חדו\"א 1"), [['חדו"א 1']])

    def test_and_with_plus(self):
        self.assertEqual(parse("מבני נתונים + לינארית 1"), [["מבני נתונים"], ["לינארית 1"]])

    def test_and_with_hebrew_word(self):
        self.assertEqual(parse("סיעוד בכירורגיה א וגם סיעוד בכירורגיה ב"),
                         [["סיעוד בכירורגיה א"], ["סיעוד בכירורגיה ב"]])

    def test_or_with_slash_and_hebrew_word(self):
        self.assertEqual(parse("A / B"), [["A", "B"]])
        self.assertEqual(parse("A או B"), [["A", "B"]])

    def test_or_binds_tighter_than_and(self):
        self.assertEqual(parse("A + B / C / D"), [["A"], ["B", "C", "D"]])
        self.assertEqual(parse("A או A2 וגם B או C"), [["A", "A2"], ["B", "C"]])

    def test_duplicates_collapse(self):
        # CS cell and the typical registrar duplicate
        self.assertEqual(parse("מבוא ללמידת מכונה / מבוא ללמידת מכונה"), [["מבוא ללמידת מכונה"]])
        self.assertEqual(parse("A או A או A וגם B או B"), [["A"], ["B"]])
        self.assertEqual(parse("A + A"), [["A"]])

    def test_cpp_is_not_split(self):
        self.assertEqual(parse("מבוא למדעי המחשב בשפת ++C"), [["מבוא למדעי המחשב בשפת ++C"]])
        self.assertEqual(parse("מתמטיקה בדידה + מבוא למדעי המחשב בשפת ++C"),
                         [["מתמטיקה בדידה"], ["מבוא למדעי המחשב בשפת ++C"]])

    def test_whitespace_nbsp_and_trailing_dot(self):
        self.assertEqual(parse("  A  B.  +   C  "), [["A B"], ["C"]])

    def test_commas_stay_inside_names(self):
        self.assertEqual(parse("עזרה ראשונה או עזרה ראשונה, BLS"),
                         [["עזרה ראשונה", "עזרה ראשונה, BLS"]])

    def test_real_nursing_cell(self):
        cell = ("סיעוד בקהילה וקידום בריאות או סיעוד בקהילה  וגם התנסות קלינית - סיעוד בכירורגיה "
                "או התנסות קלינית - סיעוד בכירורגיה  או התנסות קלינית - סיעוד במחלות פנימיות "
                "או התנסות קלינית - סיעוד במחלות פנימיות ")
        self.assertEqual(parse(cell), [
            ["סיעוד בקהילה וקידום בריאות", "סיעוד בקהילה"],
            ["התנסות קלינית - סיעוד בכירורגיה", "התנסות קלינית - סיעוד במחלות פנימיות"],
        ])


def course(i, name, prereq=None, parallel=None):
    return Course(i, name, prereq, parallel, 1, "L", 1, 1, "שעור", "חובה", 2, 0, "OK")


class ResolveTests(unittest.TestCase):
    def test_single_and_or_groups(self):
        courses = [course(1, "A"), course(2, "B"), course(3, "C"), course(4, "X", "A + B / C")]
        r = resolve_requirements(courses)
        self.assertEqual(r.prereq_all[3], [0])
        self.assertEqual(r.prereq_any[3], [[1, 2]])
        self.assertEqual(r.unresolved, [])

    def test_missing_alternative_is_dropped_and_noted(self):
        courses = [course(1, "A"), course(2, "X", "A או Old A")]
        r = resolve_requirements(courses)
        self.assertEqual(r.prereq_all[1], [0])
        self.assertEqual(r.prereq_any[1], [])
        self.assertEqual(r.notes[0]["ignored_alternatives"], ["Old A"])
        self.assertEqual(r.unresolved, [])

    def test_fully_missing_group_is_unresolved(self):
        courses = [course(1, "A"), course(2, "X", "A וגם Old B או Older B")]
        r = resolve_requirements(courses)
        self.assertEqual(r.prereq_all[1], [0])
        self.assertEqual(r.unresolved[0]["unresolved_name"], "Old B / Older B")

    def test_aliases_map_old_names(self):
        courses = [course(1, "New name"), course(2, "X", "Old name")]
        self.assertEqual(resolve_requirements(courses).prereq_all[1], [])
        r = resolve_requirements(courses, {"Old name": "New name"})
        self.assertEqual(r.prereq_all[1], [0])

    def test_alias_collapsing_or_group_to_one_course(self):
        courses = [course(1, "New"), course(2, "X", "Old / New")]
        r = resolve_requirements(courses, {"Old": "New"})
        self.assertEqual(r.prereq_all[1], [0])
        self.assertEqual(r.prereq_any[1], [])

    def test_or_group_satisfied_by_and_member_is_dropped(self):
        courses = [course(1, "A"), course(2, "B"), course(3, "X", "A + A / B")]
        r = resolve_requirements(courses)
        self.assertEqual(r.prereq_all[2], [0])
        self.assertEqual(r.prereq_any[2], [])

    def test_self_reference_ignored(self):
        courses = [course(1, "A"), course(2, "B", "B / A")]
        self.assertEqual(resolve_requirements(courses).prereq_all[1], [0])

    def test_corequisite_or_group(self):
        courses = [course(1, "A"), course(2, "B"), course(3, "X", None, "A / B")]
        r = resolve_requirements(courses)
        self.assertEqual(r.parallel_any[2], [[0, 1]])
        self.assertEqual(r.parallel_all[2], [])

    def test_dependency_sources_include_or_members(self):
        courses = [course(1, "A"), course(2, "B"), course(3, "X", "A / B")]
        self.assertEqual(resolve_requirements(courses).dependency_sources(), {0, 1})


if __name__ == "__main__":
    unittest.main()


class ParenthesesTests(unittest.TestCase):
    """Explicit grouping with parentheses (nursing cleanup format)."""

    def test_or_group_in_parentheses(self):
        self.assertEqual(parse("A + (B / C)"), [["A"], ["B", "C"]])
        self.assertEqual(parse("(A / B) + C"), [["A", "B"], ["C"]])
        self.assertEqual(parse("((A / B))"), [["A", "B"]])

    def test_parentheses_override_precedence(self):
        # (A and B) or C  ==  (A or C) and (B or C)
        self.assertEqual(parse("(A + B) / C"), [["A", "C"], ["B", "C"]])
        self.assertEqual(parse("A / (B + C)"), [["A", "B"], ["A", "C"]])

    def test_same_meaning_as_registrar_words(self):
        cell = "אנטומיה ופיזיולוגיה + (גנטיקה ואמבריולוגיה / גנטיקה, אמבריולוגיה ורפואה מותאמת אישית)"
        registrar = ("אנטומיה ופיזיולוגיה וגם גנטיקה ואמבריולוגיה "
                     "או גנטיקה, אמבריולוגיה ורפואה מותאמת אישית")
        self.assertEqual(parse(cell), parse(registrar))

    def test_parentheses_inside_a_course_name_are_kept(self):
        name = 'משק לשעת חירום (מל"ח) וניהול שע"ח'
        self.assertEqual(parse(name), [[name]])
        self.assertEqual(parse(f"(X / {name})"), [["X", name]])
        self.assertEqual(parse(f"Y + {name}"), [["Y"], [name]])

    def test_duplicates_inside_parentheses_collapse(self):
        self.assertEqual(parse("(אומדן פיזיקלי / אומדן פיזיקלי)"), [["אומדן פיזיקלי"]])

    def test_malformed_cells_raise(self):
        from requirements import RequirementSyntaxError
        for cell in ("(A / B", "A / B)", "(A / התנסות קלינית - סיעוד) בכירורגיה"):
            with self.assertRaises(RequirementSyntaxError, msg=cell):
                parse(cell)

    def test_malformed_cell_is_reported_not_guessed(self):
        courses = [Course(1, "A", None, None, 1, "L", 2, 1, "שעור", "חובה", 2, 0, "OK"),
                   Course(2, "B", "(A / B", None, 1, "L", 2, 1, "שעור", "חובה", 2, 0, "OK")]
        r = resolve_requirements(courses)
        self.assertEqual(r.prereq_all[1], [])
        self.assertEqual(len(r.unresolved), 1)
        self.assertIn("malformed", r.unresolved[0]["reason"])
