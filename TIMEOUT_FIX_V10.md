# V10 — 3-minute timeout mitigation

V9 ran up to three *complete* solves sequentially, each with Stage-2 and integrated cohort-B optimization, but the frontend subprocess has a hard 180-second timeout. This caused a runtime timeout rather than proof of infeasibility.

V10 runs **one complete solve per button click**, preserving the original CP-SAT hard constraints, existing B-sharing objective, and soft breaks. It removes repeated solves, not constraints. A single solve may still time out due to model complexity; consult timetable_debug.json if that occurs.

**Important:** This is a runtime fix, not proof that every shareable course is combined. A true joint A+B optimization is still required to guarantee the best sharing choices.
