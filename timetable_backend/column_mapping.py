"""Match a course file's own column headers to the fields the app needs.

No AI and no network: known names (synonyms), small-typo tolerance and a
look at the values in each column. Every proposal says HOW it was found so
the confirmation screen can show it, and nothing is applied until the user
confirms (see the /courses/map route).

Matching order (strongest first), each column used for one field at most:
  exact     the header is the app's own name for the field
  synonym   a known alternative name ("דרישת קדם", 'נ"ז', "שנה פדגוגית")
  saved     a header confirmed in an earlier upload (column_names.json)
  typo      a header within a small spelling distance of a known name
  values    the column's contents look like the field (א/ב/ג -> year, ...)
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

# --------------------------------------------------------------------------
# Fields. "canonical" is the header the rest of the app reads.
FIELDS = [
    {"canonical": "מס.שעור", "required": True, "he": "מספר קורס", "en": "Course ID",
     "synonyms": ["מס.שעור", "מס שעור", "מס. שעור", "מספר שעור", "מספר שיעור", "מס' קורס", "מספר קורס",
                  "קוד קורס", "מס.שעור אב", "course id", "course number", "id"]},
    {"canonical": "שם השיעור", "required": True, "he": "שם הקורס", "en": "Course name",
     "synonyms": ["שם השיעור", "שם השעור", "שם שיעור", "שם שעור", "שם הקורס", "שם קורס",
                  "course name", "name", "course"]},
    {"canonical": "תיאור ד.קדם", "required": True, "he": "דרישות קדם", "en": "Prerequisites",
     "synonyms": ["תיאור ד.קדם", "דרישת קדם", "דרישות קדם", "ד.קדם", "קורסי קדם", "קורס קדם",
                  "קדם", "prerequisites", "prerequisite", "prereq"]},
    {"canonical": "תיאור ד.מקבילה", "required": False, "he": "דרישות מקבילות", "en": "Corequisites",
     "default_he": "אין דרישות מקבילות", "default_en": "no corequisites",
     "synonyms": ["תיאור ד.מקבילה", "דרישה מקבילה", "דרישות מקבילות", "ד.מקבילה", "קורס מקביל",
                  "מקבילה", "corequisites", "corequisite", "parallel"]},
    {"canonical": "שנת לימודים", "required": True, "he": "שנת לימודים", "en": "Study year",
     "synonyms": ["שנת לימודים", "שנת לימוד", "שנה פדגוגית", "שנה", "year", "study year"]},
    {"canonical": "שם המרצה", "required": False, "he": "מרצה", "en": "Lecturer",
     "default_he": "מרצה נפרד לכל קורס", "default_en": "a separate lecturer per course",
     "synonyms": ["שם המרצה", "שם מרצה", "מרצה", "מרצים", "lecturer", "teacher", "instructor"]},
    {"canonical": "נ.זיכוי", "required": True, "he": "נקודות זכות", "en": "Credits",
     "synonyms": ["נ.זיכוי", 'נ"ז', "נ״ז", "נ.ז", "נקודות זכות", "נ.זיכוי (שעור)", "נ.זיכוי (שיעור)",
                  "credits", "credit points"]},
    {"canonical": 'ש"ש', "required": False, "he": 'ש"ש (שעות שבועיות)', "en": "Weekly hours",
     "default_he": 'לפי נקודות הזכות (שעה לכל נ"ז)', "default_en": "from the credits (1 hour per credit)",
     "synonyms": ['ש"ש', "ש״ש", "שעות שבועיות", "שעות סמסטריאליות", "weekly hours", "hours per week"]},
    {"canonical": "סוג שיעור", "required": False, "he": "סוג שיעור", "en": "Lesson type",
     "default_he": "הרצאה", "default_en": "lecture",
     "synonyms": ["סוג שיעור", "סוג שעור", "סוג השיעור", "סוג השעור", "lesson type", "class type"]},
    {"canonical": "קטגוריית קורס", "required": True, "he": "קטגוריית קורס", "en": "Course category",
     "synonyms": ["קטגוריית קורס", "קטגוריה", "קטגוריית הקורס", "סוג קורס", "חובה/בחירה",
                  "category", "course category"]},
    {"canonical": "גמישות", "required": False, "he": "גמישות", "en": "Flexibility",
     "synonyms": ["גמישות", "flexibility"]},
]
FIELD_BY_CANONICAL = {f["canonical"]: f for f in FIELDS}
METHOD_SCORE = {"exact": 1.0, "synonym": 0.95, "saved": 0.9, "typo": 0.8, "values": 0.6}
TYPO_MIN_RATIO = 0.82
YEAR_LETTERS = set("אבגדהוזח")
VALID_CATEGORIES = {"חובה", "בחירה", "סמינר", "פרויקט", "כללי"}


def normalize_header(text) -> str:
    """Compare headers by letters and digits only: quotes, gershayim, dots,
    brackets and extra spaces never decide a match."""
    text = str(text).replace(" ", " ").lower()
    text = re.sub(r"[\"'״׳`.\-_:()\[\],/\\]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------
# Value sniffers: does this column LOOK like the field? (fraction of matching
# non-empty cells must be high; columns already matched by name are skipped)
def _values(series: pd.Series) -> list[str]:
    return [str(v).strip() for v in series.dropna().tolist() if str(v).strip()]


def _numbers(values):
    out = []
    for v in values:
        try:
            out.append(float(v))
        except ValueError:
            return None
    return out


def _sniff(canonical: str, series: pd.Series) -> bool:
    values = _values(series)
    if len(values) < 2:
        return False
    numbers = _numbers(values)
    if canonical == "שנת לימודים":
        letters = [v.replace("'", "").replace("׳", "") for v in values]
        if all(v in YEAR_LETTERS for v in letters):
            return True
        return numbers is not None and all(n == int(n) and 1 <= n <= 8 for n in numbers) \
            and len(set(numbers)) <= 8
    if canonical == "מס.שעור":
        return numbers is not None and all(n == int(n) and n >= 1000 for n in numbers) \
            and len(set(numbers)) >= 0.9 * len(numbers)
    if canonical == "סוג שיעור":
        return sum(any(k in v for k in ("שעור", "שיעור", "הרצאה", "תרגול")) for v in values) >= 0.8 * len(values)
    if canonical == "קטגוריית קורס":
        return sum(v in VALID_CATEGORIES for v in values) >= 0.8 * len(values)
    return False


# --------------------------------------------------------------------------
@dataclass
class Proposal:
    canonical: str
    column: str | None
    method: str | None          # exact | synonym | saved | typo | values | None
    samples: list = field(default_factory=list)


@dataclass
class MappingResult:
    sheet: str
    columns: list               # every header in the sheet, in order
    proposals: list             # one Proposal per field, in FIELDS order
    other_sheets: list = field(default_factory=list)   # sheets that will NOT be loaded

    @property
    def missing_required(self) -> list[str]:
        return [p.canonical for p in self.proposals
                if p.column is None and FIELD_BY_CANONICAL[p.canonical]["required"]]

    @property
    def is_identity(self) -> bool:
        """Nothing to confirm: every required field and every column that was
        found already use the app's own header names."""
        return not self.missing_required and all(
            p.method in ("exact", None) for p in self.proposals)

    def to_dict(self) -> dict:
        return {"sheet": self.sheet, "columns": self.columns, "other_sheets": self.other_sheets,
                "proposals": [asdict(p) for p in self.proposals]}


def _candidates(header: str, field_spec: dict, saved: list[str]):
    """(score, method) for one header against one field, or None."""
    norm = normalize_header(header)
    if not norm:
        return None
    if norm == normalize_header(field_spec["canonical"]):
        return METHOD_SCORE["exact"], "exact"
    synonyms = [normalize_header(s) for s in field_spec["synonyms"]]
    if norm in synonyms:
        return METHOD_SCORE["synonym"], "synonym"
    if norm in {normalize_header(s) for s in saved}:
        return METHOD_SCORE["saved"], "saved"
    best = max((difflib.SequenceMatcher(None, norm, s).ratio() for s in synonyms if len(s) >= 4),
               default=0.0)
    if best >= TYPO_MIN_RATIO:
        return METHOD_SCORE["typo"] * best, "typo"
    return None


def propose_for_frame(frame: pd.DataFrame, sheet: str, saved_aliases: dict | None = None) -> MappingResult:
    saved_aliases = saved_aliases or {}
    headers = [str(c).strip() for c in frame.columns]
    frame = frame.copy()
    frame.columns = headers
    scored = []   # (score, field_index, header, method)
    for fi, spec in enumerate(FIELDS):
        for header in headers:
            hit = _candidates(header, spec, saved_aliases.get(spec["canonical"], []))
            if hit:
                scored.append((hit[0], fi, header, hit[1]))
    # Strongest evidence wins; a column serves one field, a field takes one column.
    chosen: dict[int, tuple[str, str]] = {}
    used: set[str] = set()
    for score, fi, header, method in sorted(scored, key=lambda t: (-t[0], t[1])):
        if fi in chosen or header in used:
            continue
        chosen[fi] = (header, method)
        used.add(header)
    # Values decide only for fields still open, from columns still unused.
    for fi, spec in enumerate(FIELDS):
        if fi in chosen:
            continue
        for header in headers:
            if header in used or header.startswith("Unnamed"):
                continue
            if _sniff(spec["canonical"], frame[header]):
                chosen[fi] = (header, "values")
                used.add(header)
                break
    proposals = []
    for fi, spec in enumerate(FIELDS):
        header, method = chosen.get(fi, (None, None))
        samples = _values(frame[header])[:3] if header else []
        proposals.append(Proposal(spec["canonical"], header, method, samples))
    return MappingResult(sheet, headers, proposals)


def propose_mapping(excel_path, saved_aliases: dict | None = None) -> MappingResult:
    """Best sheet = the one where the most required fields were found."""
    best = None
    with pd.ExcelFile(excel_path) as workbook:
        names = list(workbook.sheet_names)
        for name in names:
            frame = workbook.parse(sheet_name=name, nrows=200)
            if frame.empty and len(frame.columns) == 0:
                continue
            result = propose_for_frame(frame, name, saved_aliases)
            found = sum(1 for p in result.proposals if p.column and FIELD_BY_CANONICAL[p.canonical]["required"])
            if best is None or found > best[0]:
                best = (found, result)
    if best is None:
        raise ValueError("The workbook has no sheets with columns.")
    best[1].other_sheets = [n for n in names if n != best[1].sheet]
    return best[1]


EMPTY_CHOICE = "__empty__"  # "this file has no such column" (requirement fields only)
EMPTY_ALLOWED = {"תיאור ד.קדם"}  # optional fields simply stay unmapped


def apply_mapping(source_path, sheet: str, mapping: dict, destination_path, sheet_name: str) -> None:
    """Write a copy of the sheet whose headers are the app's own names.

    mapping: canonical -> source header, or EMPTY_CHOICE for a requirement
    column the file does not have (created empty). Unmapped source columns
    are kept as they are (the data check lists them as ignored)."""
    frame = pd.read_excel(source_path, sheet_name=sheet)
    frame.columns = [str(c).strip() for c in frame.columns]
    renamed = {}
    for canonical, header in mapping.items():
        if header == EMPTY_CHOICE:
            if canonical not in EMPTY_ALLOWED:
                raise ValueError(f"'{canonical}' cannot be left empty.")
            continue
        if header not in frame.columns:
            raise ValueError(f"Column '{header}' is not in sheet '{sheet}'.")
        renamed[header] = canonical
    if len(set(renamed.values())) != len(renamed) or len(renamed) != len(
            [h for h in mapping.values() if h != EMPTY_CHOICE]):
        raise ValueError("Each column can be used for one field only.")
    # A source column already called like a canonical field it is NOT mapped
    # to would collide after renaming: move it aside.
    clashes = {c: f"{c} (מקור)" for c in frame.columns
               if c in FIELD_BY_CANONICAL and c not in renamed and c in renamed.values()}
    frame = frame.rename(columns=clashes).rename(columns=renamed)
    for canonical, header in mapping.items():
        if header == EMPTY_CHOICE and canonical not in frame.columns:
            frame[canonical] = None
    ordered = [f["canonical"] for f in FIELDS if f["canonical"] in frame.columns]
    frame = frame[ordered + [c for c in frame.columns if c not in ordered]]
    with pd.ExcelWriter(destination_path, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name=sheet_name, index=False)
        writer.sheets[sheet_name].sheet_view.rightToLeft = True


# --------------------------------------------------------------------------
# Header names confirmed in earlier uploads: one shared list (no departments).
SAVED_NAMES_FILE = Path(__file__).resolve().parent / "column_names.json"


def load_saved_column_names(path: Path | None = None) -> dict[str, list[str]]:
    """{canonical field: [headers confirmed before]}; empty when none."""
    path = Path(path or SAVED_NAMES_FILE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: [str(h) for h in v] for k, v in data.items() if isinstance(v, list)}


def remember_column_names(mapping: dict[str, str], path: Path | None = None) -> None:
    """Add confirmed headers (canonical -> this file's header) to the list."""
    path = Path(path or SAVED_NAMES_FILE)
    saved = load_saved_column_names(path)
    changed = False
    for canonical, header in mapping.items():
        if not header or header == canonical or header == EMPTY_CHOICE:
            continue
        known = saved.setdefault(canonical, [])
        if header not in known:
            known.append(header)
            changed = True
    if changed:
        path.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
