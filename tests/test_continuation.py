"""Recurring attendance is not a new opening; actual additional classes are."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'timetable_backend'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'timetable_frontend'))
from scheduler import create_timetable
from timetable_export import export_timetable_html
from test_stage1 import course, config

class ContinuationTests(unittest.TestCase):
    def run_calendar(self,cs,fixed):
        cfg=config(2,end='10:00');cfg.update(allow_semester_b_start=True,a_fixed_semesters=fixed)
        cwd=os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                result=create_timetable(cs,5,cfg)
                debug=json.loads(Path('timetable_debug.json').read_text())
            finally:os.chdir(cwd)
        return result,debug,cfg

    def test_existing_recurring_offering_has_zero_opening_penalty(self):
        result,debug,cfg=self.run_calendar([course(1,'כללי')],{'1':1})
        reused=next(e for e in result if e.semester==3)
        source=next(e for e in result if e.semester==1)
        self.assertEqual(reused.offering_status,'REUSED_RECURRING')
        self.assertEqual((reused.day,reused.start,reused.end),(source.day,source.start,source.end))
        self.assertEqual(debug['b']['new_opening_course_ids'],[])
        self.assertEqual(debug['b']['repeated_courses'][0]['new_opening_penalty'],0)
        html=export_timetable_html([e.to_dict() for e in result],cfg,debug)
        self.assertIn('סמסטר 3 · השלמה למחזור ב׳',html)
        self.assertIn('עם מחזור צעיר · הצעה קיימת',html)
        self.assertNotIn('טרם נקבעו יום ושעה',html)

    def test_new_continuation_class_is_scheduled_and_penalized(self):
        result,debug,cfg=self.run_calendar([course(1),course(2,prereq='Course 1')],{'1':1,'2':2})
        new=next(e for e in result if e.course_id==2 and e.semester==3)
        self.assertEqual(new.offering_status,'NEW_B_ONLY')
        row=next(e for e in debug['b']['repeated_courses'] if e['course_id']=='2')
        self.assertGreater(row['new_opening_penalty'],0)
        self.assertFalse(row['logical_only'])

    def test_available_days_are_used_when_three_meetings_can_be_split(self):
        cs=[course(1),course(2),course(3)]
        cfg=config(1,end='10:00')
        cfg['days']={d:{'enabled':True,'start':'08:00','end':'10:00'} for d in ['Monday','Thursday','Friday']}
        cfg['a_fixed_semesters']={'1':1,'2':1,'3':1}
        cwd=os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:result=create_timetable(cs,5,cfg)
            finally:os.chdir(cwd)
        self.assertEqual(len({e.day for e in result}),3)

if __name__=='__main__':unittest.main()
