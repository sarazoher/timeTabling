# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Main audience now: poster day.** Visitors and judges at the college's project poster day watch a live demo, often on a laptop, sometimes on a phone. They see the app once, for a few minutes, and must understand what it does and why it is clever.
- **Real users later:** a department's schedule maker (coordinator or secretary) who builds a whole degree's timetable about once a semester and forgets the app in between. Every screen must explain itself on a cold return.

## Product Purpose

TimeTabling (in Hebrew "מערכת חכמה לתכנון תכניות לימודים ושיבוץ קורסים") turns one Excel course file into a complete, valid timetable for a whole degree: which semester each course is taught in, then the day and hour of every lecture and tutorial. It keeps every hard rule (prerequisites earlier, no lecturer teaching twice at once, only inside the department's teaching hours, required courses never overlapping) and prefers comfortable days. Changes are requested in free language to an assistant (Claude), which proposes a rule that the user approves before it is applied.

Success on poster day: a visitor grasps "one file in, a full legal timetable out, changes in plain words" within a minute, and believes something genuinely smart happens behind the scenes.

## Positioning

Built for the rules of one degree rather than general-purpose scheduling software (UniTime and similar are complex, need technical rule setup, and don't know the degree's own rules). The two-stage solve (semester first, then day and hour) is what makes a full degree solvable in about a minute and a half. Free-language changes need no technical knowledge, and nothing changes until the user approves.

## Operating Context

- Flow: upload the course file → check the data → settings (years, semesters, teaching days and hours) → build (about a minute) → timetable → change things through the assistant → rebuild → download a file for students (HTML/PDF).
- One timetable per account. A semester-B intake (second cohort) can share courses with cohort A.
- Runs locally on the team's laptop for the demo (Flask). Builds run in the background with a progress page.

## Capabilities and Constraints

- Hebrew is the default and the interface is right-to-left; English is the second language, switchable anywhere.
- Laptop first; every page must also work at phone width (390 px) without sideways scrolling.
- Terminology: "מערכת השעות" (the timetable), "קובץ הקורסים", "הגדרות", "כללים" (rules; built-in hard rules and preferences, plus the user's own rules from the assistant), "מחזור ב׳" (semester-B intake). Users never see solver jargon ("פותר", "solver", "infeasible", "CP-SAT") or the word "department/מחלקה" for the account.
- The assistant needs an Anthropic API key on the machine running the app.
- Undecided: classroom assignment, lecturer self-service availability forms, student elective preferences, installation on the college server (listed as future steps on the poster).

## Brand Commitments

- Name: TimeTabling. Team: Sara Abd Alkader and Wasna Qadan.
- Colours come from the poster and the college logo (plum and purple family); course-category colours and success/warning/error colours are fixed and not part of the brand palette.
- Personality the team chose: smart and impressive. It should show that something clever happens behind the scenes, while staying plain-spoken and calm in its wording.

## Evidence on Hand

- Poster results on the computer-science department's data (3 teaching days a week, Monday and Thursday 17:00–22:00, Friday 08:00–14:00 = 16 weekly hours): 38 courses, 15 lecturers, 65 weekly meetings scheduled, 0 lecturer clashes, 32 of 38 courses shared between cohorts A and B, about 100 seconds total.
- Validity checks on the built timetable: 0 lessons outside teaching hours, 0 lecturer clashes, 0 overlapping required courses, 0 courses pushed past the degree, 0 unrecognised prerequisites.
- Bundled sample data: `timetable_frontend/sample/sample_courses.xlsx` (the CS file).
- There are no testimonials, user counts or external endorsements; do not invent any.

## Product Principles

1. Show the cleverness, explain it plainly: the impressive part (rules kept, a whole degree in a minute) is made visible in words anyone understands.
2. Nothing changes without the user's approval.
3. One obvious next step on every screen; the guided setup is the core of the product, not a one-time onboarding.
4. Hebrew first, never an afterthought: RTL layouts are designed, not mirrored.
5. Honest results: when no timetable fits, say which rule or data is in the way.

## Accessibility & Inclusion

- Keyboard reachable menus and controls, visible focus, a skip link on every signed-in page, WCAG AA contrast.
- Motion must respect reduced-motion settings.
