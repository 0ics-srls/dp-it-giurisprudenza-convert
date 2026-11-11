"""Registry numbers a decision states in its epigrafe (P79-12).

Only the head of the epigrafe counts (everything before ``Ritenuto``/``Considerato``/``Rilevato``): the body of an ordinanza is also kept in
``<epigrafe>`` and mentions OTHER pending cases ("tale giudizio, iscritto al n. 71 del registro ordinanze 2026, e' tuttora pendente"), which must
not be linked to this decision. A citation counts only when its number stands directly before ``del registro <tipo> <anno>``; a list sharing one
phrase ("nn. 12 e 13 del registro ordinanze 2025") is ambiguous and returns nothing for it. The generic ``registro conflitti`` is ambiguous too.
The wordings were counted on the 2001-2026 dump (notes P79-12): ordinanze 6000, ricorsi 2358, conflitti tra enti 175, conflitti tra poteri 16.
"""
from __future__ import annotations

import re

_HEAD_END = re.compile(r"\b(?:Ritenuto|Considerato|Rilevato)\b")
_CITATION = re.compile(
    r"(?P<numero>\d{1,6})\s+(?:del\s+)?registro\s+(?:delle\s+|dei\s+)?"
    r"(?P<tipo>ordinanz[ae]|ricors[io]|conflitt[io]\s+tra\s+enti|conflitt[io]\s+tra\s+poteri)\s+(?:del\s+)?(?P<anno>\d{4})\b",
    re.IGNORECASE,
)
_LIST_BEFORE = re.compile(r"\b\d{1,6}\s*(?:,|\be)\s*$", re.IGNORECASE)
_FULL_CITATION_BEFORE = re.compile(r"registro\s+[a-zà-ù ]+?\s+(?:del\s+)?\d{4}\s*(?:,|\be)\s*$", re.IGNORECASE)


def _registro_tipo(wording: str) -> str:
    words = " ".join(wording.lower().split())
    if words.startswith("ordinanz"):
        return "ordinanze"
    if words.startswith("ricors"):
        return "ricorsi"
    return "conflitti_enti" if words.endswith("enti") else "conflitti_poteri"


def registry_citations(epigrafe: str) -> list[tuple[str, int, int]]:
    """``(registro_tipo, anno, numero)`` of every unambiguous citation in the head of the epigrafe, in order of appearance, without duplicates."""
    head = _HEAD_END.split(epigrafe, maxsplit=1)[0]
    found: list[tuple[str, int, int]] = []
    for match in _CITATION.finditer(head):
        before = head[max(0, match.start() - 40) : match.start()]
        if _LIST_BEFORE.search(before) and not _FULL_CITATION_BEFORE.search(before):
            continue  # "nn. 12 e 13 del registro ...": the number is part of a list, not attached to the phrase for certain
        citation = (_registro_tipo(match.group("tipo")), int(match.group("anno")), int(match.group("numero")))
        if citation not in found:
            found.append(citation)
    return found
