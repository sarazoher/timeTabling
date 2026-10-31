"""Plain-language pre-solve diagnosis.

Every BLOCKING check below is a *necessary* condition for a timetable to
exist, so a blocking finding proves the configuration is impossible and says
why in words a department can act on. The solver still runs afterwards; if
it ever succeeds despite a blocking finding, that is a bug in this module
(main.py prints a loud warning in that case).

Checks
------
meeting_too_long   a single meeting is longer than every teaching window
                   available to its lecturer
year_too_short     a prerequisite / corequisite chain needs more semesters
                   than the course's study year has
weekly_capacity    the core/general (non-overlapping) hours of a study year
                   exceed what its semesters can hold
long_days          meetings that fit on only one weekday outnumber what that
                   day can hold across the year's semesters, or several such
                   meetings are forced into the SAME semester by
                   prerequisites and do not fit in that one day
unresolved / malformed requirement cells  (warnings: no constraint applied)

Data check
----------
Rows the reader skipped, defaulted lecturers / weekly hours, a duplicate
course ID (blocking: the solver keys courses by ID), unknown columns,
categories guessed from names (all passed in as `reader_issues` from excel_reader.load_courses), and
OR-alternatives that are not in the catalog. Minor notices (lesson type
defaulted, no corequisites column, years as letters, unrecognised category /
flexibility / lesson-type values, 0 tutorial hours, duplicate names, a course
requiring itself, stale lecturer availability, aliases with no effect) were
removed on request; the behaviour behind them is unchanged.

Severities: "blocking" (no timetable can exist), "warning" (something the
file says is NOT being used), "info" (worth knowing, usually harmless).

Semester B (late intake) is not analysed here.
"""
from __future__ import annotations

import functools
from dataclasses import asdict, dataclass, field

from scheduler import (
    ACADEMIC_HOUR_MINUTES,
    SLOT_MINUTES,
    _requirement_set,
    _time_to_slot,
    normalize_availability,
)

PROTECTED = {"חובה", "כללי"}
DAY_HE = {"Sunday": "ראשון", "Monday": "שני", "Tuesday": "שלישי", "Wednesday": "רביעי",
          "Thursday": "חמישי", "Friday": "שישי", "Saturday": "שבת"}


@dataclass
class Finding:
    severity: str            # "blocking" | "warning" | "info"
    code: str
    he: str
    en: str
    course_ids: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _syntax_reason_he(reason: str) -> str:
    """Hebrew wording for requirements.RequirementSyntaxError messages."""
    text = reason.split(": ", 1)[-1]
    if text.startswith("misplaced parenthesis in "):
        return "סוגריים במקום שגוי ב-" + text[len("misplaced parenthesis in "):]
    if "closing ')'" in text:
        return "חסר סוגר ')'"
    if text.startswith("')' without"):
        return "סוגר ')' בלי '(' מתאים"
    return text


def _hours(minutes: int) -> str:
    h, m = divmod(int(round(minutes)), 60)
    return f"{h}:{m:02d}"


def _meetings(course):
    """(label, minutes) for each weekly meeting of a course."""
    out = []
    for label, hours in (("lecture", course.lecture_hours), ("tutorial", course.tutorial_hours)):
        if hours and float(hours) > 0:
            out.append((label, float(hours) * ACADEMIC_HOUR_MINUTES))
    return out


def _lecturer_windows(lecturer, day_windows, lecturer_availability):
    """Effective {day: minutes} for a lecturer (same rule as the solver:
    a lecturer override can only narrow the department window)."""
    result = {}
    rules = lecturer_availability.get(lecturer) or {}
    for day, (start, end) in day_windows.items():
        rule = rules.get(day)
        if rule is not None:
            if not rule.get("enabled", False):
                continue
            start = max(start, _time_to_slot(rule.get("start", "08:00")))
            end = min(end, _time_to_slot(rule.get("end", "20:00")))
        if end > start:
            result[day] = (end - start) * SLOT_MINUTES
    return result


def catalog_findings(courses, availability: dict, reader_issues=None) -> list[Finding]:
    """Everything in the course file that is skipped, ignored or replaced."""
    findings: list[Finding] = []
    for issue in reader_issues or []:
        findings.append(Finding(issue["severity"], issue["code"], issue["he"], issue["en"],
                                [issue["course_id"]] if issue.get("course_id") is not None else []))

    req = _requirement_set(courses, availability)
    for note in req.notes:
        ignored = " / ".join(note["ignored_alternatives"])
        kept = " / ".join(note["kept_alternatives"])
        findings.append(Finding(
            "info", "or_alternative_ignored",
            f"בדרישה של \"{note['course']}\" החלופה {ignored} לא קיימת בקטלוג ולכן נשמטה; נשאר: {kept}.",
            f"In a requirement of \"{note['course']}\", the alternative {ignored} is not in the catalog "
            f"and was dropped; kept: {kept}.",
            [note["course_id"]]))

    return findings


def diagnose(courses, availability: dict, reader_issues=None) -> list[Finding]:
    findings: list[Finding] = catalog_findings(courses, availability, reader_issues)
    spy = int(availability.get("semesters_per_year", 3))
    degree_years = int(availability.get("degree_years", 3))
    _, day_windows = normalize_availability(availability)
    lecturer_availability = availability.get("lecturer_availability", {}) or {}
    week_minutes = sum((end - start) * SLOT_MINUTES for start, end in day_windows.values())
    req = _requirement_set(courses, availability)
    n = len(courses)

    def year_range(i):
        y = courses[i].year
        return (y - 1) * spy + 1, y * spy

    # --- degree length ----------------------------------------------------
    for i, c in enumerate(courses):
        if c.year > degree_years:
            findings.append(Finding(
                "blocking", "degree_too_short",
                f"הקורס \"{c.name}\" שייך לשנה {c.year}, אבל התואר מוגדר ל-{degree_years} שנים.",
                f"\"{c.name}\" is a year-{c.year} course but the degree is set to {degree_years} years.",
                [c.course_id]))

    # --- meeting longer than any available day -------------------------------
    windows = {}
    for i, c in enumerate(courses):
        windows[i] = _lecturer_windows(c.lecturer, day_windows, lecturer_availability)
        longest = max(windows[i].values(), default=0)
        for label, minutes in _meetings(c):
            if minutes > longest:
                he_label = "ההרצאה" if label == "lecture" else "התרגול"
                findings.append(Finding(
                    "blocking", "meeting_too_long",
                    f"{he_label} של \"{c.name}\" נמשכת {_hours(minutes)} שעות ברצף, "
                    f"אבל היום הארוך ביותר שזמין למרצה ({c.lecturer}) הוא {_hours(longest)} שעות.",
                    f"The {label} of \"{c.name}\" is one continuous {_hours(minutes)} h meeting, "
                    f"but the longest day available to its lecturer ({c.lecturer}) is {_hours(longest)} h.",
                    [c.course_id]))

    # --- earliest / latest semester from requirement chains -----------------
    @functools.lru_cache(maxsize=None)
    def earliest(i):
        lo = year_range(i)[0]
        for p in req.prereq_all.get(i, []):
            lo = max(lo, earliest(p) + 1)
        for group in req.prereq_any.get(i, []):
            lo = max(lo, min(earliest(a) for a in group) + 1)
        for p in req.parallel_all.get(i, []):
            lo = max(lo, earliest(p))
        for group in req.parallel_any.get(i, []):
            lo = max(lo, min(earliest(a) for a in group))
        return lo

    def chain_to(i):
        lo, best = year_range(i)[0], None
        for p in req.prereq_all.get(i, []):
            if earliest(p) + 1 > lo:
                lo, best = earliest(p) + 1, p
        for group in req.prereq_any.get(i, []):
            a = min(group, key=earliest)
            if earliest(a) + 1 > lo:
                lo, best = earliest(a) + 1, a
        for p in req.parallel_all.get(i, []):
            if earliest(p) > lo:
                lo, best = earliest(p), p
        return (chain_to(best) if best is not None else []) + [courses[i].name]

    # Only AND edges force a prerequisite to be early (an OR alternative may
    # simply not be the one used), so "latest" follows AND dependents only.
    dependents = {i: [] for i in range(n)}
    for dep, prereqs in req.prereq_all.items():
        for p in prereqs:
            dependents[p].append((dep, 1))
    for dep, pars in req.parallel_all.items():
        for p in pars:
            dependents[p].append((dep, 0))

    @functools.lru_cache(maxsize=None)
    def latest(i):
        hi = year_range(i)[1]
        for dep, gap in dependents[i]:
            hi = min(hi, latest(dep) - gap)
        return hi

    try:
        bounds = {i: (earliest(i), latest(i)) for i in range(n)}
    except RecursionError:
        findings.append(Finding(
            "blocking", "cycle",
            "יש מעגל בדרישות הקדם (קורס שדורש את עצמו דרך קורסים אחרים).",
            "The prerequisites contain a cycle (a course that requires itself through other courses).", []))
        return findings

    for i, c in enumerate(courses):
        e, _ = bounds[i]
        end = year_range(i)[1]
        if e > end:
            chain = " ← ".join(chain_to(i))
            chain_en = " → ".join(chain_to(i))
            need = e - year_range(i)[0] + 1
            findings.append(Finding(
                "blocking", "year_too_short",
                f"שרשרת הקדם של \"{c.name}\" צריכה {need} סמסטרים בשנה {c.year}, "
                f"ובשנה יש {spy}: {chain}.",
                f"The prerequisite chain of \"{c.name}\" needs {need} semesters inside "
                f"year {c.year}, which has {spy}: {chain_en}.",
                [c.course_id]))

    # --- weekly capacity per study year (core + general cannot overlap) -----
    for year in sorted({c.year for c in courses}):
        idx = [i for i, c in enumerate(courses) if c.year == year and c.course_category in PROTECTED]
        need = sum(m for i in idx for _, m in _meetings(courses[i]))
        have = week_minutes * spy
        if need > have:
            findings.append(Finding(
                "blocking", "weekly_capacity",
                f"בשנה {year} יש {_hours(need)} שעות של קורסי חובה/כללי, אבל {spy} סמסטרים "
                f"של {_hours(week_minutes)} שעות שבועיות מכילים רק {_hours(have)}.",
                f"Year {year} has {_hours(need)} h of core/general teaching, but {spy} semesters "
                f"of {_hours(week_minutes)} weekly hours hold only {_hours(have)}.",
                [courses[i].course_id for i in idx]))
        elif have and need > 0.9 * have:
            findings.append(Finding(
                "warning", "weekly_capacity_tight",
                f"שנה {year} כמעט מלאה: {_hours(need)} מתוך {_hours(have)} שעות זמינות לקורסי חובה/כללי.",
                f"Year {year} is nearly full: {_hours(need)} of {_hours(have)} available core/general hours.",
                []))

    # --- meetings that fit on only one weekday ------------------------------
    single_day = {}   # (year, day) -> [(i, minutes)]
    for i, c in enumerate(courses):
        if c.course_category not in PROTECTED:
            continue
        for _, minutes in _meetings(c):
            fitting = [d for d, w in windows[i].items() if w >= minutes]
            if len(fitting) == 1:
                single_day.setdefault((c.year, fitting[0]), []).append((i, minutes))

    for (year, day), items in sorted(single_day.items()):
        day_minutes = (day_windows[day][1] - day_windows[day][0]) * SLOT_MINUTES
        names = ", ".join(f"\"{courses[i].name}\" ({_hours(m)})" for i, m in items)
        # (a) pigeonhole over the whole year
        if sum(m for _, m in items) > day_minutes * spy:
            findings.append(Finding(
                "blocking", "long_days",
                f"בשנה {year} יש מפגשים שנכנסים רק ביום {DAY_HE[day]} ({_hours(day_minutes)} שעות), "
                f"ויחד הם ארוכים מכל ימי ה{DAY_HE[day]} של השנה: {names}.",
                f"In year {year} these meetings fit only on {day} ({_hours(day_minutes)} h), and together "
                f"they exceed all {spy} {day}s of the year: {names}.",
                [courses[i].course_id for i, _ in items]))
            continue
        # (b) several forced into the same semester by prerequisites
        by_forced_semester = {}
        for i, m in items:
            e, l = bounds[i]
            if e == l:
                by_forced_semester.setdefault(e, []).append((i, m))
        for semester, forced in sorted(by_forced_semester.items()):
            if len(forced) > 1 and sum(m for _, m in forced) > day_minutes:
                in_year = semester - (year - 1) * spy
                names = " ו-".join(f"\"{courses[i].name}\"" for i, _ in forced)
                names_en = " and ".join(f"\"{courses[i].name}\"" for i, _ in forced)
                findings.append(Finding(
                    "blocking", "long_days",
                    f"בשנה {year}, סמסטר {in_year}: {names} חייבים להיות באותו סמסטר בגלל דרישות הקדם, "
                    f"וכל אחד מהם נכנס רק ביום {DAY_HE[day]} ({_hours(day_minutes)} שעות), שבו אין מקום לכולם. "
                    f"צריך יום לימודים ארוך נוסף, או לפצל את הקורסים למפגשים קצרים יותר.",
                    f"Year {year}, semester {in_year}: {names_en} must share this semester because of "
                    f"prerequisites, and each fits only on {day} ({_hours(day_minutes)} h), which cannot hold "
                    f"them all. Add another long teaching day, or split these courses into shorter meetings.",
                    [courses[i].course_id for i, _ in forced]))

    # --- requirement cells that add no constraint ---------------------------
    for item in req.unresolved:
        malformed = str(item.get("reason", "")).startswith("malformed")
        findings.append(Finding(
            "warning", "malformed_requirement" if malformed else "unresolved_requirement",
            (f"לא ניתן לקרוא את תא הדרישות של \"{item['course']}\" ({_syntax_reason_he(item['reason'])}), "
             f"ולכן הוא לא הוחל." if malformed else
             f"דרישה של \"{item['course']}\" לא נמצאה בקטלוג ולא הוחלה: {item['unresolved_name']}."),
            (f"The requirement cell of \"{item['course']}\" could not be read "
             f"({item['reason'].split(': ', 1)[-1]}) and was not applied." if malformed else
             f"A requirement of \"{item['course']}\" is not in the catalog and was not applied: "
             f"{item['unresolved_name']}."),
            [item["course_id"]]))
    return findings
