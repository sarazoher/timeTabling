"""OR-groups in prerequisites / corequisites are enforced by every solver stage."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "timetable_backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from models import Course  # noqa: E402
from scheduler import create_timetable, plan_baseline_a  # noqa: E402
from test_stage1 import config  # noqa: E402


def course(i, prereq=None, parallel=None, category="חובה"):
    return Course(i, f"Course {i}", prereq, parallel, 1, f"L{i}", 1, 1, "הרצאה",
                  category, 1, 0, "OK")


def run_full(courses, cfg):
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            return create_timetable(courses, 10, cfg)
        finally:
            os.chdir(cwd)


class Stage1OrTests(unittest.TestCase):
    # A requires X; X requires "A or B".  Read as AND this is a cycle
    # (A < X < A); read as OR the only answer is B < X < A.
    def cycle_breaker(self, x_prereq):
        return [course(1, "Course 3"), course(2), course(3, x_prereq)]

    def test_or_group_uses_the_feasible_alternative(self):
        plan = plan_baseline_a(self.cycle_breaker("Course 1 / Course 2"), config(3, end="12:00"), 10)
        self.assertLess(plan[2], plan[3])
        self.assertLess(plan[3], plan[1])

    def test_hebrew_or_word_behaves_the_same(self):
        plan = plan_baseline_a(self.cycle_breaker("Course 1 או Course 2"), config(3, end="12:00"), 10)
        self.assertLess(plan[2], plan[3])

    def test_same_cell_read_as_and_is_infeasible(self):
        with self.assertRaisesRegex(RuntimeError, "proven infeasible"):
            plan_baseline_a(self.cycle_breaker("Course 1 + Course 2"), config(3, end="12:00"), 10)

    def test_or_group_needs_at_least_one_alternative(self):
        # Both alternatives require X, so no alternative can come first.
        courses = [course(1, "Course 3"), course(2, "Course 3"), course(3, "Course 1 / Course 2")]
        with self.assertRaisesRegex(RuntimeError, "proven infeasible"):
            plan_baseline_a(courses, config(3, end="12:00"), 10)

    def test_and_plus_or_group(self):
        # X needs C AND (A or B); A needs X  ->  B and C before X, X before A.
        courses = [course(1, "Course 4"), course(2), course(3), course(4, "Course 3 + Course 1 / Course 2")]
        plan = plan_baseline_a(courses, config(3, end="12:00"), 10)
        self.assertLess(plan[3], plan[4])
        self.assertLess(plan[2], plan[4])
        self.assertLess(plan[4], plan[1])

    def test_joint_b_plan_respects_or(self):
        # A takes Course 2 (sem 1) -> X (sem 2) -> Course 1 (sem 3). B starts in
        # semester 2, so sharing X in semester 2 would leave B without either
        # alternative beforehand; the joint planner must not do that.
        cfg = config(3, end="12:00")
        cfg.update(allow_semester_b_start=True)
        plan_baseline_a(self.cycle_breaker("Course 1 / Course 2"), cfg, 10)
        b = {int(k): v for k, v in cfg["a_stage1_b_semesters"].items()}
        self.assertTrue(min(b[1], b[2]) < b[3], b)
        self.assertLess(b[3], b[1], b)

    def test_corequisite_or_allows_same_semester(self):
        # Single semester: an OR corequisite (<=) is satisfiable, a prerequisite (<) is not.
        cfg = config(1, end="12:00")
        plan = plan_baseline_a([course(1), course(2), course(3, None, "Course 1 / Course 2")], cfg, 10)
        self.assertEqual(set(plan.values()), {1})
        with self.assertRaisesRegex(RuntimeError, "proven infeasible"):
            plan_baseline_a([course(1), course(2), course(3, "Course 1 / Course 2")], cfg, 10)


class FullTimetableOrTests(unittest.TestCase):
    def semesters(self, result, cohort="A"):
        out = {}
        for item in result:
            if cohort in item.cohort_label.split("+"):
                out.setdefault(item.course_id, item.semester)
        return out

    def test_main_model_enforces_or(self):
        # X(3) needs A(1) or B(2); A needs X; B sits at the end of the chain
        # E(5) -> D(4) -> B(2). With OR the only order is E,D,B,X,A, which
        # needs both overflow semesters; without OR, X would go first.
        courses = [course(1, "Course 3"), course(2, "Course 4"), course(3, "Course 1 / Course 2"),
                   course(4, "Course 5"), course(5)]
        sem = self.semesters(run_full(courses, config(3, end="12:00")))
        self.assertEqual([sem[i] for i in (5, 4, 2, 3, 1)], [1, 2, 3, 4, 5], sem)

    def test_semester_b_cohort_also_respects_or(self):
        courses = [course(1, "Course 3"), course(2), course(3, "Course 1 / Course 2")]
        cfg = config(3, end="12:00")
        cfg.update(allow_semester_b_start=True)
        result = run_full(courses, cfg)
        for cohort in ("A", "B"):
            sem = self.semesters(result, cohort)
            self.assertEqual(set(sem), {1, 2, 3}, cohort)
            self.assertLess(sem[3], sem[1], cohort)                       # AND part
            self.assertTrue(min(sem[1], sem[2]) < sem[3], cohort)         # OR part


if __name__ == "__main__":
    unittest.main()
