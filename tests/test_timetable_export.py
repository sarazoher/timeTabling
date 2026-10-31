"""The timetable export (one HTML file) reads an existing timetable and escapes its data."""
import sys
import unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'timetable_frontend'))
from timetable_export import export_timetable_html, _clusters
from auth_support import AuthFixture


def event(start='08:00',end='09:00',name='קורס'):
    return dict(course_id=1,name=name,lecturer='מרצה',semester=1,day='Monday',
                start=start,end=end,cohort_label='A+B',course_category='חובה',event_type='Lecture')

class TimetableExportTests(AuthFixture, unittest.TestCase):
    def test_touching_meetings_are_not_overlap(self):
        self.assertEqual(len(_clusters([event(),event('09:00','10:00')])),2)
        self.assertEqual(len(_clusters([event(),event('08:30','10:00')])),1)

    def test_portable_html_escapes_catalog_and_lists_pending_courses(self):
        debug={'b':{'repeated_courses':[{'name':'השלמה','course_id':2,'logical_only':True}]}}
        html=export_timetable_html([event(name='<script>alert(1)</script>')],
                                     {'degree_years':3,'semesters_per_year':3},debug)
        self.assertIn('&lt;script&gt;',html)
        self.assertNotIn('<script>alert(1)</script>',html)
        self.assertIn('השלמה',html)
        # A general download: no target audience in the file itself.
        for word in ('לסטודנטים', 'שלכם', 'student'):
            self.assertNotIn(word, html)
        self.assertIn('window.print()',html)
        self.assertNotIn('src="http',html)
        self.assertEqual(html.count('class="semester"'),26)

    def test_download_never_runs_solver(self):
        import app
        self.register()
        with patch.object(app,'run_backend',side_effect=AssertionError('solver must not run')), \
             patch.object(app,'load_timetable',return_value=[event()]), \
             patch.object(app,'load_json',return_value={}):
            response=self.client.get('/export/timetable')
        self.assertEqual(response.status_code,200)
        self.assertIn('attachment',response.headers['Content-Disposition'])
        disposition=response.headers['Content-Disposition']
        self.assertIn('.html',disposition)
        self.assertNotIn('student',disposition)

    def test_empty_result_redirects_instead_of_exporting_blank_calendar(self):
        import app
        self.register()
        with patch.object(app,'load_timetable',return_value=[]):
            response=self.client.get('/export/timetable')
        self.assertEqual(response.status_code,302)

    def test_program_name_names_the_page_and_the_file(self):
        from urllib.parse import unquote
        from timetable_export import export_filename, export_title
        cfg={'degree_years':4,'semesters_per_year':3,'program_name':'  סיעוד  '}
        self.assertEqual(export_title(cfg),'מערכת שעות סיעוד — 4 שנים')
        self.assertEqual(export_filename({**cfg,'program_name':'סיעוד / א'}),'מערכת שעות סיעוד א — 4 שנים.html')
        self.assertEqual(export_title({'degree_years':3}),'מערכת שעות — 3 שנים')
        html=export_timetable_html([event()],cfg)
        self.assertIn('<title>מערכת שעות סיעוד — 4 שנים</title>',html)   # "Save as PDF" suggests this name
        self.assertIn('<header class="hero"><p>סיעוד · מערכת שעות</p>',html)
        import app
        self.register()
        with patch.object(app,'load_timetable',return_value=[event()]), \
             patch.object(app,'load_json',return_value={}), \
             patch.object(app,'load_config',return_value={**app.DEFAULT_CONFIG,'program_name':'מדעי המחשב','degree_years':3}):
            response=self.client.get('/export/timetable')
        self.assertIn('מערכת שעות מדעי המחשב — 3 שנים.html',unquote(response.headers['Content-Disposition']))

    def test_old_downloads_are_gone(self):
        import app
        self.register()
        self.assertEqual(self.client.get('/export/student-timetable').status_code,404)
        self.assertEqual(self.client.get('/export/system-json').status_code,404)
        for page in ('/', '/calendar'):
            with patch.object(app,'load_timetable',return_value=[event()]):
                html=self.client.get(page).get_data(as_text=True)
            self.assertIn('/export/timetable',html)
            self.assertNotIn('לסטודנטים',html)
            self.assertNotIn('JSON של',html)

if __name__=='__main__':unittest.main()
