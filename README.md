# University Timetable Scheduler

A Python application for generating university course timetables from Excel data, department availability, and academic constraints. The interface supports Hebrew and English, with Hebrew as the default.

This guide documents the supplied timetable application, including its Stage 1 interval-model performance fix.

## Accounts and sign-in

Start the app with the Windows commands below, then open http://127.0.0.1:5000/. The app now opens the sign-in screen. Choose **יצירת חשבון / Create an account**, enter your name, email and a password of at least 10 characters, and confirm the password. Registration signs you in immediately. Use **יציאה מהחשבון / Sign out** in the top bar when finished.

Each account accesses the same department workspace: courses, settings, generated timetables, and the scheduling assistant are shared. All registered users currently have the same editing permissions. This version does not introduce separate personal timetables or administrator/student roles. Registration is open to anyone who can reach this app. Email is a sign-in identifier. Email verification is not implemented. Password-reset email delivery is available after configuring SMTP; local recovery works without email.

Accounts persist in `.instance/accounts.sqlite3` in the project root. A random session signing key is generated in `.instance/secret.key` on first launch. Keep this folder on your own computer when upgrading the project; back it up privately with your department data. Do not distribute it with project ZIPs. The supplied archive contains no accounts, passwords, signing keys, or active sessions. There are no default credentials.

Passwords use Werkzeug scrypt hashing. Sessions expire after eight hours, and sign-out revokes the server session. All timetable pages, exports and modifying routes require sign-in; POST forms include CSRF protection. Repeated login attempts are temporarily limited. Existing standalone HTML files that were already downloaded remain standalone documents; signing out cannot revoke a copy someone downloaded.

Optional configuration:
- `TIMETABLE_AUTH_DIR`: absolute path to an existing persistent accounts directory when upgrading into a new project folder.
- `TIMETABLE_SECRET_KEY`: a deployment-managed random secret of at least 32 characters; otherwise the local key file is used.
- `TIMETABLE_HTTPS=1`: secure cookies for an HTTPS deployment. Leave unset for the default local HTTP address.

The app still binds to `127.0.0.1` by default. Account support does not publish it online. This remains a shared workspace with file-based timetable data, so coordinate configuration edits and generation between users.

## Forgot your password?

### Reset now on your Windows computer

Update the application files in your existing project folder, keeping its `.instance` folder. Do not delete or replace your account database. If extracting into a new folder, copy your existing `.instance` folder there first, or set `TIMETABLE_AUTH_DIR` to its original absolute location. Stop and restart the app after updating.

In PowerShell, in the project folder containing `requirements.txt`, run:

```powershell
.\.venv\Scripts\python.exe reset_password.py
```

Enter the registered email, then the new password twice. Password typing is hidden. Use 10–256 characters. The command updates only that account's password and revokes its sessions and outstanding reset links. It does not delete accounts, courses or timetables. Local recovery is an operator command: it requires access to the computer and account database, and is not exposed as an unauthenticated web reset.

### Reset by email

The sign-in screen now includes **שכחת סיסמה? / Forgot password?**. When SMTP is configured, enter the account email. The email contains a random single-use link that expires after 30 minutes. Only its hash is stored in SQLite. Requesting a newer link invalidates the older link. Opening a link does not consume it; successfully submitting a new password does. Sign in again after resetting; existing sessions on all devices are revoked. Known and unknown addresses receive the same response, and requests are rate-limited.

Set these environment variables in the app server's environment before starting it:

| Variable | Value |
| --- | --- |
| `TIMETABLE_SMTP_HOST` | Your email provider's SMTP hostname |
| `TIMETABLE_SMTP_PORT` | Usually `587` for STARTTLS or `465` for implicit TLS; use your provider's settings |
| `TIMETABLE_SMTP_SECURITY` | `starttls` (default) or `ssl` |
| `TIMETABLE_SMTP_USER` | SMTP username, if authentication is required |
| `TIMETABLE_SMTP_PASSWORD` | SMTP credential supplied by your provider |
| `TIMETABLE_SMTP_FROM` | Sender address permitted by your provider |
| `TIMETABLE_PUBLIC_URL` | The trusted base address users visit, e.g. `https://your-department.example`; for local testing, `http://127.0.0.1:5000` |

Links use the configured base URL, never the request Host header. Use HTTPS for a hosted deployment. Keep SMTP credentials outside source control and project ZIPs. If email delivery is not configured, the recovery page explains local recovery instead of claiming to send a link. SMTP failures are logged without passwords or tokens; no actual email delivery is tested by the automated suite.

The database gains a `password_resets` table automatically on startup; existing users and their passwords are preserved during the upgrade.

## 1. Install and run

Extract this archive and open a terminal in the folder containing `requirements.txt`, `timetable_frontend`, and `timetable_backend`.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python timetable_frontend/app.py
```

If PowerShell blocks activation, run the environment's Python directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe timetable_frontend/app.py
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python timetable_frontend/app.py
```

The app opens the browser automatically. If necessary, open:

http://127.0.0.1:5000/

Core dependencies are Flask, Flask-Babel, pandas, openpyxl, and OR-Tools. Use your own virtual environment rather than a bundled environment from an older archive.

## 2. Generate a timetable

1. Open the dashboard and review the loaded course catalog.
2. Upload a course Excel workbook or add courses manually when needed.
3. Open **Day & Hour Adjustment** (`/days`) to set the degree length, enabled teaching days, and each day's allowed start/end time.
4. Enable semester-B intake if the schedule must support both starting cohorts.
5. Review the built-in constraints and any natural-language rules.
6. Click **Build Timetable**. The frontend saves the configuration, runs the backend, and displays the result on `/calendar`.

The regular degree horizon is calculated as:

```text
degree_years × semesters_per_year
```

The newer dashboard separates day/hour adjustments from the main page. The older multilingual archive uses an earlier setup-page flow.

## 3. Project structure

| Path | Purpose |
| --- | --- |
| `requirements.txt` | Application dependencies |
| `timetable_frontend/app.py` | Flask application, dashboard, imports, and exports |
| `timetable_frontend/templates/` | Page templates |
| `timetable_frontend/static/` | Styles and browser assets |
| `timetable_frontend/course_translations.json` | English catalog translations indexed by course ID |
| `timetable_frontend/translations/` | Hebrew and English interface translations |
| `timetable_backend/main.py` | Backend entry point |
| `timetable_backend/excel_reader.py` | Excel parsing and course-hour calculations |
| `timetable_backend/scheduler.py` | Semester planning and weekly scheduling |
| `timetable_backend/models.py` | Course data models |
| `timetable_backend/output.py` | Output generation |
| `timetable_backend/courses.xlsx` | Main course workbook |
| `timetable_backend/schedule_config.json` | Saved scheduling settings |

## 4. Course data

The course workbook provides course identifiers, names, study years, lecturers, credits, weekly hours, lesson types, prerequisites, and parallel requirements.

Important source column names include:

| Column | Meaning |
| --- | --- |
| `מס.שעור` | Course ID |
| `שם השיעור` | Course name |
| `שם המרצה` | Lecturer |
| `נ.זיכוי` | Credits |
| `ש"ש` | Source weekly hours |
| `סוג שיעור` | Lesson type, including whether lecture and tutorial are combined |
| `תיאור ד.קדם` | Prerequisite requirements |
| `תיאור ד.מקבילה` | Parallel requirements |
| `קטגוריית קורס` | Course category, when supplied |

Lesson type and course category serve different purposes: the lesson type affects the hour calculation, while the category affects overlap rules and display colors.

Supported categories are חובה (required), כללי (general), בחירה (elective), סמינר (seminar), and פרויקט (project). When the category column is absent, the application infers the category from the available course data, defaulting to required.

Keep lecturer names consistent across rows. Different spellings can cause the solver to treat one lecturer as multiple people. Preserve distinct course IDs unless a duplicate has been confirmed.

Prerequisite parsing preserves course names containing C++ by splitting only on a plus sign surrounded by spaces.

### Hour calculation in the supplied newer code

For source weekly hours `S` and credits `C`, the reader first calculates:

```text
W = 2 × S
```

For lesson types explicitly identified as lecture plus tutorial:

```text
lecture hours = 2 × C − W
tutorial hours = 2 × (W − C)
```

Other lesson types receive `W` lecture/meeting hours and zero tutorial hours. Negative calculated durations are invalid source data and stop generation. A lecture-plus-tutorial row that calculates zero tutorial hours is flagged for review.

These formulas describe the supplied implementation. They are not a universal university credit rule; review flagged rows against the approved course data.

## 5. Scheduling constraints

### Hard constraints

- A prerequisite must occur in an earlier semester than its dependent course.
- A parallel requirement may occur in the same semester or an earlier semester.
- A course's lecture and tutorial belong to its selected semester.
- A lecturer cannot teach overlapping events.
- Events must fit within enabled department days and allowed hours, including applicable lecturer restrictions.
- Course-category overlap rules must be respected.

### Course-category overlap policy

| Combination | Policy |
| --- | --- |
| Required + any course | Forbidden |
| General + any course | Forbidden |
| Project + project | Allowed, subject to lecturer constraints |
| Project + another category | Forbidden |
| Elective + elective | Allowed with a penalty |
| Elective + seminar | Allowed with a penalty |
| Seminar + seminar | Allowed with a penalty |

Lecturer conflicts remain forbidden even when the course-category combination permits overlap.

### Soft preferences

The solver favors a convenient schedule, including balanced loads, sharing offerings between cohorts, fewer repeated offerings, and compact student days. It uses a five-minute time grid for break preferences:

- Zero-minute breaks receive a penalty.
- Breaks of 5, 10, 15, 20, 25, or 30 minutes are preferred.
- Breaks longer than 30 minutes receive increasing penalties.

Unnecessary semester gaps along prerequisite chains are also penalized. Soft preferences do not override hard constraints.

## 6. Semester-B intake

When enabled, cohort A starts in semester 1 and cohort B starts in semester 2. The semester planner considers both cohorts before fixing A’s semesters. Existing offerings can be shared within the catalog study year whenever B’s own prerequisites and parallel requirements are satisfied. New B-only repeats retain the shifted study-year window. The weekly stages then verify day/time placements and lecturer conflicts.

- Shared offerings are labeled **A+B**.
- Repeated offerings for late starters are labeled **B**.
- Repeats are minimized rather than copying the entire degree one semester later.
- Prerequisite and parallel requirements apply to each cohort's progression.
- One continuation semester is added for cohort B; it is not an extra regular semester for cohort A.

The newer solver uses staged planning. A feasible semester plan does not guarantee feasible weekly placements: department hours, lecturer availability, and forbidden overlaps can still prevent generation.

## 7. Natural-language scheduling assistant

The newer archive includes a Claude integration that converts department requests into structured scheduling rules. For example:

```text
Professor Sela cannot teach on Tuesdays.
Try to make Wednesday lighter for second-year students.
```

Configure the API key in the same terminal used to start the app.

### Windows PowerShell

```powershell
$env:ANTHROPIC_API_KEY="your-key-here"
python timetable_frontend/app.py
```

### macOS / Linux

```bash
export ANTHROPIC_API_KEY="your-key-here"
python timetable_frontend/app.py
```

The model can be overridden using `CLAUDE_MODEL`; choose a model available to your Anthropic account. An API key is required for the assistant, not for ordinary manual timetable generation.

Review interpreted rules before generating. Use the course upload or manual-add controls to maintain the catalog. Claude produces structured instructions; the OR-Tools solver performs scheduling.

## 8. Timetable download

The dashboard and calendar include **הורדת מערכת השעות** (Download timetable). The button downloads `timetable.html`, a standalone Hebrew file that opens offline in a browser.

- Select cohort A, cohort B, or all offerings.
- Each academic year is one horizontal row of semesters; academic years are stacked vertically. Column widths are calculated from the actual overlapping meetings and title lengths. Busier days and semesters receive more width. On narrow screens, each year scrolls horizontally to preserve readable cards and keep its semesters in one row. Review the complete degree by study year and semester. Each day lists exact start/end times, course names, lecturers, meeting type and cohort.
- Overlapping meetings appear in separate horizontal lanes. The layout is a readable daily agenda; vertical spacing is not a proportional time axis.
- Click **הדפסה / שמירה כ־PDF** and choose Save as PDF in the browser print dialog. The print CSS uses A3 landscape pages with one horizontal row of semesters per academic year. Check the print preview for the selected paper size.
- Semester 10 appears in B and combined views, with day/time cards. Reused recurring courses are labelled as existing offerings with a younger cohort. A remains a nine-semester plan. B semester 1 is omitted because that cohort has not enrolled yet.

This export reads the current timetable only. It never runs the solver, generates an image, or requires extra dependencies. It is created on demand through `/export/timetable` (code: `timetable_frontend/timetable_export.py`). The HTML contains its own styles and cohort-selection script, with no external assets.

The download endpoint, displayed buttons, escaping, overlap grouping and missing-timetable handling were checked. Browser print rendering could not be exercised in this environment, so check the browser print preview before distributing a PDF.

## 9. Outputs and downloads

Depending on the generation stage and enabled export path, project outputs include:

| File | Purpose |
| --- | --- |
| `courses.json` | Parsed course catalog |
| `courses_calculated.xlsx` | Calculated course hours for review |
| `unresolved_requirements.json` | Requirements that could not be resolved |
| `semester_plan.xlsx` | Planned course semesters |
| `timetable.json` | Generated timetable and diagnostic data |
| `timetable.xlsx` | Spreadsheet output where enabled |

Export behavior changed across historical versions. The later documentation describes on-demand image export, with overlapping events drawn beside each other. Normal generation should display the calendar without preparing a presentation image. Do not assume that every historical Excel or image download button is present in the current UI.

Generated files already included in an archive may belong to an earlier run. Generate again after changing course data or constraints.

## 9. Hebrew and English support

The interface uses Flask-Babel. Hebrew uses right-to-left layout; English uses left-to-right layout. Language switching is available through the UI and `/language/he` or `/language/en`.

Catalog content is translated separately using `course_translations.json`, indexed by course ID. New or changed courses require corresponding English entries; this file does not update automatically.

After adding or changing interface text:

```bash
cd timetable_frontend
pybabel extract -F babel.cfg -o messages.pot .
pybabel update -i messages.pot -d translations
# Edit the .po files to provide translations.
pybabel compile -d translations
```

The app reads compiled `.mo` files, so editing `.po` files alone is insufficient.

## 10. Troubleshooting

| Problem | What to check |
| --- | --- |
| Missing Python package | Install the root `requirements.txt` using the Python environment that runs the app. |
| Browser does not open | Open `http://127.0.0.1:5000/` manually. |
| Claude API key required | Set `ANTHROPIC_API_KEY` before starting Flask in that terminal, then restart the app. |
| Model request fails | Check API access and the configured `CLAUDE_MODEL`. |
| Course-hour review warning | Compare source weekly hours, credits, and lesson type with the official course information. |
| Invalid negative duration | Correct the source data; the solver cannot schedule negative meeting hours. |
| Unresolved prerequisite | Check course IDs/names and `unresolved_requirements.json`. |
| No feasible timetable or timeout | Review active days/hours, lecturer availability, prerequisite chains, cohort-B repeats, and course categories. Inspect backend diagnostics to locate the failing stage. |
| Category displayed incorrectly | Check `קטגוריית קורס` in the imported workbook and regenerate. |
| English catalog entry missing | Add the course ID and its translated fields to `course_translations.json`. |
| Edited translation not visible | Compile translations and restart the application. |

To run only the backend from the project root:

```bash
cd timetable_backend
python main.py
```

The backend uses the existing workbook and saved configuration. Use the frontend for normal configuration and timetable generation.

## Solver rules and validation

Semester planning considers both cohorts before A's semesters are fixed. A useful first semester is preferred, then fewer new openings, then balance and timing. Existing A/B offerings are shared when B's prerequisites and parallel requirements permit it.

### B continuation and recurring offerings

Semester 10 (year 4 semester A for the supplied three-year, three-semesters-per-year configuration) is displayed for cohort B only.

- If a course already has a regular phase-A offering in semesters 1, 4, or 7, B can join its next recurring offering with younger students. The continuation card keeps the exact source day/start/end and is marked `REUSED_RECURRING`. Its new-opening penalty is zero.
- If no matching recurring offering exists and a final-year course is opened specifically for B in semester 10, it receives a normal new-opening penalty and must be physically scheduled. It is marked `NEW_B_ONLY`.
- Availability, lecturers, durations, prerequisites, and B student overlap constraints remain enforced. A's own courses are not moved into semester 10.
- Recurrence assumes the regular curriculum offerings repeat annually in the same semester phase and keep their current source times. This is the existing-offering model requested for younger cohorts; it is not an independently verified future-year university calendar.

### Semester and day preferences

Enrolled semesters are preferred nonempty. Active teaching days are preferred occupied, and B day loads receive a balancing objective. Source phase-A offerings are also spread during Stage 1 so B's recurring completion meetings can use the available days. These are soft preferences: a hard lecturer restriction, insufficient meetings, or prerequisites can make an empty day or semester unavoidable. B's first semester is not an enrolled semester and is not displayed in the student export.

The Stage 1 interval model retains a feasible weekly witness. Stage 2 validates it against all compiled hard rules before using it as an optimisation starting point or timeout fallback. Export generation never runs the solver.

### Earlier reference result (before clock-time refinement)

With the supplied workbook, three years, three semesters per year, Monday/Thursday/Friday, 16 clock hours and B intake enabled:

- A has courses in semesters 1–9; B has courses in semesters 2–10.
- Each enrolled semester of both cohorts uses all three enabled days.
- 33 courses share the current A+B offering; three use recurring existing offerings in semester 10; only two additional course openings are penalized (Calculus 1 and Linear Algebra 1).
- Calculus 2 is A+B in semester 3. Both project alternatives are A+B in semester 9.
- Semester 10 contains Organizational Behavior, Learning How to Learn, and Computer Organization and Assembly Language. The latter has separate lecture and tutorial meetings, resulting in four continuation cards.
- All 38 courses have physical attendance events for both cohorts. No logical-only continuation course remains. The export contains 64 displayed events including recurring attendance cards; recurring cards do not mean newly created physical sessions.
- Generated events passed department-window, duration, lecturer/category conflict, lecture/tutorial semester, and both cohorts' academic progression checks. Reused continuation cards match their source offering's day/time.
- Twenty regression tests passed, including zero-cost recurring reuse, penalized new continuation meetings, day spreading, hard-rule rejection of invalid witnesses, and timeout fallback.

Run checks from the project root:

```bash
python -m unittest discover -s tests -v
```

The B solver returned OPTIMAL for the tested fixed A calendar. This does not prove global optimality across every possible combined A/B weekly calendar. Sharing counts and layouts can vary by solver choices and inputs.

### Presentation

The timetable HTML export uses the same category palette as the app: required blue, elective green, seminar pink, project yellow, and general purple. Semesters of each year remain in one horizontal table row; years stack vertically. Print CSS targets A3 landscape. Browser print rendering was not available in this environment, so check the print preview before distributing a PDF.

Course data, hour calculations and saved department settings are unchanged. The existing course-hour review warning for course 8500036 remains visible.

### Content-aware export widths

Day widths are based on simultaneous meeting lanes and course title lengths; semester widths are proportional to their total day widths. Successive meetings reuse a lane. Each cohort view is measured independently from its actual events. Screen layouts preserve readable minimum widths through horizontal scrolling; print uses the same proportions fitted to the page. Layout calculations never modify course assignments or times. Build Timetable runs OR-Tools using the catalog and saved constraints; Download formats the most recently saved solver output. The supplied HTML is a snapshot and must be downloaded again after rebuilding the timetable.

### Preferred start times

The solver prefers quarter-hour clock starts (:00, :15, :30, :45), then earlier starts within the chosen day. The five-minute domain remains available when exact availability or other hard constraints require it. A final clock optimization of up to twenty seconds preserves each A meeting's semester and day, all hard rules, and its weekly footprint or improves that footprint. If this optimization times out, the verified timetable is retained; preferred times are not guaranteed for every input. B reuse follows the actual source times, and B-only placement uses the same clock preference. The export displays the solver's exact times without rounding.

The 22 regression tests include moving an unnecessary 08:05 start to 08:00 and retaining 08:05 when an exact availability window requires it. Regenerate with **Build Timetable** after installing this version; existing downloads are snapshots.

Earlier clock-only full run: 64 attendance events, all quarter-hour starts; 33 shared courses, two recurring continuation courses, three new B-only openings. Clock refinement and the fixed-A B solver both returned OPTIMAL; full A optimization returned FEASIBLE. Sharing choices can vary between runs. Availability, duration, lecturer/category conflicts, and A/B academic progression checks passed.

### Short breaks and first-class starts

Final A timing refinement now minimizes first-class start times before break preferences. It never delays the first class relative to the selected timetable. Breaks of 5–30 minutes between consecutive non-overlapping class groups carry no break penalty; zero-minute breaks are allowed with a penalty when needed. Parallel alternative classes form one group, so no break is requested between simultaneous alternatives. The original complete weekly constraints remain active, and semesters and teaching days are fixed during refinement.

B-only meetings receive a separate refinement around fixed shared offerings; academic choices and teaching days stay fixed, and recurring offerings retain their exact source times. B uses a 15-minute base start grid with additional five- and ten-minute break candidates beside existing offerings, plus exact window-end candidates. A timeout can retain a verified incumbent with imperfect comfort; diagnostics explicitly report an unfinished A clock refinement. No export times are rounded.

Run **Build Timetable** again after restarting the updated app. Previously saved schedules and downloaded HTML files do not update automatically.

Earlier short-break validation (15-minute-target version): all 24 tests passed. The full run produced 65 attendance events with no off-quarter-hour starts. Counting each cohort's agenda separately, there were 40 fifteen-minute breaks and seven back-to-back transitions. All days beginning in the 08:00 hour began at 08:00. A and B final time refinements returned OPTIMAL for their fixed-day models. Department availability, exact durations, lecturer/category conflicts and both cohorts' academic progression checks passed. These counts describe this run, not a guarantee for every input.

### Electives and seminars: overlap only as a fallback

Elective/elective, elective/seminar and seminar/seminar conflicts are allowed with a penalty proportional to the concurrent five-minute slots. A weekly scheduling prefers fewer such conflicts before empty-day preferences, footprint compaction and clock alignment. B counts conflicts among B-only, shared and recurring attendance, with an overlap penalty below the main new-opening penalty. Its academic progression and opening objectives remain active. Final time/break refinements cannot increase the selected schedule's optional-course overlap score. Project/project treatment and all mandatory/general/lecturer hard restrictions remain unchanged.

This is a soft preference: limited availability, academic requirements and search time can still leave conflicts. It is not a proof that every remaining conflict is unavoidable across all possible semester plans. Tests cover separate elective/elective, elective/seminar and seminar/seminar classes when there is room, plus necessary overlap in a tight window.

Latest overlap-preference validation: 26 tests passed; the supplied full run produced 63 attendance events with zero elective/seminar overlap pairs for either cohort and zero off-quarter-hour starts. Availability, duration, lecturer/category constraints and A/B progression checks passed. This is a tested result, not a guarantee for every input.

Account-release validation: 40 tests passed across the six test modules, including 14 account/security tests and the existing scheduling and export tests. Hebrew and English pages were checked through Flask requests; browser visual rendering was not exercised.

Password-recovery release: 52 tests passed across seven test modules, including 12 recovery tests. SMTP transport was simulated; no real reset emails were sent during validation. Existing accounts, password hashes, and timetable data are preserved by the startup migration.

## Current rule update: shared seminars and minimum soft break

When semester-B intake is enabled, every seminar is a single shared A+B offering. This is enforced in both joint semester planning and B weekly attendance planning. The cohorts attend the same semester, day and start/end times; a separate B-only or recurring-younger-cohort seminar does not satisfy this rule. Prerequisites remain hard for both cohorts. If these requirements cannot be met, generation reports failure rather than silently splitting a seminar. With B intake disabled, the usual A-only seminar scheduling applies.

Breaks remain a soft preference. Five through thirty minutes receive no break penalty; a zero-minute transition costs six unscaled comfort units, and each additional five-minute slot beyond thirty minutes adds one. Other timing preferences can still prefer a quarter-hour start when both choices provide an acceptable break. No break is added before the first class. Back-to-back sessions remain permitted when constraints or the selected day plan leave no room. The B candidate grid adds five- and ten-minute break options beside existing offerings to its 15-minute base grid; final timing refinement uses five-minute precision.

Validation of this update: 56 regression tests passed. A full curriculum run produced 66 physical events with 32 shared courses. All five seminars have exactly one A+B offering; availability, durations, lecturer/category conflicts and both cohorts’ prerequisites were checked. Soft comfort remains subject to solver time limits.
