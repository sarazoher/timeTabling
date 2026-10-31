"""Regression coverage for prerequisite-aware A/B sharing."""
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'timetable_backend'))
from scheduler import plan_baseline_a, create_timetable
from test_stage1 import course, config


class SharingTests(unittest.TestCase):
    def test_plan_dependent_course_after_b_foundation_repeat(self):
        cs = [course(1), course(2, prereq='Course 1')]
        cfg = config(3, end='09:30')
        cfg['allow_semester_b_start'] = True
        plan = plan_baseline_a(cs, cfg, 5)
        self.assertEqual(plan[1], 1)
        self.assertEqual(plan[2], 3)
        self.assertEqual(cfg['a_stage1_b_semesters']['1'], 2)
        self.assertEqual(cfg['a_stage1_b_semesters']['2'], 3)

    def test_early_final_year_share_unlocks_shared_projects(self):
        cs = [replace(course(1), year=3),
              replace(course(2, 'בחירה', prereq='Course 1'), year=3),
              replace(course(3, 'פרויקט', prereq='Course 2'), year=3),
              replace(course(4, 'פרויקט', prereq='Course 2'), year=3)]
        cfg = config(3, end='09:30')
        cfg.update(degree_years=3, allow_semester_b_start=True,
                   a_fixed_semesters={'1': 7, '2': 8, '3': 9, '4': 9})
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                result = create_timetable(cs, 5, cfg)
            finally:
                os.chdir(cwd)
        self.assertEqual(len(result), 4)
        self.assertTrue(all(e.cohort_label == 'A+B' for e in result))
        projects = [e for e in result if e.course_category == 'פרויקט']
        self.assertEqual((projects[0].semester, projects[0].day, projects[0].start),
                         (projects[1].semester, projects[1].day, projects[1].start))

    def test_seminar_is_one_shared_offering_after_b_prerequisite(self):
        cs = [course(1), course(2, 'סמינר', prereq='Course 1')]
        cfg = config(3, end='09:30');cfg['allow_semester_b_start'] = True
        plan = plan_baseline_a(cs, cfg, 5)
        self.assertEqual(plan[2], int(cfg['a_stage1_b_semesters']['2']))
        cfg['a_fixed_semesters'] = {str(k):v for k,v in plan.items()}
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:result = create_timetable(cs, 5, cfg)
            finally:os.chdir(cwd)
        seminars = [e for e in result if e.course_category == 'סמינר']
        self.assertEqual(len(seminars),1)
        self.assertEqual(seminars[0].cohort_label,'A+B')
        self.assertGreater(seminars[0].semester,2)

    def test_shared_seminar_does_not_override_b_prerequisite(self):
        cs = [course(1), course(2, 'סמינר', prereq='Course 1')]
        cfg = config(3, end='09:30')
        cfg.update(allow_semester_b_start=True,a_fixed_semesters={'1':1,'2':2})
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                with self.assertRaisesRegex(RuntimeError,'Seminars must be shared'):
                    create_timetable(cs,5,cfg)
            finally:os.chdir(cwd)

    def test_shared_offer_cannot_skip_b_prerequisite(self):
        cs = [course(1), course(2, prereq='Course 1')]
        cfg = config(3, end='09:30')
        cfg.update(allow_semester_b_start=True, a_fixed_semesters={'1': 1, '2': 2})
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                result = create_timetable(cs, 5, cfg)
            finally:
                os.chdir(cwd)
        a_child = [e for e in result if e.course_id == 2 and e.cohort_label == 'A']
        b_child = [e for e in result if e.course_id == 2 and e.cohort_label == 'B']
        self.assertEqual(len(a_child), 1)
        self.assertEqual(len(b_child), 1)
        self.assertGreater(b_child[0].semester, 2)

if __name__ == '__main__':
    unittest.main()
