from __future__ import annotations

# Windows terminals often default to a legacy code page (cp1252/cp1255).
# Keep Hebrew diagnostics from crashing the application with a charmap error.
import sys
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, OSError):
        pass

import copy
from datetime import datetime
import re
import json
import os
import shutil
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
    send_file,
    send_from_directory,
    jsonify,
)
from flask_babel import Babel, gettext as _
from werkzeug.utils import secure_filename


app = Flask(__name__)
from auth import install_auth, local_next
install_auth(app)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PROJECT_ROOT / "timetable_backend"
CONTENT_TRANSLATIONS_FILE = FRONTEND_DIR / "course_translations.json"

# ---------------------------------------------------------------------------
# One workspace per account. Every account has its own folder with its own
# course file, settings, timetable and remembered column names, so a new
# account starts empty. The solver (main.py) runs inside that folder: all its
# files are relative paths.
# ---------------------------------------------------------------------------
DEFAULT_WORKSPACES_DIR = BACKEND_DIR / "workspaces"


def workspace_dir() -> Path:
    """The signed-in account's folder (created on first use)."""
    try:
        user = g.get("user")
    except RuntimeError:          # outside a request: there is no account
        user = None
    if not user:
        raise RuntimeError("No signed-in account: the workspace is per account.")
    root = Path(app.config.get("WORKSPACES_DIR") or DEFAULT_WORKSPACES_DIR)
    folder = root / f"user_{int(user['id'])}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


class WorkspaceFile(os.PathLike):
    """A file inside the signed-in account's workspace. Behaves like a Path
    (exists, open, read_text, unlink, name, ...), resolved per request."""

    def __init__(self, name: str):
        self._name = name

    def path(self) -> Path:
        return workspace_dir() / self._name

    def __fspath__(self) -> str:
        return str(self.path())

    def __str__(self) -> str:
        return str(self.path())

    def __repr__(self) -> str:
        return f"WorkspaceFile({self._name!r})"

    @property
    def name(self) -> str:
        return self._name

    def __getattr__(self, attribute):
        return getattr(self.path(), attribute)


TIMETABLE_FILE = WorkspaceFile("timetable.json")
CONFIG_FILE = WorkspaceFile("schedule_config.json")
COURSES_FILE = WorkspaceFile("courses.json")
COURSES_EXCEL_FILE = WorkspaceFile("courses.xlsx")
DEBUG_TIMETABLE_FILE = WorkspaceFile("timetable_debug.json")
UNRESOLVED_FILE = WorkspaceFile("unresolved_requirements.json")
SEMESTER_PLAN_FILE = WorkspaceFile("semester_plan.xlsx")
CURRICULUM_PLAN_FILE = WorkspaceFile("curriculum_plan.json")

# The natural-language interpreter lives beside the solver. Import it only
# after BACKEND_DIR is known so the frontend and backend remain separate apps.
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
from ai_constraint_interpreter import claude_is_configured, interpret_with_claude
from year_plan import (DEFAULT_YEAR_MODE, YEAR_MODES, apply_degree_years, apply_year_overrides,
                       degree_years_auto, override_findings, year_mode)

# ---------------------------------------------------------------------------
# Internationalization (Flask-Babel)
# ---------------------------------------------------------------------------
# The UI text lives in Hebrew source strings (wrapped in `_()` in the
# templates/Python below); an English catalog under translations/en supplies
# the English text. A Hebrew catalog is included too for completeness, even
# though missing entries would simply fall back to the Hebrew msgid.

app.config["LANGUAGES"] = {"he": "עברית", "en": "English"}
app.config["BABEL_DEFAULT_LOCALE"] = "he"
app.config["BABEL_TRANSLATION_DIRECTORIES"] = "translations"


def select_locale() -> str:
    return session.get("lang", app.config["BABEL_DEFAULT_LOCALE"])


babel = Babel(app, locale_selector=select_locale)


@app.context_processor
def inject_i18n():
    current_lang = select_locale()
    return {
        "current_lang": current_lang,
        "text_direction": "rtl" if current_lang == "he" else "ltr",
        "languages": app.config["LANGUAGES"],
        "day_label": lambda day_key: day_label_for(day_key, current_lang),
    }


@app.context_processor
def inject_setup_progress():
    """What the top menu needs: Rules opens once a course file is loaded,
    Timetable once a timetable has been built (redesign step 13)."""
    if not g.get("user"):
        return {}
    return {"nav_has_file": COURSES_EXCEL_FILE.exists(), "nav_has_timetable": TIMETABLE_FILE.exists()}


@app.route("/favicon.ico")
def favicon():
    """Browsers ask for /favicon.ico on their own, also for pages without a template."""
    return send_from_directory(app.static_folder, "favicon.ico", mimetype="image/vnd.microsoft.icon")


@app.route("/language/<lang_code>")
def set_language(lang_code: str):
    if lang_code in app.config["LANGUAGES"]:
        session["lang"] = lang_code
    return redirect(local_next(request.referrer.removeprefix(request.host_url.rstrip("/")) if request.referrer else "/"))


ALL_DAYS = [
    {"key": "Sunday", "hebrew": "ראשון"},
    {"key": "Monday", "hebrew": "שני"},
    {"key": "Tuesday", "hebrew": "שלישי"},
    {"key": "Wednesday", "hebrew": "רביעי"},
    {"key": "Thursday", "hebrew": "חמישי"},
    {"key": "Friday", "hebrew": "שישי"},
    {"key": "Saturday", "hebrew": "שבת"},
]
DAY_LOOKUP = {day["key"]: day for day in ALL_DAYS}

DEFAULT_CONFIG = {
    "degree_years": 3,
    # True: degree_years follows the course file's highest study year.
    "degree_years_auto": True,
    # Shown in the downloaded timetable's header and file name.
    "program_name": "",
    "semesters_per_year": 3,
    "allow_semester_b_start": False,
    # Study years: strict | suggest | auto (year_plan.py). Approved moves are
    # kept in year_overrides as {course_id: year}.
    "year_mode": DEFAULT_YEAR_MODE,
    "year_overrides": {},
    "days": {
        day["key"]: {
            "enabled": day["key"] in {
                "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"
            },
            "start": "08:00",
            "end": "20:00",
        }
        for day in ALL_DAYS
    },
}


def day_label_for(day_key: str, locale: str) -> str:
    """English day names are already the dict keys, so English needs no
    lookup; Hebrew pulls the matching label from ALL_DAYS."""
    day = DAY_LOOKUP.get(day_key)
    if not day:
        return day_key
    return day_key if locale == "en" else day["hebrew"]


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_config(config: dict) -> None:
    with CONFIG_FILE.open("w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=4)


def load_timetable() -> list[dict]:
    return load_json(TIMETABLE_FILE, [])


def load_content_translations() -> dict:
    return load_json(CONTENT_TRANSLATIONS_FILE, {})


def load_config() -> dict:
    # A copy: a new account must never change the shared defaults.
    config = load_json(CONFIG_FILE, None) or copy.deepcopy(DEFAULT_CONFIG)
    config.pop("department", None)   # departments were removed; an old config may still name one
    config.setdefault("degree_years", DEFAULT_CONFIG["degree_years"])
    config["degree_years_auto"] = degree_years_auto(config)
    config.setdefault(
        "semesters_per_year",
        DEFAULT_CONFIG["semesters_per_year"],
    )
    config.setdefault("allow_semester_b_start", DEFAULT_CONFIG["allow_semester_b_start"])
    config["year_mode"] = year_mode(config)
    config.setdefault("year_overrides", {})
    config.setdefault("days", {})
    config.setdefault("ai_constraints", [])
    config.setdefault("ai_chat", [])
    config.setdefault("lecturer_availability", {})

    for day in ALL_DAYS:
        config["days"].setdefault(
            day["key"],
            DEFAULT_CONFIG["days"][day["key"]].copy(),
        )

    return config


def time_to_minutes(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def validate_form_config(config: dict) -> None:
    degree_years = int(config["degree_years"])
    semesters_per_year = int(config["semesters_per_year"])

    if degree_years < 1 or degree_years > 10:
        raise ValueError(_("The number of years in the degree must be between 1 and 10."))

    if semesters_per_year < 1 or semesters_per_year > 4:
        raise ValueError(_("The number of semesters per year must be between 1 and 4."))

    enabled_count = 0

    for day in ALL_DAYS:
        values = config["days"][day["key"]]

        if not values["enabled"]:
            continue

        enabled_count += 1
        for value in (values["start"], values["end"]):
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(value or "")):
                raise ValueError(
                    ("Write times as hours:minutes, for example 08:00 or 17:30 (%s)." % day_label_for(day["key"], select_locale()))
                    if select_locale() == "en" else
                    ("יש לכתוב שעות בצורה שעה:דקות, למשל 08:00 או 17:30 (%s)." % day_label_for(day["key"], select_locale())))
        start = time_to_minutes(values["start"])
        end = time_to_minutes(values["end"])

        if start >= end:
            raise ValueError(
                _(
                    "On %(day)s the start time must be before the end time.",
                    day=day_label_for(day["key"], select_locale()),
                )
            )

        if start % 5 != 0 or end % 5 != 0:
            raise ValueError(_("Hours must be in 5-minute increments."))

    if enabled_count == 0:
        raise ValueError(_("You must select at least one teaching day."))



def _return_url(anchor: str = "", return_to: str | None = None) -> str:
    """Where a form goes back to: the guided Setup page when it was sent from
    there (hidden field return_to=setup), otherwise the home page."""
    if return_to is None:
        return_to = request.form.get("return_to") or request.args.get("return_to")
    if return_to == "setup":
        return url_for("guided_setup")
    if return_to == "data":
        return url_for("data_page") + anchor
    if return_to == "rules":
        return url_for("rules_page") + anchor
    return url_for("setup_page") + anchor


def config_from_form() -> dict:
    previous = load_config()
    config = {
        "degree_years": int(request.form.get("degree_years", 3)),
        "program_name": (" ".join(request.form.get("program_name", "").split())[:80]
                         if "program_name" in request.form else previous.get("program_name", "")),
        # Only the Settings section has this checkbox; other forms keep the saved choice.
        "degree_years_auto": (request.form.get("degree_years_auto") == "on"
                              if "degree_years_auto_shown" in request.form
                              else degree_years_auto(previous)),
        "semesters_per_year": int(
            request.form.get("semesters_per_year", 3)
        ),
        "allow_semester_b_start": request.form.get("allow_semester_b_start") == "on",
        "year_mode": year_mode({"year_mode": request.form.get("year_mode", previous.get("year_mode"))}),
        "year_overrides": previous.get("year_overrides", {}),
        "course_file_name": previous.get("course_file_name", ""),
        "days": {},
        # Keep natural-language requirements when the department changes
        # general availability settings.
        "ai_constraints": previous.get("ai_constraints", []),
        "ai_chat": previous.get("ai_chat", []),
        "lecturer_availability": previous.get("lecturer_availability", {}),
    }

    for day in ALL_DAYS:
        key = day["key"]
        enabled = request.form.get(f"enabled_{key}") == "on"
        start = request.form.get(f"start_{key}", "08:00")
        end = request.form.get(f"end_{key}", "20:00")

        config["days"][key] = {
            "enabled": enabled,
            "start": start,
            "end": end,
        }

    validate_form_config(config)
    return config



DIAGNOSIS_FILE = WorkspaceFile("diagnosis.json")
DATA_CHECK_FILE = WorkspaceFile("data_check.json")
BUILD_FAILURE_FILE = WorkspaceFile("last_build_failure.json")


def _save_build_failure(message: str, details: str, debug_url: str | None) -> str:
    """Store a failed build's report (plain-language diagnosis + raw log) and
    return the headline to flash. A blocking diagnosis replaces the generic
    "infeasible or out of time" guess with a definite statement."""
    findings = load_json(DIAGNOSIS_FILE, [])
    en = select_locale() == "en"
    if any(item.get("severity") == "blocking" for item in findings):
        message = ("No timetable is possible with the current course data and settings. The reasons:"
                   if en else "לא ניתן לבנות מערכת שעות עם נתוני הקורסים וההגדרות הנוכחיים. הסיבות:")
    BUILD_FAILURE_FILE.write_text(json.dumps({
        "message": message, "details": details, "debug_url": debug_url, "findings": findings,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return message


def _backend_command() -> list[str]:
    # The solver runs inside the account's workspace; main.py reads
    # courses.xlsx and the config there and writes its results there.
    return [
        sys.executable,
        str(BACKEND_DIR / "main.py"),
        "courses.xlsx",
        "--config",
        CONFIG_FILE.name,
        "--time-limit",
        "35",
    ]


def run_backend() -> tuple[bool, str]:
    """Run a build and wait for it (used when builds do not run in the background)."""
    command = _backend_command()

    try:
        result = subprocess.run(
            command,
            cwd=workspace_dir(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        # The backend should normally stop on its own internal budgets before this
        # outer safety timeout. Preserve the last machine-readable snapshot so a
        # timeout can be diagnosed without relying on truncated console logs.
        debug_note = ""
        if DEBUG_TIMETABLE_FILE.exists():
            debug_note = f"\nDIAGNOSTIC DEBUG_JSON={DEBUG_TIMETABLE_FILE.resolve()}"
        return False, _("The solver exceeded the 3-minute time limit.") + debug_note

    if result.returncode != 0:
        # Preserve BOTH streams. Diagnostics are intentionally printed to stdout
        # while tracebacks/runtime failures normally go to stderr. Choosing only
        # stderr used to hide the useful solver context, especially in 7-day runs.
        stdout_text = (result.stdout or "").strip()
        stderr_text = (result.stderr or "").strip()
        parts = []
        if stdout_text:
            parts.append("--- backend stdout ---\n" + stdout_text[-6000:])
        if stderr_text:
            parts.append("--- backend stderr ---\n" + stderr_text[-6000:])
        error_text = "\n\n".join(parts)
        return False, error_text if error_text else _("Unknown error.")

    return True, (result.stdout or "").strip()


def active_days_from_config(config: dict) -> list[dict]:
    return [
        day
        for day in ALL_DAYS
        if config["days"].get(day["key"], {}).get("enabled", False)
    ]


def semester_display_name(academic_year: int, semester_in_year: int) -> str:
    return _(
        "Year %(year)s - Semester %(semester)s",
        year=academic_year,
        semester=semester_in_year,
    )


def localize_event(event: dict, locale: str, content_translations: dict) -> dict:
    localized = dict(event)

    if locale == "en":
        overlay = content_translations.get(str(event["course_id"]))
        if overlay:
            localized["name"] = overlay.get("name", event["name"])
            localized["lecturer"] = overlay.get("lecturer", event["lecturer"])
            if event.get("prerequisites"):
                localized["prerequisites"] = overlay.get(
                    "prerequisites", event["prerequisites"]
                )
            if event.get("parallel_requirement"):
                localized["parallel_requirement"] = overlay.get(
                    "parallel_requirement", event["parallel_requirement"]
                )

    localized["event_type_label"] = (
        _("Tutorial") if event.get("event_type") == "Tutorial" else _("Lecture")
    )
    localized["day_label"] = day_label_for(event["day"], locale)
    return localized


def enrich_for_calendar(
    timetable: list[dict], config: dict
) -> tuple[list[dict], list[str]]:
    active_days = active_days_from_config(config)
    active_keys = [day["key"] for day in active_days]

    if not active_days:
        return timetable, []

    starts = [
        time_to_minutes(config["days"][day["key"]]["start"])
        for day in active_days
    ]
    ends = [
        time_to_minutes(config["days"][day["key"]]["end"])
        for day in active_days
    ]

    display_start = min(starts)
    display_end = max(ends)

    slot_labels = []
    minute = display_start
    while minute < display_end:
        slot_labels.append(f"{minute // 60:02d}:{minute % 60:02d}")
        minute += 15

    day_columns = {key: index + 2 for index, key in enumerate(active_keys)}

    enriched = []
    for item in timetable:
        copied = dict(item)
        if item["day"] in day_columns:
            start = time_to_minutes(item["start"])
            end = time_to_minutes(item["end"])
            copied["grid_column"] = day_columns[item["day"]]
            copied["grid_row"] = ((start - display_start) // 15) + 2
            copied["grid_span"] = max(1, (end - start) // 15)
        enriched.append(copied)

    return enriched, slot_labels


def _core_constraints(config: dict) -> list[dict]:
    """The built-in rules in short, plain words (Rules page). "hard" = always
    holds; otherwise it is a preference the timetable tries to meet."""
    en = select_locale() == "en"
    rules = [
        ("Prerequisites come first", "Always in an earlier semester. In the file, + means and, / means or.",
         "קורס קדם לפני הקורס שתלוי בו", "תמיד בסמסטר מוקדם יותר. בקובץ, + פירושו \"וגם\" ו־/ פירושו \"או\".", True),
        ("Parallel requirements in the same semester or earlier", "",
         "דרישה מקבילה באותו סמסטר או לפניו", "", True),
        ("Every lecture and tutorial is placed once", "In the semester chosen for its course.",
         "כל הרצאה וכל תרגול משובצים פעם אחת", "בסמסטר שנבחר לקורס.", True),
        ("A lecturer never teaches two classes at once", "",
         "מרצה לא מלמד שני שיעורים בבת אחת", "", True),
        ("Required and general courses never overlap", "With any other course.",
         "קורסי חובה וכלליים לא חופפים", "לשום קורס אחר.", True),
        ("Only inside the teaching hours", "And inside a lecturer's own hours, when they are set.",
         "רק בשעות הלימוד", "ובשעות של המרצה, אם הוגדרו לו שעות משלו.", True),
        ("Each course stays in its study year", "A semester set in the file is kept exactly.",
         "כל קורס בשנת הלימודים שלו", "סמסטר שנקבע בקובץ נשמר בדיוק.", True),
        ("Electives and seminars at separate times", "They may overlap each other, but it is better if they do not. A project overlaps only another project.",
         "בחירה וסמינרים בשעות נפרדות", "מותר שיחפפו זה לזה, אבל עדיף שלא. פרויקט חופף רק לפרויקט.", False),
        ("A balanced load", "No empty or overloaded semesters, and the hours spread across the teaching days.",
         "עומס מאוזן", "בלי סמסטרים ריקים או עמוסים מדי, והשעות מתחלקות בין ימי הלימוד.", False),
        ("A 5–30 minute break between classes", "Back-to-back classes and long gaps are avoided where possible.",
         "הפסקה של 5–30 דקות בין שיעורים", "שיעורים צמודים או הפסקות ארוכות נמנעים ככל האפשר.", False),
    ]
    if config.get("allow_semester_b_start", False):
        rules += [
            ("Seminars are shared by both intakes", "Same semester, day and time for both.",
             "סמינרים משותפים לשני המחזורים", "באותו סמסטר, יום ושעה.", True),
            ("The first intake is not moved for the second", "",
             "המחזור הראשון לא זז בשביל המחזור השני", "", True),
            ("The second intake joins the first wherever possible", "A course is opened again only when the second intake's progress needs it.",
             "המחזור השני לומד יחד עם הראשון ככל האפשר", "קורס נפתח שוב רק כשההתקדמות של המחזור השני מחייבת.", False),
        ]
    return [{"title": r[0] if en else r[2], "description": r[1] if en else r[3], "hard": r[4]} for r in rules]


@app.route("/", methods=["GET"])
def setup_page():
    """Department dashboard: AI requests, constraints and course data."""
    catalog = load_course_catalog()   # first: it may update the automatic degree length
    config = load_config()
    core_constraints = _core_constraints(config)
    return render_template(
        "setup.html",
        all_days=ALL_DAYS,
        config=config,
        courses=catalog,
        ai_constraints=config.get("ai_constraints", []),
        ai_chat=config.get("ai_chat", []),
        ai_pending=config.get("ai_pending"),
        claude_configured=claude_is_configured(),
        core_constraints=core_constraints,
        courses_excel_exists=COURSES_EXCEL_FILE.exists(),
        build_failure=load_json(BUILD_FAILURE_FILE, None),
        data_check=load_json(DATA_CHECK_FILE, []) if COURSES_EXCEL_FILE.exists() else [],
        column_mapping=_column_mapping_context(),
        year_review=_year_review(),
        year_overrides=_year_override_rows(config, catalog),
        file_years=max((int(c.get("year") or 0) for c in catalog), default=None),
        year_modes=YEAR_MODES,
        return_to="",
        built_at=(datetime.fromtimestamp(TIMETABLE_FILE.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
                  if TIMETABLE_FILE.exists() else None),
    )


def _catalog_summary(catalog: list[dict]) -> dict:
    return dict(course_count=len(catalog),
                lecturer_count=len({str(c.get("lecturer") or "").strip() for c in catalog} - {""}),
                file_years=max((int(c.get("year") or 0) for c in catalog), default=None))


@app.route("/data", methods=["GET"])
def data_page():
    """Course file, settings, data check and the course list."""
    catalog = load_course_catalog()   # first: it may update the automatic degree length
    config = load_config()
    from timetable_export import export_filename
    return render_template(
        "data.html",
        config=config, all_days=ALL_DAYS, courses=catalog, year_modes=YEAR_MODES,
        has_file=COURSES_EXCEL_FILE.exists(),
        file_label=config.get("course_file_name") or "courses.xlsx",
        data_check=load_json(DATA_CHECK_FILE, []) if COURSES_EXCEL_FILE.exists() else [],
        year_overrides=_year_override_rows(config, catalog),
        export_name=export_filename(config),
        build_failure=load_json(BUILD_FAILURE_FILE, None),
        column_mapping=_column_mapping_context(),
        year_review=_year_review(),
        return_to="data",
        **_catalog_summary(catalog),
    )


@app.route("/rules", methods=["GET"])
def rules_page():
    """Your own rules, lecturer hours and the built-in rules."""
    config = load_config()
    custom_hours = []
    for lecturer, days in sorted((config.get("lecturer_availability") or {}).items()):
        open_days = [(day_label_for(key, select_locale()), rule.get("start"), rule.get("end"))
                     for key, rule in days.items() if rule.get("enabled")]
        custom_hours.append({"lecturer": lecturer, "days": open_days})
    core = _core_constraints(config)
    return render_template(
        "rules.html",
        config=config,
        ai_constraints=config.get("ai_constraints", []),
        core_constraints=core,
        must_count=sum(1 for c in core if c["hard"]),
        custom_hours=custom_hours,
        build_failure=load_json(BUILD_FAILURE_FILE, None),
        has_file=COURSES_EXCEL_FILE.exists(),
        year_review=_year_review(),
        return_to="rules",
    )


@app.route("/rebuild", methods=["POST"])
def rebuild_timetable():
    """Build again with the saved settings (from the Data and Rules pages)."""
    if not COURSES_EXCEL_FILE.exists():
        flash("Upload a course file first." if select_locale() == "en" else "יש להעלות קובץ קורסים קודם.", "error")
        return redirect(url_for("guided_setup"))
    try:
        return _build_and_redirect()
    except Exception as error:  # noqa: BLE001
        flash(str(error), "error")
        return redirect(_return_url())


SAMPLE_COURSES_FILE = Path(__file__).resolve().parent / "sample" / "sample_courses.xlsx"
# Teaching days for the sample file: evenings plus Friday morning, which
# builds in about a minute.
SAMPLE_DAYS = {
    "Monday": ("17:00", "22:00"), "Thursday": ("17:00", "22:00"), "Friday": ("08:00", "14:00"),
}


def _data_check_counts() -> dict:
    findings = load_json(DATA_CHECK_FILE, []) if COURSES_EXCEL_FILE.exists() else []
    counts = {"blocking": 0, "warning": 0, "info": 0}
    for item in findings:
        severity = item.get("severity")
        if severity in counts:
            counts[severity] += 1
    counts["blocking_items"] = [item for item in findings if item.get("severity") == "blocking"]
    return counts


@app.route("/setup", methods=["GET"])
def guided_setup():
    """The guided first-time flow: course file, settings, check and build."""
    catalog = load_course_catalog()   # first: it may update the automatic degree length
    config = load_config()
    has_file = COURSES_EXCEL_FILE.exists()
    from timetable_export import export_filename
    return render_template(
        "guided_setup.html",
        config=config,
        all_days=ALL_DAYS,
        has_file=has_file,
        file_label=config.get("course_file_name") or "courses.xlsx",
        course_count=len(catalog),
        lecturer_count=len({str(c.get("lecturer") or "").strip() for c in catalog} - {""}),
        file_years=max((int(c.get("year") or 0) for c in catalog), default=None),
        check=_data_check_counts(),
        year_modes=YEAR_MODES,
        export_name=export_filename(config),
        build_failure=load_json(BUILD_FAILURE_FILE, None),
        column_mapping=_column_mapping_context(),
        year_review=_year_review(),
        return_to="setup",
        has_sample=SAMPLE_COURSES_FILE.exists(),
    )


@app.route("/setup/sample", methods=["POST"])
def load_sample_data():
    """Load the bundled sample course file and its teaching days, for a demo."""
    en = select_locale() == "en"
    temp_path = workspace_dir() / "_uploaded_courses.xlsx"
    try:
        shutil.copy2(SAMPLE_COURSES_FILE, temp_path)
        _store_uploaded_catalog(temp_path)
        config = load_config()
        config["program_name"] = "Computer Science (sample)" if en else "מדעי המחשב (דוגמה)"
        config["course_file_name"] = "sample_courses.xlsx"
        for day in ALL_DAYS:
            hours = SAMPLE_DAYS.get(day["key"])
            config["days"][day["key"]] = ({"enabled": True, "start": hours[0], "end": hours[1]} if hours
                                          else {**config["days"][day["key"]], "enabled": False})
        save_config(config)
        flash("Sample data loaded: a computer science course file and its teaching days. Build when ready."
              if en else "נתוני הדוגמה נטענו: קובץ קורסים של מדעי המחשב וימי הלימוד שלו. אפשר לבנות.", "success")
    except Exception as error:  # noqa: BLE001
        temp_path.unlink(missing_ok=True)
        flash(_describe_load_error(error), "error")
    return redirect(url_for("guided_setup"))


def _year_override_rows(config: dict, catalog: list[dict]) -> list[dict]:
    names = {str(c.get("course_id")): c for c in catalog}
    rows = []
    for course_id, year in (config.get("year_overrides") or {}).items():
        course = names.get(str(course_id))
        rows.append({"course_id": str(course_id), "to_year": int(year),
                     "name": course.get("name") if course else None,
                     "from_year": course.get("year") if course else None})
    return rows


@app.route("/days", methods=["GET"])
def day_adjustment_page():
    """Days and hours are edited in the settings on the Data page now."""
    return redirect(url_for("data_page") + "#settings")


@app.route("/days/save", methods=["POST"])
def save_day_adjustments():
    try:
        config = config_from_form()
        save_config(config)
        flash(_("Department days and hours were saved."), "success")
        return redirect(url_for("data_page") + "#settings")
    except Exception as error:
        flash(str(error), "error")
        return redirect(url_for("day_adjustment_page"))


@app.route("/lecturers", methods=["GET"])
def lecturer_availability_page():
    config = load_config()
    lecturers = lecturer_names()
    selected = (request.args.get("lecturer") or "").strip()
    if selected not in lecturers:
        selected = lecturers[0] if lecturers else ""

    overrides = config.get("lecturer_availability", {})
    selected_values = overrides.get(selected, {}) if selected else {}
    effective = {}
    for day in ALL_DAYS:
        key = day["key"]
        dept = config["days"][key]
        rule = selected_values.get(key)
        if rule is None:
            effective[key] = {
                "enabled": bool(dept.get("enabled", False)),
                "start": dept.get("start", "08:00"),
                "end": dept.get("end", "20:00"),
            }
        else:
            effective[key] = {
                "enabled": bool(rule.get("enabled", False)),
                "start": rule.get("start", dept.get("start", "08:00")),
                "end": rule.get("end", dept.get("end", "20:00")),
            }

    return render_template(
        "lecturer_availability.html",
        all_days=ALL_DAYS,
        config=config,
        lecturers=lecturers,
        selected_lecturer=selected,
        lecturer_values=effective,
        saved_overrides=overrides,
    )


@app.route("/lecturers/save", methods=["POST"])
def save_lecturer_availability():
    config = load_config()
    lecturer = (request.form.get("lecturer") or "").strip()
    if lecturer not in lecturer_names():
        flash(_("Choose a lecturer from the current course catalog."), "error")
        return redirect(url_for("lecturer_availability_page"))

    values = {}
    for day in ALL_DAYS:
        key = day["key"]
        values[key] = {
            "enabled": request.form.get(f"lecturer_enabled_{key}") == "on",
            "start": request.form.get(f"lecturer_start_{key}", config["days"][key]["start"]),
            "end": request.form.get(f"lecturer_end_{key}", config["days"][key]["end"]),
        }

    try:
        validate_lecturer_availability(lecturer, values, config["days"])
        config.setdefault("lecturer_availability", {})[lecturer] = values
        save_config(config)
        flash(_("Lecturer availability was saved. Rebuild the timetable to apply it."), "success")
    except Exception as error:
        flash(str(error), "error")
    return redirect(url_for("lecturer_availability_page", lecturer=lecturer))


@app.route("/lecturers/reset", methods=["POST"])
def reset_lecturer_availability():
    lecturer = (request.form.get("lecturer") or "").strip()
    config = load_config()
    config.setdefault("lecturer_availability", {}).pop(lecturer, None)
    save_config(config)
    flash(_("Lecturer availability was reset to the department defaults."), "success")
    return redirect(url_for("lecturer_availability_page", lecturer=lecturer))


@app.route("/debug-json")
def download_debug_json():
    """Download the latest solver snapshot, including partial/failed runs."""
    if not DEBUG_TIMETABLE_FILE.exists():
        flash(_("No solver debug JSON exists yet. Run the timetable solver first."), "error")
        return redirect(url_for("setup_page"))
    return send_file(
        DEBUG_TIMETABLE_FILE,
        as_attachment=True,
        download_name="timetable_debug.json",
        mimetype="application/json",
    )


YEAR_MOVES_FILE = WorkspaceFile("year_moves.json")


def _year_review() -> dict | None:
    """Year moves waiting for the user's approval (suggest mode), or None."""
    review = load_json(YEAR_MOVES_FILE, None)
    if not review or review.get("mode") != "suggest" or not review.get("moves"):
        return None
    return review


def _flash_automatic_year_moves() -> None:
    report = load_json(YEAR_MOVES_FILE, None)
    if not report or report.get("mode") != "auto" or not report.get("moves"):
        return
    en = select_locale() == "en"
    lines = [(f"• {m['name']}: year {m['from_year']} → {m['to_year']} ({m['reason_en']})" if en else
              f"• {m['name']}: משנה {m['from_year']} לשנה {m['to_year']} ({m['reason_he']})")
             for m in report["moves"]]
    head = (f"{len(lines)} courses were moved to a later study year so their prerequisites fit "
            "(automatic setting):" if en else
            f"{len(lines)} קורסים הועברו לשנת לימודים מאוחרת יותר כדי שדרישות הקדם יתאימו (הגדרה אוטומטית):")
    flash(head + "\n" + "\n".join(lines), "warning")


def _build_and_redirect():
    """Build with the saved configuration: in the background with a progress
    page, or (in tests) right away."""
    return_to = request.form.get("return_to") if request.form.get("return_to") in ("setup", "data", "rules") else ""
    if _background_builds():
        return _start_build(return_to)
    success, details = run_backend()
    return redirect(_finish_build(success, details, return_to))


def _finish_build(success: bool, details: str, return_to: str) -> str:
    """Record a finished build's result and return the page to open next."""
    if not success and _year_review() is not None:
        # Suggest mode: the backend stopped before solving and wrote the
        # fewest needed year moves. The dashboard shows them in a panel.
        BUILD_FAILURE_FILE.unlink(missing_ok=True)
        return _return_url(return_to=return_to)
    if not success:
        details_lower = details.lower()
        solver_failure = (
            "no feasible timetable" in details_lower
            or "infeasible" in details_lower
            or "time limit" in details_lower
            or "3-minute" in details_lower
            or "3 minute" in details_lower
            or "semester-b" in details_lower
            or "cohort b" in details_lower
            or "continuation semester" in details_lower
            or "repeated offering" in details_lower
        )
        if solver_failure:
            message = _(
                "We couldn't build a timetable that satisfies all the hard "
                "constraints, or the solver ran out of time. Please check the "
                "number of semesters, prerequisite/parallel courses, and the "
                "active days and hours."
            )
        else:
            message = _(
                "The backend stopped because of a system/runtime error. This is not "
                "being treated as timetable infeasibility. The technical message below "
                "identifies the code path that must be fixed."
            )
        debug_url = url_for("download_debug_json") if DEBUG_TIMETABLE_FILE.exists() else None
        headline = _save_build_failure(message, details, debug_url)
        # Only a short marker goes into the (cookie-based) flash; the full
        # report is read from disk by the dashboard.
        flash(headline, "build-failure")
        return _return_url(return_to=return_to)

    BUILD_FAILURE_FILE.unlink(missing_ok=True)
    flash(_("The timetable was built successfully by the solver."), "success")
    _flash_automatic_year_moves()
    return url_for("calendar_page")


# ---------------------------------------------------------------------------
# Background builds: the solver runs as its own process; the progress page
# asks /build/status every second. Stages are read from the markers main.py
# prints; each solver stage has a fixed time limit, so its share of the bar
# fills by elapsed time against that limit and never goes backwards.
BUILD_STATE_FILE = WorkspaceFile("build_state.json")
BUILD_OUTPUT_FILE = WorkspaceFile("build_output.log")
BUILD_ERRORS_FILE = WorkspaceFile("build_errors.log")
BUILD_SAFETY_SECONDS = 240      # the old synchronous limit was 180 s of solving
_RUNNING_BUILDS: dict[str, subprocess.Popen] = {}

# (key, marker that starts the stage, seconds budget, Hebrew, English)
BUILD_STAGES = [
    ("check", None, 5, "בודקים את הנתונים", "Checking the data"),
    ("plan", "entering Stage 1", 20, "מתכננים איזה קורס ייכנס לכל סמסטר", "Planning which course goes in each semester"),
    ("place", "entering weekly solver", 50, "משבצים ימים ושעות", "Placing days and hours"),
    ("breaks", "STAGE_2_OPTIMIZATION status", 20, "מסדרים הפסקות בין שיעורים", "Arranging breaks between classes"),
    ("intake", "B_INTEGRATED_SOLVER_START", 65, "מתכננים את מחזור ב׳", "Planning the semester-B intake"),
    ("save", "weekly solver FEASIBLE", 5, "שומרים את מערכת השעות", "Saving the timetable"),
]
# Share of the bar each stage ends at, without and with the semester-B intake.
BUILD_STAGE_ENDS = {
    False: {"check": 5, "plan": 12, "place": 72, "breaks": 92, "save": 99},
    True: {"check": 4, "plan": 10, "place": 55, "breaks": 68, "intake": 92, "save": 99},
}


def _background_builds() -> bool:
    return app.config.get("BACKGROUND_BUILDS", not app.config.get("TESTING", False))


def _workspace_key() -> str:
    return str(workspace_dir())


def _start_build(return_to: str):
    running = _RUNNING_BUILDS.get(_workspace_key())
    if running is not None and running.poll() is None:
        return redirect(url_for("building_page"))
    BUILD_OUTPUT_FILE.unlink(missing_ok=True)
    BUILD_ERRORS_FILE.unlink(missing_ok=True)
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    with open(BUILD_OUTPUT_FILE, "w", encoding="utf-8") as out, open(BUILD_ERRORS_FILE, "w", encoding="utf-8") as err:
        process = subprocess.Popen(_backend_command(), cwd=workspace_dir(), stdout=out, stderr=err, env=env)
    _RUNNING_BUILDS[_workspace_key()] = process
    BUILD_STATE_FILE.write_text(json.dumps({
        "state": "running", "started": time.time(), "pid": process.pid, "return_to": return_to,
        "semester_b": bool(load_config().get("allow_semester_b_start")), "marks": {}, "percent": 0,
    }), encoding="utf-8")
    return redirect(url_for("building_page"))


def _build_stages(semester_b: bool) -> list[tuple]:
    return [s for s in BUILD_STAGES if semester_b or s[0] != "intake"]


def _build_progress(state: dict, output: str, now: float) -> tuple[str, int]:
    """The current stage and the bar's percentage (never lower than before)."""
    stages = _build_stages(state.get("semester_b", False))
    marks = state.setdefault("marks", {})
    for key, marker, *_rest in stages:
        if marker and marker in output and key not in marks:
            marks[key] = now
    current = stages[0][0]
    for key, *_rest in stages:
        if key == "check" or key in marks:
            current = key
    ends = BUILD_STAGE_ENDS[state.get("semester_b", False)]
    keys = [s[0] for s in stages]
    index = keys.index(current)
    start_pct = 0 if index == 0 else ends[keys[index - 1]]
    budget = stages[index][2]
    began = marks.get(current, state["started"])
    fraction = min(0.95, max(0.0, (now - began) / budget))
    percent = int(start_pct + (ends[current] - start_pct) * fraction)
    state["percent"] = max(int(state.get("percent", 0)), percent)
    return current, state["percent"]


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _advance_build() -> dict:
    """Check the running build; finish it when its process has ended.
    Returns {"running": bool, "stage", "percent"} or {"url": next page}."""
    state = load_json(BUILD_STATE_FILE, None)
    if not state:
        return {"url": url_for("guided_setup")}
    if state["state"] != "running":
        return {"url": state.get("url") or _return_url(return_to=state.get("return_to"))}
    now = time.time()
    output = BUILD_OUTPUT_FILE.read_text(encoding="utf-8", errors="replace") if BUILD_OUTPUT_FILE.exists() else ""
    process = _RUNNING_BUILDS.get(_workspace_key())
    if process is not None:
        code = process.poll()
    else:   # the app restarted while building: follow the process by its id
        code = None if _pid_alive(state.get("pid")) else (0 if "Created:" in output else 1)
    if code is None and now - state["started"] > BUILD_SAFETY_SECONDS:
        _stop_process(process, state.get("pid"))
        code, output = 1, output + "\n" + _("The solver exceeded the 3-minute time limit.")
    if code is None:
        stage, percent = _build_progress(state, output, now)
        BUILD_STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
        return {"running": True, "stage": stage, "percent": percent}
    _RUNNING_BUILDS.pop(_workspace_key(), None)
    errors = BUILD_ERRORS_FILE.read_text(encoding="utf-8", errors="replace").strip() if BUILD_ERRORS_FILE.exists() else ""
    if code == 0:
        success, details = True, output.strip()
    else:
        parts = []
        if output.strip():
            parts.append("--- backend stdout ---\n" + output.strip()[-6000:])
        if errors:
            parts.append("--- backend stderr ---\n" + errors[-6000:])
        success, details = False, "\n\n".join(parts) or _("Unknown error.")
    url = _finish_build(success, details, state.get("return_to") or "")
    state.update(state="done", url=url, percent=100)
    BUILD_STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    return {"url": url}


def _stop_process(process, pid) -> None:
    try:
        if process is not None:
            process.kill()
            process.wait(timeout=10)
        elif _pid_alive(pid):
            os.kill(pid, 9)
    except Exception:  # noqa: BLE001
        pass


@app.route("/building", methods=["GET"])
def building_page():
    result = _advance_build()
    if "url" in result:
        return redirect(result["url"])
    state = load_json(BUILD_STATE_FILE, {})
    config = load_config()
    catalog = load_json(COURSES_FILE, [])
    return render_template(
        "building.html",
        stages=_build_stages(state.get("semester_b", False)),
        stage=result["stage"], percent=result["percent"],
        program_name=config.get("program_name") or "",
        course_count=len(catalog), years=config.get("degree_years"),
    )


@app.route("/build/status", methods=["GET"])
def build_status():
    return jsonify(_advance_build())


@app.route("/build/cancel", methods=["POST"])
def cancel_build():
    state = load_json(BUILD_STATE_FILE, None)
    if state and state.get("state") == "running":
        _stop_process(_RUNNING_BUILDS.pop(_workspace_key(), None), state.get("pid"))
        state["state"] = "cancelled"
        state["url"] = _return_url(return_to=state.get("return_to"))
        BUILD_STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
        flash("The build was cancelled. Nothing changed; the previous timetable is still there."
              if select_locale() == "en" else
              "הבנייה בוטלה. דבר לא השתנה, ומערכת השעות הקודמת נשארה.", "warning")
        return redirect(state["url"])
    return redirect(_advance_build().get("url", url_for("guided_setup")))


@app.route("/generate", methods=["POST"])
def generate_timetable():
    """Save the user input, run the solver, then show the generated calendar."""
    try:
        config = config_from_form()
        save_config(config)
        return _build_and_redirect()
    except Exception as error:
        flash(str(error), "error")
        return redirect(_return_url())


@app.route("/settings", methods=["POST"])
def save_settings():
    """Save the settings section (years, semesters, semester B, study-year
    rule) without running the solver."""
    try:
        save_config(config_from_form())
        YEAR_MOVES_FILE.unlink(missing_ok=True)
        flash(_("Settings saved. The timetable was not changed; build when ready.")
              if select_locale() == "en" else "ההגדרות נשמרו. מערכת השעות לא שונתה; אפשר לבנות כשמוכנים.", "success")
    except Exception as error:
        flash(str(error), "error")
    return redirect(_return_url("#settings"))


@app.route("/years/approve", methods=["POST"])
def approve_year_moves():
    """Save the suggested moves as approved overrides, then build."""
    review = _year_review()
    if review is None:
        return redirect(_return_url())
    config = load_config()
    overrides = dict(config.get("year_overrides", {}))
    for move in review["moves"]:
        overrides[str(move["course_id"])] = int(move["to_year"])
    config["year_overrides"] = overrides
    save_config(config)
    YEAR_MOVES_FILE.unlink(missing_ok=True)
    try:
        return _build_and_redirect()
    except Exception as error:
        flash(str(error), "error")
        return redirect(_return_url())


@app.route("/years/dismiss", methods=["POST"])
def dismiss_year_moves():
    YEAR_MOVES_FILE.unlink(missing_ok=True)
    flash(("Nothing was changed. The timetable cannot be built while these prerequisite chains do not fit "
           "their study year: fix the years in the Excel, or build again and approve the moves.")
          if select_locale() == "en" else
          "לא שונה דבר. לא ניתן לבנות מערכת כל עוד שרשראות הקדם האלה לא נכנסות בשנת הלימודים שלהן: "
          "אפשר לתקן את השנים בקובץ ה-Excel, או לבנות שוב ולאשר את ההעברות.", "warning")
    return redirect(_return_url("#settings"))


@app.route("/years/override/remove", methods=["POST"])
def remove_year_override():
    config = load_config()
    course_id = request.form.get("course_id", "")
    config.get("year_overrides", {}).pop(course_id, None)
    save_config(config)
    try:
        _refresh_course_catalog_only()
    except Exception as error:  # noqa: BLE001
        print(f"Warning: could not refresh the data check: {error}", file=sys.stderr)
    flash(("The course is back in its Excel year." if select_locale() == "en"
           else "הקורס חזר לשנת הלימודים שבקובץ."), "success")
    return redirect(_return_url("#settings"))


def load_course_catalog() -> list[dict]:
    """Always rebuild the displayed catalog from courses.xlsx.

    courses.json is a generated cache, never the source of truth. This makes
    manual edits to courses.xlsx visible on the next page load and also
    reapplies Elective Courses / Seminars / General Courses classifications.
    """
    if COURSES_EXCEL_FILE.exists():
        try:
            _refresh_course_catalog_only()
        except Exception as error:
            print(f"Warning: could not refresh courses.json from courses.xlsx: {error}", file=sys.stderr)
            # Never leave an older file's data check on screen.
            DATA_CHECK_FILE.write_text(json.dumps([{
                "severity": "blocking", "code": "catalog_unreadable", "course_ids": [],
                "he": _describe_load_error(error, en=False),
                "en": _describe_load_error(error, en=True),
            }], ensure_ascii=False), encoding="utf-8")
    return load_json(COURSES_FILE, [])


def lecturer_names() -> list[str]:
    """Return unique lecturer names currently present in the course catalog."""
    return sorted({
        str(course.get("lecturer", "")).strip()
        for course in load_course_catalog()
        if str(course.get("lecturer", "")).strip()
    })


def validate_lecturer_availability(lecturer: str, values: dict, department_days: dict) -> None:
    if not (lecturer or "").strip():
        raise ValueError(_("Choose a lecturer."))

    for day in ALL_DAYS:
        day_key = day["key"]
        rule = values.get(day_key, {})
        if not rule.get("enabled", False):
            continue
        if not department_days.get(day_key, {}).get("enabled", False):
            raise ValueError(
                _("%(lecturer)s cannot be enabled on %(day)s because the department is closed that day.",
                  lecturer=lecturer, day=day_label_for(day_key, select_locale()))
            )
        start = time_to_minutes(rule.get("start", "08:00"))
        end = time_to_minutes(rule.get("end", "20:00"))
        if start >= end:
            raise ValueError(_("For %(lecturer)s on %(day)s, the start time must be before the end time.",
                               lecturer=lecturer, day=day_label_for(day_key, select_locale())))
        if start % 5 != 0 or end % 5 != 0:
            raise ValueError(_("Hours must be in 5-minute increments."))

        dept = department_days[day_key]
        dept_start = time_to_minutes(dept["start"])
        dept_end = time_to_minutes(dept["end"])
        if start < dept_start or end > dept_end:
            raise ValueError(
                _("%(lecturer)s availability on %(day)s must stay inside department hours (%(start)s–%(end)s).",
                  lecturer=lecturer, day=day_label_for(day_key, select_locale()),
                  start=dept["start"], end=dept["end"])
            )


@app.route("/assistant/apply", methods=["POST"])
def apply_ai_requirement():
    """Chat with Claude only. Do NOT run the solver from a chat message."""
    user_text = (request.form.get("assistant_text") or "").strip()
    if not user_text:
        flash(_("Write a scheduling request first."), "error")
        return redirect(url_for("setup_page") + "#ai-assistant")

    config = load_config()
    catalog = load_course_catalog()
    if not catalog:
        flash(_("Add or upload courses before adding AI requirements."), "error")
        return redirect(url_for("setup_page") + "#ai-assistant")

    try:
        chat = config.setdefault("ai_chat", [])
        result = interpret_with_claude(
            user_text,
            catalog,
            active_rules=list(config.get("ai_constraints", [])),
            semesters_per_year=int(config.get("semesters_per_year", 2)),
            recent_chat=chat,
        )
        chat.append({"role": "user", "text": user_text})

        if result.get("needs_clarification"):
            message = result.get("clarification_question") or result.get("assistant_message")
            chat.append({"role": "assistant", "text": message, "status": "question"})
            config.pop("ai_pending", None)
        else:
            message = result.get("assistant_message") or _("I understood the request. Review it and apply it when you are ready.")
            pending = {
                "rules": result.get("rules", []),
                "data_actions": result.get("data_actions", []),
                "remove_rule_ids": result.get("remove_rule_ids", []),
                "clear_all": bool(result.get("clear_all")),
                "message": message,
            }
            config["ai_pending"] = pending
            chat.append({"role": "assistant", "text": message, "status": "proposal"})

        save_config(config)
        return redirect(url_for("setup_page") + "#ai-assistant")
    except Exception as error:
        config.setdefault("ai_chat", []).append({"role": "user", "text": user_text})
        config["ai_chat"].append({"role": "assistant", "text": str(error), "status": "error"})
        save_config(config)
        flash(str(error), "error")
        return redirect(url_for("setup_page") + "#ai-assistant")


def _refresh_course_catalog_only() -> None:
    """Refresh displayed course data without invoking the timetable solver."""
    from excel_reader import load_courses
    from output import save_courses_to_json, save_courses_to_excel, save_unresolved_requirements
    from scheduler import get_requirement_report
    config = load_config()
    reader_issues: list = []
    courses = load_courses(COURSES_EXCEL_FILE, reader_issues)
    # Automatic degree length: follow the file's highest study year.
    if apply_degree_years(config, courses):
        save_config(config)
    save_courses_to_json(courses, str(COURSES_FILE))
    # The data check judges the years the solver will really use.
    override_records = []
    if year_mode(config) != "strict":
        override_records = apply_year_overrides(courses, config.get("year_overrides"))
    save_courses_to_excel(courses, str(workspace_dir() / "courses_calculated.xlsx"))
    save_unresolved_requirements(get_requirement_report(courses, config), str(UNRESOLVED_FILE))
    _save_data_check(courses, config, reader_issues, override_findings(override_records))


def _save_data_check(courses, config, reader_issues, extra=None) -> None:
    """Data check shown on the dashboard: skipped rows, ignored values and
    blocking problems, computed whenever the course file is (re)read.
    A failure here must never stop the catalog from loading."""
    try:
        from diagnostics import diagnose
        findings = [f.to_dict() for f in diagnose(courses, config, reader_issues)] + list(extra or [])
    except Exception as error:  # noqa: BLE001
        findings = [{"severity": "warning", "code": "data_check_failed", "course_ids": [],
                     "he": f"בדיקת הנתונים לא הצליחה לרוץ: {error}",
                     "en": f"The data check could not run: {error}"}]
    DATA_CHECK_FILE.write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")


def _apply_claude_data_actions(actions: list[dict]) -> list[str]:
    """Apply confirmed Claude data edits. No solver is run here."""
    from openpyxl import load_workbook
    if not actions:
        return []
    if not COURSES_EXCEL_FILE.exists():
        raise ValueError("Upload the course Excel file once before adding courses through Claude.")
    wb = load_workbook(COURSES_EXCEL_FILE)
    if "גיליון2" not in wb.sheetnames:
        raise ValueError('The workbook must contain a sheet named "גיליון2".')
    ws = wb["גיליון2"]
    headers = [cell.value for cell in ws[1]]
    for optional_header in ["קטגוריית קורס", "סמסטר מקורי", "גמישות"]:
        if optional_header not in headers:
            ws.cell(row=1, column=len(headers)+1, value=optional_header)
            headers.append(optional_header)
    wanted = ["מס.שעור", "שם השיעור", "תיאור ד.קדם", "תיאור ד.מקבילה", "שנת לימודים", "שם המרצה", "נ.זיכוי", 'ש"ש', "סוג שיעור"]
    missing = [h for h in wanted if h not in headers]
    if missing:
        raise ValueError("Missing required Excel columns: " + ", ".join(missing))
    existing = load_course_catalog()
    used_ids = {int(c.get("course_id")) for c in existing if c.get("course_id") is not None}
    added = []
    for action in actions:
        if action.get("action") != "add_course":
            raise ValueError(f"Unsupported Claude data action: {action.get('action')}")
        required = ["name", "year", "lecturer", "credits", "weekly_hours", "course_type"]
        absent = [k for k in required if action.get(k) in (None, "")]
        if absent:
            raise ValueError("Claude is missing required course details: " + ", ".join(absent))
        requested_id = action.get("course_id")
        if requested_id in (None, ""):
            course_id = (max(used_ids) + 1) if used_ids else 100001
        else:
            course_id = int(requested_id)
        if course_id in used_ids:
            raise ValueError(f"Course ID {course_id} already exists.")
        used_ids.add(course_id)
        values = {
            "מס.שעור": course_id,
            "שם השיעור": str(action["name"]).strip(),
            "תיאור ד.קדם": action.get("prerequisites") or None,
            "תיאור ד.מקבילה": action.get("parallel_requirement") or None,
            "שנת לימודים": int(action["year"]),
            "שם המרצה": str(action["lecturer"]).strip(),
            "נ.זיכוי": float(action["credits"]),
            'ש"ש': float(action["weekly_hours"]),
            "סוג שיעור": str(action["course_type"]).strip(),
            "קטגוריית קורס": str(action.get("course_category") or "חובה").strip(),
            "סמסטר מקורי": action.get("original_semester"),
            "גמישות": str(action.get("flexibility") or "auto").strip(),
        }
        ws.append([values.get(h) for h in headers])
        added.append(f"{values['שם השיעור']} ({course_id})")
    wb.save(COURSES_EXCEL_FILE)
    _refresh_course_catalog_only()
    return added


@app.route("/assistant/confirm", methods=["POST"])
def confirm_ai_requirement():
    """Apply Claude's latest proposal, then invoke the solver exactly once."""
    config = load_config()
    pending = config.get("ai_pending")
    if not pending:
        flash(_("There is no Claude proposal waiting to be applied."), "error")
        return redirect(url_for("setup_page") + "#ai-assistant")

    data_actions = list(pending.get("data_actions", []))
    if data_actions and not pending.get("rules") and not pending.get("remove_rule_ids") and not pending.get("clear_all"):
        try:
            added = _apply_claude_data_actions(data_actions)
            config.pop("ai_pending", None)
            config.setdefault("ai_chat", []).append({
                "role": "assistant",
                "text": _("The course data was added successfully. The solver was not run. You can keep editing data or build the timetable when ready."),
                "status": "applied",
            })
            save_config(config)
            flash(_("Course data added successfully. The solver was not run."), "success")
        except Exception as error:
            config.setdefault("ai_chat", []).append({"role": "assistant", "text": str(error), "status": "error"})
            save_config(config)
            flash(str(error), "error")
        return redirect(url_for("setup_page") + "#ai-assistant")

    previous_rules = list(config.get("ai_constraints", []))
    rules = [] if pending.get("clear_all") else list(previous_rules)
    remove_ids = {str(v) for v in pending.get("remove_rule_ids", [])}
    if remove_ids:
        rules = [r for r in rules if str(r.get("id")) not in remove_ids]
    rules.extend(pending.get("rules", []))
    config["ai_constraints"] = rules
    save_config(config)

    config.pop("ai_pending", None)
    en = select_locale() == "en"
    config.setdefault("ai_chat", []).append({
        "role": "assistant",
        "text": ("The rule was saved and the timetable is being rebuilt with it."
                 if en else "הכלל נשמר, ומערכת השעות נבנית מחדש לפיו."),
        "status": "applied",
    })
    save_config(config)
    if not COURSES_EXCEL_FILE.exists():
        flash("The rule was saved. Upload a course file to build." if en
              else "הכלל נשמר. כדי לבנות צריך קודם להעלות קובץ קורסים.", "success")
        return redirect(url_for("setup_page") + "#ai-assistant")
    # The button says "apply and rebuild": build now, like the Rebuild button.
    try:
        return _build_and_redirect()
    except Exception as error:  # noqa: BLE001
        flash(str(error), "error")
        return redirect(url_for("setup_page") + "#ai-assistant")


@app.route("/assistant/remove/<rule_id>", methods=["POST"])
def remove_ai_requirement(rule_id: str):
    config = load_config()
    before = list(config.get("ai_constraints", []))
    after = [rule for rule in before if str(rule.get("id")) != rule_id]
    config["ai_constraints"] = after
    if len(after) == len(before):
        flash(_("That AI requirement was already removed."), "error")
        return redirect(_return_url("#ai-assistant"))

    config.setdefault("ai_chat", []).append({
        "role": "assistant",
        "text": ("Removed one rule. The timetable was not changed; rebuild when ready." if select_locale() == "en"
                 else "כלל אחד הוסר. מערכת השעות לא שונתה; אפשר לבנות מחדש כשמוכנים."),
        "status": "applied",
    })
    save_config(config)
    flash("Rule removed. The timetable was not changed; rebuild when ready." if select_locale() == "en"
          else "הכלל הוסר. מערכת השעות לא שונתה; אפשר לבנות מחדש כשמוכנים.", "success")
    return redirect(_return_url("#ai-assistant"))


@app.route("/assistant/clear", methods=["POST"])
def clear_ai_requirements():
    config = load_config()
    config["ai_constraints"] = []
    config.setdefault("ai_chat", []).append({
        "role": "assistant",
        "text": _("Cleared all AI scheduling requirements."),
        "status": "applied",
    })
    save_config(config)
    flash("All your rules were removed. The timetable was not changed; rebuild when ready." if select_locale() == "en"
          else "כל הכללים שלכם הוסרו. מערכת השעות לא שונתה; אפשר לבנות מחדש כשמוכנים.", "success")
    return redirect(_return_url("#ai-assistant"))



def _refresh_after_course_change() -> tuple[bool, str]:
    """Validate and refresh the course catalog, WITHOUT changing the timetable."""
    try:
        _refresh_course_catalog_only()
        return True, "Course catalog refreshed; existing timetable unchanged."
    except Exception as error:
        return False, str(error)



def _flash_data_check_summary() -> None:
    """After an upload: one short line pointing to the dashboard data check."""
    findings = load_json(DATA_CHECK_FILE, [])
    blocking = sum(1 for f in findings if f.get("severity") == "blocking")
    warnings = sum(1 for f in findings if f.get("severity") == "warning")
    if not (blocking or warnings):
        return
    en = select_locale() == "en"
    if blocking:
        text = (f"Data check: {blocking} problem(s) make a timetable impossible and {warnings} warning(s). "
                "See the data check on the Data page before building." if en else
                f"בדיקת נתונים: {blocking} בעיות שמונעות בניית מערכת ו-{warnings} אזהרות. "
                "כדאי לעבור על בדיקת הנתונים בעמוד הנתונים לפני הבנייה.")
    else:
        text = (f"Data check: {warnings} warning(s) about data that is not being used. See the Data page."
                if en else f"בדיקת נתונים: {warnings} אזהרות על נתונים שלא נעשה בהם שימוש. הפירוט בעמוד הנתונים.")
    flash(text, "warning")


def _describe_load_error(error, en: bool | None = None) -> str:
    """Course-file errors in the page's language. A missing-columns error
    becomes a count plus one column per line; other errors pass through."""
    from excel_reader import MissingColumnsError
    if not isinstance(error, MissingColumnsError):
        return str(error)
    isolate = lambda text: f"\u2068{text}\u2069"  # keeps a Latin sheet name from reordering Hebrew text
    lines = "\n".join(f"• {isolate(column)}" for column in error.missing)
    if en is None:
        en = select_locale() == "en"
    if en:
        return (f"The course file was not loaded. Sheet \"{isolate(error.sheet)}\" is missing "
                f"{len(error.missing)} required column(s):\n{lines}\n"
                "Add these columns and upload again. The current course list was not changed.")
    return (f"קובץ הקורסים לא נטען. בגיליון \"{isolate(error.sheet)}\" חסרות "
            f"{len(error.missing)} עמודות חובה:\n{lines}\n"
            "יש להוסיף את העמודות ולהעלות שוב. רשימת הקורסים הנוכחית לא שונתה.")


@app.route("/courses/upload", methods=["POST"])
def upload_courses_excel():
    uploaded = request.files.get("courses_excel")
    if not uploaded or not uploaded.filename:
        flash(_("Choose an Excel file first."), "error")
        return redirect(_return_url("#course-upload"))

    # Check the extension on the ORIGINAL name. secure_filename() strips all
    # non-ASCII characters, so a Hebrew name like "קורסים.xlsx" became "xlsx"
    # and was wrongly rejected. The file is saved to a fixed temp path below,
    # so the user-supplied name is never used on disk.
    extension = Path(uploaded.filename).suffix.lower()
    if extension not in (".xlsx", ".xlsm"):
        flash(_("Please upload an .xlsx or .xlsm Excel file."), "error")
        return redirect(_return_url("#course-upload"))

    temp_path = workspace_dir() / "_uploaded_courses.xlsx"
    try:
        uploaded.save(temp_path)
        PENDING_FILE_NAME.write_text(Path(uploaded.filename).name[:120], encoding="utf-8")
        from column_mapping import propose_mapping
        if not propose_mapping(temp_path).is_identity:
            # Some headers differ from the app's own names (or a required one
            # is missing): propose a mapping and let the user confirm it. This
            # also catches e.g. a lecturer column called "מרצה", which must be
            # used rather than replaced by the per-course default.
            return _start_column_mapping(temp_path)
        _store_uploaded_catalog(temp_path)
        flash(_("Course Excel uploaded. Existing timetable was not changed; build when ready."), "success")
        _flash_data_check_summary()
    except Exception as error:
        if temp_path.exists():
            temp_path.unlink()
        flash(_describe_load_error(error), "error")
    return redirect(_return_url("#course-upload"))


PENDING_FILE_NAME = WorkspaceFile("_pending_file_name.txt")


def _store_uploaded_catalog(temp_path: Path) -> None:
    """Validate an uploaded workbook (already in the app's column format) and
    make it the course file; the previous file is kept as a backup."""
    backup_path = workspace_dir() / "courses.backup.xlsx"
    # Validate against the exact schema expected by the existing backend.
    from excel_reader import SHEET_NAME, find_course_sheet, load_courses
    loaded = load_courses(temp_path)
    if not loaded:
        raise ValueError("The uploaded workbook does not contain any usable courses.")

    # The add/edit routes expect the catalog sheet to be called גיליון2.
    # Uploaded workbooks may use any sheet name (e.g. "Sheet1"), so
    # rename the detected course sheet before storing the file.
    course_sheet = find_course_sheet(temp_path)
    from excel_reader import COLUMN_ORDER
    from openpyxl import load_workbook
    wb = load_workbook(temp_path)
    if course_sheet != SHEET_NAME:
        if SHEET_NAME in wb.sheetnames:
            wb[SHEET_NAME].title = f"{SHEET_NAME}_old"
        wb[course_sheet].title = SHEET_NAME
    # Optional columns the file lacks are added EMPTY (never filled with the
    # defaults), so the add/edit course forms always find every column and
    # the data check keeps saying which values are defaults.
    sheet = wb[SHEET_NAME]
    headers = [str(c.value).strip() if c.value is not None else "" for c in sheet[1]]
    for column in COLUMN_ORDER:
        if column not in headers:
            sheet.cell(row=1, column=len(headers) + 1, value=column)
            headers.append(column)
    wb.save(temp_path)

    if COURSES_EXCEL_FILE.exists():
        shutil.copy2(COURSES_EXCEL_FILE, backup_path)
    shutil.move(str(temp_path), str(COURSES_EXCEL_FILE))

    success, details = _refresh_after_course_change()
    if not success:
        if backup_path.exists():
            shutil.copy2(backup_path, COURSES_EXCEL_FILE)
            _refresh_after_course_change()
        raise ValueError(details)
    if PENDING_FILE_NAME.exists():
        config = load_config()
        config["course_file_name"] = PENDING_FILE_NAME.read_text(encoding="utf-8").strip()
        save_config(config)
        PENDING_FILE_NAME.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Column mapping: a course file's own headers -> the app's field names.
PENDING_UPLOAD_FILE = WorkspaceFile("_pending_upload.xlsx")
PENDING_MAPPING_FILE = WorkspaceFile("_pending_mapping.json")
MAPPING_METHOD_LABELS = {
    "exact": ("same name", "שם זהה"),
    "synonym": ("known name", "שם מוכר"),
    "saved": ("confirmed before", "אושר בעבר"),
    "typo": ("similar name, check spelling", "שם דומה, כדאי לבדוק"),
    "values": ("by the column's values", "לפי תוכן העמודה"),
    "manual": ("chosen by you", "נבחר ידנית"),
}


def _start_column_mapping(temp_path: Path):
    from column_mapping import load_saved_column_names, normalize_header, propose_mapping
    saved_names = load_saved_column_names(workspace_dir() / "column_names.json")
    result = propose_mapping(temp_path, saved_names)
    shutil.move(str(temp_path), str(PENDING_UPLOAD_FILE))

    def confirmed_before(proposal) -> bool:
        if proposal.column is None or proposal.method == "exact":
            return True
        saved = {normalize_header(h) for h in saved_names.get(proposal.canonical, [])}
        return normalize_header(proposal.column) in saved

    # Every column in use is the app's own name or one confirmed in an
    # earlier upload: no question to ask.
    if not result.missing_required and all(confirmed_before(p) for p in result.proposals):
        return _finish_column_mapping(result.sheet, {p.canonical: p.column for p in result.proposals if p.column},
                                      remember=False, automatic=True)
    PENDING_MAPPING_FILE.write_text(json.dumps(result.to_dict(), ensure_ascii=False), encoding="utf-8")
    return redirect(_return_url())


def _finish_column_mapping(sheet: str, mapping: dict, remember: bool, automatic: bool = False):
    from column_mapping import apply_mapping, remember_column_names
    from excel_reader import SHEET_NAME
    temp_path = workspace_dir() / "_uploaded_courses.xlsx"
    try:
        apply_mapping(PENDING_UPLOAD_FILE, sheet, mapping, temp_path, SHEET_NAME)
        _store_uploaded_catalog(temp_path)
    except Exception as error:
        temp_path.unlink(missing_ok=True)
        flash(_describe_load_error(error), "error")
        return redirect(_return_url() if PENDING_MAPPING_FILE.exists()
                        else _return_url("#course-upload"))
    if remember:
        remember_column_names(mapping, workspace_dir() / "column_names.json")
    PENDING_UPLOAD_FILE.unlink(missing_ok=True)
    PENDING_MAPPING_FILE.unlink(missing_ok=True)
    en = select_locale() == "en"
    if automatic:
        flash("Course Excel uploaded. Its column names were matched using names you confirmed before."
              if en else "קובץ הקורסים נטען. שמות העמודות הותאמו לפי שמות שאישרתם בעבר.", "success")
    else:
        flash(_("Course Excel uploaded. Existing timetable was not changed; build when ready."), "success")
    _flash_data_check_summary()
    return redirect(_return_url("#course-upload"))


@app.route("/courses/map", methods=["GET"])
def column_mapping_page():
    """The column check is a panel on the dashboard now; old links land there."""
    return redirect(url_for("setup_page"))


def _column_mapping_context() -> dict | None:
    """Data for the column-check panel while an upload waits for confirmation."""
    pending = load_json(PENDING_MAPPING_FILE, None)
    if not pending or not PENDING_UPLOAD_FILE.exists():
        return None
    from column_mapping import EMPTY_ALLOWED, EMPTY_CHOICE, FIELD_BY_CANONICAL
    en = select_locale() == "en"
    rows = []
    for proposal in pending["proposals"]:
        spec = FIELD_BY_CANONICAL[proposal["canonical"]]
        label = MAPPING_METHOD_LABELS.get(proposal["method"])
        rows.append({
            "canonical": proposal["canonical"],
            "label": spec["en"] if en else spec["he"],
            "required": spec["required"],
            "column": proposal["column"],
            "method": proposal["method"],
            "method_label": (label[0] if en else label[1]) if label else None,
            "samples": [str(v)[:40] for v in proposal["samples"]],
            "empty_allowed": proposal["canonical"] in EMPTY_ALLOWED,
            "default": spec.get("default_en" if en else "default_he"),
        })
    used = {r["column"] for r in rows if r["column"]}
    found_required = sum(1 for r in rows if r["required"] and r["column"])
    return dict(
        rows=rows, columns=pending["columns"], sheet=pending["sheet"],
        other_sheets=pending.get("other_sheets", []),
        unused=[c for c in pending["columns"] if c not in used and not c.startswith("Unnamed")],
        found_required=found_required, total_required=sum(1 for r in rows if r["required"]),
        empty_choice=EMPTY_CHOICE,
    )


@app.route("/courses/map", methods=["POST"])
def confirm_column_mapping():
    pending = load_json(PENDING_MAPPING_FILE, None)
    if request.form.get("action") == "cancel" or not pending or not PENDING_UPLOAD_FILE.exists():
        PENDING_UPLOAD_FILE.unlink(missing_ok=True)
        PENDING_MAPPING_FILE.unlink(missing_ok=True)
        flash(_("Upload cancelled. The current course list was not changed.")
              if select_locale() == "en" else "ההעלאה בוטלה. רשימת הקורסים הנוכחית לא שונתה.", "success")
        return redirect(_return_url("#course-upload"))
    from column_mapping import FIELD_BY_CANONICAL
    en = select_locale() == "en"
    mapping, missing, chosen = {}, [], {}
    for index, proposal in enumerate(pending["proposals"]):
        canonical = proposal["canonical"]
        value = request.form.get(f"field_{index}", "").strip()
        spec = FIELD_BY_CANONICAL[canonical]
        if value:
            mapping[canonical] = value
            chosen.setdefault(value, []).append(spec["en"] if en else spec["he"])
        elif spec["required"]:
            missing.append(spec["en"] if en else spec["he"])
        # keep the user's choices if the page has to be shown again
        if (value or None) != proposal.get("column"):
            proposal["method"] = "manual" if value else None
        proposal["column"] = value or None
    PENDING_MAPPING_FILE.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
    doubled = [f"{column} ({', '.join(fields)})" for column, fields in chosen.items()
               if len(fields) > 1 and column != "__empty__"]
    if missing or doubled:
        parts = []
        if missing:
            parts.append(("These fields are not in the file, so it cannot be loaded yet. Add them to the "
                          "Excel and upload again:\n" if en else
                          "השדות האלה לא נמצאים בקובץ, ולכן אי אפשר לטעון אותו עדיין. יש להוסיף אותם "
                          "לקובץ ה-Excel ולהעלות שוב:\n") + "\n".join(f"• {m}" for m in missing))
        if doubled:
            parts.append(("The same column was chosen for more than one field:\n" if en else
                          "אותה עמודה נבחרה ליותר משדה אחד:\n") + "\n".join(f"• {d}" for d in doubled))
        flash("\n\n".join(parts), "error")
        return redirect(_return_url())
    return _finish_column_mapping(pending["sheet"], mapping, remember=request.form.get("remember") == "on")


@app.route("/courses/add", methods=["POST"])
def add_course_manually():
    try:
        from openpyxl import load_workbook
        if not COURSES_EXCEL_FILE.exists():
            raise ValueError("Upload the course Excel file once before adding courses manually.")

        course_id = int(request.form.get("course_id", "").strip())
        name = (request.form.get("course_name") or "").strip()
        year = int(request.form.get("course_year", "").strip())
        lecturer = (request.form.get("lecturer") or "").strip()
        credits = float(request.form.get("credits", "").strip())
        weekly_hours = float(request.form.get("weekly_hours", "").strip())
        course_type = (request.form.get("course_type") or "").strip()
        course_category = (request.form.get("course_category") or "חובה").strip()
        original_semester_raw = (request.form.get("original_semester") or "").strip()
        original_semester = int(original_semester_raw) if original_semester_raw else None
        flexibility = (request.form.get("flexibility") or "auto").strip()
        prerequisites = (request.form.get("prerequisites") or "").strip() or None
        parallel = (request.form.get("parallel_requirement") or "").strip() or None

        if not name or not lecturer or not course_type or course_category not in {"חובה", "בחירה", "סמינר", "פרויקט", "כללי"}:
            raise ValueError("Course name, lecturer and course type are required.")
        if flexibility not in {"fixed", "flexible", "auto"}:
            raise ValueError("Flexibility must be fixed, flexible or auto.")
        if year < 1:
            raise ValueError("Study year must be at least 1.")
        if credits <= 0 or weekly_hours <= 0:
            raise ValueError("Credits and weekly hours must be greater than 0.")

        existing_ids = {int(c.get("course_id")) for c in load_course_catalog() if c.get("course_id") is not None}
        if course_id in existing_ids:
            raise ValueError(f"Course ID {course_id} already exists.")

        wb = load_workbook(COURSES_EXCEL_FILE)
        if "גיליון2" not in wb.sheetnames:
            raise ValueError('The workbook must contain a sheet named "גיליון2".')
        ws = wb["גיליון2"]
        headers = [cell.value for cell in ws[1]]
        wanted = ["מס.שעור", "שם השיעור", "תיאור ד.קדם", "תיאור ד.מקבילה", "שנת לימודים", "שם המרצה", "נ.זיכוי", 'ש"ש', "סוג שיעור"]
        for optional_header in ["קטגוריית קורס", "סמסטר מקורי", "גמישות"]:
            if optional_header not in headers:
                ws.cell(row=1, column=len(headers)+1, value=optional_header)
                headers.append(optional_header)
        missing = [h for h in wanted if h not in headers]
        if missing:
            raise ValueError("Missing required Excel columns: " + ", ".join(missing))
        values = {
            "מס.שעור": course_id,
            "שם השיעור": name,
            "תיאור ד.קדם": prerequisites,
            "תיאור ד.מקבילה": parallel,
            "שנת לימודים": year,
            "שם המרצה": lecturer,
            "נ.זיכוי": credits,
            'ש"ש': weekly_hours,
            "סוג שיעור": course_type,
            "קטגוריית קורס": course_category,
            "סמסטר מקורי": original_semester,
            "גמישות": flexibility,
        }
        ws.append([values.get(h) for h in headers])
        wb.save(COURSES_EXCEL_FILE)

        success, details = _refresh_after_course_change()
        if not success:
            # Remove the just-added row if it made the source invalid/unsatisfiable.
            wb = load_workbook(COURSES_EXCEL_FILE)
            ws = wb["גיליון2"]
            ws.delete_rows(ws.max_row, 1)
            wb.save(COURSES_EXCEL_FILE)
            _refresh_after_course_change()
            raise ValueError(details)

        flash(_("Course added to catalog. Existing timetable was not changed; build when ready."), "success")
    except Exception as error:
        flash(str(error), "error")
    return redirect(_return_url("#manual-course"))




def _event_overlap(a: dict, b: dict) -> bool:
    if a.get("day") != b.get("day"):
        return False
    return max(time_to_minutes(a["start"]), time_to_minutes(b["start"])) < min(
        time_to_minutes(a["end"]), time_to_minutes(b["end"])
    )


@app.route("/export/timetable", methods=["GET"])
def export_timetable():
    """Download the current timetable as one HTML file (on demand; the solver
    is never run here)."""
    from io import BytesIO
    from timetable_export import export_filename, export_timetable_html
    timetable = load_timetable()
    if not timetable:
        flash("Build a timetable before downloading it." if select_locale() == "en"
              else "יש ליצור מערכת שעות לפני ההורדה.", "error")
        return redirect(url_for("calendar_page"))
    config = load_config()
    html = export_timetable_html(timetable, config, load_json(DEBUG_TIMETABLE_FILE, {}))
    return send_file(BytesIO(html.encode("utf-8")), as_attachment=True,
                     download_name=export_filename(config), mimetype="text/html; charset=utf-8")


@app.route("/calendar", methods=["GET"])
def calendar_page():
    """Second page: display only the timetable created from the selected input."""
    locale = select_locale()
    content_translations = load_content_translations()

    timetable = load_timetable()
    config = load_config()
    active_days = active_days_from_config(config)
    timetable, slot_labels = enrich_for_calendar(timetable, config)
    timetable = [
        localize_event(event, locale, content_translations) for event in timetable
    ]

    semesters_per_year = int(config.get("semesters_per_year", 2))
    degree_years = int(config.get("degree_years", 3))
    base_degree_semesters = degree_years * semesters_per_year
    total_configured_semesters = max([base_degree_semesters] + [int(e.get("semester", 0)) for e in timetable])

    # HARD degree structure:
    # always display exactly the semesters chosen by the user, even if a
    # solver/data error ever produced an empty semester.
    semesters = []

    for semester_number in range(1, total_configured_semesters + 1):
        academic_year = (
            (semester_number - 1) // semesters_per_year
        ) + 1
        semester_in_year = (
            (semester_number - 1) % semesters_per_year
        ) + 1

        display_name = semester_display_name(academic_year, semester_in_year)
        semesters.append((semester_number, display_name))

    unique_courses = len({item["course_id"] for item in timetable})

    # Data for the "all semesters" overview.
    # The degree overview is organized by YEAR.  Inside every year we show
    # one compact weekly timetable for each semester, preserving the same
    # day/time layout as the normal single-semester view.
    semester_groups = []

    for semester_number, semester_name in semesters:
        semester_events = [
            item
            for item in timetable
            if int(item["semester"]) == semester_number
        ]

        academic_year = (
            (semester_number - 1) // semesters_per_year
        ) + 1
        semester_in_year = (
            (semester_number - 1) % semesters_per_year
        ) + 1

        semester_groups.append({
            "number": semester_number,
            "name": semester_name,
            "year": academic_year,
            "semester_in_year": semester_in_year,
            "status": (
                "OVERFLOW"
                if any(
                    event.get("semester_status") == "OVERFLOW"
                    for event in semester_events
                )
                else "ON_PLAN"
            ),
            "events": semester_events,
            "course_count": len({
                event["course_id"] for event in semester_events
            }),
        })

    year_groups = []

    maximum_year = max(
        [degree_years]
        + [group["year"] for group in semester_groups]
    )

    for academic_year in range(1, maximum_year + 1):
        year_semesters = [
            group
            for group in semester_groups
            if group["year"] == academic_year
        ]

        if year_semesters:
            year_groups.append({
                "year": academic_year,
                "semesters": year_semesters,
            })

    return render_template(
        "calendar.html",
        timetable=timetable,
        days=active_days,
        config=config,
        semesters=semesters,
        unique_courses=unique_courses,
        slot_labels=slot_labels,
        semester_groups=semester_groups,
        year_groups=year_groups,
        semesters_per_year=semesters_per_year,
        ai_constraints=config.get("ai_constraints", []),
        ai_chat=config.get("ai_chat", []),
        claude_configured=claude_is_configured(),
    )


def open_browser() -> None:
    webbrowser.open_new("http://127.0.0.1:5000/")


if __name__ == "__main__":
    threading.Timer(1.0, open_browser).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
