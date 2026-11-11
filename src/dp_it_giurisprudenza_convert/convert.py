"""The ``execute`` action: per item, the grab output becomes ``<stem>.plan.json`` (plan schema v1, ``plan.schema.json``).

``meta.kind`` picks the reader: ``cc`` (open data dump: pronunce, massime or a norme registry), ``cassazione`` (Solr pages), ``cgt`` (search pages
and detail pages). The text stays raw here; anonymization is the next step. A failing item does not stop the others: the run ends with one
``system_error`` listing the failed keys.
"""
from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dp_it_giurisprudenza_convert import cassazione, cgt
from dp_it_giurisprudenza_convert.cc_xml import GiurisReader, GiurisZipSource, Massima, Pronuncia, XmlRefusedError
from dp_it_giurisprudenza_convert.epigrafe import registry_citations
from dp_it_giurisprudenza_convert.norme import parse_registry

if TYPE_CHECKING:
    from collections.abc import Callable

CONFIG_ERROR = "config_error"
SYSTEM_ERROR = "system_error"
KIND_CC, KIND_CASSAZIONE, KIND_CGT = "cc", "cassazione", "cgt"
KINDS = (KIND_CC, KIND_CASSAZIONE, KIND_CGT)
PLAN_VERSION = 1
NORME_BUNDLES = {
    "norme_ordinanze": "ordinanze",
    "norme_ricorsi": "ricorsi",
    "norme_conflitti_enti": "conflitti_enti",
    "norme_conflitti_poteri": "conflitti_poteri",
}
PRONUNCE_BUNDLES = ("pronunce_1956_1980", "pronunce_1981_2000", "pronunce_2001_oggi")
MASSIME_BUNDLES = ("massime_1956_1980", "massime_1981_2000", "massime_2001_oggi")
BUNDLES = (*PRONUNCE_BUNDLES, *MASSIME_BUNDLES, *NORME_BUNDLES)
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_ARRAYS = ("pronunce", "massime", "norme", "citations", "cassazione", "cgt", "cgt_skipped")


class ConvertError(Exception):
    """A run-level failure carrying one of the protocol error codes."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Filters:
    """Sample filters of the Source settings (P79-14); they trim the convert output, the grab still downloads whole dumps."""

    anni: frozenset[int] | None = None
    only: frozenset[tuple[int, int]] | None = None
    max_docs: int | None = None
    max_items: int | None = None

    def wants(self, anno: int, numero: int) -> bool:
        if self.anni is not None and anno not in self.anni:
            return False
        return self.only is None or (anno, numero) in self.only

    def wants_year(self, year: int) -> bool:
        if self.anni is not None and year not in self.anni:
            return False
        return self.only is None or year in {anno for anno, _ in self.only}


def safe_name(item_id: str) -> str:
    """File stem of an item id (identical to the grab class)."""
    return _UNSAFE.sub("_", item_id.replace(" ", "-"))


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_item(entry: Any) -> None:
    meta = entry.get("meta") if isinstance(entry, dict) else None
    if not isinstance(entry, dict) or not entry.get("key") or not isinstance(meta, dict) or meta.get("kind") not in KINDS:
        raise ConvertError(CONFIG_ERROR, f"item needs a key and meta.kind one of {list(KINDS)}: {entry!r}")
    if not isinstance(meta.get("itemId"), str) or not meta["itemId"]:
        raise ConvertError(CONFIG_ERROR, f"item {entry['key']}: meta.itemId is required")
    if meta["kind"] == KIND_CC and meta.get("bundle") not in BUNDLES:
        raise ConvertError(CONFIG_ERROR, f"item {entry['key']}: meta.bundle must be one of {list(BUNDLES)}")


def _filters(params: dict[str, Any]) -> Filters:
    anni, only = params.get("anni"), params.get("only")
    if anni is not None and not (isinstance(anni, list) and all(_is_int(y) for y in anni)):
        raise ConvertError(CONFIG_ERROR, "anni must be an array of integers")
    if only is not None and not (isinstance(only, list) and all(isinstance(o, dict) and _is_int(o.get("anno")) and _is_int(o.get("numero")) for o in only)):
        raise ConvertError(CONFIG_ERROR, "only must be an array of {anno, numero} integers")
    for name in ("maxDocs", "maxItems"):
        value = params.get(name)
        if value is not None and (not _is_int(value) or value < 1):
            raise ConvertError(CONFIG_ERROR, f"{name} must be an integer >= 1")
    return Filters(
        anni=frozenset(anni) if anni else None,
        only=frozenset((o["anno"], o["numero"]) for o in only) if only else None,
        max_docs=params.get("maxDocs"),
        max_items=params.get("maxItems"),
    )


def _validate(params: dict[str, Any]) -> tuple[list[dict[str, Any]], Path, Path, Filters]:
    items = params.get("items")
    if not isinstance(items, list) or not items:
        raise ConvertError(CONFIG_ERROR, "items must be a non-empty array")
    for entry in items:
        _validate_item(entry)
    for name in ("dirIn", "dirOut"):
        if not isinstance(params.get(name), str) or not params[name]:
            raise ConvertError(CONFIG_ERROR, f"{name} is required")
    return items, Path(params["dirIn"]), Path(params["dirOut"]), _filters(params)


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_bytes(data)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _empty_plan(meta: dict[str, Any]) -> dict[str, Any]:
    plan: dict[str, Any] = {"version": PLAN_VERSION, "kind": meta["kind"], "itemId": meta["itemId"], "counts": {}, "skipped": {}}
    plan.update({name: [] for name in _ARRAYS})
    return plan


def _citations(pronuncia: Pronuncia) -> list[dict[str, Any]]:
    return [
        {"ecli": pronuncia.ecli, "registro_tipo": tipo, "registro_anno": anno, "registro_numero": numero}
        for tipo, anno, numero in registry_citations(pronuncia.epigrafe)
    ]


def _convert_cc(meta: dict[str, Any], zip_path: Path, filters: Filters, plan: dict[str, Any]) -> None:
    if not zip_path.exists():
        msg = f"{zip_path.name} not found"
        raise FileNotFoundError(msg)
    bundle = meta["bundle"]
    if bundle in NORME_BUNDLES:
        _convert_norme(NORME_BUNDLES[bundle], zip_path, plan)
        return
    reader = GiurisReader()
    ignored = skipped = 0
    with GiurisZipSource(zip_path) as source:
        for year in source.enumerate_years():
            if not filters.wants_year(year.year):
                continue
            xml = year.open_xml()
            if bundle in PRONUNCE_BUNDLES:
                pronunce: list[Pronuncia] = [p for p in reader.parse_pronunce(xml) if filters.wants(p.anno, p.numero)]
                plan["pronunce"].extend(p.to_dict() for p in pronunce)
                for p in pronunce:
                    plan["citations"].extend(_citations(p))
            else:
                massime: list[Massima] = [m for m in reader.parse_massime(xml) if filters.wants(*_anno_numero(m.ecli))]
                plan["massime"].extend(m.to_dict() for m in massime)
            skipped += reader.skipped
        ignored = source.ignored_files
    plan["skipped"] = {"records": skipped, "ignored_files": ignored}


def _anno_numero(ecli: str) -> tuple[int, int]:
    parts = ecli.split(":")
    return int(parts[3]), int(parts[4])


def _convert_norme(registro_tipo: str, zip_path: Path, plan: dict[str, Any]) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        name = next((n for n in archive.namelist() if n.lower().endswith(".json")), None)
        if name is None:
            msg = f"{zip_path.name} holds no .json entry"
            raise ValueError(msg)
        parsed = parse_registry(archive.read(name), registro_tipo)
    plan["norme"].extend(row.to_dict() for row in parsed.rows)
    plan["skipped"] = {"entries": parsed.skipped_entries, "entries_without_norme": parsed.entries_without_norme}


def _convert_cassazione(stem_dir: Path, filters: Filters, plan: dict[str, Any]) -> None:
    pages = stem_dir / "pages"
    if not pages.is_dir():
        msg = f"{stem_dir.name}/pages not found"
        raise FileNotFoundError(msg)
    result = cassazione.convert_pages(cassazione.read_pages(pages), filters.max_docs)
    plan["cassazione"].extend(r.to_dict() for r in result.records)
    plan["skipped"] = {
        "empty": result.empty,
        "placeholders": result.placeholders,
        "duplicates": result.duplicates,
        "invalid": result.invalid,
        "warnings_decisione_after_deposito": result.warnings_decisione_after_deposito,
    }


def _convert_cgt(stem_dir: Path, filters: Filters, plan: dict[str, Any]) -> None:
    if not stem_dir.is_dir():
        msg = f"{stem_dir.name} not found"
        raise FileNotFoundError(msg)
    items: dict[str, cgt.CgtItem] = {}
    discarded = {"cassazione": 0, "commissioni": 0, "altri": 0}
    for search in sorted(stem_dir.glob("search-*.html")):
        parsed = cgt.parse_search(search.read_bytes())
        discarded["cassazione"] += parsed.discarded_cassazione
        discarded["commissioni"] += parsed.discarded_commissioni
        discarded["altri"] += parsed.discarded_altri
        for item in parsed.items:
            items.setdefault(item.guid, item)
    slice_path = stem_dir / "slice.json"
    listed = json.loads(slice_path.read_text(encoding="utf-8")).get("guids") if slice_path.exists() else None
    wanted = list(items) if listed is None else [guid for guid in listed if guid in items]
    if filters.max_items is not None:
        wanted = wanted[: filters.max_items]
    no_text = missing = 0
    for guid in wanted:
        detail = stem_dir / f"{guid}.html"
        if not detail.exists():
            if listed is not None:
                msg = f"the detail page of {guid} is missing in {stem_dir.name}"
                raise FileNotFoundError(msg)
            missing += 1
            continue
        outcome = cgt.build_record(items[guid], detail.read_bytes())
        if isinstance(outcome, cgt.CgtSkipped):
            plan["cgt_skipped"].append(outcome.to_dict())
            no_text += 1
        else:
            plan["cgt"].append(outcome.to_dict())
    plan["skipped"] = {
        "discarded_cassazione": discarded["cassazione"],
        "discarded_commissioni": discarded["commissioni"],
        "discarded_altri": discarded["altri"],
        "no_text": no_text,
        "missing_detail": missing,
    }


def convert_item(entry: dict[str, Any], dir_in: Path, dir_out: Path, filters: Filters) -> tuple[dict[str, str], dict[str, int]]:
    meta = entry["meta"]
    stem = safe_name(meta["itemId"])
    plan = _empty_plan(meta)
    if meta["kind"] == KIND_CC:
        _convert_cc(meta, dir_in / f"{stem}.zip", filters, plan)
    elif meta["kind"] == KIND_CASSAZIONE:
        _convert_cassazione(dir_in / stem, filters, plan)
    else:
        _convert_cgt(dir_in / stem, filters, plan)
    plan["counts"] = {name: len(plan[name]) for name in _ARRAYS}
    plan_path = dir_out / f"{stem}.plan.json"
    _write_atomic(plan_path, json.dumps(plan, ensure_ascii=False).encode("utf-8"))
    counts = {name: value for name, value in plan["counts"].items() if value or name in {"pronunce", "massime", "norme", "cassazione", "cgt"}}
    counts.update({f"skipped_{name}": value for name, value in plan["skipped"].items()})
    return {"key": entry["key"], "plan": str(plan_path), "meta": json.dumps(meta, ensure_ascii=False)}, counts


def _metrics(totals: dict[str, int]) -> dict[str, int]:
    metrics = {k: v for k, v in totals.items() if not k.startswith("skipped_discarded_")}
    metrics["skipped_discarded"] = sum(v for k, v in totals.items() if k.startswith("skipped_discarded_"))
    return metrics


def run_convert(params: dict[str, Any], on_progress: Callable[[int, str], None]) -> dict[str, Any]:
    """Converts every item; failed items are collected and raised as one system_error after the good ones were written."""
    items, dir_in, dir_out, filters = _validate(params)
    converted: list[dict[str, str]] = []
    totals: dict[str, int] = {"items": 0, "pronunce": 0, "massime": 0, "norme": 0, "citations": 0, "cassazione": 0, "cgt": 0}
    failures: list[str] = []
    for index, entry in enumerate(items):
        try:
            files, counts = convert_item(entry, dir_in, dir_out, filters)
        except (OSError, zipfile.BadZipFile, XmlRefusedError, ValueError, KeyError) as error:
            failures.append(f"{entry['key']} ({error})")
        else:
            converted.append(files)
            totals["items"] += 1
            for name, value in counts.items():
                totals[name] = totals.get(name, 0) + value
        on_progress(int((index + 1) * 100 / len(items)), f"{index + 1}/{len(items)} {entry['key']}")
    if failures:
        msg = f"{len(failures)} of {len(items)} items failed: {'; '.join(failures)}"
        raise ConvertError(SYSTEM_ERROR, msg)
    return {"dirOut": str(dir_out), "converted": converted, "metrics": _metrics(totals)}
