"""Assistant rules that choose a semester must shape the semester plan
(Stage 1), not only the weekly timetable (Stage 2). Before this, Stage 1 put
the course in a semester the rule forbids and Stage 2 was proven impossible
at once, with no reason shown (the "move it to semester B" report)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'timetable_backend'))
from models import Course  # noqa: E402
from scheduler import create_timetable, plan_baseline_a  # noqa: E402


def course(i, year=1, prereq=None):
    return Course(i, f'Course {i}', prereq, None, year, f'L{i}', 2, 2, 'הרצאה', 'חובה', 2, 0, 'OK')


def config(rules, years=1, semesters=2):
    return {'degree_years': years, 'semesters_per_year': semesters,
            'days': {'Sunday': {'enabled': True, 'start': '08:00', 'end': '14:00'},
                     'Monday': {'enabled': True, 'start': '08:00', 'end': '14:00'}},
            'lecturer_availability': {}, 'ai_constraints': rules}


def semester_rule(course_id, allowed=(), forbidden=(), hard=True):
    return {'id': 'r1', 'kind': 'placement_filter', 'description': f'Course {course_id} moves', 'hard': hard,
            'weight': 50, 'scope': {'course_ids': [course_id]},
            'allowed_days': [], 'forbidden_days': [], 'earliest_start': None, 'latest_end': None,
            'allowed_semesters': list(allowed), 'forbidden_semesters': list(forbidden)}


def build(courses, cfg):
    """The two stages the way main.py chains them."""
    planning = dict(cfg)
    plan = plan_baseline_a(courses, planning, 5)
    cfg = dict(cfg, a_fixed_semesters={str(k): v for k, v in plan.items()},
               a_stage1_placements=planning.get('a_stage1_placements', []))
    return plan, create_timetable(courses, 20, cfg)


class AssistantSemesterRuleTests(unittest.TestCase):
    def test_without_a_rule_the_gateway_course_goes_first(self):
        plan = plan_baseline_a([course(1), course(2, prereq='Course 1'), course(3)], config([]), 5)
        self.assertEqual(plan[1], 1)

    def test_hard_semester_rule_moves_the_course_and_the_build_succeeds(self):
        # Without the rule, Course 1 opens the year (Course 2 needs it).
        courses = [course(1), course(2, prereq='Course 1'), course(3)]
        self.assertEqual(plan_baseline_a(courses, config([], semesters=3), 5)[1], 1)
        plan, timetable = build(courses, config([semester_rule(1, allowed=[2])], semesters=3))
        self.assertEqual((plan[1], plan[2]), (2, 3))
        self.assertTrue(timetable)
        self.assertEqual({e.semester for e in timetable if e.course_id == 1}, {2})

    def test_forbidden_semester(self):
        plan = plan_baseline_a([course(1), course(3)], config([semester_rule(1, forbidden=[1])]), 5)
        self.assertEqual(plan[1], 2)

    def test_semesters_count_across_the_degree(self):
        plan = plan_baseline_a([course(1, year=2), course(2)], config([semester_rule(1, allowed=[4])], years=2), 5)
        self.assertEqual(plan[1], 4)

    def test_impossible_rule_names_itself(self):
        # The course needs a later semester but a course requiring it can't move.
        courses = [course(1), course(2, prereq='Course 1')]
        with self.assertRaisesRegex(RuntimeError, 'Assistant rules .* Course 1 moves'):
            plan_baseline_a(courses, config([semester_rule(1, allowed=[2])]), 5)

    def test_soft_rule_is_followed_when_it_can_be(self):
        plan = plan_baseline_a([course(1), course(3)], config([semester_rule(1, allowed=[2], hard=False)]), 5)
        self.assertEqual(plan[1], 2)

    def test_semester_order_between_two_courses(self):
        rule = {'id': 'r2', 'kind': 'course_relation', 'hard': True, 'weight': 50,
                'left_course_id': 3, 'right_course_id': 1, 'relation': 'before_semester'}
        plan = plan_baseline_a([course(1), course(3)], config([rule]), 5)
        self.assertLess(plan[3], plan[1])

    def test_day_only_rules_leave_the_semester_plan_alone(self):
        rule = semester_rule(1)
        rule['forbidden_days'] = ['Sunday']
        plan = plan_baseline_a([course(1), course(2, prereq='Course 1')], config([rule]), 5)
        self.assertEqual(plan[1], 1)


if __name__ == '__main__':
    unittest.main()
