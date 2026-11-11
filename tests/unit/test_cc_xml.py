"""Corte costituzionale open data readers: port of GiurisZipSource + GiurisReader (legacy branch 53)."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from dp_it_giurisprudenza_convert.cc_xml import GiurisReader, GiurisZipSource, XmlRefusedError


def _fixture(name: str) -> bytes:
    return (Path(__file__).resolve().parent.parent / "fixtures" / "cc" / name).read_bytes()


def _inner_zip(xml_name: str, xml: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(xml_name, xml)
    return buffer.getvalue()


def _outer_zip(tmp_path: Path, entries: list[tuple[str, bytes]]) -> Path:
    path = tmp_path / "outer.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return path


def _pronuncia(n: int) -> str:
    return (
        "<pronuncia><pronuncia_testata><anno_pronuncia>2001</anno_pronuncia>"
        f"<numero_pronuncia>{n}</numero_pronuncia><ecli>ECLI:IT:COST:2001:{n}</ecli>"
        "<tipologia_pronuncia>O</tipologia_pronuncia><presidente>P</presidente>"
        "<relatore_pronuncia>R</relatore_pronuncia><redattore_pronuncia></redattore_pronuncia>"
        "<data_decisione>10/01/2001</data_decisione><data_deposito>15/01/2001</data_deposito>"
        f"</pronuncia_testata><pronuncia_testo><collegio>C</collegio><epigrafe>E{n}</epigrafe>"
        f"<dispositivo>D{n}</dispositivo></pronuncia_testo></pronuncia>"
    )


def test_parse_pronunce_sample() -> None:
    reader = GiurisReader()

    items = list(reader.parse_pronunce(_fixture("pronunce-sample.xml")))

    assert [p.ecli for p in items] == ["ECLI:IT:COST:2001:1", "ECLI:IT:COST:2001:2", "ECLI:IT:COST:2001:3"]
    first = items[0]
    assert (first.anno, first.numero, first.tipologia) == (2001, 1, "O")
    assert (first.presidente, first.relatore) == ("Ruperto", "Contri")
    assert first.redattore is None  # empty element
    assert first.data_decisione == "2001-01-10"
    assert first.data_deposito == "2001-01-15"
    assert first.fatto is None
    assert first.diritto is None
    # an ordinanza keeps its body in <epigrafe>: it is served in the body field too
    assert first.testo == first.epigrafe
    assert first.dispositivo == "dichiara inammissibile la questione di legittimita"
    sentenza = items[1]
    assert sentenza.redattore == "Marini"
    assert sentenza.fatto
    assert sentenza.diritto
    assert sentenza.testo
    # inline <titolo> is flattened without gluing the words
    assert "SENTENZA nel giudizio" in sentenza.epigrafe
    assert "\r" not in first.text_raw
    assert "  " not in first.text_raw


def test_single_line_xml_loses_no_record() -> None:
    xml = "<elenco_pronunce>" + "".join(_pronuncia(n) for n in range(1, 6)) + "</elenco_pronunce>"

    items = list(GiurisReader().parse_pronunce(xml.encode("utf-8")))

    assert [p.numero for p in items] == [1, 2, 3, 4, 5]


def test_missing_ecli_is_skipped_and_counted() -> None:
    reader = GiurisReader()

    items = list(reader.parse_pronunce(_fixture("pronunce-sample.xml")))

    assert all(p.numero != 99 for p in items)
    assert reader.skipped == 1


def test_missing_data_decisione_is_none_not_deposit_date() -> None:
    xml = b"<elenco_pronunce>" + _fixture("consulta-1961-60-senza-data-decisione.xml") + b"</elenco_pronunce>"
    reader = GiurisReader()

    items = list(reader.parse_pronunce(xml))

    assert len(items) == 1
    assert items[0].ecli == "ECLI:IT:COST:1961:60"
    assert items[0].data_decisione is None
    assert items[0].data_deposito == "1961-11-28"
    assert reader.skipped == 0


def test_text_raw_is_joined_nonblank_sections() -> None:
    items = list(GiurisReader().parse_pronunce(_fixture("pronunce-sample.xml")))
    sentenza = items[1]

    assert sentenza.text_raw == " ".join([sentenza.epigrafe, sentenza.fatto or "", sentenza.diritto or "", sentenza.testo or "", sentenza.dispositivo])
    # an ordinanza whose epigrafe is also mapped to testo does not carry its body twice
    ordinanza = items[0]
    assert ordinanza.text_raw == f"{ordinanza.epigrafe} {ordinanza.dispositivo}"
    # a record with only some sections never gets empty gaps
    assert "  " not in items[2].text_raw


def test_massime_ecli_rebuilt_from_anno_numero() -> None:
    reader = GiurisReader()

    items = list(reader.parse_massime(_fixture("massime-sample.xml")))

    assert len(items) == 2 + 1
    assert {m.ecli for m in items} == {"ECLI:IT:COST:2001:10", "ECLI:IT:COST:2001:11"}
    assert sorted(m.numero for m in items if m.ecli == "ECLI:IT:COST:2001:10") == [1, 2]
    first = next(m for m in items if m.ecli == "ECLI:IT:COST:2001:10" and m.numero == 1)
    assert first.titolo == "Massima sulla competenza legislativa"
    assert first.text_raw == "Principio di diritto affermato dalla Corte costituzionale in materia"
    # the blank massima is skipped and counted, its siblings still parse
    assert reader.skipped == 1


def test_massime_single_line_keeps_every_pronuncia() -> None:
    xml = "<corte_costituzionale_archiviomassime>" + "".join(
        f"<pronuncia><pronuncia_testata><anno_pronuncia>2001</anno_pronuncia><numero_pronuncia>{n}</numero_pronuncia></pronuncia_testata>"
        f"<massime><massima><numero>1</numero><titolo>T{n}</titolo><testo>principio {n}</testo></massima></massime></pronuncia>"
        for n in range(1, 6)
    ) + "</corte_costituzionale_archiviomassime>"

    items = list(GiurisReader().parse_massime(xml.encode("utf-8")))

    assert [m.ecli for m in items] == [f"ECLI:IT:COST:2001:{n}" for n in range(1, 6)]


def test_zip_layout_year_entries_sorted_and_ignored_counted(tmp_path: Path) -> None:
    path = _outer_zip(
        tmp_path,
        [
            ("Cc_Opendata_Pronunce_2002.zip", _inner_zip("p_2002.xml", "<elenco_pronunce>2002</elenco_pronunce>")),
            ("Cc_Opendata_Pronunce_2001.zip", _inner_zip("p_2001.xml", "<elenco_pronunce>2001</elenco_pronunce>")),
            ("readme.txt", b"not a zip"),
            ("manifest.zip", _inner_zip("x.xml", "<x/>")),  # a zip, but no 4-digit year in the name
        ],
    )

    with GiurisZipSource(path) as source:
        entries = source.enumerate_years()

        assert [e.year for e in entries] == [2001, 2002]
        assert source.ignored_files == 2
        assert b"2001" in entries[0].open_xml()
        assert b"2002" in entries[1].open_xml()


def test_corrupt_inner_zip_fails_only_when_opened(tmp_path: Path) -> None:
    path = _outer_zip(
        tmp_path,
        [
            ("Cc_Opendata_Pronunce_2001.zip", _inner_zip("p_2001.xml", "<elenco_pronunce>ok</elenco_pronunce>")),
            ("Cc_Opendata_Pronunce_1999.zip", b"THIS IS NOT A VALID ZIP"),
        ],
    )

    with GiurisZipSource(path) as source:
        entries = source.enumerate_years()  # enumeration must not open the inner zips

        assert [e.year for e in entries] == [1999, 2001]
        assert b"ok" in next(e for e in entries if e.year == 2001).open_xml()
        with pytest.raises(zipfile.BadZipFile):
            next(e for e in entries if e.year == 1999).open_xml()


def test_inner_zip_without_xml_entry_is_an_error(tmp_path: Path) -> None:
    path = _outer_zip(tmp_path, [("Cc_Opendata_Pronunce_2001.zip", _inner_zip("notes.txt", "x"))])

    with GiurisZipSource(path) as source, pytest.raises(ValueError, match=r"no .xml entry"):
        source.enumerate_years()[0].open_xml()


def test_dtd_entity_is_refused() -> None:
    xml = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "boom">]><elenco_pronunce>&a;</elenco_pronunce>'

    with pytest.raises(XmlRefusedError):
        list(GiurisReader().parse_pronunce(xml))


def test_real_2026_excerpt_epigrafe_is_kept_whole() -> None:
    items = {p.ecli: p for p in GiurisReader().parse_pronunce(_fixture("cc2026-excerpt.xml"))}

    assert set(items) == {"ECLI:IT:COST:2026:3", "ECLI:IT:COST:2026:15", "ECLI:IT:COST:2026:95", "ECLI:IT:COST:2026:104"}
    assert "registro ordinanze" in items["ECLI:IT:COST:2026:95"].epigrafe.lower()
    assert items["ECLI:IT:COST:2026:95"].tipologia == "O"
