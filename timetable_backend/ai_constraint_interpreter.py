from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import uuid
from typing import Any


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-5"

DAY_NAMES = [
    "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"
]


def claude_is_configured() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("Claude did not return valid JSON.")
        return json.loads(match.group(0))


def _system_prompt(courses: list[dict], active_rules: list[dict], semesters_per_year: int, recent_chat: list[dict] | None = None) -> str:
    catalog = [
        {
            "course_id": c.get("course_id"),
            "name": c.get("name"),
            "lecturer": c.get("lecturer"),
            "year": c.get("year"),
        }
        for c in courses
    ]

    return f"""You are the natural-language scheduling assistant inside a university timetable application.
The department user can write requirements in Hebrew, Arabic, or English, in completely free natural language.
Your job is to have a short natural conversation until the scheduling intent is clear, then propose solver rules. The user does NOT need to know the internal rule format. The solver is NOT called during this conversation; the UI will ask the user to explicitly apply your proposal later.

COURSE CATALOG (use this to recognize existing entities. IMPORTANT: the user MAY explicitly create new courses or lecturers; absence from this catalog is NOT an error when they are asking to add/create something):
{json.dumps(catalog, ensure_ascii=False)}

ACTIVE RULES (the user may refer to, change, or cancel them):
{json.dumps(active_rules, ensure_ascii=False)}

RECENT CHAT (use this to understand follow-up answers and corrections):
{json.dumps((recent_chat or [])[-10:], ensure_ascii=False)}

The degree has {semesters_per_year} semesters per academic year.
Canonical day names are: {DAY_NAMES}.
Times must be HH:MM.

Return ONLY one JSON object with this shape:
{{
  "assistant_message": "short friendly explanation in the SAME LANGUAGE as the user's message",
  "needs_clarification": false,
  "clarification_question": null,
  "remove_rule_ids": [],
  "clear_all": false,
  "rules": [],
  "data_actions": []
}}

DATA MANAGEMENT (does NOT invoke the solver):
The assistant is also a department data assistant, not only a constraint assistant. If the user explicitly asks to add/create a new course or lecturer, edit course data, or remove course data, help them conversationally. Do NOT reject a new name merely because it is absent from the catalog.

Currently supported data action:
{{
  "action": "add_course",
  "course_id": null,
  "name": "Introduction to Computers",
  "year": 1,
  "lecturer": "Sarah",
  "credits": 3.0,
  "weekly_hours": 2.0,
  "course_type": "הרצאה",
  "prerequisites": null,
  "parallel_requirement": null
}}
A lecturer does not need a separate record: a new lecturer is created naturally by assigning their new name to a course.
For add_course, collect the required details conversationally: name, study year, lecturer, credits, weekly hours, course type, and course_category (exactly one of חובה, בחירה, סמינר, פרויקט, כללי; never guess it from the course name, ask). course_id may be null; the application will generate a unique ID. prerequisites, parallel_requirement, original_semester, and flexibility are optional. flexibility may be auto, fixed, or flexible. If important required details are missing, set needs_clarification=true and ask for the missing details. Once complete, put the action in data_actions, summarize it, and wait for explicit confirmation. Do not create solver rules for a pure data-management request.

Supported solver rule forms are intentionally expressive:

1) PLACEMENT FILTER — applies to all matching events unless event_types narrows it.
{{
  "kind": "placement_filter",
  "description": "human readable rule",
  "hard": true,
  "weight": 50,
  "scope": {{
    "course_ids": [],
    "course_names": [],
    "lecturers": [],
    "years": [],
    "event_types": []
  }},
  "allowed_days": [],
  "forbidden_days": [],
  "earliest_start": null,
  "latest_end": null,
  "allowed_semesters": [],
  "forbidden_semesters": []
}}
Examples: lecturer unavailable Tuesday; a course only Monday/Wednesday; no second-year classes after 18:00; professor prefers starting after 10:00; a course must be semester 4.
For preferences use hard=false and a sensible weight (10-100). Words such as can't/must/never/only => usually hard. Words such as prefer/try/avoid/if possible => soft.

2) COURSE RELATION
{{
  "kind": "course_relation",
  "description": "...",
  "hard": true,
  "weight": 50,
  "left_course_id": 123,
  "right_course_id": 456,
  "relation": "different_day"
}}
Supported relation values: different_day, before_semester, after_semester, same_semester, different_semester.

3) DAY LOAD
{{
  "kind": "day_load",
  "description": "...",
  "hard": false,
  "weight": 20,
  "day": "Wednesday",
  "scope": {{"years": [], "course_ids": [], "lecturers": []}},
  "max_hours": null,
  "minimize": true
}}
Use max_hours for statements such as 'at most 4 teaching hours on Wednesday'. Use minimize=true for 'make Wednesday lighter'.

Interpret broad free text by combining as many rules as needed. Do not force the user to phrase things like a form. If an important detail is vague (for example what "lighter" means when several interpretations are plausible), prefer needs_clarification=true and ask a concise conversational question. Once the intent is sufficiently precise, return the proposed rules and summarize exactly what you understood; do not claim that the timetable has already changed.
If the intent cannot be represented by these primitives, set needs_clarification=true and ask one concise question rather than inventing behavior.
For scheduling rules that refer to an existing entity, ambiguity or absence from the catalog should trigger clarification. But when the user explicitly says add/create/new, new course and lecturer names are allowed and expected. Matching a surname is okay only when it uniquely identifies one existing lecturer.
If the user says to undo/cancel a prior requirement, use remove_rule_ids. If they clearly ask to remove all AI requirements, use clear_all=true.
Do not output Python, OR-Tools code, markdown, or commentary outside the JSON object.
"""


def interpret_with_claude(
    user_text: str,
    courses: list[dict],
    active_rules: list[dict] | None = None,
    semesters_per_year: int = 2,
    recent_chat: list[dict] | None = None,
) -> dict[str, Any]:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "Claude is not configured. Set the ANTHROPIC_API_KEY environment variable, restart the app, and try again."
        )

    model = os.getenv("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL).strip() or DEFAULT_CLAUDE_MODEL
    payload = {
        "model": model,
        "max_tokens": 1800,
        "system": _system_prompt(courses, active_rules or [], semesters_per_year, recent_chat),
        "messages": [{"role": "user", "content": user_text}],
    }

    request = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Claude API error ({error.code}): {body[:800]}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Could not reach Claude API: {error.reason}") from error

    content = raw.get("content") or []
    text = "".join(part.get("text", "") for part in content if part.get("type") == "text")
    parsed = _extract_json(text)

    parsed.setdefault("assistant_message", "I interpreted the scheduling request.")
    parsed.setdefault("needs_clarification", False)
    parsed.setdefault("clarification_question", None)
    parsed.setdefault("remove_rule_ids", [])
    parsed.setdefault("clear_all", False)
    parsed.setdefault("rules", [])
    parsed.setdefault("data_actions", [])

    for rule in parsed["rules"]:
        rule.setdefault("id", str(uuid.uuid4())[:8])
        rule.setdefault("hard", True)
        rule.setdefault("weight", 50)
        rule.setdefault("description", (user_text or "").strip())

    return parsed
