"""Regression tests for semester planning and interval feasibility guards."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'timetable_backend'))
from models import Course
from scheduler import plan_baseline_a


def course(i, category='חובה', hours=1, lecturer=None, prereq=None):
    return Course(i, f'Course {i}', prereq, None, 1, lecturer or f'L{i}',
                  1, hours, 'הרצאה', category, hours, 0, 'OK')


def config(semesters=1, end='08:45', lecturers=None):
    return {'degree_years': 1, 'semesters_per_year': semesters,
            'days': {'Monday': {'enabled': True, 'start': '08:00', 'end': end}},
            'lecturer_availability': lecturers or {}}


class Stage1Tests(unittest.TestCase):
    def test_electives_can_overlap(self):
        self.assertEqual(len(plan_baseline_a([course(1, 'בחירה'), course(2, 'בחירה')], config(), 5)), 2)

    def test_required_cannot_overlap_elective(self):
        with self.assertRaisesRegex(RuntimeError, 'proven infeasible'):
            plan_baseline_a([course(1), course(2, 'בחירה')], config(), 5)

    def test_same_lecturer_cannot_overlap_electives(self):
        with self.assertRaisesRegex(RuntimeError, 'proven infeasible'):
            plan_baseline_a([course(1, 'בחירה', lecturer='L'), course(2, 'בחירה', lecturer='L')], config(), 5)

    def test_prerequisite_stays_earlier(self):
        result = plan_baseline_a([course(1), course(2, prereq='Course 1')], config(2), 5)
        self.assertLess(result[1], result[2])

    def test_lecturer_time_window_is_parsed(self):
        cfg = config(end='09:30', lecturers={'L': {'Monday': {'enabled': True, 'start': '08:15', 'end': '09:00'}}})
        self.assertEqual(plan_baseline_a([course(1, lecturer='L')], cfg, 5), {1: 1})

    def test_too_short_lecturer_window_is_infeasible(self):
        cfg = config(end='09:30', lecturers={'L': {'Monday': {'enabled': True, 'start': '08:15', 'end': '08:45'}}})
        with self.assertRaisesRegex(RuntimeError, 'proven infeasible'):
            plan_baseline_a([course(1, lecturer='L')], cfg, 5)

    def test_project_alternatives_share_start(self):
        cfg = config(end='09:30', lecturers={
            'L1': {'Monday': {'enabled': True, 'start': '08:00', 'end': '08:45'}},
            'L2': {'Monday': {'enabled': True, 'start': '08:45', 'end': '09:30'}}})
        with self.assertRaisesRegex(RuntimeError, 'proven infeasible'):
            plan_baseline_a([course(1, 'פרויקט'), course(2, 'פרויקט')], cfg, 5)
        self.assertEqual(len(plan_baseline_a([course(1, 'פרויקט'), course(2, 'פרויקט')], config(), 5)), 2)

if __name__ == '__main__':
    unittest.main()
