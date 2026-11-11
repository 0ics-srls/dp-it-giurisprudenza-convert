"""plan.schema.json validates every plan the convert class produces (the same file is kept in sync in the anonymize and ingest repos)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError
from test_main import CAMPANIA, cc_item, plan_of, raw, run  # noqa: F401 - raw is a fixture

SCHEMA = Path(__file__).resolve().parent.parent.parent / "plan.schema.json"


def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _items() -> list[dict[str, Any]]:
    return [
        cc_item("pronunce_2001_oggi"),
        cc_item("massime_2001_oggi"),
        cc_item("norme_ordinanze"),
        {"key": "cass:snciv:2025:01|v1", "meta": {"kind": "cassazione", "itemId": "cass:snciv:2025:01", "version": "1", "cassKind": "snciv", "anno": 2025, "mese": 1}},
        {"key": "cgt:2024-12-31:2024-12-31|v1", "meta": {"kind": "cgt", "itemId": "cgt:2024-12-31:2024-12-31", "version": "1", "from": "2024-12-31", "to": "2024-12-31"}},
    ]


def test_every_produced_plan_validates(raw: Path, tmp_path: Path) -> None:  # noqa: F811
    validator = _validator()
    for item in _items():
        run(raw, tmp_path, item)
        plan = plan_of(tmp_path, item["meta"]["itemId"].replace(":", "_"))
        validator.validate(plan)
        assert plan["kind"] == item["meta"]["kind"]


def test_plan_without_a_required_array_is_rejected() -> None:
    plan = {"kind": "cc", "itemId": "cc:x", "counts": {}, "skipped": {}, "pronunce": []}

    with pytest.raises(ValidationError):
        _validator().validate(plan)


def test_a_pronuncia_missing_its_ecli_is_rejected(raw: Path, tmp_path: Path) -> None:  # noqa: F811
    run(raw, tmp_path, cc_item("norme_ordinanze"))
    plan = plan_of(tmp_path, "cc_norme_ordinanze")
    plan["pronunce"] = [{"anno": 2026}]

    with pytest.raises(ValidationError):
        _validator().validate(plan)


def test_unknown_kind_is_rejected() -> None:
    plan = {"kind": "gu", "itemId": "x", "counts": {}, "skipped": {}, "pronunce": [], "massime": [], "norme": [], "citations": [],
            "cassazione": [], "cgt": [], "cgt_skipped": []}

    with pytest.raises(ValidationError):
        _validator().validate(plan)


def test_campania_guid_is_a_uuid_string() -> None:
    assert CAMPANIA.strip("{}").count("-") == 4
