"""CGT (MEF DocTrib giurisprudenza) search / detail parsing and records (port of DocTribParser, ambito G)."""
from __future__ import annotations

from pathlib import Path

import pytest

from dp_it_giurisprudenza_convert.cgt import (
    CgtRecord,
    CgtSkipped,
    build_record,
    classify_ente,
    parse_cgt_ente,
    parse_detail,
    parse_search,
)

CAMPANIA_GUID = "{E69E3100-5261-4D7D-B967-2BC6ABDDAF2A}"


def _fixture(name: str) -> bytes:
    return (Path(__file__).resolve().parent.parent / "fixtures" / "cgt" / name).read_bytes()


def _page(declared: int, estremi: list[str]) -> bytes:
    provvedimenti = "".join(
        f'<Provvedimento idProvvedimento=\\"{{00000000-0000-0000-0000-{n:012d}}}\\"><estremi link=\\"true\\">{text.replace("/", "\\/")}<\\/estremi><\\/Provvedimento>'
        for n, text in enumerate(estremi, start=1)
    )
    xml = (
        '<?xml version=\\"1.0\\" encoding=\\"UTF-8\\"?><risultatiRicerca><contatori><contatoreGiurisprudenza>'
        f"{declared}<\\/contatoreGiurisprudenza><\\/contatori><risultati>{provvedimenti}<\\/risultati><\\/risultatiRicerca>"
    )
    return f"<html><script>var xmlResult = '{xml}';</script></html>".encode("iso-8859-1")


def test_search_g_2024_counts_34_cgt_14_cassazione_among_first_50() -> None:
    result = parse_search(_fixture("search-g-2024-real.html"))

    assert result.declared == 2184
    assert len(result.items) == 34
    assert sum(1 for i in result.items if i.grado == 1) == 4
    assert sum(1 for i in result.items if i.grado == 2) == 30
    assert result.discarded_cassazione == 14
    assert result.discarded_commissioni == 0
    assert result.discarded_altri == 2
    assert len(result.items) + result.discarded_cassazione + result.discarded_commissioni + result.discarded_altri == 50
    assert all(i.tipo in {"Sentenza", "Ordinanza"} and i.numero and i.luogo for i in result.items)
    assert {"Ragusa", "Piemonte", "Marche", "Emilia Romagna"} <= {i.luogo for i in result.items}


@pytest.mark.parametrize("name", ["search-g-day-real.html", "search-g-day-2026-11-11.html"])
def test_day_page_2_results_one_cgt_campania_sezione_2(name: str) -> None:
    result = parse_search(_fixture(name))

    assert result.declared == 2
    assert result.discarded_cassazione == 1
    assert len(result.items) == 1
    item = result.items[0]
    assert item.guid == CAMPANIA_GUID
    assert (item.tipo, item.data, item.grado, item.luogo, item.sezione, item.numero) == ("Sentenza", "2024-12-31", 2, "Campania", "2", "7355")
    assert item.estremi == "Sentenza del 31/12/2024 n. 7355 - Corte di giustizia tributaria di secondo grado della Campania - Sezione/Collegio 2"


@pytest.mark.parametrize(
    ("ente", "grado", "luogo"),
    [
        ("Corte di giustizia tributaria di primo grado di Ragusa", 1, "Ragusa"),
        ("Corte di giustizia tributaria di secondo grado del Piemonte", 2, "Piemonte"),
        ("Corte di giustizia tributaria di secondo grado della Campania", 2, "Campania"),
        ("Corte di giustizia tributaria di secondo grado delle Marche", 2, "Marche"),
        ("Corte di giustizia tributaria di secondo grado dell'Emilia Romagna", 2, "Emilia Romagna"),
    ],
)
def test_luogo_without_preposition(ente: str, grado: int, luogo: str) -> None:
    assert parse_cgt_ente(ente) == (grado, luogo)


@pytest.mark.parametrize(
    ("ente", "expected"),
    [
        ("Corte di giustizia tributaria di primo grado di Ragusa", "cgt"),
        ("Corte di giustizia tributaria di secondo grado della Campania", "cgt"),
        ("Corte di Cassazione", "cassazione"),
        ("Commissione tributaria provinciale di Milano", "commissione"),
        ("Commissione tributaria regionale della Lombardia", "commissione"),
        ("Comm. Trib. Reg. per la Campania", "commissione"),
        ("Comm. Trib. Prov. di Salerno", "commissione"),
        ("Corte Costituzionale", "altro"),
        ("Corte di Giustizia UE", "altro"),
    ],
)
def test_classify_ente(ente: str, expected: str) -> None:
    assert classify_ente(ente) == expected


def test_commissione_and_cassazione_discarded_and_counted() -> None:
    page = _page(
        10,
        [
            "Sentenza del 01/03/2022 n. 10 - Commissione tributaria provinciale di Milano - Sezione/Collegio 3",
            "Sentenza del 02/03/2022 n. 11 - Comm. Trib. Reg. per la Campania",
            "Ordinanza del 03/03/2022 n. 12 - Corte di Cassazione - Sezione/Collegio 5",
            "Sentenza del 04/03/2022 n. 13 - Corte Costituzionale",
            "Sentenza del 05/03/2024 n. 14 - Corte di giustizia tributaria di primo grado di Ragusa - Sezione/Collegio 1",
        ],
    )

    result = parse_search(page)

    assert [i.numero for i in result.items] == ["14"]
    assert (result.discarded_commissioni, result.discarded_cassazione, result.discarded_altri) == (2, 1, 1)


def test_numero_may_be_missing() -> None:
    page = _page(1, ["Ordinanza del 05/03/2024 - Corte di giustizia tributaria di primo grado di Ragusa"])

    item = parse_search(page).items[0]

    assert item.numero is None
    assert item.sezione is None
    assert (item.tipo, item.data, item.luogo) == ("Ordinanza", "2024-03-05", "Ragusa")


def test_counter_is_declared_total_not_served() -> None:
    result = parse_search(_fixture("search-g-2024-real.html"))

    served = len(result.items) + result.discarded_cassazione + result.discarded_commissioni + result.discarded_altri
    assert result.declared == 2184
    assert served == 50  # the source serves at most 50 per answer and declares the real total
    assert result.declared > served


def test_page_without_result_carrier_is_empty() -> None:
    result = parse_search(b"<html>no xml here</html>")

    assert (result.declared, result.items) == (0, [])


def test_detail_text_nonempty_iso_8859_1_decoded() -> None:
    text = parse_detail(_fixture("detail-cgt-real.html"))

    assert len(text) > 3000
    assert "SVOLGIMENTO DEL PROCESSO" in text
    assert "MOTIVI" in text
    assert "P.Q.M" in text
    assert "M. F. ha presentato appello" in text  # the parties come already anonymized from the source
    assert "già" in text  # ISO-8859-1 and the JS escape (gi\\a) are decoded, not served raw
    assert "<p" not in text
    assert "\\'" not in text


def test_detail_js_escapes_are_decoded() -> None:
    html = (
        "<html><script>var xmlDettaglio = '<r><testo><![CDATA[<p>Recupero di "
        "\\u20AC 2.338,14 e di \\u20AC 927,04.\\u00A0Ricorso della societ\\à \\'Alfa\\'.</p>]]></testo></r>';</script></html>"
    ).encode("iso-8859-1")

    text = parse_detail(html)

    assert "€ 2.338,14" in text
    assert "€ 927,04" in text
    assert "u20AC" not in text
    assert "u00A0" not in text
    assert "società" in text
    assert "'Alfa'" in text


def test_detail_blocks_become_line_breaks() -> None:
    html = "<html><script>var xmlDettaglio = '<r><testo><![CDATA[<p>Primo capoverso.</p><p>Secondo capoverso.</p>]]></testo></r>';</script></html>"

    assert parse_detail(html.encode("iso-8859-1")) == "Primo capoverso.\nSecondo capoverso."


def test_detail_without_text_becomes_skipped_record() -> None:
    item = parse_search(_fixture("search-g-day-real.html")).items[0]
    empty = b"<html><script>var xmlDettaglio = '<r><testo><![CDATA[]]></testo></r>';</script></html>"

    outcome = build_record(item, empty)

    assert isinstance(outcome, CgtSkipped)
    assert (outcome.guid, outcome.reason) == (CAMPANIA_GUID, "no_text")
    assert outcome.estremi == item.estremi
    assert isinstance(build_record(item, b"<html>no dettaglio</html>"), CgtSkipped)


def test_record_from_item_and_detail() -> None:
    item = parse_search(_fixture("search-g-day-real.html")).items[0]

    outcome = build_record(item, _fixture("detail-cgt-real.html"))

    assert isinstance(outcome, CgtRecord)
    assert outcome.guid == CAMPANIA_GUID
    assert (outcome.tipo, outcome.data, outcome.grado, outcome.luogo, outcome.sezione, outcome.numero, outcome.anno) == (
        "Sentenza",
        "2024-12-31",
        2,
        "Campania",
        "2",
        "7355",
        2024,
    )
    assert outcome.source_url == f"https://def.finanze.it/DocTribFrontend/getGiurisprudenzaDetail.do?id={CAMPANIA_GUID}"
    assert outcome.text_raw_original.startswith("SVOLGIMENTO DEL PROCESSO")
