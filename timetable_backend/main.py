from __future__ import annotations

# Windows terminals often default to a legacy code page (cp1252/cp1255).
# Keep Hebrew diagnostics from crashing the application with a charmap error.
import sys
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, OSError):
        pass

import argparse
import json
from pathlib import Path

from excel_reader import load_courses
from output import (
    save_courses_to_excel,
    save_courses_to_json,
    save_timetable_to_json,
    save_unresolved_requirements,
)
from scheduler import create_timetable, get_requirement_notes, get_requirement_report, plan_baseline_a


DEFAULT_EXCEL_FILE = "courses.xlsx"
DIAGNOSIS_FILE = "diagnosis.json"
YEAR_MOVES_FILE = "year_moves.json"
YEAR_MOVES_EXIT_CODE = 3


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate calculated course data, a prerequisite/parallel-aware "
            "semester plan, and a weekly timetable."
        )
    )

    parser.add_argument(
        "excel_file",
        nargs="?",
        default=DEFAULT_EXCEL_FILE,
        help="Path to the input Excel file.",
    )

    parser.add_argument(
        "--time-limit",
        type=int,
        default=60,
        help="Solver time limit in seconds.",
    )

    parser.add_argument(
        "--config",
        default=None,
        help="Optional JSON file containing department teaching days/hours.",
    )

    return parser.parse_args()


def load_schedule_config(config_path: str | None) -> dict | None:
    if not config_path:
        return None

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Schedule configuration not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def main() -> None:
    args = parse_arguments()
    excel_path = Path(args.excel_file)
    # A diagnosis from an earlier run must never be shown for this one.
    Path(DIAGNOSIS_FILE).unlink(missing_ok=True)
    availability = load_schedule_config(args.config) or {}

    reader_issues: list = []
    courses = load_courses(excel_path, reader_issues)

    # Early data diagnostics: make bad input visible before CP-SAT runs.
    from collections import Counter
    category_counts = Counter(c.course_category for c in courses)
    print("Course categories:", dict(category_counts))
    review_courses = [c for c in courses if str(c.calculation_status).startswith("REVIEW")]
    if review_courses:
        print("Course-hour rows requiring review:")
        for c in review_courses:
            print(f"  - {c.course_id} {c.name}: raw weekly={c.weekly_hours}, lecture={c.lecture_hours}, tutorial={c.tutorial_hours}, status={c.calculation_status}")

    # A negative value from the approved hours formula is invalid source data,
    # not a timetabling constraint. Never feed an invented/clamped duration to
    # CP-SAT because that can create false feasibility/infeasibility results.
    invalid_hour_courses = [
        c for c in courses if str(c.calculation_status).startswith("REVIEW_REQUIRED")
    ]
    if invalid_hour_courses:
        details = []
        for c in invalid_hour_courses:
            W = round(float(c.weekly_hours) * 2, 2)
            raw_t = round(2 * (W - float(c.credits)), 2)
            raw_l = round((2 * float(c.credits)) - W, 2)
            details.append(
                f"{c.course_id} {c.name}: credits={c.credits:g}, Excel weekly={c.weekly_hours:g}, "
                f"W={W:g}, calculated lecture={raw_l:g}, tutorial={raw_t:g}"
            )
        raise RuntimeError(
            "DATA_ERROR: invalid lecture/tutorial source data. "
            "Approved formula is W=2*ExcelWeekly, T=2*(W-credits), L=2*credits-W, "
            "and requires L>=0 and T>=0. Fix the source row before solving. "
            + " | ".join(details)
        )


    # Study-year setting (year_plan.py). The year stays a hard rule in the
    # solver; approved overrides and, in "auto" mode, the fewest needed moves
    # change course.year BEFORE anything else reads it.
    from year_plan import (apply_degree_years, apply_year_overrides, degree_years_auto,
                           find_year_moves, year_mode)
    Path(YEAR_MOVES_FILE).unlink(missing_ok=True)
    # Degree length: the file's highest study year unless Settings fix it.
    apply_degree_years(availability, courses)
    print(f"DEGREE_YEARS {availability.get('degree_years')} "
          f"({'from the course file' if degree_years_auto(availability) else 'set in Settings'})")
    mode = year_mode(availability)
    print(f"STUDY_YEARS mode={mode}")
    override_records = []
    if mode != "strict":
        override_records = apply_year_overrides(courses, availability.get("year_overrides"))
        for record in override_records:
            if record["applied"]:
                print(f"  approved move: {record['course_id']} {record['name']} "
                      f"year {record['from_year']} -> {record['to_year']}")
            else:
                print(f"  approved move ignored, course not in the file: {record['course_id']}")
        moves = find_year_moves(courses, availability)
        if moves:
            report = {"mode": mode, "moves": [m.to_dict() for m in moves], "overrides": override_records}
            Path(YEAR_MOVES_FILE).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            for m in moves:
                print(f"  needed move: {m.course_id} {m.name} year {m.from_year} -> {m.to_year} ({m.reason_en})")
            if mode == "suggest":
                # Nothing can be solved before the user decides; stop here (cheap).
                print("YEAR_MOVES_SUGGESTED: waiting for approval in the dashboard.")
                raise SystemExit(YEAR_MOVES_EXIT_CODE)
            by_id = {c.course_id: c for c in courses}
            for m in moves:
                course = by_id[m.course_id]
                course.original_year = course.year if course.original_year is None else course.original_year
                course.year = m.to_year
            print(f"YEAR_MOVES_APPLIED {len(moves)} (automatic mode)")

    # Refresh derived catalog files BEFORE any solver stage. This guarantees
    # that edits in courses.xlsx are visible in courses.json even when Stage 1
    # or Stage 2 later reports infeasibility.
    courses_json_path = save_courses_to_json(courses, "courses.json")
    calculated_excel_path = save_courses_to_excel(courses, "courses_calculated.xlsx")
    unresolved = get_requirement_report(courses, availability)
    for note in get_requirement_notes(courses, availability):
        print(f"NOTE {note['course_id']} {note['course']}: OR-alternatives not in catalog ignored: "
              f"{' / '.join(note['ignored_alternatives'])} (kept: {' / '.join(note['kept_alternatives'])})")
    unresolved_path = save_unresolved_requirements(unresolved, "unresolved_requirements.json")

    # Plain-language pre-solve diagnosis (diagnostics.py). Blocking findings
    # are necessary conditions, so they explain an INFEASIBLE result in words.
    # Never let a diagnosis problem break a build.
    blocking_findings = []
    try:
        from diagnostics import diagnose
        findings = diagnose(courses, availability, reader_issues)
        blocking_findings = [f for f in findings if f.severity == "blocking"]
        Path(DIAGNOSIS_FILE).write_text(
            json.dumps([f.to_dict() for f in findings], ensure_ascii=False, indent=2), encoding="utf-8")
        if findings:
            print("DIAGNOSIS (before solving):")
            for finding in findings:
                print(f"  [{finding.severity.upper()}] {finding.en}")
        else:
            print("DIAGNOSIS (before solving): no problems found.")
    except Exception as diagnosis_error:  # noqa: BLE001
        print(f"DIAGNOSIS_WARNING could not run the pre-solve diagnosis: {diagnosis_error}", file=sys.stderr)

    # A duplicate course ID would make the solver mix two courses up WITHOUT
    # failing, so it stops the build instead of only being reported.
    duplicate_ids = [f for f in blocking_findings if f.code == "duplicate_id"]
    if duplicate_ids:
        raise RuntimeError("DATA_ERROR: duplicate course ID. " + " | ".join(f.en for f in duplicate_ids))

    # Stage 1: build cohort A's academic curriculum plan BEFORE the weekly
    # timetable. The Excel fixes only the study year; the planner chooses the
    # semester inside that year.
    curriculum_plan_path = Path("curriculum_plan.json")

    # courses.xlsx is the source of truth. Always rebuild Stage 1 from the
    # current workbook instead of reusing a stale curriculum_plan.json.
    # This is essential when year, prerequisites, hours or categories change.
    enabled_days = [d for d, v in (availability.get("days", {}) or {}).items() if (v or {}).get("enabled")]
    weekly_minutes = 0
    for d in enabled_days:
        v = availability["days"][d]
        try:
            sh, sm = map(int, str(v.get("start", "00:00")).split(":"))
            eh, em = map(int, str(v.get("end", "00:00")).split(":"))
            weekly_minutes += max(0, (eh * 60 + em) - (sh * 60 + sm))
        except Exception:
            pass
    print(f"DIAGNOSTIC active_days={enabled_days} department_hours_per_week={weekly_minutes/60:.1f}")
    print(f"DIAGNOSTIC entering Stage 1 semester planning A_weekly_guard=True joint_cohort_curriculum={bool(availability.get('allow_semester_b_start', False))}")
    try:
        # Plan both cohorts' academic progression before freezing A semesters.
        # Stage 1 keeps an exact A weekly witness; B physical repeats are placed
        # by the integrated solver after A compaction.
        a_planning_availability = dict(availability)
        a_plan = plan_baseline_a(
            courses,
            availability=a_planning_availability,
            time_limit_seconds=min(25, args.time_limit),
        )
    except Exception as exc:
        print(f"DIAGNOSTIC FAILURE_STAGE=STAGE_1 type={type(exc).__name__} message={exc}", file=sys.stderr)
        print("DIAGNOSTIC NOTE: Stage 1 checks both curriculum progression and weekly availability. UNKNOWN is a search timeout, not proof of infeasibility.", file=sys.stderr)
        raise
    print("DIAGNOSTIC Stage 1 FEASIBLE; entering weekly solver")

    availability["a_fixed_semesters"] = {str(k): int(v) for k, v in a_plan.items()}
    availability["a_stage1_placements"] = a_planning_availability.get("a_stage1_placements", [])
    with curriculum_plan_path.open("w", encoding="utf-8") as file:
        json.dump({
            "cohort_A_fixed_semesters": availability["a_fixed_semesters"],
            "degree_years": int(availability.get("degree_years", 3)),
            "semesters_per_year": int(availability.get("semesters_per_year", 3)),
            "allow_semester_b_start": bool(availability.get("allow_semester_b_start", False)),
            "planner_version": 11,
            "source": "Rebuilt from the current courses.xlsx on every run. Course year is the catalog boundary; the planner chooses the semester inside that year.",
        }, file, ensure_ascii=False, indent=2)

    planned_hours = {}
    for c in courses:
        sem = a_plan.get(int(c.course_id))
        if sem is not None:
            planned_hours[sem] = planned_hours.get(sem, 0.0) + float(c.lecture_hours or 0) + float(c.tutorial_hours or 0)
    print(f"DIAGNOSTIC planned_raw_hours_by_semester={dict(sorted(planned_hours.items()))}")
    for _sem in sorted(planned_hours):
        _items = []
        for _c in courses:
            if a_plan.get(int(_c.course_id)) == _sem:
                _items.append(f"{_c.course_id}:{_c.name}[{_c.course_category}] L={float(_c.lecture_hours or 0):g} T={float(_c.tutorial_hours or 0):g}")
        print(f"DIAGNOSTIC A_SEMESTER {_sem}: " + " | ".join(_items))
    try:
        # Search multiple complete weekly arrangements without naming or special-
        # casing any course.  A's academic semesters stay fixed, but the weekly
        # placements can differ.  Choose the feasible solution with the most
        # A+B courses, then the fewest continuation/repeat offerings.
        # A single attempt remains the default to avoid unexpected slowdowns.
        attempts = 1  # One full solve per request: repeated Stage-2/B solves exceeded the 180s web timeout.
        best_timetable = None
        best_score = None
        errors = []
        for attempt in range(attempts):
            attempt_availability = dict(availability)
            attempt_availability["search_seed"] = 1 + attempt * 7919
            try:
                candidate = create_timetable(
                    courses,
                    time_limit_seconds=args.time_limit,
                    availability=attempt_availability,
                )
            except Exception as candidate_error:
                errors.append(str(candidate_error))
                print(f"DIAGNOSTIC SHARING_ATTEMPT={attempt+1}/{attempts} failed={candidate_error}")
                continue
            shared = {str(e.course_id) for e in candidate if e.cohort_label == "A+B"}
            b_only = {str(e.course_id) for e in candidate if e.cohort_label == "B"}
            continuation = {str(e.course_id) for e in candidate if e.cohort_label == "B" and e.semester > int(availability.get("degree_years", 3)) * int(availability.get("semesters_per_year", 3))}
            score = (len(shared), -len(b_only), -len(continuation))
            print(f"DIAGNOSTIC SHARING_ATTEMPT={attempt+1}/{attempts} score={score}")
            if best_score is None or score > best_score:
                best_timetable, best_score = candidate, score
        if best_timetable is None:
            raise RuntimeError("All sharing search attempts failed: " + " | ".join(errors[:3]))
        timetable = best_timetable
        print(f"DIAGNOSTIC SELECTED_SHARING_SCORE={best_score} attempts={attempts}")
    except Exception as exc:
        print(f"DIAGNOSTIC FAILURE_STAGE=STAGE_2_OR_B type={type(exc).__name__} message={exc}", file=sys.stderr)
        print(f"DIAGNOSTIC active_days_at_failure={enabled_days} allow_semester_b_start={bool(availability.get('allow_semester_b_start', False))}", file=sys.stderr)
        debug_path = Path("timetable_debug.json")
        try:
            partial = {}
            if debug_path.exists():
                partial = json.loads(debug_path.read_text(encoding="utf-8"))
            partial.update({
                "status": "FAILED_OR_TIMED_OUT",
                "failure": {"type": type(exc).__name__, "message": str(exc)},
                "configuration": availability,
                "cohort_A_semester_plan": {str(k): int(v) for k, v in a_plan.items()},
                "course_catalog": [c.to_dict() for c in courses],
            })
            debug_path.write_text(json.dumps(partial, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"DIAGNOSTIC DEBUG_JSON={debug_path.resolve()}")
        except Exception as debug_exc:
            print(f"DIAGNOSTIC DEBUG_JSON_WRITE_WARNING stage=failure error={debug_exc}", file=sys.stderr)
        raise
    print("DIAGNOSTIC weekly solver FEASIBLE")

    timetable_json_path = save_timetable_to_json(
        timetable,
        "timetable.json",
    )
    debug_path = Path("timetable_debug.json")
    try:
        debug_payload = {}
        if debug_path.exists():
            debug_payload = json.loads(debug_path.read_text(encoding="utf-8"))
        debug_payload.update({
            "status": "FEASIBLE",
            "configuration": availability,
            "cohort_A_semester_plan": {str(k): int(v) for k, v in a_plan.items()},
            "final_timetable": [item.to_dict() for item in timetable],
            "course_catalog": [c.to_dict() for c in courses],
        })
        debug_path.write_text(json.dumps(debug_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"DIAGNOSTIC DEBUG_JSON={debug_path.resolve()}")
    except Exception as debug_exc:
        print(f"DIAGNOSTIC DEBUG_JSON_WRITE_WARNING stage=success error={debug_exc}", file=sys.stderr)

    # No post-solver Excel exports. courses_calculated.xlsx is intentionally kept.

    overflow_courses = {
        item.course_id
        for item in timetable
        if item.semester_status == "OVERFLOW"
    }

    degree_years = (
        int(availability.get("degree_years", 3))
        if availability
        else 3
    )
    semesters_per_year = (
        int(availability.get("semesters_per_year", 3))
        if availability
        else 3
    )
    expected_semesters = degree_years * semesters_per_year

    if blocking_findings:
        # The checks are necessary conditions: a feasible timetable here means
        # a diagnosis rule is wrong. Make that loud instead of hiding it.
        print("DIAGNOSIS_BUG: the solver found a timetable although the pre-solve diagnosis "
              "reported it impossible. Please report this case.", file=sys.stderr)

    print(f"Loaded {len(courses)} courses.")
    print(f"Degree years: {degree_years}")
    print(f"Semesters per year: {semesters_per_year}")
    print(f"Expected degree semesters: {expected_semesters}")
    print(f"Unresolved prerequisite/parallel references: {len(unresolved)}")
    print(
        f"Courses placed after semester {expected_semesters}: "
        f"{len(overflow_courses)}"
    )
    print()
    print(f"Created: {curriculum_plan_path.resolve()}")
    print(f"Created: {courses_json_path.resolve()}")
    print(f"Created: {calculated_excel_path.resolve()}")
    print(f"Created: {unresolved_path.resolve()}")
    print(f"Created: {timetable_json_path.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Keep the web UI readable: return the useful solver message without
        # dumping a full Python traceback into the dashboard.
        import sys
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
