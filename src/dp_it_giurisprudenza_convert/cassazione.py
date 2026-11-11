"""Cassazione: raw Solr pages -> sentence records (port of lexcrps-casstransform ``transform.py`` + the legacy CassazioneReader derivations).

Every string field of a Solr document may be a single-element list. The record key is the Solr ``id`` (``source_id``); the ECLI is rebuilt as
``ECLI:IT:CASS:<anno>:<numdec>`` and is NOT unique (P79-05). ``anno`` is the Solr deposit year, never the decision year (P79-17). Documents
without text, or with a source-side placeholder in place of the text, are counted and skipped.
"""
from __future__ import annotations

import html
import json
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

PLACEHOLDER_PENDING = "in fase di valutazione oscuramento"
PLACEHOLDER_PREFIX = "CORTE SUPREMA DI CASSAZIONE ITALGIUREWEB"
SOURCE_URL_PREFIX = "https://www.italgiure.giustizia.it/sncass/#/ricerca?id="


@dataclass(frozen=True)
class SentenceRecord:
    """One Cassazione sentence as the convert step hands it on; the NER text fields are produced later by the anonymize class."""

    source_id: str
    kind: str
    anno: int
    num_dec: str | None
    sezione: str | None
    tipo_prov: str | None
    data_decisione: str | None
    data_deposito: str | None
    materia: str | None
    presidente: str | None
    relatore: str | None
    ecli: str | None
    text_raw_original: str
    ocr_dis: str | None
    source_url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConvertResult:
    records: list[SentenceRecord]
    empty: int
    placeholders: int
    duplicates: int
    invalid: int
    warnings_decisione_after_deposito: int


def first_of(value: Any) -> Any:
    """Solr returns several fields as single-element lists; tolerate a scalar or a list. Absent or empty -> None."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _decode(value: Any) -> str | None:
    """The OCR carries literal double-encoded HTML entities (``&#8364;``, ``&laquo;``): resolve them, then recompose accents (NFC)."""
    if not isinstance(value, str):
        return None
    return unicodedata.normalize("NFC", html.unescape(value))


def _text(value: Any) -> str | None:
    decoded = _decode(first_of(value))
    return decoded if decoded is not None and decoded.strip() else None


def _scalar(value: Any) -> str | None:
    one = first_of(value)
    if one is None:
        return None
    text = str(one).strip()
    return text or None


def _iso_date(value: Any) -> str | None:
    text = _scalar(value)
    if text is None:
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date().isoformat()  # noqa: DTZ007 - a calendar date, no time zone involved
    except ValueError:
        return None


def is_placeholder(text: str) -> bool:
    """True when the OCR is a source-side notice and not a sentence (pending redaction, or the ITALGIUREWEB 'not available' page)."""
    stripped = text.strip()
    if not stripped:
        return False  # empty is the separate no-OCR skip
    return stripped == PLACEHOLDER_PENDING or stripped.lstrip("!").startswith(PLACEHOLDER_PREFIX)


def _anno(value: Any) -> int:
    try:
        return int(first_of(value))
    except (TypeError, ValueError):
        return 0


def record_from_solr(doc: dict[str, Any]) -> SentenceRecord | None:
    """One raw Solr document -> record, or None when it has no id or no usable text (the caller classifies the reason)."""
    source_id = _scalar(doc.get("id"))
    if source_id is None:
        return None
    anno = _anno(doc.get("anno"))
    num_dec = _scalar(doc.get("numdec"))
    text = _decode(first_of(doc.get("ocr"))) or ""
    return SentenceRecord(
        source_id=source_id,
        kind=_scalar(doc.get("kind")) or "",
        anno=anno,
        num_dec=num_dec,
        sezione=_scalar(doc.get("szdec")),
        tipo_prov=_scalar(doc.get("tipoprov")),
        data_decisione=_iso_date(doc.get("datdec")),
        data_deposito=_iso_date(doc.get("datdep")),
        materia=_text(doc.get("materia")),
        presidente=_text(doc.get("presidente")),
        relatore=_text(doc.get("relatore")),
        ecli=f"ECLI:IT:CASS:{anno}:{num_dec}" if num_dec is not None else None,
        text_raw_original=text,
        ocr_dis=_text(doc.get("ocrdis")),
        source_url=SOURCE_URL_PREFIX + source_id,
    )


def read_pages(pages_dir: Path) -> list[dict[str, Any]]:
    """The raw Solr responses of ``pages_dir`` (``*.json``), in file name order."""
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(pages_dir.glob("*.json"))]


def convert_pages(pages: list[dict[str, Any]], max_docs: int | None = None) -> ConvertResult:
    """Explodes the pages into sentence records: empty and placeholder texts are skipped and counted, duplicates by id merge (last wins),
    the result is sorted by id and ``max_docs`` trims after that sort."""
    by_id: dict[str, SentenceRecord] = {}
    empty = placeholders = duplicates = invalid = 0
    for page in pages:
        for doc in (page.get("response") or {}).get("docs") or []:
            record = record_from_solr(doc) if isinstance(doc, dict) else None
            if record is None:
                invalid += 1
                continue
            if not record.text_raw_original.strip():
                empty += 1
                continue
            if is_placeholder(record.text_raw_original):
                placeholders += 1
                continue
            if record.source_id in by_id:
                duplicates += 1
            by_id[record.source_id] = record
    records = [by_id[key] for key in sorted(by_id)]
    if max_docs is not None:
        records = records[:max_docs]
    after_deposit = sum(1 for r in records if r.data_decisione and r.data_deposito and r.data_decisione > r.data_deposito)
    return ConvertResult(records, empty, placeholders, duplicates, invalid, after_deposit)
