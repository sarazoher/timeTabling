from __future__ import annotations

import math
import re
from pathlib import Path

import pandas as pd

from models import Course


SHEET_NAME = "גיליון2"


COURSE_CATEGORY_COLUMN = "קטגוריית קורס"
ORIGINAL_SEMESTER_COLUMN = "סמסטר מקורי"
FLEXIBILITY_COLUMN = "גמישות"
VALID_COURSE_CATEGORIES = {"חובה", "בחירה", "סמינר", "פרויקט", "כללי"}
VALID_FLEXIBILITY = {"fixed", "flexible", "auto", "קבוע", "גמיש", "אוטומטי"}

def normalize_flexibility(value, name: str, category: str) -> str:
    raw = clean_text(value) if value is not None else None
    mapping = {"קבוע": "fixed", "גמיש": "flexible", "אוטומטי": "auto"}
    if raw in mapping:
        return mapping[raw]
    if raw in {"fixed", "flexible", "auto"}:
        return raw
    # Conservative automatic defaults. Only clearly flexible academic types
    # and a few obviously general-skills courses are treated as delayable.
    if category in {"כללי", "בחירה", "סמינר", "פרויקט"}:
        return "flexible"
    return "auto"

# Accepted spellings of each category (the cell is compared after trimming;
# English case-insensitive). Nothing is guessed from the course name.
CATEGORY_SPELLINGS = {
    "חובה": "חובה", "קורס חובה": "חובה", "required": "חובה", "core": "חובה", "mandatory": "חובה",
    "בחירה": "בחירה", "קורס בחירה": "בחירה", "elective": "בחירה",
    "סמינר": "סמינר", "סמינריון": "סמינר", "seminar": "סמינר",
    "פרויקט": "פרויקט", "פרוייקט": "פרויקט", "project": "פרויקט",
    "כללי": "כללי", "לימודים כלליים": "כללי", "general": "כללי",
}


def parse_course_category(value) -> str | None:
    """A category cell -> one of VALID_COURSE_CATEGORIES, or None when empty
    or not recognised (the row is then reported and not loaded)."""
    text = clean_text(value) if value is not None else None
    if not text:
        return None
    return CATEGORY_SPELLINGS.get(text) or CATEGORY_SPELLINGS.get(text.lower())


# Columns a course file must have. Every course needs these values (the
# prerequisites column must exist; its cells may be empty).
REQUIRED_COLUMNS = {
    "מס.שעור",
    "שם השיעור",
    "תיאור ד.קדם",
    "שנת לימודים",
    "נ.זיכוי",
    COURSE_CATEGORY_COLUMN,   # cannot be guessed: an elective's name rarely says so
}
# Columns that may be missing (or have empty cells): a stated default is used
# and the data check says so.
#   תיאור ד.מקבילה  no corequisites
#   שם המרצה        each course gets its own lecturer "מרצה <course ID>"
#   סוג שיעור       lecture ("שעור")
#   ש"ש             one academic hour per credit, lecture only (rounded up
#                   to whole hours); the credit rule credits = L + T/2
DEFAULTED_COLUMNS = {"תיאור ד.מקבילה", "שם המרצה", "סוג שיעור", 'ש"ש'}
DEFAULT_LESSON_TYPE = "שעור"


def clean_text(value) -> str | None:
    if pd.isna(value):
        return None

    text = str(value).replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def normalize_lecturer_name(value) -> str:
    name = clean_text(value)

    if not name:
        raise ValueError("Missing lecturer name")

    return name.replace('ד״ר', 'ד"ר')


def calculate_lecture_tutorial_hours(
    credits: float,
    weekly_hours: float,
    course_type: str,
) -> tuple[float, float, str]:
    """Calculate actual weekly meeting hours from the Excel source.

    The Excel ש"ש value is stored in half-hour units, therefore every course
    first gets its real weekly duration:
        W = 2 * weekly_hours_from_excel

    IMPORTANT: the credit-based lecture/tutorial split is valid ONLY when the
    source column סוג שיעור explicitly says lecture + tutorial.  All other
    lesson types (lecture only, project, seminar, etc.) are a single continuous
    weekly block of W hours with no tutorial split.

    For lecture+tutorial only:
        T = 2 * (W - credits)
        L = 2 * credits - W
    """
    actual_weekly_hours = round(float(weekly_hours) * 2, 2)
    normalized_type = (clean_text(course_type) or "").replace("+", " ו")
    normalized_type = re.sub(r"\s+", " ", normalized_type).strip()

    # The current workbook uses "שעור ותרגול". Accept the common Hebrew
    # spelling "הרצאה ותרגול" as the same explicit source type.
    lecture_tutorial_types = {
        "שעור ותרגול",
        "שיעור ותרגול",
        "הרצאה ותרגול",
    }

    if normalized_type not in lecture_tutorial_types:
        # No credit-based split for lecture-only/project/seminar/etc.
        return actual_weekly_hours, 0.0, "OK"

    tutorial_hours = round(2 * (actual_weekly_hours - credits), 2)
    lecture_hours = round((2 * credits) - actual_weekly_hours, 2)

    if lecture_hours < 0 or tutorial_hours < 0:
        return (
            lecture_hours,
            tutorial_hours,
            "REVIEW_REQUIRED: lecture+tutorial formula produces a negative duration",
        )

    status = "OK"
    if tutorial_hours == 0:
        status = "REVIEW: source says lecture+tutorial but formula gives 0 tutorial hours"
    return lecture_hours, tutorial_hours, status

def find_course_sheet(excel_path: str | Path) -> str:
    """Return the worksheet that holds the course catalog.

    Prefers the historical sheet name (גיליון2); otherwise uses the first
    sheet whose header row contains every required column, so workbooks
    saved as "Sheet1" or with a department-specific sheet name also work.
    """
    best_sheet, best_missing = None, None
    # Context manager closes the file handle (Windows cannot move/delete an
    # upload that is still open).
    with pd.ExcelFile(excel_path) as workbook:
        for name in sorted(workbook.sheet_names, key=lambda n: n != SHEET_NAME):
            header = workbook.parse(sheet_name=name, nrows=0)
            columns = {str(column).strip() for column in header.columns}
            missing = REQUIRED_COLUMNS.difference(columns)
            if not missing:
                return name
            if best_missing is None or len(missing) < len(best_missing):
                best_sheet, best_missing = name, missing
    raise MissingColumnsError(best_sheet, best_missing or REQUIRED_COLUMNS)


# Required columns in the order they appear in the course workbook.
COLUMN_ORDER = ["מס.שעור", "שם השיעור", "תיאור ד.קדם", "תיאור ד.מקבילה", "שנת לימודים",
                "שם המרצה", "נ.זיכוי", 'ש"ש', "סוג שיעור", COURSE_CATEGORY_COLUMN]


class MissingColumnsError(ValueError):
    """No sheet has every required column. Carries the closest sheet and the
    missing columns (in workbook order) so the UI can write the message in
    its own language instead of showing this English text."""

    def __init__(self, sheet, missing):
        self.sheet = sheet
        self.missing = [c for c in COLUMN_ORDER if c in missing] + sorted(set(missing) - set(COLUMN_ORDER))
        super().__init__(
            "No worksheet contains all required course columns. "
            f"Closest sheet '{sheet}' is missing {len(self.missing)}: {', '.join(self.missing)}")


HEBREW_YEAR_LETTERS = {"א": 1, "ב": 2, "ג": 3, "ד": 4, "ה": 5, "ו": 6, "ז": 7, "ח": 8}
KNOWN_COLUMNS = REQUIRED_COLUMNS | DEFAULTED_COLUMNS | {COURSE_CATEGORY_COLUMN, FLEXIBILITY_COLUMN,
                                                        ORIGINAL_SEMESTER_COLUMN}
FIELD_LABELS = {  # column -> (Hebrew, English) for messages
    "מס.שעור": ("מספר קורס", "course ID"), "שם השיעור": ("שם הקורס", "course name"),
    "שנת לימודים": ("שנת לימודים", "study year"), "שם המרצה": ("מרצה", "lecturer"),
    "נ.זיכוי": ("נקודות זכות", "credits"), 'ש"ש': ('ש"ש', "weekly hours"),
    "סוג שיעור": ("סוג שיעור", "lesson type"),
    COURSE_CATEGORY_COLUMN: ("קטגוריית קורס", "course category"),
}


def parse_study_year(value) -> int | None:
    """1, 2.0, "3" or a Hebrew letter (א, ב', ג״) -> int; anything else -> None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = clean_text(value) or ""
    letter = text.replace("'", "").replace("׳", "").replace('"', "").replace("״", "").strip()
    if letter in HEBREW_YEAR_LETTERS:
        return HEBREW_YEAR_LETTERS[letter]
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number == int(number) and number >= 1 else None


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


def default_weekly_hours(credits: float) -> tuple[float, bool]:
    """ש"ש for a lecture-only course without one: W = credits academic hours
    (the app's own rule credits = L + T/2 with no tutorial), rounded up to a
    whole academic hour when the credits are fractional, because meetings
    are built from 45-minute hours on a 5-minute grid. Returns (ש"ש, rounded)."""
    hours = float(credits)
    rounded = hours != int(hours)
    return (math.ceil(hours) if rounded else hours) / 2, rounded


def _names(items, limit=6):
    shown = ", ".join(items[:limit])
    return shown + (f" ועוד {len(items) - limit}" if len(items) > limit else ""), \
        ", ".join(items[:limit]) + (f" and {len(items) - limit} more" if len(items) > limit else "")


def _issue(issues, code, severity, row, course_id, course, he, en):
    """Record a load problem. A caller that collects issues shows them itself
    (dashboard data check, backend DIAGNOSIS block); otherwise print it, as
    the reader always did."""
    if issues is None:
        print(f"[{severity}] " + (f"Excel row {row}: " if row and row > 1 else "") + en)
        return
    issues.append({"code": code, "severity": severity, "row": row, "course_id": course_id,
                   "course": course, "he": he, "en": en})


def load_courses(
    excel_path: str | Path,
    issues: list | None = None,
) -> list[Course]:
    """Read the course catalog. Only the file is used: there are no
    department-specific rules.
    If `issues` is a list, every row that is skipped and every value that is
    ignored or replaced is appended to it (see _issue); nothing is dropped
    silently."""
    excel_path = Path(excel_path)

    if not excel_path.exists():
        raise FileNotFoundError(f"Excel file not found: {excel_path}")

    sheet_name = find_course_sheet(excel_path)
    dataframe = pd.read_excel(excel_path, sheet_name=sheet_name)
    dataframe.columns = [str(column).strip() for column in dataframe.columns]

    unknown = [c for c in dataframe.columns
               if c not in KNOWN_COLUMNS and not (c.startswith("Unnamed") and dataframe[c].isna().all())]
    if unknown:
        listed = ", ".join(f"'{c}'" for c in unknown)
        _issue(issues, "unknown_columns", "info", 1, None, None,
               f"עמודות שהמערכת לא מכירה ולכן מתעלמת מהן: {listed}. אם אחת מהן אמורה להיות "
               f"'{COURSE_CATEGORY_COLUMN}' או '{FLEXIBILITY_COLUMN}', יש לתקן את הכותרת.",
               f"Columns the app does not use and ignores: {listed}. If one of them is meant to be "
               f"'{COURSE_CATEGORY_COLUMN}' or '{FLEXIBILITY_COLUMN}', fix the header.")

    courses: list[Course] = []
    seen_ids: dict[int, int] = {}
    letter_years: list[str] = []
    defaulted = {"lecturer": [], "type": [], "hours": [], "rounded": [], "split_unknown": []}

    def cell(row, column):
        return row[column] if column in dataframe.columns else None

    for excel_row, row in dataframe.iterrows():
        row_number = excel_row + 2

        essential_values = [
            row["מס.שעור"],
            row["שם השיעור"],
            row["שנת לימודים"],
            row["נ.זיכוי"],
            row[COURSE_CATEGORY_COLUMN],
        ]

        label_name = clean_text(row["שם השיעור"])
        label_id = clean_text(row["מס.שעור"])
        who_he = f"\"{label_name}\"" if label_name else (f"קורס {label_id}" if label_id else "הקורס")
        who_en = f"\"{label_name}\"" if label_name else (f"course {label_id}" if label_id else "the course")

        essential_columns = ["מס.שעור", "שם השיעור", "שנת לימודים", "נ.זיכוי", COURSE_CATEGORY_COLUMN]
        missing = [column for column, value in zip(essential_columns, essential_values) if pd.isna(value)
                   or not clean_text(value)]
        if missing:
            if len(missing) == len(essential_columns) and all(
                    pd.isna(v) or not clean_text(v) for v in row.tolist()):
                continue  # a completely empty row is not a course
            _issue(issues, "row_skipped_missing", "warning", row_number, label_id, label_name,
                   f"{who_he} (שורה {row_number}) לא נטען, כי חסר: "
                   + ", ".join(FIELD_LABELS[c][0] for c in missing) + ".",
                   f"{who_en} (row {row_number}) was NOT loaded because it is missing: "
                   + ", ".join(FIELD_LABELS[c][1] for c in missing) + ".")
            continue

        credits = _number(row["נ.זיכוי"])
        raw_hours = cell(row, 'ש"ש')
        hours_given = clean_text(raw_hours) is not None if raw_hours is not None else False
        weekly_hours = _number(raw_hours) if hours_given else None
        id_number = _number(row["מס.שעור"])
        study_year = parse_study_year(row["שנת לימודים"])
        invalid = [c for c, ok in (("מס.שעור", id_number is not None and id_number == int(id_number)),
                                   ("שנת לימודים", study_year is not None),
                                   ("נ.זיכוי", credits is not None and credits > 0),
                                   (COURSE_CATEGORY_COLUMN, parse_course_category(row[COURSE_CATEGORY_COLUMN]) is not None),
                                   ('ש"ש', not hours_given or (weekly_hours is not None and weekly_hours > 0)))
                   if not ok]
        if invalid:
            _issue(issues, "row_skipped_invalid", "warning", row_number, label_id, label_name,
                   f"{who_he} (שורה {row_number}) לא נטען, כי הערך לא תקין ב: "
                   + ", ".join(f"{FIELD_LABELS[c][0]} ('{clean_text(row[c])}')" for c in invalid) + "."
                   + (" קטגוריה אפשרית: חובה, בחירה, סמינר, פרויקט או כללי." if COURSE_CATEGORY_COLUMN in invalid else ""),
                   f"{who_en} (row {row_number}) was NOT loaded because of an invalid value in: "
                   + ", ".join(f"{FIELD_LABELS[c][1]} ('{clean_text(row[c])}')" for c in invalid) + "."
                   + (" A category must be חובה, בחירה, סמינר, פרויקט or כללי." if COURSE_CATEGORY_COLUMN in invalid else ""))
            continue
        if not isinstance(row["שנת לימודים"], (int, float)) and clean_text(row["שנת לימודים"]) not in {
                str(study_year), f"{study_year}.0"}:
            letter_years.append(clean_text(row["שנת לימודים"]))

        course_name = clean_text(row["שם השיעור"]) or ""
        course_id = int(id_number)
        course_type = clean_text(cell(row, "סוג שיעור")) if cell(row, "סוג שיעור") is not None else None
        if not course_type:
            course_type = DEFAULT_LESSON_TYPE
            defaulted["type"].append(course_name)
        lecturer_value = cell(row, "שם המרצה")
        lecturer = clean_text(lecturer_value) if lecturer_value is not None else None
        if not lecturer:
            lecturer = f"מרצה {course_id}"
            defaulted["lecturer"].append(course_name)
        hours_type = course_type
        if not hours_given:
            weekly_hours, rounded = default_weekly_hours(credits)
            defaulted["hours"].append(course_name)
            if rounded:
                defaulted["rounded"].append(f"{course_name} ({credits:g}→{weekly_hours * 2:g})")
            if "תרגול" in course_type:
                defaulted["split_unknown"].append(course_name)
                hours_type = DEFAULT_LESSON_TYPE  # the lecture/tutorial split cannot be known
        if course_id in seen_ids:
            # Blocking: the solver stores each course's semester by its ID, so two
            # courses with one ID would be mixed up without any error.
            _issue(issues, "duplicate_id", "blocking", row_number, course_id, course_name,
                   f"מספר הקורס {course_id} מופיע פעמיים (שורות {seen_ids[course_id]} ו-{row_number}). "
                   f"לכל קורס צריך מספר משלו; יש לתקן את הקובץ לפני בניית המערכת.",
                   f"Course ID {course_id} appears twice (rows {seen_ids[course_id]} and {row_number}). "
                   f"Every course needs its own ID; fix the file before building.")
        seen_ids.setdefault(course_id, row_number)

        raw_flex = row[FLEXIBILITY_COLUMN] if FLEXIBILITY_COLUMN in dataframe.columns else None
        course_category = parse_course_category(row[COURSE_CATEGORY_COLUMN])
        # Semester is NEVER fixed by the workbook in this project.
        # The catalog fixes only the study year; Stage 1 chooses the semester.
        original_semester = None
        flexibility = normalize_flexibility(
            raw_flex,
            course_name,
            course_category,
        )

        lecture_hours, tutorial_hours, calculation_status = (
            calculate_lecture_tutorial_hours(
                credits=credits,
                weekly_hours=weekly_hours,
                course_type=hours_type,
            )
        )
        courses.append(
            Course(
                course_id=course_id,
                name=course_name,
                prerequisites=clean_text(row["תיאור ד.קדם"]),
                parallel_requirement=clean_text(cell(row, "תיאור ד.מקבילה"))
                if cell(row, "תיאור ד.מקבילה") is not None else None,
                year=study_year,
                lecturer=normalize_lecturer_name(lecturer),
                credits=credits,
                weekly_hours=weekly_hours,
                course_type=course_type,
                course_category=course_category,
                lecture_hours=lecture_hours,
                tutorial_hours=tutorial_hours,
                calculation_status=calculation_status,
                original_semester=original_semester,
                flexibility=flexibility,
            )
        )

    total = len(courses)
    if defaulted["lecturer"]:
        n = len(defaulted["lecturer"])
        he_names, en_names = _names(defaulted["lecturer"])
        _issue(issues, "lecturer_default", "warning", None, None, None,
               (f"אין מרצה בקובץ לאף קורס" if n == total else f"ל-{n} קורסים אין מרצה ({he_names})")
               + ': כל אחד מהם קיבל מרצה משלו ("מרצה <מספר קורס>"), ולכן התנגשויות בין מרצים לא נבדקות עבורם.',
               ("No course in the file has a lecturer" if n == total else f"{n} course(s) have no lecturer ({en_names})")
               + ': each got its own lecturer ("מרצה <course ID>"), so lecturer clashes are not checked for them.')
    if defaulted["hours"]:
        n = len(defaulted["hours"])
        he_names, en_names = _names(defaulted["hours"])
        he = (('אין ש"ש בקובץ לאף קורס' if n == total else f'ל-{n} קורסים אין ש"ש ({he_names})')
              + ': השעות חושבו לפי נקודות הזכות, שעה אקדמית אחת לכל נ"ז, כהרצאה בלבד.')
        en = (("No course in the file has weekly hours" if n == total else f"{n} course(s) have no weekly hours ({en_names})")
              + ": hours were computed from the credits, one academic hour per credit, lecture only.")
        if defaulted["rounded"]:
            he_r, en_r = _names(defaulted["rounded"])
            he += f" נקודות זכות לא שלמות עוגלו כלפי מעלה לשעה שלמה: {he_r}."
            en += f" Fractional credits were rounded up to a whole hour: {en_r}."
        if defaulted["split_unknown"]:
            he_s, en_s = _names(defaulted["split_unknown"])
            he += f" מסומנים 'שעור ותרגול' בלי ש\"ש, ולכן שובצה הרצאה בלבד: {he_s}."
            en += f" Marked lecture + tutorial without weekly hours, so only a lecture was scheduled: {en_s}."
        _issue(issues, "hours_default", "warning", None, None, None, he, en)
    # Not reported (by request): lesson type defaulted, no corequisites column,
    # years written as letters, unrecognised category/flexibility values,
    # lesson types that mention תרגול, and 0 tutorial hours. The behaviour is
    # unchanged; only the messages are gone.
    return courses
