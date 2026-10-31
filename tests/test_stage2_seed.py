"""Tests for verifying and retaining the Stage-1 weekly incumbent."""
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'timetable_backend'))
from ortools.sat.python import cp_model
from scheduler import create_timetable, plan_baseline_a
from test_stage1 import course, config


class SeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.temp.name)

    def tearDown(self):
        os.chdir(self.cwd)
        self.temp.cleanup()

    def prepared(self):
        cs = [course(1)]
        cfg = config(end='09:30')
        plan = plan_baseline_a(cs, cfg, 5)
        cfg['a_fixed_semesters'] = {str(k): v for k, v in plan.items()}
        self.assertEqual(len(cfg['a_stage1_placements']), 1)
        return cs, cfg

    def test_optimization_timeout_keeps_verified_incumbent(self):
        cs, cfg = self.prepared()
        original = cp_model.CpSolver.Solve
        def time_out_optimization(solver, model, *args, **kwargs):
            if model.HasObjective():
                solver.parameters.max_time_in_seconds = 0
            return original(solver, model, *args, **kwargs)
        with patch.object(cp_model.CpSolver, 'Solve', time_out_optimization):
            result = create_timetable(cs, 5, cfg)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].semester, 1)

    def test_invalid_seed_cannot_bypass_hard_ai_rule(self):
        cs, cfg = self.prepared()
        cfg['a_stage1_placements'][0]['start_slot'] = 96  # 08:00
        cfg['ai_constraints'] = [{'kind': 'placement_filter', 'hard': True,
                                   'scope': {'course_ids': [1]}, 'earliest_start': '08:15'}]
        result = create_timetable(cs, 5, cfg)
        self.assertGreaterEqual(result[0].start, '08:15')

    def test_unnecessary_five_minute_delay_is_removed(self):
        cs, cfg = self.prepared()
        cfg['a_stage1_placements'][0]['start_slot'] = 97  # 08:05
        result = create_timetable(cs, 5, cfg)
        self.assertEqual((result[0].start, result[0].end), ('08:00', '08:45'))

    def test_exact_non_quarter_hour_window_remains_feasible(self):
        cs = [course(1)]
        cfg = config(end='08:50')
        cfg['days']['Monday']['start'] = '08:05'
        plan = plan_baseline_a(cs, cfg, 5)
        cfg['a_fixed_semesters'] = {str(k): v for k, v in plan.items()}
        result = create_timetable(cs, 5, cfg)
        self.assertEqual((result[0].start, result[0].end), ('08:05', '08:50'))

    def test_short_break_without_delaying_first_class(self):
        cs = [course(1), course(2)]
        cfg = config(end='10:00')
        plan = plan_baseline_a(cs, cfg, 5)
        cfg['a_fixed_semesters'] = {str(k): v for k, v in plan.items()}
        result = sorted(create_timetable(cs, 5, cfg), key=lambda e: e.start)
        self.assertEqual([e.start for e in result], ['08:00', '09:00'])
        self.assertEqual(result[0].end, '08:45')

    def test_tight_day_keeps_back_to_back_classes_feasible(self):
        cs = [course(1), course(2)]
        cfg = config(end='09:30')
        plan = plan_baseline_a(cs, cfg, 5)
        cfg['a_fixed_semesters'] = {str(k): v for k, v in plan.items()}
        result = sorted(create_timetable(cs, 5, cfg), key=lambda e: e.start)
        self.assertEqual([e.start for e in result], ['08:00', '08:45'])

    def test_optional_courses_separate_when_there_is_room(self):
        for categories in [('בחירה', 'בחירה'), ('בחירה', 'סמינר'), ('סמינר', 'סמינר')]:
            with self.subTest(categories=categories):
                cs = [course(1, categories[0]), course(2, categories[1])]
                cfg = config(end='10:00')
                plan = plan_baseline_a(cs, cfg, 5)
                cfg['a_fixed_semesters'] = {str(k): v for k, v in plan.items()}
                result = sorted(create_timetable(cs, 5, cfg), key=lambda e: e.start)
                self.assertLessEqual(result[0].end, result[1].start)

    def test_optional_overlap_stays_legal_in_tight_window(self):
        cs = [course(1, 'בחירה'), course(2, 'סמינר')]
        cfg = config(end='08:45')
        plan = plan_baseline_a(cs, cfg, 5)
        cfg['a_fixed_semesters'] = {str(k): v for k, v in plan.items()}
        result = create_timetable(cs, 5, cfg)
        self.assertEqual([e.start for e in result], ['08:00', '08:00'])

    def test_five_minute_break_fits_when_fifteen_cannot(self):
        cs = [course(1), course(2)]
        cfg = config(end='09:35')
        plan = plan_baseline_a(cs, cfg, 5)
        cfg['a_fixed_semesters'] = {str(k): v for k, v in plan.items()}
        result = sorted(create_timetable(cs, 5, cfg), key=lambda e: e.start)
        self.assertEqual([(e.start, e.end) for e in result], [('08:00','08:45'), ('08:50','09:35')])

    def test_ten_minute_break_is_acceptable(self):
        cs = [course(1), course(2)]
        cfg = config(end='09:40', lecturers={'L2': {'Monday': {'enabled':True, 'start':'08:55','end':'09:40'}}})
        plan = plan_baseline_a(cs, cfg, 5)
        cfg['a_fixed_semesters'] = {str(k): v for k, v in plan.items()}
        result = sorted(create_timetable(cs, 5, cfg), key=lambda e: e.start)
        self.assertEqual([(e.start, e.end) for e in result], [('08:00','08:45'), ('08:55','09:40')])

    def test_missing_witness_does_not_hide_timeout(self):
        cs, cfg = self.prepared()
        cfg.pop('a_stage1_placements')
        original = cp_model.CpSolver.Solve
        def time_out(solver, model, *args, **kwargs):
            solver.parameters.max_time_in_seconds = 0
            return original(solver, model, *args, **kwargs)
        with patch.object(cp_model.CpSolver, 'Solve', time_out):
            with self.assertRaisesRegex(RuntimeError, 'Stage-2 search timeout'):
                create_timetable(cs, 5, cfg)

if __name__ == '__main__':
    unittest.main()
