# Update: department profiles + AND/OR prerequisites

## Install
1. Stop the app. Copy these files over your project folder (same paths).
2. Delete the three old CS catalog files from `timetable_backend/`
   (`Elective Courses .xlsx`, `General Courses .xlsx`, `Seminars.xlsx`);
   they now live in `timetable_backend/departments/cs/`.
3. Your `schedule_config.json` and `courses.xlsx` are NOT included, so your
   settings and catalog are untouched. Start the app, open the dashboard and
   choose the department under "Department rules in use" (CS -> מדעי המחשב).
   Without a choice the app uses **Generic**: no department's special rules.
4. Run the tests: `python -m unittest discover -s tests` (121 tests).

## What changed
* Requirement cells: `+` / `וגם` = AND, `/` / `או` = OR, OR binds tighter:
  `A + B / C` = A AND (B OR C). OR is enforced in every solver stage.
* Department profiles (`timetable_backend/departments/<id>/profile.json`):
  every department-specific rule (name aliases, category catalogs/overrides,
  retired IDs, name tokens) is declared there and shown on the dashboard and
  at the top of each backend run. Nothing is applied unless that profile is chosen.
* Student export header shows the active department instead of "מדעי המחשב".
