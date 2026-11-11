"""Registry numbers cited in the epigrafe of a decision (P79-12): the link from a decision to the norme impugnate registries."""
from __future__ import annotations

from pathlib import Path

from dp_it_giurisprudenza_convert.cc_xml import GiurisReader
from dp_it_giurisprudenza_convert.epigrafe import registry_citations


def _real_epigrafi() -> dict[str, str]:
    path = Path(__file__).resolve().parent.parent / "fixtures" / "cc" / "cc2026-excerpt.xml"
    return {p.ecli: p.epigrafe for p in GiurisReader().parse_pronunce(path.read_bytes())}


def test_real_epigrafe_of_2026_95_cites_only_its_own_referral_order() -> None:
    # The body of the ordinanza (also kept in <epigrafe>) mentions ANOTHER pending case, "iscritto al n. 71 del registro ordinanze 2026":
    # that is not the registry number of this decision and must not be linked (P79-24).
    assert registry_citations(_real_epigrafi()["ECLI:IT:COST:2026:95"]) == [("ordinanze", 2025, 173)]


def test_real_epigrafe_with_two_citations_in_one_sentence() -> None:
    assert registry_citations(_real_epigrafi()["ECLI:IT:COST:2026:104"]) == [("ordinanze", 2025, 230), ("ordinanze", 2026, 27)]


def test_real_epigrafe_of_a_ricorso_and_of_a_sentenza() -> None:
    epigrafi = _real_epigrafi()

    assert registry_citations(epigrafi["ECLI:IT:COST:2026:15"]) == [("ricorsi", 2025, 29)]
    assert registry_citations(epigrafi["ECLI:IT:COST:2026:3"]) == [("ordinanze", 2025, 83)]


def test_conflitti_wordings() -> None:
    assert registry_citations("iscritto al n. 4 del registro conflitti tra enti 2024") == [("conflitti_enti", 2024, 4)]
    assert registry_citations("iscritto al n. 7 del registro conflitti tra poteri 2025") == [("conflitti_poteri", 2025, 7)]
    # the generic "registro conflitti" does not say which of the two registries it is: not linked
    assert registry_citations("iscritto al n. 7 del registro conflitti 2025") == []


def test_list_of_numbers_with_one_registry_phrase_is_not_supported() -> None:
    # "nn. 12 e 13 del registro ordinanze 2025": only one number is attached to the phrase for certain, so nothing is returned
    assert registry_citations("iscritte ai nn. 12 e 13 del registro ordinanze 2025 e pubblicate") == []
    assert registry_citations("iscritte ai nn. 12, 13 del registro ordinanze 2025") == []


def test_no_citation_is_empty() -> None:
    assert registry_citations("ha pronunciato la seguente SENTENZA nel giudizio di legittimita") == []
    assert registry_citations("") == []


def test_same_citation_twice_is_returned_once() -> None:
    text = "ordinanza iscritta al n. 9 del registro ordinanze 1961; richiamata la n. 9 del registro ordinanze 1961"

    assert registry_citations(text) == [("ordinanze", 1961, 9)]


def test_citation_after_ritenuto_is_ignored() -> None:
    text = "ha pronunciato la seguente ORDINANZA. Ritenuto che il giudizio iscritto al n. 71 del registro ordinanze 2026 e pendente"

    assert registry_citations(text) == []
