from __future__ import annotations

import re
import time
import json
from pathlib import Path
from dataclasses import dataclass, replace

from ortools.sat.python import cp_model

from models import Course
from requirements import RequirementSet, canonical_name, resolve_requirements


ALL_DAYS = [
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
]

DAY_NAMES_HE = {
    "Sunday": "ראשון",
    "Monday": "שני",
    "Tuesday": "שלישי",
    "Wednesday": "רביעי",
    "Thursday": "חמישי",
    "Friday": "שישי",
    "Saturday": "שבת",
}

SLOT_MINUTES = 5
ACADEMIC_HOUR_MINUTES = 45  # 1 weekly/academic hour (ש״ש) = 45 clock minutes
B_START_GRID_MINUTES = 15  # Timetabling decision grid; durations/availability still retain 5-minute precision.
def _clock_break_objective(model, groups, prefix):
    """Fixed-day agenda refinement. Rows: (start expression, duration, old start).

    Preserve overlap groups (parallel alternatives), adding breaks BETWEEN
    groups rather than between classes that students take as alternatives.
    Prefer real breaks (including seminars), then early starts and tidy times.
    """
    first_starts, break_costs, odd_starts, all_starts = [], [], [], []
    count = sum(len(rows) for rows in groups.values())
    bound = 24 * 60 // SLOT_MINUTES
    for group_index, rows in enumerate(groups.values()):
        clusters = []
        old_end = -1
        for row in sorted(rows, key=lambda row: row[2]):
            start, duration, old_start = row
            if not clusters or old_start >= old_end:
                clusters.append([])
                old_end = old_start + duration
            else:
                old_end = max(old_end, old_start + duration)
            clusters[-1].append(row)
            remainder = model.NewIntVar(0, 2, f'{prefix}_mod_{group_index}_{len(all_starts)}')
            model.AddModuloEquality(remainder, start, 3)
            odd = model.NewBoolVar(f'{prefix}_odd_{group_index}_{len(all_starts)}')
            model.Add(remainder != 0).OnlyEnforceIf(odd)
            model.Add(remainder == 0).OnlyEnforceIf(odd.Not())
            odd_starts.append(odd)
            all_starts.append(start)
        # Parallel optional meetings (notably seminars) used to be invisible
        # to break scoring because they were collapsed into one cluster.
        # Reward separating them where possible, without forbidding overlap.
        for ci, cluster in enumerate(clusters):
            if len(cluster) > 1:
                ordered = sorted(cluster, key=lambda row: (row[2], row[1]))
                for pi, (earlier, later) in enumerate(zip(ordered, ordered[1:])):
                    # Desired gap: 10 minutes (two 5-minute slots).
                    # Negative gaps (overlaps) remain feasible, but incur a
                    # larger soft penalty than touching or short breaks.
                    deficit = model.NewIntVar(-bound, bound, f'{prefix}_parallel_raw_{group_index}_{ci}_{pi}')
                    model.Add(deficit == earlier[0] + earlier[1] + 2 - later[0])
                    penalty = model.NewIntVar(0, bound, f'{prefix}_parallel_penalty_{group_index}_{ci}_{pi}')
                    model.AddMaxEquality(penalty, [deficit, 0])
                    break_costs.append(penalty)
        previous_end = None
        for ci, cluster in enumerate(clusters):
            left = model.NewIntVar(0, bound, f'{prefix}_left_{group_index}_{ci}')
            right = model.NewIntVar(0, bound, f'{prefix}_right_{group_index}_{ci}')
            model.AddMinEquality(left, [r[0] for r in cluster])
            model.AddMaxEquality(right, [r[0] + r[1] for r in cluster])
            if previous_end is None:
                first_starts.append(left)
                # A break preference must never delay the day's first class.
                model.Add(left <= min(r[2] for r in cluster))
            else:
                gap = model.NewIntVar(0, bound, f'{prefix}_gap_{group_index}_{ci}')
                model.Add(gap == left - previous_end)
                deviation = model.NewIntVar(0, bound, f'{prefix}_gapdev_{group_index}_{ci}')
                # 5-minute slots: 10–15 minutes are ideal, 5 minutes is
                # acceptable, zero is strongly discouraged; >30 costs more.
                model.AddMaxEquality(deviation, [gap - 6, 0])
                touching = model.NewBoolVar(f'{prefix}_touch_{group_index}_{ci}')
                model.Add(gap == 0).OnlyEnforceIf(touching)
                model.Add(gap >= 1).OnlyEnforceIf(touching.Not())
                five_only = model.NewBoolVar(f'{prefix}_five_{group_index}_{ci}')
                model.Add(gap == 1).OnlyEnforceIf(five_only)
                model.Add(gap != 1).OnlyEnforceIf(five_only.Not())
                break_costs.append(deviation + touching * 20 + five_only * 2)
            previous_end = right
    # Provable bounds maintain priorities rather than arbitrary fixed weights.
    start_bound = max(1, count * bound)
    tidy = sum(odd_starts) * (start_bound + 1) + sum(all_starts)
    tidy_bound = count * (start_bound + 1) + start_bound
    comfort = sum(break_costs) * (tidy_bound + 1) + tidy
    comfort_bound = count * (bound + 22) * (tidy_bound + 1) + tidy_bound
    # Break comfort outranks early starts; both remain soft preferences.
    # Keep the first class no later than its original placement (constraint above).
    return sum(break_costs) * ((start_bound + 1) * (tidy_bound + 1)) + sum(first_starts) * (tidy_bound + 1) + tidy


DEFAULT_DEGREE_YEARS = 3
DEFAULT_SEMESTERS_PER_YEAR = 3
OVERFLOW_SEMESTERS = 2

DEFAULT_AVAILABILITY = {
    "days": {
        day: {
            "enabled": day in {"Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"},
            "start": "08:00",
            "end": "20:00",
        }
        for day in ALL_DAYS
    }
}


@dataclass
class CourseEvent:
    course_index: int
    event_type: str
    duration_hours: float


@dataclass
class ScheduledCourse:
    course_id: int
    name: str
    lecturer: str
    year: int
    course_type: str
    course_category: str
    credits: float
    weekly_hours: float
    lecture_hours: float
    tutorial_hours: float
    calculation_status: str
    prerequisites: str | None
    parallel_requirement: str | None
    semester: int
    semester_name: str
    semester_status: str
    event_type: str
    event_duration: float
    day: str
    day_hebrew: str
    start: str
    end: str
    cohort_label: str = "A"
    cohort_start_semester: int = 1
    offering_status: str = "REGULAR"
    source_semester: int | None = None

    def to_dict(self) -> dict:
        return {
            "course_id": self.course_id,
            "name": self.name,
            "lecturer": self.lecturer,
            "year": self.year,
            "course_type": self.course_type,
            "course_category": self.course_category,
            "credits": self.credits,
            "weekly_hours": self.weekly_hours,
            "lecture_hours": self.lecture_hours,
            "tutorial_hours": self.tutorial_hours,
            "calculation_status": self.calculation_status,
            "prerequisites": self.prerequisites,
            "parallel_requirement": self.parallel_requirement,
            "semester": self.semester,
            "semester_name": self.semester_name,
            "semester_status": self.semester_status,
            "event_type": self.event_type,
            "event_duration": self.event_duration,
            "day": self.day,
            "day_hebrew": self.day_hebrew,
            "start": self.start,
            "end": self.end,
            "cohort_label": self.cohort_label,
            "cohort_start_semester": self.cohort_start_semester,
            "offering_status": self.offering_status,
            "source_semester": self.source_semester,
        }


def _course_aliases(availability: dict | None) -> dict[str, str]:
    """No alias tables: requirement cells must use the catalog's own course
    names (the data check lists any name that matches no course)."""
    return {}


def _normalize_course_name(name: str, aliases: dict[str, str] | None = None) -> str:
    return canonical_name(name, aliases)


def _requirement_set(courses: list[Course], availability: dict | None = None) -> RequirementSet:
    """Resolve prerequisite and corequisite cells (AND of OR-groups).

    prerequisite:  (one of) the required course(s) in an EARLIER semester
    corequisite:   (one of) the required course(s) in the same or an earlier semester
    """
    return resolve_requirements(courses, _course_aliases(availability))


def _resolve_requirements(
    courses: list[Course], availability: dict | None = None
) -> tuple[dict[int, list[int]], dict[int, list[int]], list[dict]]:
    """Backward-compatible view: single-course (AND) requirements only."""
    req = _requirement_set(courses, availability)
    return req.prereq_all, req.parallel_all, req.unresolved


def _add_or_precedence(model, any_map, semester_of, strict: bool, prefix: str) -> None:
    """For each OR-group, at least one alternative must be scheduled before
    the dependent course (strict) or no later than it (not strict)."""
    for dependent, groups in any_map.items():
        for group_number, group in enumerate(groups):
            witnesses = []
            for alternative in group:
                witness = model.NewBoolVar(f"{prefix}_or_c{dependent}_g{group_number}_a{alternative}")
                if strict:
                    model.Add(semester_of(alternative) < semester_of(dependent)).OnlyEnforceIf(witness)
                else:
                    model.Add(semester_of(alternative) <= semester_of(dependent)).OnlyEnforceIf(witness)
                witnesses.append(witness)
            model.AddBoolOr(witnesses)


def get_requirement_report(courses: list[Course], availability: dict | None = None) -> list[dict]:
    return _requirement_set(courses, availability).unresolved


def get_requirement_notes(courses: list[Course], availability: dict | None = None) -> list[dict]:
    """OR-alternatives that were ignored because they are not in the catalog."""
    return _requirement_set(courses, availability).notes


def _apply_assistant_semester_rules(model, courses, choose, sem_expr, availability, objective) -> list[dict]:
    """Stage 1 part of the assistant's rules: semester choices and semester
    order between two courses. Day and hour parts stay in Stage 2.

    Semesters are counted across the whole degree (year 2, semester 1 of 3 is
    semester 4), as in Stage 2. A hard rule removes the semesters it forbids;
    a soft one costs weight x 1000 when broken, below the planner's strategic
    costs. Returns the rules that were applied, for error messages."""
    aliases = _course_aliases(availability)
    applied: list[dict] = []

    def scoped(scope: dict | None) -> list[int]:
        scope = scope or {}
        ids = {int(v) for v in scope.get("course_ids", []) if str(v).strip().lstrip("-").isdigit()}
        names = {_normalize_course_name(v, aliases) for v in scope.get("course_names", [])}
        lecturers = {str(v).strip() for v in scope.get("lecturers", [])}
        years = {int(v) for v in scope.get("years", []) if str(v).strip().isdigit()}
        types = set(scope.get("event_types") or [])
        found = []
        for i, course in enumerate(courses):
            if ids and course.course_id not in ids:
                continue
            if names and _normalize_course_name(course.name, aliases) not in names:
                continue
            if lecturers and course.lecturer not in lecturers:
                continue
            if years and course.year not in years:
                continue
            # A rule only for tutorials says nothing about a course without one.
            if types and types <= {"Tutorial"} and not course.tutorial_hours:
                continue
            found.append(i)
        return found

    def weight(rule: dict) -> int:
        try:
            return max(1, min(10000, int(rule.get("weight", 50)))) * 1000
        except (TypeError, ValueError):
            return 50_000

    by_id = {course.course_id: i for i, course in enumerate(courses)}
    for rule in availability.get("ai_constraints", []) or []:
        hard = bool(rule.get("hard", True))
        if rule.get("kind") == "placement_filter":
            allowed = {int(v) for v in (rule.get("allowed_semesters") or [])}
            forbidden = {int(v) for v in (rule.get("forbidden_semesters") or [])}
            if not (allowed or forbidden):
                continue
            used = False
            for i in scoped(rule.get("scope")):
                for (course_index, sem), var in choose.items():
                    if course_index != i or not ((allowed and sem not in allowed) or sem in forbidden):
                        continue
                    used = True
                    if hard:
                        model.Add(var == 0)
                    else:
                        objective.append(var * weight(rule))
            if used:
                applied.append(rule)
        elif rule.get("kind") == "course_relation" and rule.get("relation") in {
                "before_semester", "after_semester", "same_semester", "different_semester"}:
            try:
                left, right = by_id.get(int(rule.get("left_course_id"))), by_id.get(int(rule.get("right_course_id")))
            except (TypeError, ValueError):
                continue
            if left is None or right is None or left == right:
                continue
            a, b = sem_expr[left], sem_expr[right]
            relation = rule["relation"]
            broken = None if hard else model.NewBoolVar(f"a_plan_ai_rel_{left}_{right}_{len(applied)}")
            if relation in {"before_semester", "after_semester", "same_semester"}:
                constraint = model.Add(a < b if relation == "before_semester" else a > b if relation == "after_semester" else a == b)
                if broken is not None:
                    constraint.OnlyEnforceIf(broken.Not())
            else:
                lower = model.NewBoolVar(f"a_plan_ai_ne_{left}_{right}_{len(applied)}")
                keep = [] if broken is None else [broken.Not()]
                model.Add(a < b).OnlyEnforceIf([lower] + keep)
                model.Add(a > b).OnlyEnforceIf([lower.Not()] + keep)
            if broken is not None:
                objective.append(broken * weight(rule))
            applied.append(rule)
    return applied


def plan_baseline_a(
    courses: list[Course],
    availability: dict | None = None,
    time_limit_seconds: int = 20,
) -> dict[int, int]:
    """Stage 1A: strategically place cohort A before weekly scheduling.

    Rules:
      * the catalog fixes only the study year;
      * the solver chooses the semester inside that study year;
      * prerequisites / parallel requirements remain hard;
      * when semester-B intake is enabled, A and B semester progression are
        planned together to reduce repeats before weekly placements are frozen;
      * the planner deliberately keeps
        Year-1 Semester-1 light in *blocking/gateway* courses so that the late
        intake can join cohort A from Semester-2 onward instead of forcing a
        chain of duplicated offerings;
      * flexible, non-blocking courses are preferred in Year-1 Semester-1,
        because cohort A can finish them there while cohort B may complete them
        in the continuation semester;
      * load balance is still considered, but it is secondary to avoiding
        avoidable duplicated gateway courses.

    This stage chooses semesters and checks baseline weekly feasibility.
    Lecture and tutorial are never assigned separate semesters.  A single semester is chosen for the whole course
    offering, and the weekly solver later places all of that offering's events
    inside that same semester.
    """
    availability = availability or {}
    degree_years = int(availability.get("degree_years", DEFAULT_DEGREE_YEARS))
    semesters_per_year = int(availability.get("semesters_per_year", DEFAULT_SEMESTERS_PER_YEAR))
    # B-aware academic planning precedes the fixed-A weekly solve. The A
    # weekly witness remains separate from the later B repeat-placement model.
    allow_b = bool(availability.get("allow_semester_b_start", False))
    requirements = _requirement_set(courses, availability)
    prereq_map, parallel_map, unresolved = (
        requirements.prereq_all, requirements.parallel_all, requirements.unresolved)

    model = cp_model.CpModel()
    choose: dict[tuple[int, int], cp_model.IntVar] = {}
    sem_expr: dict[int, cp_model.LinearExpr] = {}

    for i, course in enumerate(courses):
        first = _minimum_semester_for_year(course.year, semesters_per_year)
        last = course.year * semesters_per_year
        vars_ = []
        for sem in range(first, last + 1):
            v = model.NewBoolVar(f"a_plan_c{i}_s{sem}")
            choose[(i, sem)] = v
            vars_.append(v)
        model.AddExactlyOne(vars_)
        sem_expr[i] = sum(sem * choose[(i, sem)] for sem in range(first, last + 1))


    # Assistant rules that pick or forbid semesters, or order two courses by
    # semester, must shape the semester plan itself: Stage 2 keeps every
    # course in the semester chosen here, so a rule applied only in Stage 2
    # contradicts this plan and makes it infeasible at once.
    objective = []
    assistant_semester_rules = _apply_assistant_semester_rules(
        model, courses, choose, sem_expr, availability, objective)

    # Academic progression for cohort A is hard.
    for i, prereqs in prereq_map.items():
        for pidx in prereqs:
            model.Add(sem_expr[pidx] < sem_expr[i])
    for i, pars in parallel_map.items():
        for pidx in pars:
            model.Add(sem_expr[pidx] <= sem_expr[i])
    # OR-groups ("A / B"): at least one alternative earlier (prerequisite) or
    # no later (corequisite).
    _add_or_precedence(model, requirements.prereq_any, lambda i: sem_expr[i], True, "a_plan_pre")
    _add_or_precedence(model, requirements.parallel_any, lambda i: sem_expr[i], False, "a_plan_par")

    # Project alternatives are a single choice slot: students choose one project,
    # so Stage 1 must already place all project alternatives in the SAME semester.
    # Previously this rule existed only in Stage 2, allowing Stage 1 to put the
    # two projects in different semesters and making Stage 2 immediately infeasible.
    stage1_project_indexes = [
        i for i, course in enumerate(courses) if course.course_category == "פרויקט"
    ]
    if len(stage1_project_indexes) > 1:
        stage1_project_anchor = stage1_project_indexes[0]
        for other_project in stage1_project_indexes[1:]:
            model.Add(sem_expr[other_project] == sem_expr[stage1_project_anchor])

    # Reverse prerequisite graph and transitive descendant counts.  A course
    # that unlocks many later courses is a gateway course.  When B starts in
    # Semester 2, putting such a course in Semester 1 almost guarantees a
    # duplicate B-only offering and often causes a whole cascade of repeats.
    reverse_prereq: dict[int, set[int]] = {i: set() for i in range(len(courses))}
    for dependent, prereqs in requirements.prerequisite_edges().items():
        for prereq in prereqs:
            reverse_prereq[prereq].add(dependent)

    def _descendant_count(root: int) -> int:
        seen: set[int] = set()
        stack = list(reverse_prereq.get(root, set()))
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(reverse_prereq.get(node, set()))
        return len(seen)

    # Courses that some other course depends on (AND members and OR alternatives).
    blocking_courses = requirements.dependency_sources()

    # Strategic first-year policy for a Semester-B intake.
    #
    # We do NOT minimize repeats at any cost.  A small number of deliberate
    # foundation repeats can be globally better than leaving Semester 1 almost
    # empty and forcing cohort A to wait.  The planner therefore:
    #   * strongly protects true high-impact gateways (e.g. Intro to CS) for a
    #     shared semester when possible;
    #   * allows lower-impact prerequisite foundations (e.g. mathematics) to be
    #     taught to A in Semester 1 and repeated for B in Semester 2;
    #   * fills Semester 1 to a useful minimum load rather than treating any
    #     repeat as a failure;
    #   * still prefers flexible/non-blocking material in Semester 1.
    if allow_b and semesters_per_year >= 2:
        first_year_first = 1
        first_year_second = 2
        year1_indexes = [
            i for i, c in enumerate(courses)
            if c.year == 1
        ]

        # Soft minimum number of A offerings in Semester 1.  With three
        # semesters, roughly 40-45% of the average yearly course count gives a
        # useful first semester without overloading it.  For the current
        # curriculum this targets four offerings instead of only two.
        if year1_indexes:
            target_first_count = min(
                len(year1_indexes),
                max(3, round(len(year1_indexes) / semesters_per_year) + 1),
            )
            first_count = model.NewIntVar(0, len(year1_indexes), "a_plan_y1_s1_count")
            model.Add(first_count == sum(choose[(i, first_year_first)] for i in year1_indexes))
            first_shortage = model.NewIntVar(0, target_first_count, "a_plan_y1_s1_shortage")
            model.Add(first_shortage >= target_first_count - first_count)
            model.Add(first_shortage >= 0)
            objective.append(first_shortage * 120_000)

        for i, course in enumerate(courses):
            if course.year != 1:
                continue
            descendants = _descendant_count(i)
            is_blocking = i in blocking_courses or descendants > 0
            is_flexible_nonblocking = (course.flexibility == "flexible" and not is_blocking)
            no_prereqs = (not prereq_map.get(i) and not parallel_map.get(i)
                          and not requirements.prereq_any.get(i) and not requirements.parallel_any.get(i))

            if course.course_category == "כללי" and not is_blocking:
                # Strong policy requested for the late Semester-B intake:
                # use A's first semester for as many non-blocking GENERAL
                # courses as feasibility allows.  B does not lose these
                # courses; it may complete them in the continuation semester.
                # This is deliberately soft (hard prerequisites/availability
                # always win), but much stronger than ordinary balancing.
                for sem in range(2, semesters_per_year + 1):
                    if (i, sem) in choose:
                        objective.append(choose[(i, sem)] * 160_000)
            elif is_flexible_nonblocking:
                # Other flexible/non-blocking material is also good Semester-1
                # material for A; B can complete it later.
                for sem in range(2, semesters_per_year + 1):
                    if (i, sem) in choose:
                        objective.append(choose[(i, sem)] * 20_000)
            elif is_blocking and no_prereqs:
                # Foundation/gateway course.  A small number of foundation
                # repeats is intentional: Semester 1 should not be left almost
                # empty just to avoid every repeat.  What we DO protect is a
                # very broad gateway with many *direct* dependants (for example
                # Intro to CS), because repeating that course tends to create a
                # cascade.  Mathematics foundations with only a few direct
                # dependants are cheap enough to place in Semester 1 and repeat
                # for B in Semester 2.
                direct_dependants = len(reverse_prereq.get(i, set()))
                weekly_slots = max(1, round((course.lecture_hours + course.tutorial_hours) * ACADEMIC_HOUR_MINUTES / SLOT_MINUTES))
                if direct_dependants >= 4:
                    strategic_repeat_cost = 180_000 + (direct_dependants - 4) * 20_000
                else:
                    strategic_repeat_cost = (
                        4_000
                        + direct_dependants * 2_000
                        + descendants * 400
                        + weekly_slots * 20
                    )
                objective.append(choose[(i, first_year_first)] * strategic_repeat_cost)

                # Once not in Semester 1, Semester 2 is the preferred shared
                # point; later placement is only a small delay penalty.
                for sem in range(first_year_second + 1, semesters_per_year + 1):
                    if (i, sem) in choose:
                        objective.append(choose[(i, sem)] * (sem - first_year_second) * 2_000)
            elif is_blocking:
                # Dependent courses should normally stay after their
                # prerequisites.  A mild Semester-1 penalty is enough because
                # hard progression already constrains them.
                objective.append(choose[(i, first_year_first)] * 12_000)
            else:
                # Neutral non-blocking Year-1 courses mildly prefer Semester 1.
                for sem in range(2, semesters_per_year + 1):
                    if (i, sem) in choose:
                        objective.append(choose[(i, sem)] * 1_000)

    # Weekly-capacity guard for Stage 1.
    #
    # CORE (חובה) and GENERAL (כללי) courses may not overlap anything in the
    # weekly timetable. Therefore their combined weekly duration in a semester
    # can never exceed the department's total enabled teaching time. Stage 1
    # previously ignored this, so it could produce (for example) a 17-hour
    # protected Semester 2 while the configured Monday+Thursday+Friday windows
    # contained only 16 hours. Stage 2 then had no possible solution even though
    # Stage 1 itself was feasible.
    #
    # Elective/seminar hours are intentionally NOT included in this capacity
    # bound because elective+elective, elective+seminar and seminar+seminar may
    # overlap. Project hours are also not folded into this simple bound because
    # project+project overlap is allowed/preferred. Their exact compatibility is
    # still handled by Stage 2.
    try:
        _active_days, _day_windows = normalize_availability(availability)
        weekly_capacity_slots = sum(end - start for start, end in _day_windows.values())
    except Exception:
        weekly_capacity_slots = 0

    if weekly_capacity_slots > 0:
        # Stage 1 must also respect the SHAPE of the enabled teaching windows,
        # not only their total weekly sum. Example: with 5h + 5h + 6h days,
        # two separate 6h mandatory blocks cannot both fit even though their
        # combined 12h is below the 16h weekly total.  Model a lightweight
        # per-day bin packing for protected (core/general) lecture/tutorial
        # blocks so Stage 1 cannot hand Stage 2 an obviously unplaceable plan.
        day_caps = {day: end - start for day, (start, end) in _day_windows.items()}
        protected_event_specs = []
        for i, c in enumerate(courses):
            if c.course_category not in {"חובה", "כללי"}:
                continue
            for kind, hours in (("L", c.lecture_hours), ("T", c.tutorial_hours)):
                if hours and float(hours) > 0:
                    dur = max(1, round(float(hours) * ACADEMIC_HOUR_MINUTES / SLOT_MINUTES))
                    protected_event_specs.append((i, kind, dur))

        for year in range(1, degree_years + 1):
            first = _minimum_semester_for_year(year, semesters_per_year)
            last = year * semesters_per_year
            year_events = [(i, kind, dur) for i, kind, dur in protected_event_specs if courses[i].year == year]
            for sem in range(first, last + 1):
                assignments = {}
                for i, kind, dur in year_events:
                    feasible_days = [d for d, cap in day_caps.items() if dur <= cap]
                    if not feasible_days:
                        # If this course is selected into this semester, Stage 2
                        # could never place the block in any enabled day.
                        model.Add(choose[(i, sem)] == 0)
                        continue
                    vars_ = []
                    for d in feasible_days:
                        v = model.NewBoolVar(f"a_pack_c{i}_{kind}_s{sem}_{d}")
                        assignments[(i, kind, d)] = (v, dur)
                        vars_.append(v)
                    model.Add(sum(vars_) == choose[(i, sem)])
                for d, cap in day_caps.items():
                    terms = [v * dur for (i, kind, day), (v, dur) in assignments.items() if day == d]
                    if terms:
                        model.Add(sum(terms) <= cap)

        # Exact Stage-1 weekly-feasibility guard.
        #
        # The protected-hours/bin-packing checks above are necessary but not
        # sufficient: they do not see lecturer collisions or the asymmetric
        # category policy (for example, an elective may overlap seminars but
        # not a core/general course).  That allowed Stage 1 to choose a
        # semester that looked fine by total hours yet had no legal weekly
        # placement in Stage 2.  Build a lightweight placement model here for
        # ALL meetings, using the same hard overlap policy as Stage 2.  There
        # is deliberately no break/compactness objective here; this is only a
        # feasibility guard for the semester plan.
        plan_event_specs = []  # (course_index, kind, duration_slots)
        for i, c in enumerate(courses):
            for kind, hours in (("L", c.lecture_hours), ("T", c.tutorial_hours)):
                if hours and float(hours) > 0:
                    plan_event_specs.append((i, kind, max(1, round(float(hours) * ACADEMIC_HOUR_MINUTES / SLOT_MINUTES))))

        lecturer_availability = availability.get("lecturer_availability", {}) or {}

        def _stage1_effective_window(course, day):
            ds, de = _day_windows[day]
            lecturer_days = lecturer_availability.get(course.lecturer)
            if not lecturer_days:
                return ds, de
            rule = lecturer_days.get(day)
            if rule is None:
                return ds, de
            if not rule.get("enabled", False):
                return None
            try:
                ls = _time_to_slot(rule.get("start", "08:00"))
                le = _time_to_slot(rule.get("end", "20:00"))
            except Exception:
                return None
            start, end = max(ds, ls), min(de, le)
            return (start, end) if end > start else None

        # One optional interval per meeting/semester/day replaces one Boolean
        # per possible five-minute start. Integer starts retain the full grid.
        plan_intervals = {}
        plan_presence = {}
        plan_starts = {}
        for eidx, (i, kind, dur) in enumerate(plan_event_specs):
            c = courses[i]
            first = _minimum_semester_for_year(c.year, semesters_per_year)
            last = c.year * semesters_per_year
            for sem in range(first, last + 1):
                choices = []
                for day in _active_days:
                    window = _stage1_effective_window(c, day)
                    if window is None:
                        continue
                    ds, de = window
                    if de - ds < dur:
                        continue
                    key = (eidx, sem, day)
                    present = model.NewBoolVar(f"a_interval_e{eidx}_s{sem}_{day}")
                    start = model.NewIntVar(ds, de - dur, f"a_start_e{eidx}_s{sem}_{day}")
                    interval = model.NewOptionalFixedSizeIntervalVar(
                        start, dur, present, f"a_meeting_e{eidx}_s{sem}_{day}"
                    )
                    plan_presence[key] = present
                    plan_starts[key] = start
                    plan_intervals[key] = interval
                    choices.append(present)
                model.Add(sum(choices) == choose[(i, sem)])

        # Project alternatives must share their semester, day and start,
        # including when lecturers have different available windows.
        if len(stage1_project_indexes) > 1:
            anchor = stage1_project_indexes[0]
            anchor_events = [e for e, (ci, _, _) in enumerate(plan_event_specs) if ci == anchor]
            for other_project in stage1_project_indexes[1:]:
                other_events = [e for e, (ci, _, _) in enumerate(plan_event_specs) if ci == other_project]
                for anchor_event in anchor_events:
                    kind = plan_event_specs[anchor_event][1]
                    matches = [e for e in other_events if plan_event_specs[e][1] == kind]
                    if not matches and len(anchor_events) == len(other_events) == 1:
                        matches = other_events
                    for other_event in matches:
                        coords = {(sem, day) for e, sem, day in plan_presence
                                  if e in {anchor_event, other_event}}
                        for sem, day in coords:
                            ak, ok = (anchor_event, sem, day), (other_event, sem, day)
                            av, ov = plan_presence.get(ak), plan_presence.get(ok)
                            if av is None:
                                model.Add(ov == 0)
                            elif ov is None:
                                model.Add(av == 0)
                            else:
                                model.Add(av == ov)
                                model.Add(plan_starts[ak] == plan_starts[ok]).OnlyEnforceIf(av)

        def _stage1_conflict(e1, e2):
            i1, _k1, _d1 = plan_event_specs[e1]
            i2, _k2, _d2 = plan_event_specs[e2]
            c1, c2 = courses[i1], courses[i2]
            if i1 == i2:
                return True
            if c1.lecturer == c2.lecturer:
                return True
            cat1, cat2 = c1.course_category, c2.course_category
            if cat1 in {"חובה", "כללי"} or cat2 in {"חובה", "כללי"}:
                return True
            if cat1 == "פרויקט" and cat2 != "פרויקט":
                return True
            if cat2 == "פרויקט" and cat1 != "פרויקט":
                return True
            return False

        # Pairwise NoOverlap preserves the category policy without forbidding
        # permitted elective/seminar or project/project overlaps.
        for year in range(1, degree_years + 1):
            first = _minimum_semester_for_year(year, semesters_per_year)
            last = year * semesters_per_year
            year_events = [e for e, (i, _, _) in enumerate(plan_event_specs) if courses[i].year == year]
            for pos, a in enumerate(year_events):
                for b in year_events[pos + 1:]:
                    if not _stage1_conflict(a, b):
                        continue
                    for sem in range(first, last + 1):
                        for day in _active_days:
                            ak, bk = (a, sem, day), (b, sem, day)
                            if ak in plan_intervals and bk in plan_intervals:
                                model.AddNoOverlap([plan_intervals[ak], plan_intervals[bk]])

        for year in range(1, degree_years + 1):
            first = _minimum_semester_for_year(year, semesters_per_year)
            last = year * semesters_per_year
            protected_indexes = [
                i for i, c in enumerate(courses)
                if c.year == year and c.course_category in {"חובה", "כללי"}
            ]
            for sem in range(first, last + 1):
                if protected_indexes:
                    model.Add(
                        sum(
                            max(1, round((courses[i].lecture_hours + courses[i].tutorial_hours) * ACADEMIC_HOUR_MINUTES / SLOT_MINUTES))
                            * choose[(i, sem)]
                            for i in protected_indexes
                        )
                        <= weekly_capacity_slots
                    )

    # Plan B's progression BEFORE freezing A. The old first-year heuristic
    # could not see downstream sharing losses (e.g. Calculus 2 or projects).
    if allow_b:
        continuation = degree_years * semesters_per_year + 1
        joint_b_sem = {}
        joint_b_choice = {}
        joint_share = {}
        for i, course in enumerate(courses):
            first = _minimum_semester_for_year(course.year, semesters_per_year)
            last = course.year * semesters_per_year
            # Shares can occur in the catalog year as soon as B has completed
            # prerequisites. Physical repeats retain B's shifted year window.
            positions = set(range(max(2, first), min(continuation, last + 1) + 1))
            special = (course.year == 1 and i not in blocking_courses and
                       (course.course_category == "כללי" or course.flexibility == "flexible"))
            positions.add(continuation)
            choices = []
            for sem in sorted(positions):
                v = model.NewBoolVar(f"joint_b_c{i}_s{sem}")
                joint_b_choice[(i, sem)] = v
                choices.append(v)
            model.AddExactlyOne(choices)
            joint_b_sem[i] = sum(sem * joint_b_choice[(i, sem)] for sem in positions)
            share = model.NewBoolVar(f"joint_share_c{i}")
            joint_share[i] = share
            if course.course_category == "סמינר":
                model.Add(share == 1)  # Both intakes attend the same offering.
            model.Add(joint_b_sem[i] == sem_expr[i]).OnlyEnforceIf(share)
            model.Add(joint_b_sem[i] != sem_expr[i]).OnlyEnforceIf(share.Not())
            # At the original-year first semester B may SHARE, but it cannot
            # create an early repeat outside its shifted year window.
            if first >= 2 and (i, first) in joint_b_choice:
                model.Add(joint_b_choice[(i, first)] <= share)
            if special:
                model.Add(joint_b_sem[i] == continuation).OnlyEnforceIf(choose[(i, 1)])
            phase_a = sum(choose[(i, s)] for s in range(first, last + 1) if (s - 1) % semesters_per_year == 0)
            if course.year != degree_years:
                model.Add(joint_b_choice[(i, continuation)] <= phase_a)
            reuse = model.NewBoolVar(f"joint_recurring_reuse_c{i}")
            model.AddMultiplicationEquality(reuse, [joint_b_choice[(i, continuation)], phase_a])
            objective.append((1 - share - reuse) * 1_000_000_000)
            # Prefer early blocking repeats only AFTER minimizing repeats.
            if i in blocking_courses:
                objective.append(joint_b_sem[i] * 1000 * max(1, _descendant_count(i)))
        for i, prereqs in prereq_map.items():
            for j in prereqs:
                model.Add(joint_b_sem[j] < joint_b_sem[i])
        for i, pars in parallel_map.items():
            for j in pars:
                model.Add(joint_b_sem[j] <= joint_b_sem[i])
        _add_or_precedence(model, requirements.prereq_any, lambda i: joint_b_sem[i], True, "joint_b_pre")
        _add_or_precedence(model, requirements.parallel_any, lambda i: joint_b_sem[i], False, "joint_b_par")
        if stage1_project_indexes:
            anchor = stage1_project_indexes[0]
            for i in stage1_project_indexes[1:]:
                model.Add(joint_b_sem[i] == joint_b_sem[anchor])
                model.Add(joint_share[i] == joint_share[anchor])
        # Preserve the useful first-semester target ahead of repeat minimisation
        # rather than emptying A semester 1 to obtain a superficially shared plan.
        if semesters_per_year >= 2 and year1_indexes:
            objective.append(first_shortage * (len(courses) + 1) * 1_000_000_000)
        for sem in range(2, continuation + 1):
            positions_here = [v for (i, s), v in joint_b_choice.items() if s == sem]
            if positions_here:
                empty = model.NewBoolVar(f"joint_b_empty_s{sem}")
                model.Add(sum(positions_here) == 0).OnlyEnforceIf(empty)
                model.Add(sum(positions_here) >= 1).OnlyEnforceIf(empty.Not())
                objective.append(empty * 5_000_000)
        # The exact A witness should already use available teaching days.
        if weekly_capacity_slots > 0:
            for sem in range(1, continuation):
                for day in _active_days:
                    present = [v for (e, s, d), v in plan_presence.items() if s == sem and d == day]
                    if present:
                        used = model.NewBoolVar(f"a_witness_day_s{sem}_{day}")
                        model.AddMaxEquality(used, present)
                        objective.append((1 - used) * 1_000_000)

        # Recurring completion attendance keeps the next cohort's source times.
        # Spread these SOURCE offerings now, before A's placements are frozen.
        if weekly_capacity_slots > 0:
            for day in _active_days:
                recurring_attendance = []
                for (ei, sem, d), present in plan_presence.items():
                    if d != day or (sem - 1) % semesters_per_year != 0:
                        continue
                    i = plan_event_specs[ei][0]
                    attending = model.NewBoolVar(f"joint_cont_e{ei}_s{sem}_{day}")
                    model.AddMinEquality(attending, [present, joint_b_choice[(i, continuation)]])
                    recurring_attendance.append(attending)
                used = model.NewBoolVar(f"joint_cont_day_{day}")
                model.AddMaxEquality(used, recurring_attendance) if recurring_attendance else model.Add(used == 0)
                objective.append((1 - used) * 5_000_000)

        # Protect B's actual day shapes, not just total weekly hours.
        if weekly_capacity_slots > 0:
            for sem in range(2, continuation):
                packs = {}
                for i, kind, dur in protected_event_specs:
                    selected = joint_b_choice.get((i, sem))
                    if selected is None:
                        continue
                    days = [d for d, cap in day_caps.items() if dur <= cap]
                    vars_ = []
                    for day in days:
                        v = model.NewBoolVar(f"joint_b_pack_c{i}_{kind}_s{sem}_{day}")
                        packs[(i, kind, day)] = (v, dur)
                        vars_.append(v)
                    model.Add(sum(vars_) == selected)
                for day, cap in day_caps.items():
                    model.Add(sum(v * dur for (_, _, d), (v, dur) in packs.items() if d == day) <= cap)

    # Balance A within each catalog study year.  This remains important, but is
    # deliberately secondary to the share/minimize-repeat strategy above.
    for year in range(1, degree_years + 1):
        idxs = [i for i, c in enumerate(courses) if c.year == year]
        if not idxs:
            continue
        first = _minimum_semester_for_year(year, semesters_per_year)
        last = year * semesters_per_year
        loads = []
        counts = []
        total_slots = sum(
            max(1, round((courses[i].lecture_hours + courses[i].tutorial_hours) * ACADEMIC_HOUR_MINUTES / SLOT_MINUTES))
            for i in idxs
        )
        for sem in range(first, last + 1):
            load = model.NewIntVar(0, total_slots, f"a_plan_load_y{year}_s{sem}")
            model.Add(load == sum(
                max(1, round((courses[i].lecture_hours + courses[i].tutorial_hours) * ACADEMIC_HOUR_MINUTES / SLOT_MINUTES))
                * choose[(i, sem)]
                for i in idxs
            ))
            loads.append(load)
            count = model.NewIntVar(0, len(idxs), f"a_plan_count_y{year}_s{sem}")
            model.Add(count == sum(choose[(i, sem)] for i in idxs))
            counts.append(count)
            if len(idxs) >= semesters_per_year:
                empty = model.NewBoolVar(f"a_plan_empty_y{year}_s{sem}")
                model.Add(count == 0).OnlyEnforceIf(empty)
                model.Add(count >= 1).OnlyEnforceIf(empty.Not())
                objective.append(empty * 12_000)
        max_load = model.NewIntVar(0, total_slots, f"a_plan_maxload_y{year}")
        min_load = model.NewIntVar(0, total_slots, f"a_plan_minload_y{year}")
        model.AddMaxEquality(max_load, loads)
        model.AddMinEquality(min_load, loads)
        objective.append(max_load * 90)
        spread = model.NewIntVar(0, total_slots, f"a_plan_spread_y{year}")
        model.Add(spread == max_load - min_load)
        objective.append(spread * 55)

    # Small tie-breaker: once sharing has been protected, prerequisite-unlocking
    # courses should not drift later than necessary.
    for i, course in enumerate(courses):
        first = _minimum_semester_for_year(course.year, semesters_per_year)
        last = course.year * semesters_per_year
        descendants = _descendant_count(i)
        for sem in range(first, last + 1):
            if descendants:
                objective.append(choose[(i, sem)] * (sem - first) * descendants * 10)

    model.Minimize(sum(objective))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(5, time_limit_seconds)
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)
    print(f"DIAGNOSTIC STAGE_1 status={solver.StatusName(status)} wall={solver.WallTime():.2f}s variables={len(model.Proto().variables)} constraints={len(model.Proto().constraints)}")
    if status == cp_model.UNKNOWN:
        raise RuntimeError(
            "Stage 1 reached its own time limit before CP-SAT proved feasibility or infeasibility. "
            "This is a Stage-1 search timeout, not a proven curriculum infeasibility."
        )
    if status == cp_model.INFEASIBLE:
        named = "; ".join(str(rule.get("description") or rule.get("id")) for rule in assistant_semester_rules)
        raise RuntimeError(
            "Could not build cohort A's curriculum inside the catalog study years. "
            "Stage 1 is proven infeasible. Check prerequisite/parallel relationships, meeting durations, department/lecturer availability, and shared A+B seminars."
            + (f" Assistant rules that fix semesters are active and may be the cause: {named}." if named else "")
        )
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(f"Stage 1 stopped with unexpected CP-SAT status {solver.StatusName(status)}.")

    result = {}
    for i, course in enumerate(courses):
        first = _minimum_semester_for_year(course.year, semesters_per_year)
        last = course.year * semesters_per_year
        for sem in range(first, last + 1):
            if solver.Value(choose[(i, sem)]):
                result[int(course.course_id)] = sem
                break
    if allow_b:
        availability["a_stage1_b_semesters"] = {str(courses[i].course_id): solver.Value(joint_b_sem[i]) for i in range(len(courses))}
        shared_count = sum(solver.Value(joint_share[i]) for i in range(len(courses)))
        print(f"DIAGNOSTIC JOINT_CURRICULUM shared_target={shared_count}/{len(courses)} A_semesters_planned_with_B=True")

    # Retain the exact weekly witness that made this semester plan feasible.
    # Stage 2 verifies it against its full model before using it as an incumbent.
    availability["a_stage1_placements"] = []
    if weekly_capacity_slots > 0:
        for (eidx, sem, day), present in plan_presence.items():
            if solver.Value(present):
                i, kind, _ = plan_event_specs[eidx]
                availability["a_stage1_placements"].append({
                    "course_id": int(courses[i].course_id),
                    "event_type": "Lecture" if kind == "L" else "Tutorial",
                    "semester": sem, "day": day,
                    "start_slot": solver.Value(plan_starts[(eidx, sem, day)]),
                })
    return result

def _time_to_slot(value: str) -> int:
    """Convert HH:MM to an absolute 5-minute slot from midnight."""
    try:
        hour_text, minute_text = value.split(":")
        hour = int(hour_text)
        minute = int(minute_text)
    except (ValueError, AttributeError) as error:
        raise ValueError(f"Invalid time: {value!r}") from error

    if not (0 <= hour <= 23 and 0 <= minute <= 59 and minute % SLOT_MINUTES == 0):
        raise ValueError(
            f"Time {value!r} must use {SLOT_MINUTES}-minute boundaries, e.g. 08:00 or 08:05."
        )

    return (hour * 60 + minute) // SLOT_MINUTES


def _slot_to_time(slot: int) -> str:
    total_minutes = slot * SLOT_MINUTES
    hour = total_minutes // 60
    minute = total_minutes % 60
    return f"{hour:02d}:{minute:02d}"


def normalize_availability(config: dict | None) -> tuple[list[str], dict[str, tuple[int, int]]]:
    """
    Validate the frontend availability configuration.

    Expected shape:
    {
        "days": {
            "Sunday": {"enabled": true, "start": "08:00", "end": "20:00"},
            ...
        }
    }
    """
    config = config or DEFAULT_AVAILABILITY
    raw_days = config.get("days", {})

    active_days: list[str] = []
    windows: dict[str, tuple[int, int]] = {}

    for day in ALL_DAYS:
        values = raw_days.get(day, {})
        if not values.get("enabled", False):
            continue

        start = _time_to_slot(values.get("start", "08:00"))
        end = _time_to_slot(values.get("end", "20:00"))

        if start >= end:
            raise ValueError(
                f"{day}: start time must be earlier than end time."
            )

        active_days.append(day)
        windows[day] = (start, end)

    if not active_days:
        raise ValueError("At least one teaching day must be enabled.")

    return active_days, windows


def _duration_slots(hours: float, label: str) -> int:
    slots = round(hours * ACADEMIC_HOUR_MINUTES / SLOT_MINUTES)

    if slots <= 0:
        raise ValueError(f"{label} has invalid duration: {hours}")

    represented_hours = slots * SLOT_MINUTES / ACADEMIC_HOUR_MINUTES

    if abs(represented_hours - hours) > 1e-9:
        raise ValueError(
            f"{label} duration={hours} is incompatible with "
            f"{SLOT_MINUTES}-minute slots."
        )

    return slots


def _build_events(courses: list[Course]) -> list[CourseEvent]:
    # Each lecture and each tutorial is represented as ONE CourseEvent.
    # A CourseEvent receives exactly one start time and one duration, so its
    # full duration is necessarily continuous (it can never be split).
    # Lecture and tutorial are separate events: they may be on different days
    # and there is no adjacency/order constraint between them.
    events: list[CourseEvent] = []

    for course_index, course in enumerate(courses):
        if course.lecture_hours > 0:
            events.append(CourseEvent(course_index, "Lecture", course.lecture_hours))

        if course.tutorial_hours > 0:
            events.append(CourseEvent(course_index, "Tutorial", course.tutorial_hours))

        if course.lecture_hours <= 0 and course.tutorial_hours <= 0:
            raise ValueError(f"Course {course.course_id} has no schedulable hours.")

    return events


def _minimum_semester_for_year(
    year: int,
    semesters_per_year: int,
) -> int:
    return (year - 1) * semesters_per_year + 1


def _semester_name(
    semester: int,
    semesters_per_year: int,
    expected_last_semester: int,
) -> str:
    if semester > expected_last_semester:
        return f"סמסטר {semester} - חריגה מהתכנית"

    year = ((semester - 1) // semesters_per_year) + 1
    semester_in_year = ((semester - 1) % semesters_per_year) + 1
    return f"שנה {year} - סמסטר {semester_in_year}"


def create_timetable(
    courses: list[Course],
    time_limit_seconds: int = 60,
    availability: dict | None = None,
) -> list[ScheduledCourse]:
    """
    Build a semester-aware timetable using the department's selected days/hours.

    Hard constraints:
      * prerequisite semester < course semester
      * parallel-condition semester <= course semester
      * every lecture/tutorial is scheduled once
      * same lecturer cannot overlap
      * required/general courses in the same cohort cannot overlap; elective/elective, elective/seminar, and seminar/seminar overlaps are fully allowed
      * every event must fit inside the department window and any lecturer-specific availability
    """
    if not courses:
        raise ValueError("No courses were loaded.")

    active_days, day_windows = normalize_availability(availability)
    day_rank = {day: index for index, day in enumerate(active_days)}

    availability = availability or {}
    lecturer_availability = availability.get("lecturer_availability", {}) or {}

    def lecturer_window(lecturer: str, day: str) -> tuple[int, int] | None:
        """Return this lecturer's effective window for a day, intersected with
        the department window. No explicit lecturer override means the
        department-wide window applies."""
        department_window = day_windows.get(day)
        if department_window is None:
            return None
        lecturer_days = lecturer_availability.get(lecturer)
        if not lecturer_days:
            return department_window
        rule = lecturer_days.get(day)
        if rule is None:
            return department_window
        if not rule.get("enabled", False):
            return None
        start = max(department_window[0], _time_to_slot(rule.get("start", "08:00")))
        end = min(department_window[1], _time_to_slot(rule.get("end", "20:00")))
        if start >= end:
            return None
        return start, end
    degree_years = int(
        availability.get("degree_years", DEFAULT_DEGREE_YEARS)
    )
    semesters_per_year = int(
        availability.get(
            "semesters_per_year",
            DEFAULT_SEMESTERS_PER_YEAR,
        )
    )

    if degree_years < 1:
        raise ValueError("degree_years must be at least 1.")

    if semesters_per_year < 1:
        raise ValueError("semesters_per_year must be at least 1.")

    max_excel_year = max(course.year for course in courses)

    if max_excel_year > degree_years:
        raise ValueError(
            f"The Excel contains a course from study year {max_excel_year}, "
            f"but the selected degree has only {degree_years} years."
        )

    expected_last_semester = degree_years * semesters_per_year

    # v10.4 performance: when Stage 1 has already fixed every cohort-A course
    # to a semester, the weekly solver must not recreate placement variables
    # for every other semester.  That used to multiply the search space by
    # roughly the number of semesters and was the main reason the stricter
    # overlap policy could hit the 3-minute frontend timeout.
    fixed_a_semesters_raw = availability.get("a_fixed_semesters", {}) or {}
    fixed_a_semesters = {str(k): int(v) for k, v in fixed_a_semesters_raw.items()}
    all_a_courses_fixed = all(str(course.course_id) in fixed_a_semesters for course in courses)

    # Overflow semesters are only needed when the weekly solver still has to
    # choose curriculum semesters itself.  With a complete Stage-1 plan, A is
    # confined to the normal degree horizon; B continuation is handled later.
    max_semester = (
        expected_last_semester
        if all_a_courses_fixed
        else expected_last_semester + OVERFLOW_SEMESTERS
    )

    events = _build_events(courses)
    run_aliases = _course_aliases(availability)
    requirements = _requirement_set(courses, availability)
    prerequisite_map, parallel_map, unresolved = (
        requirements.prereq_all, requirements.parallel_all, requirements.unresolved)

    # Semester-B must not make the already-feasible cohort-A timetable
    # infeasible by blocking an arbitrary reserved day/time window.
    # The previous "strategic repeat reservation" did exactly that: merely
    # enabling cohort B changed hard constraints in the main A solve.
    # B-only repeats are handled by the dedicated B placement stage below.
    b_repeat_reservation = None

    if unresolved:
        print(
            f"WARNING: {len(unresolved)} prerequisite/parallel references "
            "could not be matched. See unresolved_requirements.json."
        )

    model = cp_model.CpModel()

    # 1. Course -> semester variables.
    semester_choice: dict[tuple[int, int], cp_model.IntVar] = {}

    for course_index, course in enumerate(courses):
        min_semester = _minimum_semester_for_year(course.year, semesters_per_year)
        allowed = []

        for semester in range(min_semester, max_semester + 1):
            variable = model.NewBoolVar(f"semester_c{course_index}_s{semester}")
            semester_choice[(course_index, semester)] = variable
            allowed.append(variable)

        model.AddExactlyOne(allowed)

    # Cohort A is the baseline curriculum.  When a previously approved/visible
    # semester plan is supplied, keep A in exactly those semesters.  Cohort B
    # is the flexible intake; A must not move merely to make B easier.
    for course_index, course in enumerate(courses):
        fixed_semester = fixed_a_semesters.get(str(course.course_id))
        if fixed_semester is None:
            continue
        if not (1 <= fixed_semester <= expected_last_semester):
            continue
        key = (course_index, fixed_semester)
        if key in semester_choice:
            model.Add(semester_choice[key] == 1)

    def semester_expression(course_index: int):
        course = courses[course_index]
        min_semester = _minimum_semester_for_year(course.year, semesters_per_year)
        return sum(
            semester * semester_choice[(course_index, semester)]
            for semester in range(min_semester, max_semester + 1)
        )

    # 2. Prerequisites AND parallel conditions from the Excel.
    for course_index, prerequisite_indexes in prerequisite_map.items():
        for prerequisite_index in prerequisite_indexes:
            model.Add(
                semester_expression(prerequisite_index)
                < semester_expression(course_index)
            )

    for course_index, parallel_indexes in parallel_map.items():
        for parallel_index in parallel_indexes:
            model.Add(
                semester_expression(parallel_index)
                <= semester_expression(course_index)
            )

    _add_or_precedence(model, requirements.prereq_any, semester_expression, True, "main_pre")
    _add_or_precedence(model, requirements.parallel_any, semester_expression, False, "main_par")

    # 3. Event placement variables.
    durations = [
        _duration_slots(
            event.duration_hours,
            f"Course {courses[event.course_index].course_id} {event.event_type}",
        )
        for event in events
    ]

    # x[(event, semester, day, absolute_start_slot)] = 1
    x: dict[tuple[int, int, str, int], cp_model.IntVar] = {}

    for event_index, event in enumerate(events):
        course = courses[event.course_index]
        duration = durations[event_index]
        min_semester = _minimum_semester_for_year(course.year, semesters_per_year)

        event_has_any_placement = False

        fixed_semester_for_course = fixed_a_semesters.get(str(course.course_id))
        if fixed_semester_for_course is not None:
            candidate_semesters = [fixed_semester_for_course]
            # Explicitly force all non-selected semester-choice vars to zero
            # without creating any day/time placement variables for them.
            for other_semester in range(min_semester, max_semester + 1):
                if other_semester != fixed_semester_for_course:
                    model.Add(semester_choice[(event.course_index, other_semester)] == 0)
        else:
            candidate_semesters = range(min_semester, max_semester + 1)

        for semester in candidate_semesters:
            placements_in_semester = []

            for day in active_days:
                effective_window = lecturer_window(course.lecturer, day)
                if effective_window is None:
                    continue
                day_start, day_end = effective_window
                latest_start = day_end - duration

                if latest_start < day_start:
                    continue

                event_has_any_placement = True

                for start_slot in range(day_start, latest_start + 1):
                    variable = model.NewBoolVar(
                        f"x_e{event_index}_sem{semester}_{day}_t{start_slot}"
                    )
                    x[(event_index, semester, day, start_slot)] = variable
                    placements_in_semester.append(variable)

                    # Keep the compact Semester-2 block free for strategic
                    # B-only foundation repeats.  Without this, the regular
                    # timetable can scatter A+B meetings across every day and
                    # leave enough total hours but no usable contiguous slots.
                    if b_repeat_reservation is not None:
                        if (
                            semester == b_repeat_reservation["semester"]
                            and day == b_repeat_reservation["day"]
                            and start_slot < b_repeat_reservation["end"]
                            and start_slot + duration > b_repeat_reservation["start"]
                        ):
                            model.Add(variable == 0)

            if not placements_in_semester:
                # If a course chooses this semester, it would be impossible to place.
                model.Add(semester_choice[(event.course_index, semester)] == 0)
            else:
                model.Add(
                    sum(placements_in_semester)
                    == semester_choice[(event.course_index, semester)]
                )

        if not event_has_any_placement:
            raise ValueError(
                f"{course.name} {event.event_type} ({event.duration_hours} hours) "
                "does not fit inside any enabled teaching-day window."
            )

    # 3a. PROJECT CHOICE SLOT (hard rule).
    # Students choose ONE project rather than taking every project course.
    # Therefore all project alternatives are offered in the exact same
    # semester/day/start time. This intentionally creates a project-vs-project
    # overlap and prevents projects from consuming separate student timetable
    # slots. Lecturer conflicts are still enforced normally.
    project_course_indexes = [
        i for i, course in enumerate(courses) if course.course_category == "פרויקט"
    ]
    if len(project_course_indexes) > 1:
        anchor_project = project_course_indexes[0]
        for other_project in project_course_indexes[1:]:
            model.Add(semester_expression(other_project) == semester_expression(anchor_project))

            anchor_events = [
                e for e, event in enumerate(events) if event.course_index == anchor_project
            ]
            other_events = [
                e for e, event in enumerate(events) if event.course_index == other_project
            ]
            # Projects in the current catalog each have one continuous meeting.
            # Match corresponding meetings by event type when possible.
            for anchor_event in anchor_events:
                event_type = events[anchor_event].event_type
                matches = [e for e in other_events if events[e].event_type == event_type]
                if not matches and len(anchor_events) == len(other_events) == 1:
                    matches = other_events
                for other_event in matches:
                    coords = {
                        (sem, day, st)
                        for (e, sem, day, st) in x
                        if e in {anchor_event, other_event}
                    }
                    for sem, day, st in coords:
                        av = x.get((anchor_event, sem, day, st))
                        ov = x.get((other_event, sem, day, st))
                        if av is None:
                            model.Add(ov == 0)
                        elif ov is None:
                            model.Add(av == 0)
                        else:
                            model.Add(av == ov)

    # 3b. Natural-language rules interpreted by Claude.
    #
    # The UI is intentionally free text. Claude translates the text into a
    # small, expressive internal rule language; this compiler turns those
    # rules into CP-SAT constraints/penalties. Claude never edits solver code.
    ai_rules = list(availability.get("ai_constraints", []) or [])
    ai_objective_terms: list = []

    def _scope_course_indexes(scope: dict | None) -> set[int]:
        scope = scope or {}
        course_ids = {int(v) for v in scope.get("course_ids", []) if str(v).strip()}
        course_names = {_normalize_course_name(v, run_aliases) for v in scope.get("course_names", [])}
        lecturers = {str(v).strip() for v in scope.get("lecturers", [])}
        years = {int(v) for v in scope.get("years", []) if str(v).strip()}

        # An empty scope means "all courses". Otherwise every populated field
        # acts as an AND filter while values within the same field are ORed.
        result: set[int] = set()
        for i, course in enumerate(courses):
            if course_ids and course.course_id not in course_ids:
                continue
            if course_names and _normalize_course_name(course.name, run_aliases) not in course_names:
                continue
            if lecturers and course.lecturer not in lecturers:
                continue
            if years and course.year not in years:
                continue
            result.add(i)
        return result

    def _rule_weight(rule: dict) -> int:
        try:
            return max(1, min(10000, int(rule.get("weight", 50))))
        except (TypeError, ValueError):
            return 50

    def _placement_violates(rule: dict, event_index: int, semester: int, day: str, start_slot: int) -> bool:
        event = events[event_index]
        duration = durations[event_index]
        allowed_days = set(rule.get("allowed_days") or [])
        forbidden_days = set(rule.get("forbidden_days") or [])
        allowed_semesters = {int(v) for v in (rule.get("allowed_semesters") or [])}
        forbidden_semesters = {int(v) for v in (rule.get("forbidden_semesters") or [])}
        event_types = set((rule.get("scope") or {}).get("event_types") or [])

        if event_types and event.event_type not in event_types:
            return False
        if allowed_days and day not in allowed_days:
            return True
        if day in forbidden_days:
            return True
        if allowed_semesters and semester not in allowed_semesters:
            return True
        if semester in forbidden_semesters:
            return True

        earliest = rule.get("earliest_start")
        if earliest and start_slot < _time_to_slot(earliest):
            return True
        latest = rule.get("latest_end")
        if latest and start_slot + duration > _time_to_slot(latest):
            return True
        return False

    def _course_day_presence(course_index: int, semester: int, day: str) -> cp_model.IntVar:
        variables = [
            variable
            for (event_index, sem, placement_day, _), variable in x.items()
            if sem == semester
            and placement_day == day
            and events[event_index].course_index == course_index
        ]
        presence = model.NewBoolVar(f"course{course_index}_sem{semester}_{day}_present")
        if variables:
            model.AddMaxEquality(presence, variables)
        else:
            model.Add(presence == 0)
        return presence

    course_day_presence_cache: dict[tuple[int, int, str], cp_model.IntVar] = {}

    def course_day_presence(course_index: int, semester: int, day: str) -> cp_model.IntVar:
        key = (course_index, semester, day)
        if key not in course_day_presence_cache:
            course_day_presence_cache[key] = _course_day_presence(course_index, semester, day)
        return course_day_presence_cache[key]

    def _course_index_by_id(course_id) -> int | None:
        try:
            wanted = int(course_id)
        except (TypeError, ValueError):
            return None
        for i, course in enumerate(courses):
            if course.course_id == wanted:
                return i
        return None

    for rule in ai_rules:
        kind = rule.get("kind")
        hard = bool(rule.get("hard", True))
        weight = _rule_weight(rule)

        if kind == "placement_filter":
            matched_courses = _scope_course_indexes(rule.get("scope"))
            if not matched_courses:
                continue
            for (event_index, semester, day, start_slot), variable in x.items():
                if events[event_index].course_index not in matched_courses:
                    continue
                if _placement_violates(rule, event_index, semester, day, start_slot):
                    if hard:
                        model.Add(variable == 0)
                    else:
                        ai_objective_terms.append(variable * weight)

        elif kind == "course_relation":
            left = _course_index_by_id(rule.get("left_course_id"))
            right = _course_index_by_id(rule.get("right_course_id"))
            relation = rule.get("relation")
            if left is None or right is None or left == right:
                continue

            if relation in {"before_semester", "after_semester", "same_semester", "different_semester"}:
                left_expr = semester_expression(left)
                right_expr = semester_expression(right)
                if hard:
                    if relation == "before_semester":
                        model.Add(left_expr < right_expr)
                    elif relation == "after_semester":
                        model.Add(left_expr > right_expr)
                    elif relation == "same_semester":
                        model.Add(left_expr == right_expr)
                    else:
                        model.Add(left_expr != right_expr)
                else:
                    # Penalize every selected semester pair that violates the relation.
                    left_min = _minimum_semester_for_year(courses[left].year, semesters_per_year)
                    right_min = _minimum_semester_for_year(courses[right].year, semesters_per_year)
                    for ls in range(left_min, max_semester + 1):
                        for rs in range(right_min, max_semester + 1):
                            violates = (
                                (relation == "before_semester" and not (ls < rs))
                                or (relation == "after_semester" and not (ls > rs))
                                or (relation == "same_semester" and ls != rs)
                                or (relation == "different_semester" and ls == rs)
                            )
                            if not violates:
                                continue
                            pair = model.NewBoolVar(f"ai_pair_sem_{left}_{ls}_{right}_{rs}")
                            a = semester_choice[(left, ls)]
                            b = semester_choice[(right, rs)]
                            model.Add(pair <= a)
                            model.Add(pair <= b)
                            model.Add(pair >= a + b - 1)
                            ai_objective_terms.append(pair * weight)

            elif relation == "different_day":
                for semester in range(1, max_semester + 1):
                    for day in active_days:
                        a = course_day_presence(left, semester, day)
                        b = course_day_presence(right, semester, day)
                        if hard:
                            model.Add(a + b <= 1)
                        else:
                            pair = model.NewBoolVar(f"ai_pair_day_{left}_{right}_{semester}_{day}")
                            model.Add(pair <= a)
                            model.Add(pair <= b)
                            model.Add(pair >= a + b - 1)
                            ai_objective_terms.append(pair * weight)

        elif kind == "day_load":
            day = rule.get("day")
            if day not in active_days:
                continue
            matched_courses = _scope_course_indexes(rule.get("scope"))
            if not matched_courses:
                continue
            max_hours = rule.get("max_hours")
            minimize = bool(rule.get("minimize", False))

            for semester in range(1, max_semester + 1):
                terms = []
                for (event_index, sem, placement_day, _), variable in x.items():
                    if sem != semester or placement_day != day:
                        continue
                    if events[event_index].course_index in matched_courses:
                        terms.append(durations[event_index] * variable)
                if not terms:
                    continue
                max_possible = sum(durations[e] for e, ev in enumerate(events) if ev.course_index in matched_courses)
                load = model.NewIntVar(0, max_possible, f"ai_dayload_{semester}_{day}_{len(ai_objective_terms)}")
                model.Add(load == sum(terms))

                if max_hours is not None:
                    max_slots = _duration_slots(float(max_hours), "AI day-load maximum")
                    if hard:
                        model.Add(load <= max_slots)
                    else:
                        excess = model.NewIntVar(0, max_possible, f"ai_excess_{semester}_{day}_{len(ai_objective_terms)}")
                        model.AddMaxEquality(excess, [load - max_slots, 0])
                        ai_objective_terms.append(excess * weight)
                elif minimize:
                    ai_objective_terms.append(load * weight)

    # Utility: variables that cover one occupied absolute slot.
    def covering_variables(
        event_indexes: list[int], semester: int, day: str, occupied_slot: int
    ) -> list[cp_model.IntVar]:
        result = []

        for event_index in event_indexes:
            event = events[event_index]
            course = courses[event.course_index]
            if semester < _minimum_semester_for_year(course.year, semesters_per_year):
                continue

            duration = durations[event_index]
            day_start, day_end = day_windows[day]
            latest_start = day_end - duration

            for start_slot in range(day_start, latest_start + 1):
                variable = x.get((event_index, semester, day, start_slot))
                if variable is not None and start_slot <= occupied_slot < start_slot + duration:
                    result.append(variable)

        return result

    # 4a. Same lecturer cannot overlap.
    all_event_indexes = list(range(len(events)))
    if all_a_courses_fixed:
        active_semesters = sorted(set(fixed_a_semesters.values()))
    else:
        active_semesters = list(range(1, max_semester + 1))

    for lecturer in sorted({course.lecturer for course in courses}):
        lecturer_events = [
            event_index
            for event_index, event in enumerate(events)
            if courses[event.course_index].lecturer == lecturer
        ]

        for semester in active_semesters:
            for day in active_days:
                day_start, day_end = day_windows[day]
                for occupied_slot in range(day_start, day_end):
                    overlapping = covering_variables(
                        lecturer_events, semester, day, occupied_slot
                    )
                    if overlapping:
                        model.Add(sum(overlapping) <= 1)

    # 4b. Student-overlap policy by course category.
    #
    # Final department policy:
    #   * CORE (חובה) may not overlap ANY other course.
    #   * GENERAL (כללי) behaves exactly like CORE and may not overlap ANY course.
    #   * PROJECT may overlap PROJECT only.
    #   * ELECTIVE and SEMINAR may overlap each other (including same-category)
    #     without a penalty.
    # Lecturer conflicts remain hard for every category.
    core_event_indexes = [i for i, event in enumerate(events) if courses[event.course_index].course_category == "חובה"]
    general_event_indexes = [i for i, event in enumerate(events) if courses[event.course_index].course_category == "כללי"]
    elective_event_indexes = [i for i, event in enumerate(events) if courses[event.course_index].course_category == "בחירה"]
    seminar_event_indexes = [i for i, event in enumerate(events) if courses[event.course_index].course_category == "סמינר"]
    project_event_indexes = [i for i, event in enumerate(events) if courses[event.course_index].course_category == "פרויקט"]


    # Meetings of the SAME course can never overlap.
    for course_index in range(len(courses)):
        own_events = [i for i, event in enumerate(events) if event.course_index == course_index]
        if len(own_events) <= 1:
            continue
        for semester in active_semesters:
            for day in active_days:
                ds, de = day_windows[day]
                for occupied_slot in range(ds, de):
                    own_overlap = covering_variables(own_events, semester, day, occupied_slot)
                    if own_overlap:
                        model.Add(sum(own_overlap) <= 1)

    optional_overlap_terms = []
    for semester in active_semesters:
        for day in active_days:
            day_start, day_end = day_windows[day]
            for occupied_slot in range(day_start, day_end):
                core = covering_variables(core_event_indexes, semester, day, occupied_slot)
                general = covering_variables(general_event_indexes, semester, day, occupied_slot)
                elective = covering_variables(elective_event_indexes, semester, day, occupied_slot)
                seminar = covering_variables(seminar_event_indexes, semester, day, occupied_slot)
                project = covering_variables(project_event_indexes, semester, day, occupied_slot)

                protected = core + general
                all_other = elective + seminar + project

                # חובה/כללי: if one is present, NOTHING else may occupy that slot.
                if protected:
                    model.Add(sum(protected) <= 1)
                    for pv in protected:
                        for ov in all_other:
                            model.Add(pv + ov <= 1)

                # Project may overlap project only; never elective/seminar.
                if project and (elective or seminar):
                    for pv in project:
                        for ov in elective + seminar:
                            model.Add(pv + ov <= 1)

                # Allowed does not mean preferred. Penalize concurrent
                # elective/seminar attendance per five-minute slot.
                optional = elective + seminar
                if optional:
                    excess = model.NewIntVar(0, len(events),
                        f'optional_overlap_{semester}_{day}_{occupied_slot}')
                    model.AddMaxEquality(excess, [sum(optional) - 1, 0])
                    optional_overlap_terms.append(excess)

                # Project/project is also allowed. Project overlap preference is
                # handled by the general compactness objective rather than by a
                # hard restriction.

    # 5. Objective.
    #
    # Semester planning priorities:
    #   1) Keep courses inside their intended study year whenever possible.
    #   2) Spread the weekly teaching load across all semesters of that year,
    #      instead of packing everything into semesters 1 and 2 and leaving
    #      semester 3 empty.
    #   3) Use earlier days/times only as a small tie-breaker.
    #
    # There is intentionally NO constraint that a lecture must be before its
    # tutorial, and NO objective that tries to keep the two events close.
    objective_terms = []
    objective_terms.extend(ai_objective_terms)
    optional_overlap_cost = sum(optional_overlap_terms)
    objective_terms.append(optional_overlap_cost * 100_000)

    # Strong penalty for pushing a course beyond the last semester of the
    # study year listed in Excel. Small within-year preference is only a
    # tie-breaker so the balancing objective can use every semester.
    for course_index, course in enumerate(courses):
        first_semester = _minimum_semester_for_year(
            course.year, semesters_per_year
        )
        last_semester_in_year = course.year * semesters_per_year

        for semester in range(first_semester, max_semester + 1):
            cost = 0

            if semester <= last_semester_in_year:
                # Tiny preference for earlier semesters; not strong enough to
                # cause the old "pack early, leave semester 3 empty" behavior.
                cost += (semester - first_semester) * 2
            else:
                # Moving a course into a later study year is allowed only when
                # prerequisites/capacity make it useful or necessary.
                cost += 5000 + (semester - last_semester_in_year) * 1000

            if semester > expected_last_semester:
                cost += 20000 + (semester - expected_last_semester) * 5000

            objective_terms.append(
                semester_choice[(course_index, semester)] * cost
            )

    # When a semester-B intake is enabled, make Year 1 / Semester 1 friendly
    # to a later-starting cohort.  Courses that do not unlock later courses are
    # good candidates for the first semester because cohort B can safely leave
    # them until its continuation semester.  Conversely, putting many blocking
    # prerequisite/parallel courses in Semester 1 forces extra B-only offerings.
    # This is a SOFT preference only; prerequisite chains can still require a
    # critical course to stay early.
    if availability.get("allow_semester_b_start", False):
        blocking_for_late_intake = requirements.dependency_sources()
        first_calendar_semester = 1
        for course_index, course in enumerate(courses):
            if course.year != 1 or (course_index, first_calendar_semester) not in semester_choice:
                continue
            in_first = semester_choice[(course_index, first_calendar_semester)]
            is_flexible_nonblocking = (
                course_index not in blocking_for_late_intake
                and course.flexibility == "flexible"
            )
            if is_flexible_nonblocking:
                # Encourage flexible/general material in the first semester.
                # Penalize every alternative semester slightly instead of using
                # a negative objective coefficient.
                for semester in range(2, min(semesters_per_year, max_semester) + 1):
                    if (course_index, semester) in semester_choice:
                        objective_terms.append(semester_choice[(course_index, semester)] * 350)
            elif course_index in blocking_for_late_intake:
                # A blocking course in Semester 1 is sometimes necessary, but
                # it usually forces a B-only repeat because B has not started yet.
                objective_terms.append(in_first * 900)

    # Strongly balance each curriculum year's workload across ALL of its
    # semesters.  The previous objective only penalized deviation from an
    # average, which could still prefer 6/2/0 when prerequisite-chain penalties
    # pulled courses forward.  Here we explicitly penalize empty semesters and
    # minimize the heaviest semester as well as the average deviation.
    # Workload is measured in 5-minute slots, so longer courses count more.
    for year in range(1, degree_years + 1):
        year_course_indexes = [
            i for i, course in enumerate(courses) if course.year == year
        ]
        if not year_course_indexes:
            continue

        course_load_slots = {
            i: _duration_slots(
                courses[i].lecture_hours + courses[i].tutorial_hours,
                f"Course {courses[i].course_id} total weekly load",
            )
            for i in year_course_indexes
        }
        total_load = sum(course_load_slots.values())
        target_load = int(round(total_load / semesters_per_year))

        first_semester = _minimum_semester_for_year(year, semesters_per_year)
        last_semester = year * semesters_per_year
        semester_loads = []

        for semester in range(first_semester, last_semester + 1):
            load_var = model.NewIntVar(
                0, total_load, f"year{year}_semester{semester}_load"
            )
            model.Add(
                load_var
                == sum(
                    course_load_slots[i] * semester_choice[(i, semester)]
                    for i in year_course_indexes
                )
            )
            semester_loads.append(load_var)

            course_count = model.NewIntVar(
                0, len(year_course_indexes), f"year{year}_semester{semester}_course_count"
            )
            model.Add(
                course_count
                == sum(semester_choice[(i, semester)] for i in year_course_indexes)
            )

            # An empty semester is allowed if prerequisites truly force it, but
            # it is deliberately expensive when the year has enough courses to
            # populate every semester.
            if len(year_course_indexes) >= semesters_per_year:
                empty = model.NewBoolVar(f"year{year}_semester{semester}_empty")
                model.Add(course_count == 0).OnlyEnforceIf(empty)
                model.Add(course_count >= 1).OnlyEnforceIf(empty.Not())
                objective_terms.append(empty * 12000)

            deviation = model.NewIntVar(
                0, total_load, f"year{year}_semester{semester}_load_deviation"
            )
            model.AddAbsEquality(deviation, load_var - target_load)
            objective_terms.append(deviation * 900)

        if len(semester_loads) > 1:
            max_sem_load = model.NewIntVar(
                0, total_load, f"year{year}_maximum_semester_load"
            )
            min_sem_load = model.NewIntVar(
                0, total_load, f"year{year}_minimum_semester_load"
            )
            model.AddMaxEquality(max_sem_load, semester_loads)
            model.AddMinEquality(min_sem_load, semester_loads)
            semester_spread = model.NewIntVar(
                0, total_load, f"year{year}_semester_load_spread"
            )
            model.Add(semester_spread == max_sem_load - min_sem_load)
            objective_terms.append(max_sem_load * 1200)
            objective_terms.append(semester_spread * 1400)

    # Balance the STUDENT CALENDAR FOOTPRINT across active days.  This is
    # intentionally based on occupied time slots (OR occupancy), not on the
    # arithmetic sum of course durations.  Therefore overlapping electives /
    # seminars can genuinely improve the objective, while projects are excluded
    # because they are allowed to run in parallel.
    footprint_event_indexes = [
        i for i, event in enumerate(events)
        if courses[event.course_index].course_category != "פרויקט"
    ]
    max_day_window = max(day_windows[d][1] - day_windows[d][0] for d in active_days)
    if len(active_days) > 1 and footprint_event_indexes:
        for semester in active_semesters:
            day_load_vars = []
            for day in active_days:
                ds, de = day_windows[day]
                occ_flags = []
                for slot in range(ds, de):
                    vals = covering_variables(footprint_event_indexes, semester, day, slot)
                    occ = model.NewBoolVar(f"comfort_occ_{semester}_{day}_{slot}")
                    if vals:
                        model.AddMaxEquality(occ, vals)
                    else:
                        model.Add(occ == 0)
                    occ_flags.append(occ)
                day_load = model.NewIntVar(0, de-ds, f"semester{semester}_{day}_footprint")
                model.Add(day_load == sum(occ_flags))
                day_load_vars.append(day_load)

            max_day_load = model.NewIntVar(0, max_day_window, f"semester{semester}_max_day_footprint")
            model.AddMaxEquality(max_day_load, day_load_vars)
            objective_terms.append(max_day_load * 260)
            for i in range(len(day_load_vars)):
                for j in range(i + 1, len(day_load_vars)):
                    diff = model.NewIntVar(0, max_day_window, f"semester{semester}_daydiff_{i}_{j}")
                    model.AddAbsEquality(diff, day_load_vars[i] - day_load_vars[j])
                    objective_terms.append(diff * 20)

    # Break preference removed.
    # Zero-minute, 5--30 minute, and longer gaps are all allowed with no
    # penalty. This is intentionally not part of feasibility or optimization.

    # Soft prerequisite-chain compactness objective. A direct prerequisite may
    # be in the immediately previous semester at no cost; every additional
    # semester of waiting is penalized. This applies to the single degree plan.
    PREREQUISITE_WAIT_PENALTY = 25
    for course_index, prerequisite_indexes in prerequisite_map.items():
        for prerequisite_index in prerequisite_indexes:
            gap = model.NewIntVar(
                1, max_semester,
                f"prereq_gap_{prerequisite_index}_{course_index}"
            )
            model.Add(
                gap
                == semester_expression(course_index)
                - semester_expression(prerequisite_index)
            )
            extra_wait = model.NewIntVar(
                0, max_semester,
                f"prereq_extra_wait_{prerequisite_index}_{course_index}"
            )
            model.AddMaxEquality(extra_wait, [gap - 1, 0])
            objective_terms.append(extra_wait * PREREQUISITE_WAIT_PENALTY)

    # Final day/time tie-breaker: after all curriculum/semester objectives,
    # prefer meetings to start as early as possible inside the selected day.
    # The cost is the offset from that day's opening slot, so 08:00 costs 0,
    # 08:15 costs 3 slots, 08:45 costs 9 slots, etc. This never changes a hard
    # constraint and is deliberately lower priority than the existing goals.
    a_early_start_terms = []
    for (event_index, semester, day, start_slot), variable in x.items():
        day_open = day_windows[day][0]
        a_early_start_terms.append(variable * max(0, start_slot - day_open))
    a_early_start_cost = sum(a_early_start_terms) if a_early_start_terms else 0
    # Quarter-hour starts are preferred, never required: retain exact windows
    # and five-minute options when hard constraints make them necessary.
    clock_offset_bound = len(events) * (24 * 60 // SLOT_MINUTES)
    a_off_grid_cost = sum(v for (_, _, _, st), v in x.items()
                          if (st * SLOT_MINUTES) % 15)
    a_clock_cost = a_off_grid_cost * (clock_offset_bound + 1) + a_early_start_cost
    a_clock_bound = len(events) * (clock_offset_bound + 1) + clock_offset_bound

    # Stage 1 has already chosen every A semester.  In that case Stage 2's
    # first responsibility is to obtain an exact weekly placement, not to spend
    # the whole runtime proving/optimising comfort tie-breakers.  The previous
    # build could have a perfectly valid fixed curriculum and still return
    # UNKNOWN because CP-SAT was optimizing breaks/day-balance.
    #
    # Use a pure feasibility objective for fixed-A runs.  All hard constraints
    # (lecturer conflicts, category overlap policy, continuous meetings,
    # availability, same-course L/T non-overlap) remain in the model.  B is
    # handled afterwards by its dedicated search.
    if all_a_courses_fixed:
        # B-aware lightweight objective: Stage 1 already fixed every A semester,
        # so do not run the expensive comfort objective.  However, a completely
        # arbitrary feasible A placement can spread overlap-compatible A courses
        # over the whole week and leave no continuous room for a B-only repeat.
        # Prefer separate elective/seminar attendance before compaction.
        # Project alternatives keep their existing simultaneous-meeting policy.
        compact_terms = []
        all_event_indexes = list(range(len(events)))
        for semester in active_semesters:
            for day in active_days:
                ds, de = day_windows[day]
                for slot in range(ds, de):
                    vals = covering_variables(all_event_indexes, semester, day, slot)
                    if not vals:
                        continue
                    occ = model.NewBoolVar(f"bready_occ_{semester}_{day}_{slot}")
                    model.AddMaxEquality(occ, vals)
                    compact_terms.append(occ)
        # Preserve B-ready compaction as the primary objective. Earliest-start
        # preference is only a tie-breaker among equally compact placements.
        EARLY_TIEBREAK_SCALE = a_clock_bound + 1
        compact_cost = sum(compact_terms) if compact_terms else 0
        empty_day_terms = []
        for sem in active_semesters:
            for day in active_days:
                vals = [v for (e, s, d, st), v in x.items() if s == sem and d == day]
                used = model.NewBoolVar(f"a_day_used_s{sem}_{day}")
                model.AddMaxEquality(used, vals) if vals else model.Add(used == 0)
                empty_day_terms.append(1 - used)
        footprint_bound = sum(de - ds for ds, de in day_windows.values()) * len(active_semesters)
        compact_and_day_bound = len(empty_day_terms) * (footprint_bound + 1) + footprint_bound
        model.Minimize((optional_overlap_cost * (compact_and_day_bound + 1)
                        + sum(empty_day_terms) * (footprint_bound + 1)
                        + compact_cost) * EARLY_TIEBREAK_SCALE + a_clock_cost)
        print(
            "DIAGNOSTIC Stage 2 mode=FIXED_A_B_READY_COMPACTION "
            "objective=minimize_optional_overlap_then_day_balance_then_footprint_then_clock soft_comfort_skipped=True"
        )
    else:
        # Keep every existing curriculum objective ahead of clock-time choice.
        # Only when those objectives tie do earlier starts win.
        EARLY_TIEBREAK_SCALE = a_clock_bound + 1
        model.Minimize(sum(objective_terms) * EARLY_TIEBREAK_SCALE + a_clock_cost)

    # Validate the Stage-1 witness in the COMPLETE weekly model (including
    # AI constraints). Pin placements only in a clone; optimisation is free to
    # improve them in the original model. This gives us a verified incumbent
    # even if the compaction search subsequently times out without a solution.
    seed_solver = None
    seed_status = None
    solve_budget = max(0.01, float(time_limit_seconds))
    witness = availability.get("a_stage1_placements", []) or []
    if all_a_courses_fixed and witness:
        event_lookup = {(int(courses[e.course_index].course_id), e.event_type): i
                        for i, e in enumerate(events)}
        selected = set()
        for row in witness:
            eidx = event_lookup.get((int(row["course_id"]), row["event_type"]))
            if eidx is not None:
                selected.add((eidx, int(row["semester"]), row["day"], int(row["start_slot"])))
        if len(selected) == len(events) and all(key in x for key in selected):
            seed_model = model.Clone()
            seed_model.ClearObjective()
            for key, variable in x.items():
                seed_model.Add(seed_model.GetBoolVarFromProtoIndex(variable.Index()) == int(key in selected))
            seed_solver = cp_model.CpSolver()
            seed_solver.parameters.max_time_in_seconds = min(15.0, solve_budget / 2)
            seed_solver.parameters.num_search_workers = 8
            seed_status = seed_solver.Solve(seed_model)
            solve_budget = max(0.01, solve_budget - seed_solver.WallTime())
            print(f"DIAGNOSTIC STAGE_2_SEED status={seed_solver.StatusName(seed_status)} wall={seed_solver.WallTime():.2f}s")
            if seed_status in (cp_model.FEASIBLE, cp_model.OPTIMAL):
                model.ClearHints()
                for index in range(len(model.Proto().variables)):
                    variable = model.GetIntVarFromProtoIndex(index)
                    model.AddHint(variable, seed_solver.Value(variable))
            else:
                # A stale witness or additional hard AI rule may invalidate it.
                # Do not mistake rejection of one placement for infeasibility.
                seed_solver = None

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = solve_budget
    solver.parameters.random_seed = int(availability.get("search_seed", 1))
    solver.parameters.randomize_search = bool(availability.get("search_seed", 1) != 1)
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)
    print(f"DIAGNOSTIC STAGE_2_OPTIMIZATION status={solver.StatusName(status)} wall={solver.WallTime():.2f}s")
    if status == cp_model.UNKNOWN and seed_solver is not None:
        solver = seed_solver
        status = cp_model.FEASIBLE
        print("DIAGNOSTIC STAGE_2_USING_VERIFIED_SEED optimization_timeout=True")

    if status == cp_model.UNKNOWN:
        raise RuntimeError(
            "The fixed-A weekly feasibility solver reached its time limit before finding a first placement. "
            "This is a Stage-2 search timeout, not proof of timetable infeasibility."
        )
    if status == cp_model.INFEASIBLE:
        raise RuntimeError(
            "The hard constraints are infeasible for the current data. Check prerequisites/parallel "
            "requirements, lecturer availability, and teaching days/hours."
        )
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError("The solver stopped without producing a timetable.")

    print(f"DIAGNOSTIC Stage 2 A-only FEASIBLE status={solver.StatusName(status)} semester_b_requested={bool(availability.get('allow_semester_b_start', False))}")

    # 7. Read selected semesters.
    course_semesters: dict[int, int] = {}

    for course_index, course in enumerate(courses):
        min_semester = _minimum_semester_for_year(course.year, semesters_per_year)
        selected_semester = None

        for semester in range(min_semester, max_semester + 1):
            if solver.Value(semester_choice[(course_index, semester)]) == 1:
                selected_semester = semester
                break

        if selected_semester is None:
            raise RuntimeError(f"No semester chosen for {course.name}.")

        course_semesters[course_index] = selected_semester

    # Finish with a small exact clock-time solve. Fix each meeting's semester
    # and teaching day, preserving day balance and curriculum. Keep the full
    # weekly model (including lecturer/category/project/AI hard constraints).
    # This avoids leaving arbitrary five-minute starts in a timed-out incumbent.
    if all_a_courses_fixed:
        clock_model = model.Clone()
        clock_model.ClearHints()
        chosen_days = {(ei, sem, day) for (ei, sem, day, st), v in x.items()
                       if solver.Value(v)}
        for (ei, sem, day, st), v in x.items():
            if (ei, sem, day) not in chosen_days:
                clock_model.Add(v == 0)
        # Permit up to 30 minutes of additional daily footprint in the comfort
        # pass. The old strict bound prevented a 10–15 minute break even
        # when all hard constraints and lecturer availability allowed one.
        # This is a soft tradeoff: the optimizer may retain zero breaks.
        clock_model.Add(compact_cost <= solver.Value(compact_cost) + 6 * max(1, len(chosen_days)))
        clock_groups = {}
        for ei, sem, day in sorted(chosen_days):
            candidates = [(st, v) for (e, ss, d, st), v in x.items()
                          if (e, ss, d) == (ei, sem, day)]
            start = clock_model.NewIntVar(0, 288, f'clock_start_{ei}')
            clock_model.Add(start == sum(st * v for st, v in candidates))
            old_start = next(st for st, v in candidates if solver.Value(v))
            clock_groups.setdefault((sem, day), []).append((start, durations[ei], old_start))
        clock_model.Minimize(_clock_break_objective(clock_model, clock_groups, 'a_comfort'))
        for index in range(len(model.Proto().variables)):
            variable = clock_model.GetIntVarFromProtoIndex(index)
            clock_model.AddHint(variable, solver.Value(variable))
        clock_solver = cp_model.CpSolver()
        clock_solver.parameters.max_time_in_seconds = 20.0
        clock_solver.parameters.num_search_workers = 8
        clock_status = clock_solver.Solve(clock_model)
        print(f"DIAGNOSTIC STAGE_2_CLOCK_POLISH status={clock_solver.StatusName(clock_status)} "
              f"wall={clock_solver.WallTime():.2f}s")
        if clock_status in (cp_model.FEASIBLE, cp_model.OPTIMAL):
            solver = clock_solver
        else:
            print("DIAGNOSTIC CLOCK_PREFERENCE_NOT_FINISHED retaining_verified_times=True")

    # 8. Read event placements.
    timetable: list[ScheduledCourse] = []

    for event_index, event in enumerate(events):
        course = courses[event.course_index]
        semester = course_semesters[event.course_index]
        selected: tuple[str, int] | None = None

        for day in active_days:
            day_start, day_end = day_windows[day]
            duration = durations[event_index]
            latest_start = day_end - duration

            for start_slot in range(day_start, latest_start + 1):
                variable = x.get((event_index, semester, day, start_slot))
                if variable is not None and solver.Value(variable) == 1:
                    selected = (day, start_slot)
                    break

            if selected is not None:
                break

        if selected is None:
            raise RuntimeError(f"{course.name} {event.event_type} was not scheduled.")

        day, start_slot = selected
        duration = durations[event_index]

        timetable.append(
            ScheduledCourse(
                course_id=course.course_id,
                name=course.name,
                lecturer=course.lecturer,
                year=course.year,
                course_type=course.course_type,
                course_category=course.course_category,
                credits=course.credits,
                weekly_hours=course.weekly_hours,
                lecture_hours=course.lecture_hours,
                tutorial_hours=course.tutorial_hours,
                calculation_status=course.calculation_status,
                prerequisites=course.prerequisites,
                parallel_requirement=course.parallel_requirement,
                semester=semester,
                semester_name=_semester_name(
                    semester,
                    semesters_per_year,
                    expected_last_semester,
                ),
                semester_status=(
                    "ON_PLAN" if semester <= expected_last_semester else "OVERFLOW"
                ),
                event_type=event.event_type,
                event_duration=event.duration_hours,
                day=day,
                day_hebrew=DAY_NAMES_HE[day],
                start=_slot_to_time(start_slot),
                end=_slot_to_time(start_slot + duration),
            )
        )


    # Always leave a compact machine-readable diagnostic artifact before the
    # expensive Semester-B solve.  If B times out, this still preserves the
    # exact feasible A timetable that B was built around.
    try:
        Path("timetable_debug.json").write_text(
            json.dumps({
                "status": "A_FEASIBLE_B_PENDING",
                "solver_version": "B_DIRECT_TIMESLOT_V2",
                "start_grid_minutes": B_START_GRID_MINUTES,
                "a_timetable": [item.to_dict() for item in timetable],
                "b": {"status": "PENDING"},
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as debug_exc:
        print(f"DIAGNOSTIC DEBUG_JSON_WRITE_WARNING stage=A error={debug_exc}")

    # 9. Optional second intake: students who start in semester B.
    #
    # This is NOT a fixed k -> k+1 copy of cohort A.  The late intake is
    # planned around three priorities:
    #   1) share as many existing A offerings as possible,
    #   2) repeat only courses that B cannot share without breaking its
    #      prerequisite/parallel progression, and
    #   3) keep non-blocking first-semester material flexible so B may finish
    #      it in the continuation semester instead of creating an early repeat.
    #
    # The extra continuation semester belongs to cohort B.  A students may
    # administratively retake an offering there, but it is never planned as an
    # A+B semester.
    if availability.get("allow_semester_b_start", False):
        continuation_semester = expected_last_semester + 1

        # Reverse prerequisite graph -> how many later courses a course unlocks.
        # We use this only as a SOFT urgency signal for unavoidable B repeats.
        reverse_prereq: dict[int, set[int]] = {i: set() for i in range(len(courses))}
        for dependent, prereqs in requirements.prerequisite_edges().items():
            for prereq in prereqs:
                reverse_prereq[prereq].add(dependent)

        def _descendant_count(root: int) -> int:
            seen: set[int] = set()
            stack = list(reverse_prereq.get(root, set()))
            while stack:
                node = stack.pop()
                if node in seen:
                    continue
                seen.add(node)
                stack.extend(reverse_prereq.get(node, set()))
            return len(seen)

        blocking_courses = requirements.dependency_sources()

        # 9a. Semester-level plan for cohort B.  For every course B either
        # attends A's existing offering (shared) or gets one B-only repeat in a
        # chosen semester.  Sharing is overwhelmingly preferred.
        b_plan = cp_model.CpModel()
        b_semester_var: dict[int, cp_model.IntVar] = {}
        b_share: dict[int, cp_model.IntVar] = {}
        b_repeat_choice: dict[tuple[int, int], cp_model.IntVar] = {}
        b_plan_objective = []

        # Repeats are costly, but not forbidden in practice.  A deliberate
        # Semester-1 foundation repeat can be the best global solution when it
        # gives cohort A a useful first semester and lets both cohorts merge
        # again for downstream courses.  Unnecessary repeats remain much more
        # expensive than strategic foundation repeats.
        # PRIMARY objective: maximize A+B sharing / minimize B-only course copies.
        # This coefficient deliberately dominates every timing/urgency preference below.
        # A prerequisite is repeated early only when a repeat is actually needed (or when
        # that one repeat avoids even more repeats downstream); importance alone must not
        # make duplication cheap.
        REPEAT_COURSE_PENALTY = 1_000_000_000
        STRATEGIC_FOUNDATION_REPEAT_PENALTY = REPEAT_COURSE_PENALTY
        # Semester 10 is B's continuation view. Phase-A courses reuse their
        # existing recurring offerings with younger cohorts (same source times,
        # no new-opening penalty). Other courses require a new B-only opening.
        CORE_CONTINUATION_PENALTY = 1_500_000
        CORE_DELAY_PENALTY_PER_SEMESTER = 20_000
        # Secondary preference: projects are alternatives, and we prefer cohort B
        # to join cohort A's existing project choice slot instead of opening a
        # separate B-only project slot. This is deliberately much smaller than
        # the primary 1e9 repeat penalty, so it only breaks ties after minimizing
        # the total number of duplicated courses.
        PROJECT_B_ONLY_EXTRA_PENALTY = 50_000_000
        for course_index, course in enumerate(courses):
            a_semester = course_semesters[course_index]
            b_sem = b_plan.NewIntVar(2, continuation_semester, f"b_semester_{course_index}")
            b_semester_var[course_index] = b_sem

            share = b_plan.NewBoolVar(f"b_share_a_{course_index}")
            b_share[course_index] = share
            if course.course_category == "סמינר":
                b_plan.Add(share == 1)  # Same physical A+B seminar, never a B repeat.
            choices = []

            is_nonblocking_flexible = (
                course_index not in blocking_courses
                and course.flexibility == "flexible"
            )

            # A shared course is eligible within its catalog study year once
            # B's own prerequisites/parallel requirements have been satisfied.
            # Do not reject an existing offering merely because B entered one
            # semester later. Only new repeat offerings use the shifted window.
            b_year_start = (int(course.year) - 1) * semesters_per_year + 2
            b_year_end = int(course.year) * semesters_per_year + 1
            physical_end = min(expected_last_semester, b_year_end)
            share_available = max(2, _minimum_semester_for_year(course.year, semesters_per_year)) <= a_semester <= physical_end
            if share_available:
                choices.append(share)
                b_plan.Add(b_sem == a_semester).OnlyEnforceIf(share)
            else:
                b_plan.Add(share == 0)

            # Special rule agreed with the department: if A takes a flexible,
            # non-blocking course in Year-1 Semester-1, B does NOT get an
            # automatic Semester-2 copy. B completes it in the continuation
            # semester instead.  This is how Semester-1 can contain useful
            # general material without forcing needless duplication.
            is_general_nonblocking = (
                course.course_category == "כללי" and course_index not in blocking_courses
            )
            is_semester_a_offering = ((a_semester - 1) % semesters_per_year == 0)
            # HARD B curriculum-year window.  B starts one calendar semester
            # after A, so its physical Year-1/2/3 windows are shifted by +1:
            # with 3 semesters/year -> Y1=2..4, Y2=5..7, Y3=8..9 physically.
            # Semester 10 can reuse an existing phase-A offering (1/4/7),
            # or contain a newly opened final-year B meeting. Earlier physical
            # repeats retain their shifted catalog-year window.
            allowed_repeat_semesters = list(range(max(2, b_year_start), physical_end + 1))

            # The continuation value is a logical completion bucket for cohort B,
            # not a physical tenth timetable column.  It may be used for ANY
            # still-unfinished B course at the end of its shifted Year 3.
            # Restricting it only to courses that A happened to place in an
            # A-phase semester makes valid prerequisite chains impossible.
            # Example in the current data: ML -> Deep Learning -> ML Seminar
            # needs three ordered B positions in Year 3 (8 < 9 < logical 10).
            if int(course.year) == degree_years or is_semester_a_offering:
                allowed_repeat_semesters.append(continuation_semester)

            if a_semester == 1 and (is_nonblocking_flexible or is_general_nonblocking):
                # These useful A-Semester-1 flexible/general courses are not
                # duplicated immediately for B.  B joins their next Semester-A
                # occurrence, represented logically by continuation_semester.
                allowed_repeat_semesters = [continuation_semester]

            # Never create a B-only copy in the exact same calendar semester
            # in which cohort A already offers the course and B is allowed to
            # share it.  Such a repeat is strictly redundant: it produces the
            # same course twice in one semester instead of one A+B offering.
            if share_available:
                allowed_repeat_semesters = [
                    semester for semester in allowed_repeat_semesters
                    if semester != a_semester
                ]

            for semester in allowed_repeat_semesters:
                repeat = b_plan.NewBoolVar(f"b_repeat_c{course_index}_s{semester}")
                b_repeat_choice[(course_index, semester)] = repeat
                choices.append(repeat)
                b_plan.Add(b_sem == semester).OnlyEnforceIf(repeat)
                is_strategic_foundation_repeat = (
                    a_semester == 1
                    and course.year == 1
                    and course_index in blocking_courses
                    and not prerequisite_map.get(course_index)
                    and not parallel_map.get(course_index)
                    and not requirements.prereq_any.get(course_index)
                    and not requirements.parallel_any.get(course_index)
                )
                repeat_penalty = (
                    STRATEGIC_FOUNDATION_REPEAT_PENALTY
                    if is_strategic_foundation_repeat
                    else REPEAT_COURSE_PENALTY
                )
                recurring_reuse = semester == continuation_semester and is_semester_a_offering
                if recurring_reuse:
                    # Reusing a younger cohort's offering saves a new opening,
                    # but it is NOT equivalent to A+B attending together now.
                    # Prefer the actual shared offering whenever all hard
                    # constraints (including B prerequisites) permit it.
                    repeat_penalty = REPEAT_COURSE_PENALTY if share_available else 0
                b_plan_objective.append(repeat * repeat_penalty)
                if course.course_category == "פרויקט" and not recurring_reuse:
                    b_plan_objective.append(repeat * PROJECT_B_ONLY_EXTRA_PENALTY)

                if is_strategic_foundation_repeat:
                    # SECONDARY objective: once this repeat is genuinely selected,
                    # reopen the foundation course as early as possible so it does
                    # not delay dependent B courses.  It no longer receives a cheap
                    # repeat penalty merely because it is a prerequisite.
                    urgency = max(1, _descendant_count(course_index) + 1)
                    b_plan_objective.append(repeat * max(0, semester - 2) * urgency * 40_000)
                if a_semester == 1 and is_nonblocking_flexible:
                    # Only the final semester is allowed, so no timing cost.
                    pass
                elif course_index in blocking_courses:
                    urgency = max(1, _descendant_count(course_index) + 1)
                    b_plan_objective.append(repeat * (semester - 2) * urgency * 20_000)
                elif course.course_category == "חובה" and course.flexibility != "flexible":
                    # Required/core courses remain part of B's normal academic
                    # progression even when they are not prerequisites for any
                    # later course.  Prefer them as early as practical and make
                    # the continuation semester a last resort rather than the
                    # default 'late parking' location.
                    b_plan_objective.append(
                        repeat * max(0, semester - 2) * CORE_DELAY_PENALTY_PER_SEMESTER
                    )
                    if semester == continuation_semester and not recurring_reuse:
                        b_plan_objective.append(repeat * CORE_CONTINUATION_PENALTY)
                else:
                    # Flexible/general/non-core material should be delayed when
                    # that frees B's early semesters. Semester 10 has zero delay
                    # cost and is therefore the preferred completion point when
                    # the course cannot be shared.
                    late_weight = 8_000 if course.course_category == "כללי" else 1_000
                    b_plan_objective.append(
                        repeat * (continuation_semester - semester) * late_weight
                    )

            b_plan.AddExactlyOne(choices)

        # Project alternatives are one student choice slot for cohort B too.
        # Keep the same project alternatives in the same B semester and make
        # the share-vs-repeat decision together. If they must be repeated,
        # their physical meetings are synchronized below as well.
        b_project_indexes = [
            i for i, course in enumerate(courses) if course.course_category == "פרויקט"
        ]
        if len(b_project_indexes) > 1:
            b_anchor_project = b_project_indexes[0]
            for other_project in b_project_indexes[1:]:
                b_plan.Add(b_semester_var[other_project] == b_semester_var[b_anchor_project])
                b_plan.Add(b_share[other_project] == b_share[b_anchor_project])

        # B must satisfy the same academic progression as A, regardless of
        # whether each course is shared or repeated.
        for course_index, prerequisite_indexes in prerequisite_map.items():
            for prerequisite_index in prerequisite_indexes:
                b_plan.Add(
                    b_semester_var[prerequisite_index]
                    < b_semester_var[course_index]
                )
        for course_index, parallel_indexes in parallel_map.items():
            for parallel_index in parallel_indexes:
                b_plan.Add(
                    b_semester_var[parallel_index]
                    <= b_semester_var[course_index]
                )
        _add_or_precedence(b_plan, requirements.prereq_any, lambda i: b_semester_var[i], True, "b_pre")
        _add_or_precedence(b_plan, requirements.parallel_any, lambda i: b_semester_var[i], False, "b_par")

        recurring_indexes = {i for i, sem in course_semesters.items()
                             if (sem - 1) % semesters_per_year == 0}
        recurring_sources = {(str(item.course_id), item.event_type): item for item in timetable
                             if (item.semester - 1) % semesters_per_year == 0}
        recurring_calendar = [replace(item, semester=continuation_semester)
                              for item in recurring_sources.values()]
        b_existing_offerings = timetable + recurring_calendar

        def _b_attendance(i, sem):
            if sem == continuation_semester and i in recurring_indexes:
                return b_repeat_choice.get((i, sem), 0)
            return b_share[i]

        from functools import lru_cache

        @lru_cache(maxsize=None)
        def _b_candidates(eidx, sem, day, ds, de):
            ev = events[eidx]
            if sem == continuation_semester and ev.course_index in recurring_indexes:
                source = recurring_sources.get((str(courses[ev.course_index].course_id), ev.event_type))
                if source is None or source.day != day:
                    return []
                return [_time_to_slot(source.start)]
            dur = durations[eidx]
            starts = list(range(ds, de - dur + 1, max(1, B_START_GRID_MINUTES // SLOT_MINUTES)))
            last = de - dur
            if last >= ds and last not in starts:
                starts.append(last)
            # Fine-grained break options around existing offerings.
            for fixed in b_existing_offerings:
                if fixed.semester == sem and fixed.day == day:
                    for gap in (1, 2):
                        starts.extend((_time_to_slot(fixed.end) + gap,
                                       _time_to_slot(fixed.start) - dur - gap))
            return sorted({st for st in starts if ds <= st <= last})

        # Safe necessary capacity cuts for physical B semesters.  These are not
        # heuristic aggregate limits: they encode resources that truly cannot
        # overlap.  This lets the path model reject impossible choices before
        # launching an expensive weekly placement solve.
        weekly_capacity_slots = sum(de - ds for ds, de in day_windows.values())
        course_slots = {
            i: sum(durations[e] for e, ev in enumerate(events) if ev.course_index == i)
            for i in range(len(courses))
        }

        # (1) B's חובה/כללי courses are protected from every other B course and
        # from one another.  Therefore their total occupied time in a physical
        # semester can never exceed the department's weekly horizon.
        for semester in range(2, continuation_semester + 1):
            protected_terms = []
            for i, course in enumerate(courses):
                if course.course_category not in {"חובה", "כללי"}:
                    continue
                if course_slots[i] <= 0:
                    continue
                if (i, semester) in b_repeat_choice:
                    protected_terms.append(course_slots[i] * b_repeat_choice[(i, semester)])
                if course_semesters[i] == semester:
                    protected_terms.append(course_slots[i] * b_share[i])
            if protected_terms:
                b_plan.Add(sum(protected_terms) <= weekly_capacity_slots)

        # (2) A is already frozen.  A repeat taught by the same lecturer must fit
        # in that lecturer's remaining physical time.  Shared courses are NOT
        # counted again because they reuse A's existing meeting.
        lecturer_names = sorted({c.lecturer for c in courses if c.lecturer})
        for semester in range(2, continuation_semester + 1):
            for lecturer in lecturer_names:
                fixed_slots = sum(
                    _time_to_slot(item.end) - _time_to_slot(item.start)
                    for item in b_existing_offerings
                    if item.semester == semester and item.lecturer == lecturer
                )
                repeat_terms = []
                for i, course in enumerate(courses):
                    if course.lecturer != lecturer or (i, semester) not in b_repeat_choice:
                        continue
                    if semester == continuation_semester and i in recurring_indexes:
                        continue  # already counted in the existing lecturer calendar
                    repeat_terms.append(course_slots[i] * b_repeat_choice[(i, semester)])
                if repeat_terms:
                    b_plan.Add(fixed_slots + sum(repeat_terms) <= weekly_capacity_slots)

        print(
            "DIAGNOSTIC B_PLANNER_GUARDS=PREREQUISITE_READY_SHARING+SHIFTED_YEAR_REPEATS+FINAL_YEAR_LOGICAL_COMPLETION+"
            "SAFE_PROTECTED_CAPACITY+SAFE_LECTURER_REMAINING_CAPACITY; "
            "break_preference=FINAL_CLOCK_REFINEMENT"
        )
        b_plan.Minimize(sum(b_plan_objective))

        # Integrated Semester-B solve: academic share/repeat decisions and exact
        # weekly placement are solved in ONE CP-SAT model.  The old decomposed
        # path -> placement -> cut loop repeatedly rediscovered that Semester 8
        # was impossible only after committing to an academic path.  Here every
        # physical repeat choice is feasible only if its meetings can actually
        # be placed around frozen cohort A and the simultaneously selected shared
        # A+B offerings.
        def _cat_group(category: str) -> str:
            if category == "חובה":
                return "core"
            if category == "כללי":
                return "general"
            if category == "בחירה":
                return "elective"
            if category == "סמינר":
                return "seminar"
            return "project"

        course_index_by_id = {str(c.course_id): i for i, c in enumerate(courses)}
        b_start = {}
        # (event, semester) -> all possible starts. Their sum equals the repeat
        # literal, so unselected repeats create no meeting at all.
        for event_index, event in enumerate(events):
            course = courses[event.course_index]
            duration = durations[event_index]
            for semester in range(2, continuation_semester + 1):
                repeat_lit = b_repeat_choice.get((event.course_index, semester))
                if repeat_lit is None:
                    continue
                starts = []
                for day in active_days:
                    effective_window = lecturer_window(course.lecturer, day)
                    if effective_window is None:
                        continue
                    ds, de = effective_window
                    # University timetabling is modeled on discrete candidate timeslots.
                    # Keep the existing 5-minute representation for exact durations/windows,
                    # use a 15-minute base grid plus 5/10-minute break candidates
                    # beside existing offerings and the exact window boundary. This cuts the integrated model dramatically without
                    # shortening or moving department availability.
                    grid_slots = max(1, B_START_GRID_MINUTES // SLOT_MINUTES)
                    candidate_starts = _b_candidates(event_index, semester, day, ds, de)
                    for st in candidate_starts:
                        # A lecturer is frozen: a B-only repeat can never overlap
                        # any A meeting taught by the same lecturer.
                        lecturer_blocked = False
                        for fixed in b_existing_offerings:
                            if fixed.semester != semester or fixed.day != day or fixed.lecturer != course.lecturer:
                                continue
                            if semester == continuation_semester and event.course_index in recurring_indexes and fixed.course_id == course.course_id:
                                continue  # attendance reuses this same physical offering
                            fs, fe = _time_to_slot(fixed.start), _time_to_slot(fixed.end)
                            if st < fe and st + duration > fs:
                                lecturer_blocked = True
                                break
                        if lecturer_blocked:
                            continue
                        v = b_plan.NewBoolVar(f"bi_e{event_index}_s{semester}_{day}_{st}")
                        b_start[(event_index, semester, day, st)] = v
                        starts.append(v)

                        # A fixed course blocks B students only when B actually
                        # selected that A offering as shared.  This is the key
                        # coupling missing from the old two-stage search.
                        my_group = _cat_group(course.course_category)
                        for fixed in b_existing_offerings:
                            if fixed.semester != semester or fixed.day != day:
                                continue
                            fs, fe = _time_to_slot(fixed.start), _time_to_slot(fixed.end)
                            if not (st < fe and st + duration > fs):
                                continue
                            if semester == continuation_semester and event.course_index in recurring_indexes and fixed.course_id == course.course_id:
                                continue
                            fixed_i = course_index_by_id.get(str(fixed.course_id))
                            if fixed_i is None:
                                continue
                            fixed_group = _cat_group(fixed.course_category)
                            student_conflict = (
                                my_group in {"core", "general"}
                                or fixed_group in {"core", "general"}
                                or ("project" in {my_group, fixed_group} and not (my_group == fixed_group == "project"))
                            )
                            if student_conflict:
                                b_plan.Add(v + _b_attendance(fixed_i, semester) <= 1)
                # If this repeat is selected, every event of the course must be
                # placed exactly once. If there is no legal start, the repeat
                # choice is automatically forbidden.
                if starts:
                    b_plan.Add(sum(starts) == repeat_lit)
                else:
                    b_plan.Add(repeat_lit == 0)

        for pos, left in enumerate(recurring_calendar):
            li = course_index_by_id[str(left.course_id)]
            for right in recurring_calendar[pos + 1:]:
                ri = course_index_by_id[str(right.course_id)]
                if li == ri or left.day != right.day:
                    continue
                if max(_time_to_slot(left.start), _time_to_slot(right.start)) >= min(_time_to_slot(left.end), _time_to_slot(right.end)):
                    continue
                lc, rc = _cat_group(left.course_category), _cat_group(right.course_category)
                if lc in {"core", "general"} or rc in {"core", "general"} or ("project" in {lc, rc} and lc != rc):
                    b_plan.Add(_b_attendance(li, continuation_semester) + _b_attendance(ri, continuation_semester) <= 1)

        # Synchronize physical B-only project repeats. Since the project
        # alternatives are mutually exclusive choices for students, a repeat
        # of one project must occupy the same day/start as the other project.
        if len(b_project_indexes) > 1:
            b_anchor_project = b_project_indexes[0]
            anchor_events = [e for e, ev in enumerate(events) if ev.course_index == b_anchor_project]
            for other_project in b_project_indexes[1:]:
                other_events = [e for e, ev in enumerate(events) if ev.course_index == other_project]
                for anchor_event in anchor_events:
                    event_type = events[anchor_event].event_type
                    matches = [e for e in other_events if events[e].event_type == event_type]
                    if not matches and len(anchor_events) == len(other_events) == 1:
                        matches = other_events
                    for other_event in matches:
                        coords = {
                            (sem, day, st)
                            for (e, sem, day, st) in b_start
                            if e in {anchor_event, other_event}
                        }
                        for sem, day, st in coords:
                            av = b_start.get((anchor_event, sem, day, st))
                            ov = b_start.get((other_event, sem, day, st))
                            if av is None:
                                b_plan.Add(ov == 0)
                            elif ov is None:
                                b_plan.Add(av == 0)
                            else:
                                b_plan.Add(av == ov)

        def _integrated_cover(event_indexes, semester, day, slot):
            vals = []
            for event_index in event_indexes:
                duration = durations[event_index]
                course = courses[events[event_index].course_index]
                effective_window = lecturer_window(course.lecturer, day)
                if effective_window is None:
                    continue
                ds, de = effective_window
                grid_slots = max(1, B_START_GRID_MINUTES // SLOT_MINUTES)
                candidate_starts = _b_candidates(event_index, semester, day, ds, de)
                for st in candidate_starts:
                    v = b_start.get((event_index, semester, day, st))
                    if v is not None and st <= slot < st + duration:
                        vals.append(v)
            return vals

        # Same-course lecture/tutorial cannot overlap, even for categories that
        # otherwise permit overlap.
        events_by_course = {i: [e for e, ev in enumerate(events) if ev.course_index == i] for i in range(len(courses))}
        for course_index, own_events in events_by_course.items():
            if len(own_events) <= 1:
                continue
            for semester in range(2, continuation_semester + 1):
                if (course_index, semester) not in b_repeat_choice:
                    continue
                for day in active_days:
                    ds, de = day_windows[day]
                    for slot in range(ds, de):
                        vals = _integrated_cover(own_events, semester, day, slot)
                        if vals:
                            b_plan.Add(sum(vals) <= 1)

        # Same lecturer can never overlap between B repeats.
        for lecturer in sorted({c.lecturer for c in courses if c.lecturer}):
            lecturer_events = [e for e, ev in enumerate(events) if courses[ev.course_index].lecturer == lecturer]
            for semester in range(2, continuation_semester + 1):
                for day in active_days:
                    ds, de = day_windows[day]
                    for slot in range(ds, de):
                        vals = _integrated_cover(lecturer_events, semester, day, slot)
                        if vals:
                            b_plan.Add(sum(vals) <= 1)

        # B repeat-vs-repeat student overlap policy. Shared-vs-repeat conflicts
        # were coupled above; shared-vs-shared is already valid in frozen A.
        by_category = {
            cat: [e for e, ev in enumerate(events) if courses[ev.course_index].course_category == cat]
            for cat in ("חובה", "כללי", "בחירה", "סמינר", "פרויקט")
        }
        b_optional_overlap_terms = []
        for semester in range(2, continuation_semester + 1):
            for day in active_days:
                ds, de = day_windows[day]
                for slot in range(ds, de):
                    cvals = _integrated_cover(by_category["חובה"], semester, day, slot)
                    gvals = _integrated_cover(by_category["כללי"], semester, day, slot)
                    evals = _integrated_cover(by_category["בחירה"], semester, day, slot)
                    svals = _integrated_cover(by_category["סמינר"], semester, day, slot)
                    pvals = _integrated_cover(by_category["פרויקט"], semester, day, slot)
                    # Count actual B attendance, including shared and
                    # younger-cohort recurring offerings. Recurring entries
                    # already have b_start variables, so only add current
                    # shared A offerings here to avoid double counting.
                    optional = evals + svals
                    optional += [b_share[course_index_by_id[str(item.course_id)]]
                                 for item in timetable
                                 if item.semester == semester and item.day == day
                                 and item.course_category in ('בחירה', 'סמינר')
                                 and _time_to_slot(item.start) <= slot < _time_to_slot(item.end)]
                    if optional:
                        excess = b_plan.NewIntVar(0, len(events),
                            f'b_optional_overlap_{semester}_{day}_{slot}')
                        b_plan.AddMaxEquality(excess, [sum(optional) - 1, 0])
                        b_optional_overlap_terms.append(excess)
                    protected = cvals + gvals
                    others = evals + svals + pvals
                    if protected:
                        b_plan.Add(sum(protected) <= 1)
                        for pv in protected:
                            for ov in others:
                                b_plan.Add(pv + ov <= 1)
                    if pvals and (evals or svals):
                        for pv in pvals:
                            for ov in evals + svals:
                                b_plan.Add(pv + ov <= 1)

        # Keep the existing academic objective (share first, strategic repeats,
        # progression timing) as the primary objective. Among solutions with the
        # same academic score, prefer every physical B-only meeting as early as
        # possible inside its selected day. This prevents arbitrary 08:15/08:45
        # starts when 08:00 is equally feasible.
        b_early_start_terms = []
        for (event_index, semester, day, start_slot), variable in b_start.items():
            day_open = day_windows[day][0]
            b_early_start_terms.append(variable * max(0, start_slot - day_open))
        b_early_start_cost = sum(b_early_start_terms) if b_early_start_terms else 0
        b_offset_bound = len(events) * (24 * 60 // SLOT_MINUTES)
        b_off_grid_cost = sum(v for (_, _, _, st), v in b_start.items()
                              if (st * SLOT_MINUTES) % 15)
        b_clock_cost = b_off_grid_cost * (b_offset_bound + 1) + b_early_start_cost
        EARLY_TIEBREAK_SCALE = len(events) * (b_offset_bound + 1) + b_offset_bound + 1
        # Spread B's enrolled semesters over available days when feasible.
        b_empty_days = []
        b_load_spreads = []
        for sem in range(2, continuation_semester + 1):
            day_loads = []
            for day in active_days:
                vals = [v for (ei, ss, dd, st), v in b_start.items() if ss == sem and dd == day]
                vals += [_b_attendance(course_index_by_id[str(item.course_id)], sem)
                         for item in b_existing_offerings if item.semester == sem and item.day == day]
                used = b_plan.NewBoolVar(f"b_day_used_s{sem}_{day}")
                b_plan.AddMaxEquality(used, vals) if vals else b_plan.Add(used == 0)
                b_empty_days.append(1 - used)
                terms = [durations[ei] * v for (ei, ss, dd, st), v in b_start.items() if ss == sem and dd == day]
                terms += [(_time_to_slot(item.end) - _time_to_slot(item.start)) * _b_attendance(course_index_by_id[str(item.course_id)], sem)
                          for item in timetable if item.semester == sem and item.day == day]
                bound = sum(durations) * 2
                load = b_plan.NewIntVar(0, bound, f"b_day_load_s{sem}_{day}")
                b_plan.Add(load == sum(terms))
                day_loads.append(load)
            if day_loads:
                hi = b_plan.NewIntVar(0, sum(durations) * 2, f"b_day_max_s{sem}")
                lo = b_plan.NewIntVar(0, sum(durations) * 2, f"b_day_min_s{sem}")
                b_plan.AddMaxEquality(hi, day_loads)
                b_plan.AddMinEquality(lo, day_loads)
                b_load_spreads.append(hi - lo)
        b_optional_overlap_cost = sum(b_optional_overlap_terms)
        b_plan.Minimize((sum(b_plan_objective) + b_optional_overlap_cost * 100_000 + sum(b_empty_days) * 5_000_000 + sum(b_load_spreads) * 1000) * EARLY_TIEBREAK_SCALE + b_clock_cost)

        integrated_solver = cp_model.CpSolver()
        integrated_solver.parameters.max_time_in_seconds = min(55.0, max(30.0, float(time_limit_seconds) * 1.25))
        integrated_solver.parameters.num_search_workers = 8
        integrated_solver.parameters.random_seed = int(availability.get("search_seed", 1))
        integrated_solver.parameters.randomize_search = bool(availability.get("search_seed", 1) != 1)
        integrated_solver.parameters.log_search_progress = False
        print(
            f"DIAGNOSTIC B_INTEGRATED_SOLVER_START physical_semesters=2..{continuation_semester} "
            f"displayed_continuation={continuation_semester} placement_vars={len(b_start)} "
            f"start_grid_minutes={B_START_GRID_MINUTES} break_preference=FINAL_CLOCK_REFINEMENT"
        )
        integrated_status = integrated_solver.Solve(b_plan)
        integrated_status_name = integrated_solver.StatusName(integrated_status)
        print(f"DIAGNOSTIC B_INTEGRATED_SOLVER_STATUS={integrated_status_name} wall={integrated_solver.WallTime():.2f}s")
        if integrated_status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise RuntimeError(
                f"Integrated Semester-B solver did not find an exact academic+weekly solution. "
                f"status={integrated_status_name}. A remains feasible. Seminars must be shared by A+B with prerequisites satisfied for both cohorts."
            )

        # Refine B-only starts around the actual shared/reused timetable.
        # Freeze academic choices and each B event's day before adding breaks.
        b_clock_model = b_plan.Clone()
        b_clock_model.ClearHints()
        for variable in list(b_semester_var.values()) + list(b_share.values()):
            b_clock_model.Add(variable == integrated_solver.Value(variable))
        selected_b_days = {(ei, sem, day) for (ei, sem, day, st), v in b_start.items()
                           if integrated_solver.Value(v)}
        for (ei, sem, day, st), v in b_start.items():
            if (ei, sem, day) not in selected_b_days:
                b_clock_model.Add(v == 0)
        b_groups = {}
        for ei, sem, day in sorted(selected_b_days):
            candidates = [(st, v) for (e, ss, d, st), v in b_start.items()
                          if (e, ss, d) == (ei, sem, day)]
            start = b_clock_model.NewIntVar(0, 288, f'b_clock_start_{ei}_{sem}')
            b_clock_model.Add(start == sum(st * v for st, v in candidates))
            old_start = next(st for st, v in candidates if integrated_solver.Value(v))
            b_groups.setdefault((sem, day), []).append((start, durations[ei], old_start))
        # Shared offerings are fixed constants; recurring B attendance already
        # has an exact-source-time candidate above, so do not add it twice.
        for item in timetable:
            i = course_index_by_id[str(item.course_id)]
            if integrated_solver.Value(b_share[i]):
                st = _time_to_slot(item.start)
                b_groups.setdefault((item.semester, item.day), []).append(
                    (st, _time_to_slot(item.end) - st, st))
        b_clock_model.Minimize(_clock_break_objective(b_clock_model, b_groups, 'b_comfort'))
        for index in range(len(b_plan.Proto().variables)):
            v = b_clock_model.GetIntVarFromProtoIndex(index)
            b_clock_model.AddHint(v, integrated_solver.Value(v))
        b_clock_solver = cp_model.CpSolver()
        b_clock_solver.parameters.max_time_in_seconds = 10.0
        b_clock_solver.parameters.num_search_workers = 8
        b_clock_status = b_clock_solver.Solve(b_clock_model)
        print(f"DIAGNOSTIC B_CLOCK_BREAKS status={b_clock_solver.StatusName(b_clock_status)}")
        if b_clock_status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            integrated_solver = b_clock_solver

        b_course_semesters = {i: integrated_solver.Value(b_semester_var[i]) for i in range(len(courses))}
        shared_courses = {i for i in range(len(courses)) if integrated_solver.Value(b_share[i])}
        repeated_courses = set(range(len(courses))) - shared_courses
        reused_continuation = {i for i in repeated_courses if b_course_semesters[i] == continuation_semester and i in recurring_indexes}
        new_openings = repeated_courses - reused_continuation
        scheduled = []
        for event_index, event in enumerate(events):
            course_index = event.course_index
            semester = b_course_semesters[course_index]
            if course_index not in repeated_courses:
                continue
            duration = durations[event_index]
            chosen = None
            for day in active_days:
                effective_window = lecturer_window(courses[course_index].lecturer, day)
                if effective_window is None:
                    continue
                ds, de = effective_window
                grid_slots = max(1, B_START_GRID_MINUTES // SLOT_MINUTES)
                candidate_starts = _b_candidates(event_index, semester, day, ds, de)
                for st in candidate_starts:
                    v = b_start.get((event_index, semester, day, st))
                    if v is not None and integrated_solver.Value(v):
                        chosen = (day, st)
                        break
                if chosen:
                    break
            if chosen is None:
                raise RuntimeError(
                    f"Integrated B internal placement error for course {courses[course_index].course_id} "
                    f"event={event.kind} semester={semester}."
                )
            scheduled.append((event_index, semester, chosen[0], chosen[1], duration))

        print(
            f"DIAGNOSTIC B_INTEGRATED_RESULT shared={len(shared_courses)} "
            f"new_openings={len(new_openings)} reused_continuation={len(reused_continuation)} attendance_events={len(scheduled)}"
        )
        print(
            "DIAGNOSTIC B_OBJECTIVE_PRIORITY="
            "MAX_SHARED_FIRST_THEN_PREREQUISITE_URGENCY "
            f"shared={len(shared_courses)}/{len(courses)} duplicates={len(new_openings)} reused_continuation={len(reused_continuation)}"
        )
        try:
            Path("timetable_debug.json").write_text(
                json.dumps({
                    "status": "B_FEASIBLE_BUILDING_FINAL",
                    "solver_version": "B_DIRECT_TIMESLOT_V2",
                    "start_grid_minutes": B_START_GRID_MINUTES,
                    "a_timetable": [item.to_dict() for item in timetable],
                    "b": {
                        "status": integrated_status_name,
                        "wall_time_seconds": integrated_solver.WallTime(),
                        "shared_course_ids": [str(courses[i].course_id) for i in sorted(shared_courses)],
                        "reused_continuation_course_ids": [str(courses[i].course_id) for i in sorted(reused_continuation)],
                        "new_opening_course_ids": [str(courses[i].course_id) for i in sorted(new_openings)],
                        "repeated_courses": [
                            {
                                "course_id": str(courses[i].course_id),
                                "name": courses[i].name,
                                "category": courses[i].course_category,
                                "lecturer": courses[i].lecturer,
                                "semester": b_course_semesters[i],
                                "logical_only": False,
                                "offering_status": "REUSED_RECURRING" if i in reused_continuation else "NEW_B_ONLY",
                                "new_opening_penalty": 0 if i in reused_continuation else REPEAT_COURSE_PENALTY,
                                "source_semester": course_semesters[i] if i in reused_continuation else None,
                            }
                            for i in sorted(repeated_courses, key=lambda j: (b_course_semesters[j], str(courses[j].course_id)))
                        ],
                        "physical_repeat_events": [
                            {
                                "course_id": str(courses[events[e].course_index].course_id),
                                "name": courses[events[e].course_index].name,
                                "event_type": events[e].event_type,
                                "semester": sem,
                                "day": day,
                                "start": _slot_to_time(st),
                                "end": _slot_to_time(st + dur),
                            }
                            for e, sem, day, st, dur in scheduled
                        ],
                    },
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as debug_exc:
            print(f"DIAGNOSTIC DEBUG_JSON_WRITE_WARNING stage=B error={debug_exc}")
        for i in sorted(repeated_courses, key=lambda j: (b_course_semesters[j], courses[j].course_id)):
            c = courses[i]
            kind = "REUSED_RECURRING" if i in reused_continuation else "NEW_B_ONLY"
            print(
                f"DIAGNOSTIC B_CHOICE semester={b_course_semesters[i]} kind={kind} "
                f"id={c.course_id} category={c.course_category} lecturer={c.lecturer} name={c.name}"
            )

        shared_ids = {courses[i].course_id for i in shared_courses}
        for item in timetable:
            if item.course_id in shared_ids and item.semester <= expected_last_semester:
                item.cohort_label = "A+B"

        for event_index, semester, day, start_slot, duration in scheduled:
            event = events[event_index]
            course = courses[event.course_index]
            timetable.append(
                ScheduledCourse(
                    course_id=course.course_id,
                    name=course.name,
                    lecturer=course.lecturer,
                    year=course.year,
                    course_type=course.course_type,
                    course_category=course.course_category,
                    credits=course.credits,
                    weekly_hours=course.weekly_hours,
                    lecture_hours=course.lecture_hours,
                    tutorial_hours=course.tutorial_hours,
                    calculation_status=course.calculation_status,
                    prerequisites=course.prerequisites,
                    parallel_requirement=course.parallel_requirement,
                    semester=semester,
                    semester_name=(
                        f"שנה {degree_years + 1} - סמסטר 1"
                        if semester == continuation_semester
                        else _semester_name(semester, semesters_per_year, expected_last_semester)
                    ),
                    semester_status=("CONTINUATION" if semester == continuation_semester else "ON_PLAN"),
                    event_type=event.event_type,
                    event_duration=event.duration_hours,
                    day=day,
                    day_hebrew=DAY_NAMES_HE[day],
                    start=_slot_to_time(start_slot),
                    end=_slot_to_time(start_slot + duration),
                    cohort_label="B",
                    cohort_start_semester=2,
                    offering_status="REUSED_RECURRING" if event.course_index in reused_continuation and semester == continuation_semester else "NEW_B_ONLY",
                    source_semester=course_semesters[event.course_index] if event.course_index in reused_continuation and semester == continuation_semester else None,
                )
            )

    timetable.sort(
        key=lambda item: (
            item.semester,
            day_rank[item.day],
            item.start,
            item.course_id,
            item.event_type,
        )
    )

    return timetable
