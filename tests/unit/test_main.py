"""convert: kind dispatch, plan files, the Proc-protocol loop (end to end on the trimmed real fixtures)."""
from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import pytest

import main
from dp_it_giurisprudenza_convert.convert import CONFIG_ERROR, SYSTEM_ERROR, ConvertError, run_convert

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CAMPANIA = "{E69E3100-5261-4D7D-B967-2BC6ABDDAF2A}"

MASSIME_XML = """<?xml version="1.0" encoding="UTF-8"?>
<corte_costituzionale_archiviomassime>
<pronuncia><pronuncia_testata><anno_pronuncia>2026</anno_pronuncia><numero_pronuncia>95</numero_pronuncia></pronuncia_testata>
<massime><massima><numero>1</numero><titolo><![CDATA[Appello contro il proscioglimento]]></titolo><testo><![CDATA[Principio sul divieto di appello.]]></testo></massima></massime></pronuncia>
<pronuncia><pronuncia_testata><anno_pronuncia>2026</anno_pronuncia><numero_pronuncia>3</numero_pronuncia></pronuncia_testata>
<massime><massima><numero>1</numero><titolo><![CDATA[Rapina]]></titolo><testo><![CDATA[Principio sulla rapina.]]></testo></massima></massime></pronuncia>
<pronuncia><pronuncia_testata><anno_pronuncia>2025</anno_pronuncia><numero_pronuncia>7</numero_pronuncia></pronuncia_testata>
<massime><massima><numero>1</numero><titolo><![CDATA[Altro anno]]></titolo><testo><![CDATA[Principio del 2025.]]></testo></massima></massime></pronuncia>
</corte_costituzionale_archiviomassime>
"""


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _bundle(prefix: str, xml: bytes, year: int = 2026) -> bytes:
    return _zip({f"Cc_Opendata_{prefix}_{year}.zip": _zip({f"Cc_Opendata_{prefix}_{year}.xml": xml})})


def cc_item(bundle: str) -> dict[str, Any]:
    return {"key": f"cc:{bundle}|v1", "meta": {"kind": "cc", "itemId": f"cc:{bundle}", "version": "1", "bundle": bundle}}


@pytest.fixture
def raw(tmp_path: Path) -> Path:
    directory = tmp_path / "raw"
    directory.mkdir()
    (directory / "cc_pronunce_2001_oggi.zip").write_bytes(_bundle("Pronunce", (FIXTURES / "cc" / "cc2026-excerpt.xml").read_bytes()))
    (directory / "cc_massime_2001_oggi.zip").write_bytes(_bundle("Massime", MASSIME_XML.encode("utf-8")))
    registry = (FIXTURES / "norme" / "Cc_Opendata_NormeImpugnate_RegistroOrdinanze.json").read_bytes()
    (directory / "cc_norme_ordinanze.zip").write_bytes(_zip({"Cc_Opendata_NormeImpugnate_RegistroOrdinanze.json": registry}))
    shutil.copytree(FIXTURES / "cassazione" / "pages", directory / "cass_snciv_2025_01" / "pages")
    cgt = directory / "cgt_2024-12-31_2024-12-31"
    cgt.mkdir()
    shutil.copy(FIXTURES / "cgt" / "search-g-day-real.html", cgt / "search-2024-12-31.html")
    shutil.copy(FIXTURES / "cgt" / "detail-cgt-real.html", cgt / f"{CAMPANIA}.html")
    (cgt / "slice.json").write_text(json.dumps({"guids": [CAMPANIA], "discarded": {"cassazione": 1, "commissioni": 0, "altri": 0}}), encoding="utf-8")
    return directory


def run(raw: Path, tmp_path: Path, *items: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return run_convert({"items": list(items), "dirIn": str(raw), "dirOut": str(tmp_path / "plan"), **extra}, lambda *_: None)


def plan_of(tmp_path: Path, stem: str) -> dict[str, Any]:
    return json.loads((tmp_path / "plan" / f"{stem}.plan.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_cc_pronunce_with_only_filter_gives_one_pronuncia_and_its_registry_citations(raw: Path, tmp_path: Path) -> None:
    result = run(raw, tmp_path, cc_item("pronunce_2001_oggi"), only=[{"anno": 2026, "numero": 95}])

    plan = plan_of(tmp_path, "cc_pronunce_2001_oggi")
    assert [p["ecli"] for p in plan["pronunce"]] == ["ECLI:IT:COST:2026:95"]
    assert plan["citations"] == [{"ecli": "ECLI:IT:COST:2026:95", "registro_tipo": "ordinanze", "registro_anno": 2025, "registro_numero": 173}]
    assert plan["kind"] == "cc"
    assert plan["itemId"] == "cc:pronunce_2001_oggi"
    assert plan["counts"]["pronunce"] == 1
    assert plan["massime"] == plan["norme"] == plan["cassazione"] == plan["cgt"] == plan["cgt_skipped"] == []
    assert result["converted"][0]["plan"].endswith("cc_pronunce_2001_oggi.plan.json")


def test_cc_pronunce_anni_filter_and_all_citations(raw: Path, tmp_path: Path) -> None:
    run(raw, tmp_path, cc_item("pronunce_2001_oggi"), anni=[2026])

    plan = plan_of(tmp_path, "cc_pronunce_2001_oggi")
    assert len(plan["pronunce"]) == 4
    cited = {(c["ecli"], c["registro_tipo"], c["registro_anno"], c["registro_numero"]) for c in plan["citations"]}
    assert ("ECLI:IT:COST:2026:104", "ordinanze", 2026, 27) in cited
    assert ("ECLI:IT:COST:2026:15", "ricorsi", 2025, 29) in cited
    run(raw, tmp_path, cc_item("pronunce_2001_oggi"), anni=[1999])
    assert plan_of(tmp_path, "cc_pronunce_2001_oggi")["pronunce"] == []  # the 2026 year is skipped before it is parsed


def test_cc_massime_apply_the_same_filter_through_the_rebuilt_ecli(raw: Path, tmp_path: Path) -> None:
    run(raw, tmp_path, cc_item("massime_2001_oggi"), only=[{"anno": 2026, "numero": 95}])

    plan = plan_of(tmp_path, "cc_massime_2001_oggi")
    assert [(m["ecli"], m["numero"]) for m in plan["massime"]] == [("ECLI:IT:COST:2026:95", 1)]
    assert plan["pronunce"] == []
    run(raw, tmp_path, cc_item("massime_2001_oggi"), anni=[2026])
    assert len(plan_of(tmp_path, "cc_massime_2001_oggi")["massime"]) == 2


def test_norme_item_gives_rows_with_ord(raw: Path, tmp_path: Path) -> None:
    result = run(raw, tmp_path, cc_item("norme_ordinanze"))

    plan = plan_of(tmp_path, "cc_norme_ordinanze")
    assert len(plan["norme"]) == 8
    row = next(r for r in plan["norme"] if (r["registro_anno"], r["registro_numero"], r["ord"]) == (2026, 23, 0))
    assert row["registro_tipo"] == "ordinanze"
    assert row["norma_urn"] == "urn:nir:stato:decreto.legislativo:1998-07-25;286~art40"
    assert result["metrics"]["norme"] == 8


def test_cassazione_item_from_recorded_pages(raw: Path, tmp_path: Path) -> None:
    item = {"key": "cass:snciv:2025:01|v1", "meta": {"kind": "cassazione", "itemId": "cass:snciv:2025:01", "version": "1", "cassKind": "snciv", "anno": 2025, "mese": 1}}

    result = run(raw, tmp_path, item)

    plan = plan_of(tmp_path, "cass_snciv_2025_01")
    assert plan["kind"] == "cassazione"
    assert len(plan["cassazione"]) == 7
    assert plan["counts"]["cassazione"] == 7
    assert plan["skipped"]["empty"] == 2
    assert plan["skipped"]["placeholders"] == 1
    assert plan["skipped"]["warnings_decisione_after_deposito"] == 1
    first = plan["cassazione"][0]
    assert {"source_id", "ecli", "text_raw_original", "source_url"} <= set(first)
    assert result["metrics"]["cassazione"] == 7
    assert result["metrics"]["skipped_empty"] == 2


def test_cassazione_max_docs(raw: Path, tmp_path: Path) -> None:
    item = {"key": "cass:snciv:2025:01|v1", "meta": {"kind": "cassazione", "itemId": "cass:snciv:2025:01", "version": "1", "cassKind": "snciv", "anno": 2025, "mese": 1}}

    run(raw, tmp_path, item, maxDocs=3)

    assert len(plan_of(tmp_path, "cass_snciv_2025_01")["cassazione"]) == 3


def test_cgt_item_from_the_day_page_and_detail_gives_one_record_and_one_discarded(raw: Path, tmp_path: Path) -> None:
    item = {"key": "cgt:2024-12-31:2024-12-31|v1", "meta": {"kind": "cgt", "itemId": "cgt:2024-12-31:2024-12-31", "version": "1", "from": "2024-12-31", "to": "2024-12-31"}}

    result = run(raw, tmp_path, item)

    plan = plan_of(tmp_path, "cgt_2024-12-31_2024-12-31")
    assert [r["guid"] for r in plan["cgt"]] == [CAMPANIA]
    assert plan["cgt"][0]["numero"] == "7355"
    assert plan["cgt"][0]["text_raw_original"].startswith("SVOLGIMENTO DEL PROCESSO")
    assert plan["skipped"]["discarded_cassazione"] == 1
    assert plan["cgt_skipped"] == []
    assert result["metrics"]["cgt"] == 1
    assert result["metrics"]["skipped_discarded"] == 1


def test_cgt_decision_without_text_goes_to_cgt_skipped(raw: Path, tmp_path: Path) -> None:
    empty = b"<html><script>var xmlDettaglio = '<r><testo><![CDATA[]]></testo></r>';</script></html>"
    (raw / "cgt_2024-12-31_2024-12-31" / f"{CAMPANIA}.html").write_bytes(empty)
    item = {"key": "cgt:2024-12-31:2024-12-31|v1", "meta": {"kind": "cgt", "itemId": "cgt:2024-12-31:2024-12-31", "version": "1", "from": "2024-12-31", "to": "2024-12-31"}}

    result = run(raw, tmp_path, item)

    plan = plan_of(tmp_path, "cgt_2024-12-31_2024-12-31")
    assert plan["cgt"] == []
    assert plan["cgt_skipped"] == [{"guid": CAMPANIA, "estremi": plan["cgt_skipped"][0]["estremi"], "reason": "no_text"}]
    assert result["metrics"]["skipped_no_text"] == 1


def test_corrupt_zip_is_a_failed_item_and_the_run_ends_with_system_error(raw: Path, tmp_path: Path) -> None:
    (raw / "cc_pronunce_2001_oggi.zip").write_bytes(b"not a zip")

    with pytest.raises(ConvertError) as error:
        run(raw, tmp_path, cc_item("pronunce_2001_oggi"), cc_item("massime_2001_oggi"))

    assert error.value.code == SYSTEM_ERROR
    assert "cc:pronunce_2001_oggi|v1" in str(error.value)
    assert (tmp_path / "plan" / "cc_massime_2001_oggi.plan.json").exists()  # the healthy item was still converted


def test_missing_grab_output_is_a_failed_item(raw: Path, tmp_path: Path) -> None:
    (raw / "cc_norme_ordinanze.zip").unlink()

    with pytest.raises(ConvertError) as error:
        run(raw, tmp_path, cc_item("norme_ordinanze"))

    assert error.value.code == SYSTEM_ERROR


@pytest.mark.parametrize(
    "params",
    [
        {"items": [], "dirIn": "a", "dirOut": "b"},
        {"items": [{"key": "k", "meta": {"kind": "xyz", "itemId": "i"}}], "dirIn": "a", "dirOut": "b"},
        {"items": [{"key": "k", "meta": {"kind": "cc", "itemId": "i", "bundle": "nonexistent"}}], "dirIn": "a", "dirOut": "b"},
        {"items": [cc_item("pronunce_2001_oggi")], "dirOut": "b"},
        {"items": [cc_item("pronunce_2001_oggi")], "dirIn": "a"},
        {"items": [cc_item("pronunce_2001_oggi")], "dirIn": "a", "dirOut": "b", "anni": ["x"]},
        {"items": [cc_item("pronunce_2001_oggi")], "dirIn": "a", "dirOut": "b", "only": [{"anno": 2026}]},
        {"items": [cc_item("pronunce_2001_oggi")], "dirIn": "a", "dirOut": "b", "maxDocs": 0},
    ],
)
def test_invalid_params_are_config_errors(params: dict[str, Any]) -> None:
    with pytest.raises(ConvertError) as error:
        run_convert(params, lambda *_: None)

    assert error.value.code == CONFIG_ERROR


def test_absent_or_null_filters_are_accepted(raw: Path, tmp_path: Path) -> None:
    result = run(raw, tmp_path, cc_item("norme_ordinanze"), anni=None, only=None, maxDocs=None, maxItems=None)

    assert result["metrics"]["norme"] == 8


def _lines(text: str) -> tuple[int, list[dict[str, Any]]]:
    out = io.StringIO()
    code = main.main(io.StringIO(text), out)
    return code, [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def _request(action: str, **params: Any) -> str:
    return json.dumps({"_action": action, "_meta": {"executionId": "e1"}, **params}) + "\n"


def test_protocol_execute_emits_progress_then_one_result(raw: Path, tmp_path: Path) -> None:
    code, lines = _lines(_request("execute", items=[cc_item("norme_ordinanze")], dirIn=str(raw), dirOut=str(tmp_path / "plan")))

    assert code == 0
    assert lines[-1]["type"] == "result"
    assert all(m["type"] == "progress" for m in lines[:-1])
    assert lines[-1]["data"]["metrics"]["norme"] == 8


def test_protocol_failing_item_prints_one_system_error(raw: Path, tmp_path: Path) -> None:
    (raw / "cc_norme_ordinanze.zip").unlink()

    code, lines = _lines(_request("execute", items=[cc_item("norme_ordinanze")], dirIn=str(raw), dirOut=str(tmp_path / "plan")))

    assert code != 0
    errors = [m for m in lines if m["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["code"] == "system_error"
    assert "cc:norme_ordinanze|v1" in errors[0]["message"]


def test_protocol_bad_line_unknown_action_and_terminate() -> None:
    _, lines = _lines("not json\n" + _request("bogus"))
    assert [m["code"] for m in lines] == ["config_error", "config_error"]

    _, after = _lines(_request("terminate") + _request("bogus"))
    assert after == []


def test_manifest_declares_execute_with_items_dirs_and_sample_filters() -> None:
    manifest = json.loads((FIXTURES.parent.parent / "manifest.json").read_text(encoding="utf-8"))

    properties = manifest["actions"]["execute"]["input"]["properties"]
    assert manifest["name"] == "dp-it-giurisprudenza-convert"
    assert {"items", "dirIn", "dirOut", "anni", "only", "maxDocs", "maxItems"} <= set(properties)
