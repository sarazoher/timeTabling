"""Study-year moves: the fewest changes that let every prerequisite chain fit.

The study year stays a HARD rule in the solver. This module only answers one
question before solving: "if some courses cannot fit their prerequisite chain
inside their own year, which courses must move to a later year, and how few
moves are enough?"

The number of degree years itself comes from the course file (its highest
study year) unless Settings fix it by hand: see `degree_years_auto`.

How the answer is used depends on the setting `year_mode` (schedule config):

    strict   years exactly as in the Excel; nothing is suggested
    suggest  (default) the moves are shown in the dashboard and applied only
             after the user approves them; approved moves are saved in
             `year_overrides` ({course_id: new year}) and listed in the data check
    auto     the moves are applied for this build and reported afterwards

Model (CP-SAT, small: one integer per course)
--------------------------------------------
* each course gets a semester between the first semester of its Excel year and
  the last semester of the degree (courses only move LATER, never earlier);
* prerequisites (AND / OR) are strictly earlier, corequisites no later, exactly
  as in the solver;
* objective: fewest moved courses first, then the fewest years moved in total.

Only prerequisite structure is considered here. Every other hard rule (rooms,
hours, lecturers, overlaps) is still checked by the full solver afterwards.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from ortools.sat.python import cp_model

YEAR_MODES = ("strict", "suggest", "auto")
DEFAULT_YEAR_MODE = "suggest"


@dataclass
class YearMove:
    course_id: int
    name: str
    from_year: int
    to_year: int
    reason_he: str
    reason_en: str

    def to_dict(self) -> dict:
        return asdict(self)


def year_mode(config: dict | None) -> str:
    mode = str((config or {}).get("year_mode", DEFAULT_YEAR_MODE)).strip().lower()
    return mode if mode in YEAR_MODES else DEFAULT_YEAR_MODE


def degree_years_auto(config: dict | None) -> bool:
    """Number of degree years taken from the course file (default) or typed
    in Settings (`degree_years_auto: false`)."""
    return bool((config or {}).get("degree_years_auto", True))


def file_degree_years(courses) -> int | None:
    """The highest study year written in the course file (before any approved
    year move), or None for an empty file."""
    years = [c.original_year if c.original_year is not None else c.year for c in courses]
    return max(years) if years else None


def apply_degree_years(config: dict, courses) -> bool:
    """In automatic mode set config["degree_years"] to the file's highest
    year. Returns True when the value changed."""
    if not degree_years_auto(config):
        return False
    years = file_degree_years(courses)
    if years is None or int(config.get("degree_years", 0) or 0) == years:
        return False
    config["degree_years"] = years
    return True


def apply_year_overrides(courses, overrides: dict | None) -> list[dict]:
    """Set course.year from approved overrides. Returns one record per applied
    override, plus records for overrides whose course is not in the catalog
    (`applied: False`), so nothing is ignored silently."""
    records = []
    by_id = {str(c.course_id): c for c in courses}
    for raw_id, new_year in (overrides or {}).items():
        course = by_id.get(str(raw_id))
        if course is None:
            records.append({"course_id": raw_id, "to_year": int(new_year), "applied": False})
            continue
        original = course.original_year if course.original_year is not None else course.year
        if int(new_year) == original:
            continue
        course.original_year = original
        course.year = int(new_year)
        records.append({"course_id": course.course_id, "name": course.name,
                        "from_year": original, "to_year": int(new_year), "applied": True})
    return records


def override_findings(records: list[dict]) -> list[dict]:
    """Data-check lines for approved moves, so a changed year is never hidden."""
    findings = []
    applied = [r for r in records if r["applied"]]
    if applied:
        findings.append({
            "severity": "info", "code": "year_override",
            "course_ids": [r["course_id"] for r in applied],
            "he": "שנת לימודים שונתה באישורך (הקובץ עצמו לא שונה): " + "; ".join(
                f"\"{r['name']}\" משנה {r['from_year']} לשנה {r['to_year']}" for r in applied),
            "en": "Study year changed with your approval (the file itself is unchanged): " + "; ".join(
                f"\"{r['name']}\" from year {r['from_year']} to year {r['to_year']}" for r in applied),
        })
    stale = [r for r in records if not r["applied"]]
    if stale:
        ids = ", ".join(str(r["course_id"]) for r in stale)
        findings.append({
            "severity": "info", "code": "year_override_stale", "course_ids": [],
            "he": f"שינויי שנה שאושרו בעבר לקורסים שאינם בקובץ הנוכחי לא הופעלו: {ids}",
            "en": f"Year changes approved earlier for courses not in the current file were not used: {ids}",
        })
    return findings


def find_year_moves(courses, availability: dict | None, time_limit_seconds: float = 10.0):
    """Return the list of YearMove needed ([] when everything fits), or None
    when even moving courses later cannot make the chains fit the degree."""
    from scheduler import _add_or_precedence, _requirement_set

    availability = availability or {}
    spy = int(availability.get("semesters_per_year", 3))
    years = int(availability.get("degree_years", 3))
    total = years * spy
    req = _requirement_set(courses, availability)
    n = len(courses)
    if n == 0 or any(c.year > years for c in courses):
        return None

    model = cp_model.CpModel()
    sem, year_of, shift, moved = {}, {}, {}, {}
    for i, c in enumerate(courses):
        first = (c.year - 1) * spy + 1
        sem[i] = model.NewIntVar(first, total, f"s{i}")
        year_of[i] = model.NewIntVar(c.year, years, f"y{i}")
        zero_based = model.NewIntVar(first - 1, total - 1, f"z{i}")
        model.Add(zero_based == sem[i] - 1)
        index = model.NewIntVar(c.year - 1, years - 1, f"yi{i}")
        model.AddDivisionEquality(index, zero_based, spy)
        model.Add(year_of[i] == index + 1)
        shift[i] = model.NewIntVar(0, years - c.year, f"d{i}")
        model.Add(shift[i] == year_of[i] - c.year)
        moved[i] = model.NewBoolVar(f"m{i}")
        model.Add(shift[i] == 0).OnlyEnforceIf(moved[i].Not())
        model.Add(shift[i] >= 1).OnlyEnforceIf(moved[i])

    for i, prereqs in req.prereq_all.items():
        for p in prereqs:
            model.Add(sem[p] < sem[i])
    for i, pars in req.parallel_all.items():
        for p in pars:
            model.Add(sem[p] <= sem[i])
    _add_or_precedence(model, req.prereq_any, lambda i: sem[i], True, "yp_pre")
    _add_or_precedence(model, req.parallel_any, lambda i: sem[i], False, "yp_par")

    model.Minimize(sum(1000 * moved[i] + shift[i] for i in range(n)))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_search_workers = 1
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    new_year = {i: solver.Value(year_of[i]) for i in range(n)}
    moves = []
    for i, c in enumerate(courses):
        if new_year[i] == c.year:
            continue
        he, en = _reason(i, courses, req, new_year, spy)
        moves.append(YearMove(c.course_id, c.name, c.year, new_year[i], he, en))
    return moves


def _reason(i, courses, req, new_year, spy):
    """Why course i moves: a prerequisite that moves too (to the same or a
    later year), otherwise the length of its own chain inside its year."""
    candidates = list(req.prereq_all.get(i, [])) + list(req.parallel_all.get(i, []))
    candidates += [a for group in req.prereq_any.get(i, []) for a in group]
    for p in candidates:
        if new_year[p] != courses[p].year and new_year[p] >= new_year[i]:
            name = courses[p].name
            return (f"כי גם קורס הקדם \"{name}\" עובר לשנה {new_year[p]}",
                    f"because its prerequisite \"{name}\" also moves to year {new_year[p]}")
    length = _chain_length(i, courses, req)
    return (f"שרשרת הקדם שלו בתוך שנה {courses[i].year} צריכה {length} סמסטרים, ובשנה יש {spy}",
            f"its prerequisite chain inside year {courses[i].year} needs {length} semesters, "
            f"and the year has {spy}")


def _chain_length(i, courses, req, seen=None) -> int:
    """Number of semesters the AND-prerequisite chain ending at i needs inside
    i's own year (the course itself included)."""
    seen = seen or set()
    if i in seen:
        return 1
    seen = seen | {i}
    best = 1
    for p in req.prereq_all.get(i, []):
        if courses[p].year == courses[i].year:
            best = max(best, 1 + _chain_length(p, courses, req, seen))
    for group in req.prereq_any.get(i, []):
        same_year = [a for a in group if courses[a].year == courses[i].year]
        if same_year and len(same_year) == len(group):
            best = max(best, 1 + min(_chain_length(a, courses, req, seen) for a in same_year))
    for p in req.parallel_all.get(i, []):
        if courses[p].year == courses[i].year:
            best = max(best, _chain_length(p, courses, req, seen))
    return best
