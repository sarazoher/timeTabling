"""Portable timetable export (one HTML file): no solver, network or image renderer."""
import re
from collections import defaultdict
from html import escape
from datetime import datetime
from zoneinfo import ZoneInfo

DAYS = [('Sunday','ראשון'),('Monday','שני'),('Tuesday','שלישי'),('Wednesday','רביעי'),('Thursday','חמישי'),('Friday','שישי'),('Saturday','שבת')]
COLORS = {'חובה':'core','כללי':'general','בחירה':'elective','סמינר':'seminar','פרויקט':'project'}

def _minutes(s):
    h,m = map(int, s.split(':'))
    return h*60+m

def _clusters(events):
    """Connected overlap groups; touching endpoints are separate groups."""
    groups=[]
    end=-1
    for e in sorted(events,key=lambda e: (_minutes(e['start']),_minutes(e['end']),str(e.get('course_id')))):
        if not groups or _minutes(e['start']) >= end:
            groups.append([])
            end=_minutes(e['end'])
        else:
            end=max(end,_minutes(e['end']))
        groups[-1].append(e)
    return groups

def _lanes(group):
    """Pack successive meetings into the fewest simultaneous display lanes."""
    lanes, ends = [], []
    for event in group:
        index = next((i for i, end in enumerate(ends)
                      if end <= _minutes(event['start'])), len(lanes))
        if index == len(lanes):
            lanes.append([])
            ends.append(0)
        lanes[index].append(event)
        ends[index] = _minutes(event['end'])
    return lanes


def _lane_width(events):
    # A readable time range and about two title lines, without allowing one
    # unusually long catalog title to consume the entire academic year.
    title_length = max((len(str(x.get('name', ''))) for x in events), default=0)
    return min(240, max(170, 32 + title_length * 3.5))


def _day_width(events):
    return max([170] + [sum(_lane_width(lane) for lane in lanes)
                       + 8 * (len(lanes) - 1) + 14
                       for lanes in (_lanes(group) for group in _clusters(events))])


def program_name(config):
    """The program name typed in Settings ("" when none)."""
    return ' '.join(str((config or {}).get('program_name') or '').split())


def export_title(config):
    """'מערכת שעות <program name> — <years> שנים': the page title, so a browser's
    "Save as PDF" suggests it, and the downloaded file's name."""
    name = program_name(config)
    years = int((config or {}).get('degree_years', 3))
    return f"מערכת שעות {name} — {years} שנים" if name else f"מערכת שעות — {years} שנים"


def export_filename(config):
    """export_title as a safe file name (no characters Windows/macOS reject)."""
    return ' '.join(re.sub(r'[\\/:*?"<>|]+', ' ', export_title(config)).split()) + '.html'


def export_timetable_html(events, config, debug=None):
    if not events:
        raise ValueError('יש ליצור מערכת שעות לפני ההורדה.')
    debug=debug or {}
    years=int(config.get('degree_years',3))
    per_year=int(config.get('semesters_per_year',3))
    horizon=years*per_year
    name_label=(escape(program_name(config)) + " · ") if program_name(config) else ""
    page_title=escape(export_title(config))
    e=lambda v: escape(str(v or ''),quote=True)
    active=[(d,n) for d,n in DAYS if (config.get('days',{}).get(d,{}) or {}).get('enabled') or any(x.get('day')==d for x in events)]
    pieces=[]
    for cohort in ['A','B','ALL']:
        selected=[x for x in events if cohort=='ALL' or cohort in str(x.get('cohort_label','A')).split('+')]
        title={'A':'מחזור א׳ · תחילת לימודים בסמסטר א׳','B':'מחזור ב׳ · תחילת לימודים בסמסטר ב׳','ALL':'כל ההצעות · מחזורים א׳ וב׳'}[cohort]
        pieces.append(f'<div class="cohort-view" data-view="{cohort}"'+(' hidden' if cohort!='A' else '')+f'><p class="cohort-title">{title}</p>')
        max_semester = max([horizon] + [int(x.get("semester", 0)) for x in selected])
        displayed_years = (max_semester - 1) // per_year + 1
        for year in range(1,displayed_years+1):
            pieces.append(f'<section class="academic-year" data-year="{year}"><div class="year-header"><h2>שנה {year}</h2></div><div class="year-scroll"><table class="year-grid" role="presentation"><tbody><tr>')
            phases = [phase for phase in range(1, per_year + 1)
                      if (year - 1) * per_year + phase <= max_semester
                      and not (cohort == "B" and year == 1 and phase == 1)]
            semester_widths = {
                phase: sum(_day_width([x for x in selected
                                      if int(x.get('semester', 0)) == (year - 1) * per_year + phase
                                      and x.get('day') == day]) for day, _ in active)
                for phase in phases
            }
            year_width = sum(semester_widths.values()) or 1
            pieces[-1] = pieces[-1].replace('class="year-grid"',
                f'class="year-grid" style="--year-min:{year_width + 12 * (len(phases) + 1):.0f}px"')
            for phase in phases:
                sem=(year-1)*per_year+phase
                semester=[x for x in selected if int(x.get('semester',0))==sem]
                unique=len({str(x.get('course_id')) for x in semester})
                label={1:'א׳',2:'ב׳',3:'ג׳'}.get(phase,str(phase))
                semester_caption = f"סמסטר {sem} · השלמה למחזור ב׳" if sem > horizon else f"סמסטר {label}"
                pieces.append(f'<td class="semester-cell" style="width:{100 * semester_widths[phase] / year_width:.4f}%"><section class="semester"><header class="semester-header" data-print-year="שנה {year}"><div><span class="kicker">שנה {year}</span><h3>{semester_caption}</h3></div><span>{unique} קורסים · {title.split(" · ")[0]}</span></header>')
                if not semester:
                    text='הלימודים במחזור ב׳ מתחילים בסמסטר הבא.' if cohort=='B' and sem==1 else 'אין מפגשים משובצים בסמסטר זה.'
                    pieces.append(f'<p class="empty">{text}</p></section></td>')
                    continue
                day_widths = [_day_width([x for x in semester if x.get('day') == day])
                              for day, _ in active]
                day_tracks = ' '.join(f'minmax(0,{width:.2f}fr)' for width in day_widths)
                pieces.append(f'<div class="days" style="--day-tracks:{day_tracks};--print-day-count:{len(active)}">')
                for day,name in active:
                    pieces.append(f'<div class="day"><h4>יום {name}</h4><div class="day-body">')
                    grouped=_clusters([x for x in semester if x.get('day')==day])
                    if not grouped:pieces.append('<p class="empty">ללא שיעורים</p>')
                    for group in grouped:
                        # Horizontal lanes for overlapping events; successive
                        # events in one connected group can reuse a lane.
                        lanes = _lanes(group)
                        lane_tracks = ' '.join(f'minmax(0,{_lane_width(lane):.2f}fr)' for lane in lanes)
                        pieces.append(f'<div class="overlap-group" style="--lane-tracks:{lane_tracks}">')
                        for lane in lanes:
                            pieces.append('<div class="lane">')
                            for x in lane:
                                category=x.get('course_category','חובה');typ='תרגול' if x.get('event_type')=='Tutorial' else 'הרצאה'
                                cohort_label={'A':'א׳','B':'ב׳','A+B':'א׳ + ב׳'}.get(x.get('cohort_label','A'),'')
                                reuse_label = '<p class="reuse-label">עם מחזור צעיר · הצעה קיימת</p>' if x.get("offering_status") == "REUSED_RECURRING" else ('<p class="reuse-label">פתיחה נוספת למחזור ב׳</p>' if sem > horizon else '')
                                pieces.append(f'<article class="course {COLORS.get(category,"core")}" data-course="{e(x.get("course_id"))}" data-semester="{sem}"><div class="time" dir="ltr">{e(x["start"])} – {e(x["end"])}</div><h5>{e(x.get("name"))}</h5><p class="lecturer">{e(x.get("lecturer"))}</p><div class="meta"><span>{typ} · {e(category)}</span><span>מחזור {cohort_label}</span></div><div class="course-id">קורס {e(x.get("course_id"))}</div>{reuse_label}</article>')
                            pieces.append('</div>')
                        pieces.append('</div>')
                    pieces.append('</div></div>')
                pieces.append('</div><footer class="semester-note">השעות הן שעות שעון. שיעורים המופיעים זה לצד זה חופפים בזמן; יש לבדוק את מסלול הבחירה המתאים.</footer></section></td>')
            pieces.append('</tr></tbody></table></div></section>')
        if cohort in ['B','ALL']:
            pending=[x for x in debug.get('b',{}).get('repeated_courses',[]) if x.get('logical_only')]
            if pending:
                pieces.append('<section class="continuation"><h3>השלמות למחזור ב׳ — לאחר שלוש השנים</h3><p>לקורסים הבאים הוקצה סמסטר השלמה, אך טרם נקבעו יום ושעה. הם אינם משובצים במערכת השבועית המוצגת.</p><ul>')
                for x in pending:pieces.append(f'<li>{e(x.get("name"))} <small>({e(x.get("course_id"))})</small></li>')
                pieces.append('</ul></section>')
        pieces.append('</div>')
    try:
        created=datetime.now(ZoneInfo('Asia/Jerusalem')).strftime('%d.%m.%Y %H:%M')
    except Exception:
        created=datetime.now().strftime('%d.%m.%Y %H:%M')
    css='''*{box-sizing:border-box}body{margin:0;background:#f2f5fa;color:#14243b;font-family:Arial,sans-serif;direction:rtl}.page{max-width:2200px;margin:auto;padding:30px}.hero{background:#14243b;color:white;padding:28px 32px;border-radius:16px}.hero h1{font-size:32px;margin:5px 0 12px}.hero p{margin:8px 0;color:#d9e5f5}.controls{display:flex;gap:12px;align-items:center;flex-wrap:wrap;background:white;padding:16px;margin:16px 0;border-radius:12px;position:sticky;top:0;z-index:2;border:1px solid #d7dfeb}button,select{padding:10px 16px;border-radius:8px;border:1px solid #b8c7da;background:white;font:inherit}button{background:#2457aa;color:white;cursor:pointer}.legend{display:flex;gap:16px;flex-wrap:wrap;margin:20px 0;font-size:14px}.legend span:before{content:"";display:inline-block;width:12px;height:12px;background:var(--color);margin-left:6px;border-radius:3px}.cohort-title{font-size:20px;font-weight:bold}.year-header{border-bottom:2px solid #c9d7e8;margin:28px 0 16px}.year-header h2{margin:8px 0}.academic-year{margin-bottom:30px}.year-scroll{width:100%;padding-bottom:8px;overflow-x:auto}.year-grid{width:100%;min-width:var(--year-min);table-layout:fixed;border-spacing:12px 0;border-collapse:separate}.semester-cell{vertical-align:top;padding:0;min-width:0}.semester-cell .semester{height:100%}.semester{min-width:0;background:white;margin:0;border:1px solid #d4deeb;border-radius:12px;overflow:hidden}.semester-header{padding:12px;flex-wrap:wrap;background:#eaf0f9;display:flex;justify-content:space-between;align-items:center;gap:12px}.semester-header h3{margin:3px 0;font-size:25px}.semester-header>span,.kicker{font-size:13px;color:#4d6381}.days{display:grid;grid-template-columns:var(--day-tracks)}.day{border-left:1px solid #e3e9f2;min-width:0}.day:last-child{border-left:0}.day h4{background:#f7f9fc;margin:0;padding:14px;text-align:center;font-size:18px;border-bottom:1px solid #dce5f0}.day-body{padding:7px}.overlap-group{display:grid;grid-template-columns:var(--lane-tracks);gap:8px;margin-bottom:12px}.lane{min-width:0;display:flex;flex-direction:column;gap:8px}.course{padding:8px;border:1px solid var(--border);border-right:5px solid var(--border);background:var(--bg);border-radius:8px;overflow-wrap:anywhere}.core{--bg:#dbeafe;--border:#3b82f6}.general{--bg:#ede9fe;--border:#8b5cf6}.elective{--bg:#dcfce7;--border:#22c55e}.seminar{--bg:#fce7f3;--border:#ec4899}.project{--bg:#fef3c7;--border:#f59e0b}.course h5{font-size:14px;line-height:1.45;margin:7px 0}.time{font-size:14px;font-weight:bold;text-align:right;font-variant-numeric:tabular-nums}.lecturer{font-size:13px;line-height:1.4;margin:6px 0}.meta{font-size:11px;display:flex;justify-content:space-between;gap:6px;flex-wrap:wrap;color:#415571}.reuse-label{font-size:11px;line-height:1.4;color:#235d4a;font-weight:bold;margin:7px 0 0}.course-id{font-size:10px;margin-top:6px;color:#52647b}.semester-note{padding:12px 20px;color:#52647b;font-size:12px;background:#f7f9fc}.empty{padding:12px;color:#64738b}.continuation{background:#fff7e6;border:1px solid #dbc58e;padding:20px;border-radius:12px;margin-bottom:25px}.continuation p{line-height:1.6}li{margin:10px 0}.stamp{font-size:12px;color:#64738b;margin:20px 0}[hidden]{display:none!important}@media(max-width:800px){.page{padding:12px}.hero h1{font-size:25px}.controls{position:static}}@page{size:A4 portrait;margin:9mm}@media print{body{background:#fff;color:#14243b;-webkit-print-color-adjust:exact;print-color-adjust:exact}.page{padding:0;max-width:none}.controls,.hero,.stamp,.cohort-title{display:none}.legend{display:flex;margin:0 0 3mm;gap:10px;font-size:9px}.legend span:before{width:8px;height:8px}.academic-year{break-before:page;page-break-before:always;break-inside:avoid;page-break-inside:avoid;margin:0}.academic-year:first-of-type{break-before:auto;page-break-before:auto}.year-header{margin:0 0 3mm;border-bottom:1px solid #b7c9df}.year-header h2{font-size:16px;margin:0 0 2mm}.year-scroll{overflow:visible;padding:0}.year-grid,.year-grid tbody,.year-grid tr{display:block;width:100%;min-width:0;border:0;border-spacing:0}.semester-cell{display:block;width:100%!important;min-width:0;margin:0 0 4mm;padding:0;break-inside:avoid;page-break-inside:avoid}.semester{height:auto!important;break-inside:avoid;page-break-inside:avoid;border-radius:3px;border:1px solid #cdd8e5}.semester-header{padding:5px 8px;gap:4px;background:#eaf0f9}.semester-header h3{font-size:13px;margin:0}.semester-header>span,.kicker{font-size:9px}.days{grid-template-columns:repeat(var(--print-day-count,3),minmax(0,1fr));align-items:start}.day h4{font-size:11px;padding:4px;background:#f5f8fc}.day-body{padding:4px}.overlap-group{gap:3px;margin-bottom:4px}.lane{gap:3px}.course{padding:4px 5px;border-radius:2px;border-right-width:3px;break-inside:avoid;page-break-inside:avoid}.course h5{font-size:10px;line-height:1.2;margin:2px 0;font-weight:700}.time{font-size:10px}.lecturer{font-size:9px;line-height:1.2;margin:2px 0}.meta{font-size:8px;gap:2px}.course-id{font-size:8px;margin-top:2px}.reuse-label{font-size:8px;margin:2px 0 0}.semester-note{font-size:8px;padding:3px 6px}.empty{font-size:9px;padding:6px}.continuation{break-inside:avoid;padding:8px;margin:3mm 0}.continuation p,.continuation li{font-size:9px;line-height:1.3;margin:3px 0}.core{--bg:#dbeafe;--border:#3b82f6}.general{--bg:#ede9fe;--border:#8b5cf6}.elective{--bg:#dcfce7;--border:#22c55e}.seminar{--bg:#fce7f3;--border:#ec4899}.project{--bg:#fef3c7;--border:#f59e0b}.course,.semester-header,.day h4,.legend span:before{print-color-adjust:exact;-webkit-print-color-adjust:exact}}'''
    return f'''<!DOCTYPE html><html lang="he" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{page_title}</title><style>{css}</style></head><body><main class="page"><header class="hero"><p>{name_label}מערכת שעות</p><h1>מסלול התואר · {years} שנות לימוד</h1><p>כל הסמסטרים של אותה שנה בשורה אחת · השנים מופיעות זו מתחת לזו.</p></header><div class="controls"><label for="cohort">הצגת מערכת:</label><select id="cohort"><option value="A">מחזור א׳</option><option value="B">מחזור ב׳</option><option value="ALL">כל ההצעות — א׳ וב׳</option></select><button onclick="window.print()">הדפסה / שמירה כ־PDF</button><span>לשמירת PDF: בחרו “שמירה כ־PDF” בחלון ההדפסה.</span></div><div class="legend"><span style="--color:#3b82f6">חובה</span><span style="--color:#8b5cf6">כללי</span><span style="--color:#22c55e">בחירה</span><span style="--color:#ec4899">סמינר</span><span style="--color:#f59e0b">פרויקט</span></div>{''.join(pieces)}<p class="stamp">נוצר בתאריך {created} · מקור: מערכת השעות שנוצרה ביישום. השעות המופיעות בכל כרטיס הן שעות המפגש המדויקות.</p></main><script>const select=document.getElementById('cohort');select.addEventListener('change',()=>{{document.querySelectorAll('[data-view]').forEach(el=>{{el.hidden=el.dataset.view!==select.value;}});}});</script></body></html>'''
