from __future__ import annotations
import re
from routing.models import ExplicitReference

STANDARD_RE=re.compile(r"(?P<prefix>GB(?:/T)?|JGJ(?:/T)?)\s*(?P<number>\d+(?:\.\d+)?)\s*-\s*(?P<year>\d{4})",re.I)
CLAUSE_RE=re.compile(r"第\s*(?P<clause>\d+(?:\.\d+){1,4})\s*条")


def normalize_standard_code(match:re.Match)->str:
    prefix=match.group("prefix").upper()
    return f"{prefix} {match.group('number')}-{match.group('year')}"


def parse_explicit_reference(question:str)->ExplicitReference:
    text=question or ""
    standard=STANDARD_RE.search(text)
    clause=CLAUSE_RE.search(text)
    return ExplicitReference(
        standard_code=normalize_standard_code(standard) if standard else "",
        clause_no=clause.group("clause") if clause else "",
        explicit_standard=bool(standard),
        explicit_clause=bool(clause),
    )
