"""Read-only timetable audit: python validate_timetable.py [path/to/timetable.json]."""
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'timetable_backend' / 'timetable.json'
data = json.loads(path.read_text(encoding='utf-8'))
if isinstance(data, dict):
    data = data.get('timetable', data.get('events', []))
if not isinstance(data, list):
    raise SystemExit('Expected a list of timetable events')

def minute(t):
    h, m = map(int, t.split(':')[:2]); return 60*h+m

def cohort(e):
    return set(('A','B')) if e.get('cohort_label') == 'A+B' else {e.get('cohort_label')}

hard = []; warnings = []
by_day = defaultdict(list); by_course = defaultdict(list)
for e in data:
    by_day[(e['semester'], e['day'])].append(e)
    by_course[(str(e['course_id']),e['semester'])].append(e)
for (sem, day), rows in by_day.items():
    for i, a in enumerate(rows):
        for b in rows[i+1:]:
            a0,a1,b0,b1 = minute(a['start']),minute(a['end']),minute(b['start']),minute(b['end'])
            if a0 < b1 and b0 < a1:
                if a['lecturer'] == b['lecturer'] and str(a['course_id']) != str(b['course_id']):
                    hard.append(f'Lecturer overlap semester {sem} {day}: {a["name"]} / {b["name"]}')
                if cohort(a)&cohort(b):
                    cats = {a.get('course_category'),b.get('course_category')}
                    if 'חובה' in cats or 'כללי' in cats or ('פרויקט' in cats and cats != {'פרויקט'}):
                        hard.append(f'Student/core overlap semester {sem} {day}: {a["name"]} / {b["name"]}')
    for group_label in ('A','B'):
        attended = [e for e in rows if group_label in cohort(e) and e.get('offering_status') != 'REUSED_RECURRING']
        # Simultaneous alternatives should not count as a missing break.
        attended.sort(key=lambda e:(minute(e['start']),minute(e['end'])))
        for a,b in zip(attended, attended[1:]):
            gap = minute(b['start'])-minute(a['end'])
            if 0 <= gap < 5:
                warnings.append(f'No student break: semester {sem}, {day}, cohort {group_label}: {a["name"]} -> {b["name"]} ({gap}m)')
    lecturers = defaultdict(list)
    for e in rows: lecturers[e['lecturer']].append(e)
    for lecturer, meetings in lecturers.items():
        meetings.sort(key=lambda e: minute(e['start']))
        for a,b in zip(meetings, meetings[1:]):
            gap=minute(b['start'])-minute(a['end'])
            if 0 <= gap < 5:
                warnings.append(f'No lecturer break: semester {sem}, {day}, {lecturer}: {a["name"]} -> {b["name"]} ({gap}m)')
for (cid,sem), rows in by_course.items():
    labels={e['cohort_label'] for e in rows if e.get('offering_status')!='REUSED_RECURRING'}
    if 'A' in labels and 'B' in labels:
        warnings.append(f'Split A/B course {cid} in semester {sem}; inspect share feasibility')
    if any(e.get('course_category')=='סמינר' for e in rows) and labels != {'A+B'}:
        hard.append(f'Seminar {cid} semester {sem} is not shared A+B')
    if any(e.get('event_type')=='Lecture' for e in rows) and any(e.get('event_type')=='Tutorial' for e in rows):
        pass
# Identify B's delayed reuse of an A course that is already taught in an eligible shared semester.
for (cid,sem), rows in by_course.items():
    if sem == 10 and any(e.get('offering_status')=='REUSED_RECURRING' for e in rows):
        src=rows[0].get('source_semester')
        if src is not None and src >= 2:
            warnings.append(f'B reuses course {cid} in semester 10 (A offered it in semester {src}); check whether B could join A')
print(f'AUDIT {path.name}: {len(data)} events; {len(hard)} hard-constraint warnings; {len(warnings)} soft/sharing warnings')
for item in hard: print('HARD:', item)
for item in warnings: print('CHECK:', item)
sys.exit(1 if hard else 0)
