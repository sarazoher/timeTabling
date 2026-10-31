# Timetable V8: share priority and audit

* Changed B's recurring-continuation cost when a same-year A+B share is available from 0.5× to 1× the standard repeat cost. This removes an incentive to choose semester 10 rather than a feasible shared offering. It does **not** force sharing when hard constraints make it infeasible.
* Added read-only `validate_timetable.py`, run with `python validate_timetable.py timetable_backend/timetable.json`. It reports zero-minute breaks, lecturer overlaps, core/student overlaps, non-shared seminars, and B continuation courses that may have been shareable.
* The auditor is a diagnostic, **not** a formal proof of every prerequisite/parallel constraint; those require original catalog and solver context.
* Clock refinement still fixes weekdays and is limited by its time budget. A future solver improvement should permit weekday swaps to obtain better breaks.
* Full end-to-end timetable generation was not run; only compilation and a diagnostic run against the bundled timetable were performed.
