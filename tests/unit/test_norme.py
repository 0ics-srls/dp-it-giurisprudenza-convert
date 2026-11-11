"""Norme impugnate registries (port of GiurisNormeReader + NormaUrnMapper)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dp_it_giurisprudenza_convert.norme import parse_registry, registry_type_from_file_name, try_map_urn


def _fixture(name: str) -> bytes:
    return (Path(__file__).resolve().parent.parent / "fixtures" / "norme" / name).read_bytes()


def test_ordinanze_sample_rows() -> None:
    result = parse_registry(_fixture("Cc_Opendata_NormeImpugnate_RegistroOrdinanze.json"), "ordinanze")

    assert result.skipped_entries == 0
    by_key: dict[tuple[int, int], list[int]] = {}
    for row in result.rows:
        by_key.setdefault((row.registro_anno, row.registro_numero), []).append(row.ord)
    # entry 2026/23 has 5 norme (ord 0..4), 2025/173 has 1, 2026/71 has 2
    assert by_key == {(2026, 23): [0, 1, 2, 3, 4], (2025, 173): [0], (2026, 71): [0, 1]}
    first = next(r for r in result.rows if (r.registro_anno, r.registro_numero, r.ord) == (2026, 23, 0))
    assert first.registro_tipo == "ordinanze"
    assert first.norma_descrizione == "decreto legislativo"
    assert first.norma_numero == "286"
    assert first.norma_data == "25/07/1998"
    assert first.norma_articolo == "40"
    assert first.norma_urn == "urn:nir:stato:decreto.legislativo:1998-07-25;286~art40"


def test_legacy_sample_rows() -> None:
    result = parse_registry(_fixture("norme-impugnate-sample.json"), "ordinanze")

    assert len(result.rows) == 2
    mapped = next(r for r in result.rows if r.registro_numero == 10)
    assert (mapped.registro_anno, mapped.norma_comma) == (2001, "19")
    assert mapped.norma_urn == "urn:nir:stato:legge:2000-12-23;388~art80"
    unmappable = next(r for r in result.rows if r.registro_numero == 11)
    assert unmappable.norma_urn is None
    assert unmappable.norma_descrizione is None  # blank is stored as None, never as an empty string


def test_norma_object_instead_of_array() -> None:
    norma = {"descrizione": "legge", "numero": "1", "data": "01/01/2000", "articolo": "2", "comma": "", "nesso": ""}
    payload = {"registro_ordinanze": [{"anno": 2001, "numero": 5, "parti": {"norme": norma}}]}

    result = parse_registry(json.dumps(payload).encode("utf-8"), "ordinanze")

    assert [(r.registro_anno, r.registro_numero, r.ord) for r in result.rows] == [(2001, 5, 0)]


def test_year_and_number_may_be_strings_or_numbers() -> None:
    payload = {"registro_ordinanze": [{"anno": "2024", "numero": "65", "parti": {"norme": [{"descrizione": "legge"}]}}]}

    rows = parse_registry(json.dumps(payload).encode("utf-8"), "ordinanze").rows

    assert [(r.registro_anno, r.registro_numero) for r in rows] == [(2024, 65)]


def test_entry_without_usable_key_is_skipped_and_counted() -> None:
    payload = {"registro_ordinanze": [{"anno": "x", "numero": "1", "parti": {"norme": [{"descrizione": "legge"}]}}]}

    result = parse_registry(json.dumps(payload).encode("utf-8"), "ordinanze")

    assert result.rows == []
    assert result.skipped_entries == 1


def test_conflitti_entes_have_norme_directly_under_the_entry() -> None:
    result = parse_registry(_fixture("Cc_Opendata_NormeImpugnate_RegistroConflittiEnti.json"), "conflitti_enti")

    assert len(result.rows) >= 4  # 3 norme under entry 2024/2, then the next entries
    assert {r.registro_tipo for r in result.rows} == {"conflitti_enti"}
    assert result.rows[0].norma_descrizione == "Decreto"


def test_conflitti_poteri_file_is_not_utf8() -> None:
    result = parse_registry(_fixture("Cc_Opendata_NormeImpugnate_RegistroConflittiPoteri.json"), "conflitti_poteri")

    assert [(r.registro_anno, r.registro_numero) for r in result.rows] == [(2025, 8), (2025, 9), (2026, 1), (2026, 2)]
    assert next(r for r in result.rows if r.registro_numero == 9 and r.registro_anno == 2025).norma_numero == "Doc. IV n. 5-A"


def test_registry_type_from_file_name() -> None:
    assert registry_type_from_file_name("Cc_Opendata_NormeImpugnate_RegistroOrdinanze.json") == "ordinanze"
    assert registry_type_from_file_name("Cc_Opendata_NormeImpugnate_RegistroOrdinanze_json.zip") == "ordinanze"
    assert registry_type_from_file_name("Cc_Opendata_NormeImpugnate_RegistroRicorsi_json.zip") == "ricorsi"
    assert registry_type_from_file_name("Cc_Opendata_NormeImpugnate_RegistroConflittiEnti_json.zip") == "conflitti_enti"
    assert registry_type_from_file_name("Cc_Opendata_NormeImpugnate_RegistroConflittiPoteri_json.zip") == "conflitti_poteri"
    assert registry_type_from_file_name("Cc_Opendata_Pronunce_2001.zip") is None


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("legge", "29/12/2022", "197", "1"), "urn:nir:stato:legge:2022-12-29;197~art1"),
        (("legge", "23/12/2000", "388", None), "urn:nir:stato:legge:2000-12-23;388"),
        (("legge", "23/12/2000", "388", ""), "urn:nir:stato:legge:2000-12-23;388"),
        (("decreto legislativo", "30/03/2001", "165", "1"), "urn:nir:stato:decreto.legislativo:2001-03-30;165~art1"),
        (("decreto-legge", "02/03/2024", "19", "1"), "urn:nir:stato:decreto.legge:2024-03-02;19~art1"),
        (("Decreto legge", "02/03/2024", "19", "1"), "urn:nir:stato:decreto.legge:2024-03-02;19~art1"),
        (("d.P.R.", "28/12/2000", "445", "3"), "urn:nir:stato:decreto.del.presidente.della.repubblica:2000-12-28;445~art3"),
        (("decreto del presidente della repubblica", "28/12/2000", "445", ""), "urn:nir:stato:decreto.del.presidente.della.repubblica:2000-12-28;445"),
        (("legge", "29/12/2022", "197", "25-bis"), "urn:nir:stato:legge:2022-12-29;197~art25-bis"),
        (("regolamento", "29/12/2022", "197", "1"), None),
        (("", "23/12/2000", "388", "80"), None),
        (("tipo sconosciuto xyz", "23/12/2000", "388", "80"), None),
        (("legge", "", "388", "80"), None),
        (("legge", None, "388", "80"), None),
        (("legge", "23/12/2000", "", "80"), None),
        (("legge", "31/02/2000", "388", "80"), None),
        (("legge", "2000-12-23", "388", "80"), None),
    ],
)
def test_urn_mapper_cases(args: tuple[str | None, str | None, str | None, str | None], expected: str | None) -> None:
    assert try_map_urn(*args) == expected


def test_raw_fields_always_preserved() -> None:
    payload = {
        "registro_ricorsi": [
            {
                "anno": "2024",
                "numero": "5",
                "parti": {
                    "norme": [
                        {
                            "descrizione": "legge della Regione Emilia Romagna",
                            "numero": "17",
                            "data": "28/12/2023",
                            "articolo": "3",
                            "comma": "2",
                            "specificazione_articolo": "bis",
                            "specificazione_comma": "ter",
                            "nesso": "aggiuntivo del",
                        },
                    ],
                },
            },
        ],
    }

    row = parse_registry(json.dumps(payload).encode("utf-8"), "ricorsi").rows[0]

    assert row.norma_urn is None  # regional law: not mapped, but nothing is lost
    assert (row.norma_descrizione, row.norma_numero, row.norma_data) == ("legge della Regione Emilia Romagna", "17", "28/12/2023")
    assert (row.norma_articolo, row.norma_comma) == ("3", "2")
    assert (row.norma_specificazione_articolo, row.norma_specificazione_comma, row.norma_nesso) == ("bis", "ter", "aggiuntivo del")
