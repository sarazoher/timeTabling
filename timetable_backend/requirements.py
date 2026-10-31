"""Prerequisite / corequisite parsing and resolution.

Cell syntax (one convention for every department)
-------------------------------------------------
* AND separators:  " + "   or  " וגם "
* OR  separators:  "/"     or  " או "
* Parentheses group explicitly: "(A / B) + C", "A / (B + C)".
* Without parentheses OR binds tighter than AND; every cell is converted to
  an AND of OR-groups:

      "A + B / C / D"            ->  A  AND (B OR C OR D)
      "A או A וגם B או C"        ->  (A OR A) AND (B OR C)  ->  A AND (B OR C)

  This is the format exported by the registrar ("A או A' וגם B או C").
* "+" only separates when surrounded by spaces, so "++C" in a course
  name is never split.
* Course names are matched exactly after whitespace clean-up. A name that
  matches no course is reported (data check), never guessed.

Resolution of each OR-group against the catalog
-----------------------------------------------
* alternatives not in the catalog are dropped (recorded as notes);
* no alternative left            -> reported as unresolved, no constraint;
* exactly one alternative left   -> ordinary hard requirement;
* two or more alternatives left  -> OR requirement: at least one of them
  must be taken in an earlier semester (prerequisite) or in the same or an
  earlier semester (corequisite).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Placeholder values found in legacy CS exports that mean "no requirement".
EMPTY_MARKERS = {"6", "7"}


def clean_name(name) -> str:
    text = str(name).replace(" ", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.rstrip(".").strip()


def canonical_name(name, aliases: dict[str, str] | None = None) -> str:
    text = clean_name(name)
    if aliases:
        text = aliases.get(text, text)
    return text


class RequirementSyntaxError(ValueError):
    """A requirement cell that cannot be read unambiguously (e.g. unbalanced
    or misplaced parentheses). The cell is reported, never guessed."""


# Operators; the capturing group keeps them in re.split's output.
OPERATOR_SPLIT = re.compile(r"(\s+\+\s+|\s+וגם\s+|\s*/\s*|\s+או\s+)")


def _operator_kind(token: str) -> str:
    return "OR" if token.strip() in {"/", "או"} else "AND"


def _strip_wrapping_pair(text: str) -> str:
    """Remove one pair of parentheses that wraps the whole text, if the
    opening '(' really matches the final ')'."""
    if not (text.startswith("(") and text.endswith(")")):
        return text
    depth = 0
    for position, char in enumerate(text):
        depth += char == "("
        depth -= char == ")"
        if depth == 0 and position < len(text) - 1:
            return text  # the first '(' closes before the end
    return text[1:-1].strip()


def _tokenize(raw: str) -> list[tuple[str, str]]:
    """Split a cell into NAME / AND / OR / LP / RP tokens.

    A parenthesis is a grouping symbol only at the edge of an operand (next
    to the cell boundary, an operator or another grouping parenthesis) and
    only while it is unbalanced within that operand. Balanced parentheses
    inside a name, e.g. 'משק לשעת חירום (מל"ח) וניהול', stay part of the name.
    """
    tokens: list[tuple[str, str]] = []
    pieces = OPERATOR_SPLIT.split(f" {raw} ")
    for index, piece in enumerate(pieces):
        if index % 2:
            tokens.append((_operator_kind(piece), piece.strip()))
            continue
        text = re.sub(r"\s+", " ", piece.replace("\u00a0", " ")).strip()
        opens = closes = 0
        while text.startswith("(") and text.count("(") > text.count(")"):
            opens += 1
            text = text[1:].strip()
        while text.endswith(")") and text.count(")") > text.count("("):
            closes += 1
            text = text[:-1].strip()
        previous = None
        while previous != text:
            previous, text = text, _strip_wrapping_pair(text)
        # Whatever parentheses remain belong to the course name and must be
        # balanced there; anything else is a misplaced grouping parenthesis.
        depth = 0
        for char in text:
            depth += (char == "(") - (char == ")")
            if depth < 0:
                break
        if depth != 0:
            raise RequirementSyntaxError(f"misplaced parenthesis in '{piece.strip()}'")
        tokens.extend([("LP", "(")] * opens)
        name = clean_name(text)
        if name:
            tokens.append(("NAME", name))
        tokens.extend([("RP", ")")] * closes)
    return tokens


def _drop_empty_operands(tokens):
    """Lenient like the old parser: an operator with nothing on one side
    (e.g. a trailing ' + ') is ignored instead of failing the whole cell."""
    cleaned: list[tuple[str, str]] = []
    for kind, value in tokens:
        if kind in {"AND", "OR"}:
            if not cleaned or cleaned[-1][0] in {"AND", "OR", "LP"}:
                continue
        if kind == "RP" and cleaned and cleaned[-1][0] in {"AND", "OR"}:
            cleaned.pop()
        cleaned.append((kind, value))
    while cleaned and cleaned[-1][0] in {"AND", "OR"}:
        cleaned.pop()
    return cleaned


def _unique(items):
    return list(dict.fromkeys(items))


def _to_cnf(tokens: list[tuple[str, str]]) -> list[list[str]]:
    """Recursive descent: expr := term (AND term)* ; term := factor (OR factor)*;
    factor := NAME | '(' expr ')'. Returns an AND-list of OR-groups (CNF).
    OR binds tighter than AND; parentheses override that."""
    position = 0

    def peek():
        return tokens[position][0] if position < len(tokens) else None

    def take(kind):
        nonlocal position
        if peek() != kind:
            found = tokens[position][1] if position < len(tokens) else "end of cell"
            expected = {"RP": "a closing ')'", "NAME": "a course name"}.get(kind, kind)
            raise RequirementSyntaxError(f"expected {expected} but found '{found}'")
        position += 1
        return tokens[position - 1][1]

    def expr():
        groups = term()
        while peek() == "AND":
            take("AND")
            groups = groups + term()
        return groups

    def term():
        groups = factor()
        while peek() == "OR":
            take("OR")
            right = factor()
            # (a1 AND a2) OR (b1 AND b2) = (a1 OR b1) AND (a1 OR b2) AND ...
            groups = [_unique(left + other) for left in groups for other in right]
        return groups

    def factor():
        if peek() == "LP":
            take("LP")
            inner = expr()
            take("RP")
            return inner
        return [[take("NAME")]]

    if not tokens:
        return []
    result = expr()
    if position != len(tokens):
        found = tokens[position][1]
        raise RequirementSyntaxError(
            "')' without a matching '('" if found == ")" else f"unexpected '{found}'")
    return result


def parse_requirement_groups(text, aliases: dict[str, str] | None = None) -> list[list[str]]:
    """Parse a requirement cell into an AND-list of OR-groups of names.

    Parentheses group explicitly: "(A / B) + C", "A / (B + C)". Without them
    OR binds tighter than AND. Duplicate alternatives inside a group and
    duplicate groups are removed; order is preserved.
    Raises RequirementSyntaxError for a cell that cannot be read safely.
    """
    if text is None:
        return []
    raw = str(text).replace("\u00a0", " ").strip()
    if raw != raw:  # NaN that slipped through as a float string
        return []
    if not raw or raw.lower() == "nan" or raw in EMPTY_MARKERS:
        return []

    cnf = _to_cnf(_drop_empty_operands(_tokenize(raw)))
    groups: list[list[str]] = []
    seen_groups: set[tuple[str, ...]] = set()
    for group in cnf:
        alternatives = _unique(canonical_name(name, aliases) for name in group)
        alternatives = [name for name in alternatives if name]
        key = tuple(sorted(alternatives))
        if alternatives and key not in seen_groups:
            groups.append(alternatives)
            seen_groups.add(key)
    return groups


def format_requirement_groups(groups: list[list[str]]) -> str | None:
    """Canonical text form: AND as ' + ', OR as ' / '."""
    if not groups:
        return None
    return " + ".join(" / ".join(group) for group in groups)


@dataclass
class RequirementSet:
    """Resolved requirements, keyed by course index in the course list."""

    prereq_all: dict[int, list[int]] = field(default_factory=dict)
    prereq_any: dict[int, list[list[int]]] = field(default_factory=dict)
    parallel_all: dict[int, list[int]] = field(default_factory=dict)
    parallel_any: dict[int, list[list[int]]] = field(default_factory=dict)
    unresolved: list[dict] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)

    def dependency_sources(self) -> set[int]:
        """Every course that some other course depends on (AND or OR)."""
        sources: set[int] = set()
        for mapping in (self.prereq_all, self.parallel_all):
            for values in mapping.values():
                sources.update(values)
        for mapping in (self.prereq_any, self.parallel_any):
            for groups in mapping.values():
                for group in groups:
                    sources.update(group)
        return sources

    def prerequisite_edges(self) -> dict[int, set[int]]:
        """dependent -> every prerequisite it can use (AND members and OR
        alternatives). Used only for soft heuristics such as "how many
        courses does this one unlock"."""
        edges: dict[int, set[int]] = {}
        for dependent, values in self.prereq_all.items():
            edges.setdefault(dependent, set()).update(values)
        for dependent, groups in self.prereq_any.items():
            for group in groups:
                edges.setdefault(dependent, set()).update(group)
        return edges


def resolve_requirements(courses, aliases: dict[str, str] | None = None) -> RequirementSet:
    name_index: dict[str, int] = {}
    for index, course in enumerate(courses):
        name_index.setdefault(canonical_name(course.name, aliases), index)

    result = RequirementSet()
    specs = (
        ("prerequisite", "prerequisites", result.prereq_all, result.prereq_any),
        ("parallel", "parallel_requirement", result.parallel_all, result.parallel_any),
    )
    for course_index, course in enumerate(courses):
        for kind, attribute, all_map, any_map in specs:
            all_map[course_index] = []
            any_map[course_index] = []
            try:
                cell_groups = parse_requirement_groups(getattr(course, attribute))
            except RequirementSyntaxError as error:
                # Never guess: report the whole cell, add no constraint from it.
                result.unresolved.append({
                    "course_id": course.course_id,
                    "course": course.name,
                    "requirement_type": kind,
                    "unresolved_name": str(getattr(course, attribute)).strip(),
                    "reason": f"malformed cell: {error}",
                })
                cell_groups = []
            for group in cell_groups:
                resolved: list[int] = []
                missing: list[str] = []
                for name in group:
                    index = name_index.get(canonical_name(name, aliases))
                    if index is None:
                        missing.append(name)
                    elif index != course_index and index not in resolved:
                        resolved.append(index)

                if not resolved:
                    if missing:
                        result.unresolved.append({
                            "course_id": course.course_id,
                            "course": course.name,
                            "requirement_type": kind,
                            "unresolved_name": " / ".join(missing),
                        })
                    continue
                if missing:
                    result.notes.append({
                        "course_id": course.course_id,
                        "course": course.name,
                        "requirement_type": kind,
                        "ignored_alternatives": missing,
                        "kept_alternatives": [courses[i].name for i in resolved],
                    })
                if len(resolved) == 1:
                    if resolved[0] not in all_map[course_index]:
                        all_map[course_index].append(resolved[0])
                elif resolved not in any_map[course_index]:
                    any_map[course_index].append(resolved)

            # An OR-group that contains a course already required with AND is
            # automatically satisfied; drop it so the model stays minimal.
            required = set(all_map[course_index])
            any_map[course_index] = [
                group for group in any_map[course_index] if not required.intersection(group)
            ]
    return result
