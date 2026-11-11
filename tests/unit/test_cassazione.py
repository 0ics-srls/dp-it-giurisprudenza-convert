"""Cassazione Solr pages -> sentence records (port of lexcrps-casstransform + CassazioneReader derivations)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dp_it_giurisprudenza_convert.cassazione import ConvertResult, convert_pages, first_of, read_pages


def _pages_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "fixtures" / "cassazione" / "pages"


def _recorded() -> ConvertResult:
    return convert_pages(read_pages(_pages_dir()))


def _by_id(result: ConvertResult) -> dict[str, Any]:
    return {r.source_id: r for r in result.records}


def _doc(source_id: str, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": source_id, "kind": "snciv", "anno": 2025, "numdec": "1", "szdec": "5", "ocr": ["testo della sentenza"]}
    base.update(fields)
    return base


def _page(*docs: dict[str, Any]) -> dict[str, Any]:
    return {"response": {"numFound": len(docs), "start": 0, "docs": list(docs)}}


def test_first_of_unwraps_single_element_lists() -> None:
    assert first_of(["a"]) == "a"
    assert first_of("a") == "a"
    assert first_of([]) is None
    assert first_of(None) is None


def test_list_valued_fields_unwrapped() -> None:
    record = _by_id(_recorded())["snciv2021521495O"]

    assert record.kind == "snciv"  # kind, anno, numdec are lists too in some answers
    assert record.anno == 2021
    assert record.num_dec == "21495"
    assert record.sezione == "5"
    assert record.materia == "TRIBUTI"  # list -> first element, not dropped
    assert record.ocr_dis == "P.Q.M. rigetta il ricorso"
    assert record.data_deposito == "2021-07-22"
    assert len(record.text_raw_original) > 1000


def test_ecli_rebuilt_when_absent() -> None:
    records = _by_id(_recorded())

    assert records["snciv2021521495O"].ecli == "ECLI:IT:CASS:2021:21495"
    assert records["snpen2025706063O"].ecli == "ECLI:IT:CASS:2025:32009"
    # numdec missing: no ECLI is invented
    result = convert_pages([_page(_doc("snciv2025900001O", numdec=None))])
    assert result.records[0].ecli is None


def test_ecli_not_unique_two_sentences_same_ecli_are_both_kept() -> None:
    result = _recorded()

    same = [r for r in result.records if r.ecli == "ECLI:IT:CASS:2021:21495"]
    assert sorted(r.source_id for r in same) == ["snciv2021121495O", "snciv2021521495O"]  # the key is the Solr id, not the ECLI


def test_dates_yyyymmdd_to_iso_and_invalid_to_none() -> None:
    records = _by_id(_recorded())

    assert records["snciv2021521495O"].data_decisione == "2021-05-10"
    assert records["snciv2025500006O"].data_decisione is None  # 20251345 is no date
    assert records["snciv2025500006O"].data_deposito is None  # [""] is no date either


def test_empty_ocr_counted_and_skipped() -> None:
    result = _recorded()

    ids = {r.source_id for r in result.records}
    assert "snciv2021500001O" not in ids  # no ocr
    assert "snciv2021500002O" not in ids  # whitespace only
    assert result.empty == 2


def test_placeholder_ocr_counted_and_skipped() -> None:
    result = _recorded()

    assert "snciv2025500003O" not in {r.source_id for r in result.records}
    assert result.placeholders == 1
    assert convert_pages([_page(_doc("x1", ocr=["CORTE SUPREMA DI CASSAZIONE ITALGIUREWEB sentenza non disponibile"]))]).placeholders == 1


def test_duplicate_source_id_last_wins() -> None:
    first = _doc("snciv2025900002O", ocr=["primo testo"])
    second = _doc("snciv2025900002O", ocr=["secondo testo"])

    result = convert_pages([_page(first), _page(second)])

    assert [r.text_raw_original for r in result.records] == ["secondo testo"]
    assert result.duplicates == 1


def test_max_docs_after_sort_by_id() -> None:
    docs = [_doc(f"snciv2025{n:06d}O") for n in (30, 10, 20, 40)]

    result = convert_pages([_page(*docs)], max_docs=2)

    assert [r.source_id for r in result.records] == ["snciv2025000010O", "snciv2025000020O"]


def test_anno_is_solr_anno_not_decision_year() -> None:
    record = _by_id(_recorded())["snciv2025500004O"]

    assert record.anno == 2025
    assert record.data_decisione == "2024-12-30"
    assert record.ecli == "ECLI:IT:CASS:2025:4"


def test_source_url_format() -> None:
    record = _by_id(_recorded())["snpen2025706063O"]

    assert record.source_url == "https://www.italgiure.giustizia.it/sncass/#/ricerca?id=snpen2025706063O"


def test_data_decisione_after_deposito_counts_warning() -> None:
    result = _recorded()

    assert result.warnings_decisione_after_deposito == 1  # snciv2025500005O: decided 2025-09-15, deposited 2025-09-01
    assert "snciv2025500005O" in {r.source_id for r in result.records}  # kept: a warning, not a skip


def test_html_entities_are_decoded() -> None:
    record = _by_id(_recorded())["snciv2025500007O"]

    assert record.text_raw_original == "Il collegio «osserva» che € 100 sono dovuti."
    assert record.materia == "PROPRIETA'"


def test_doc_without_id_is_skipped_and_counted() -> None:
    result = convert_pages([_page({"kind": "snciv", "ocr": ["testo"]})])

    assert result.records == []
    assert result.invalid == 1


def test_read_pages_reads_sorted_files(tmp_path: Path) -> None:
    (tmp_path / "b.json").write_text(json.dumps(_page(_doc("snciv2025000002O", ocr=["B"]))), encoding="utf-8")
    (tmp_path / "a.json").write_text(json.dumps(_page(_doc("snciv2025000002O", ocr=["A"]))), encoding="utf-8")

    result = convert_pages(read_pages(tmp_path))

    assert [r.text_raw_original for r in result.records] == ["B"]  # b.json is read last and wins
