"""Norme impugnate registries of the Corte costituzionale (port of GiurisNormeReader + NormaUrnMapper).

One row per ``(registry entry, norma)``; ``ord`` is the position of the norma inside the entry, so ``(registro_tipo, anno, numero, ord)`` is the
natural key. Every raw field is kept; ``norma_urn`` is best effort. Real files differ from the legacy assumption (P79-24): ``anno`` and
``numero`` are strings, the conflitti registries carry ``norme`` directly under the entry, the enti root key is misspelled and the poteri file is
not UTF-8, so all of that is handled here.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

_KNOWN_TYPES = {
    "legge": "legge",
    "decreto legislativo": "decreto.legislativo",
    "decreto-legge": "decreto.legge",
    "decreto legge": "decreto.legge",
    "d.p.r.": "decreto.del.presidente.della.repubblica",
    "decreto del presidente della repubblica": "decreto.del.presidente.della.repubblica",
}
_FILE_TYPES = {
    "ordinanze": "ordinanze",
    "ricorsi": "ricorsi",
    "conflittienti": "conflitti_enti",
    "conflittipoteri": "conflitti_poteri",
}
_FILE_NAME = re.compile(r"Registro(Ordinanze|Ricorsi|ConflittiEnti|ConflittiPoteri)", re.IGNORECASE)


@dataclass(frozen=True)
class NormaRow:
    """One challenged norm of one registry entry. Blank source values are None."""

    registro_tipo: str
    registro_anno: int
    registro_numero: int
    ord: int
    norma_descrizione: str | None
    norma_numero: str | None
    norma_data: str | None
    norma_articolo: str | None
    norma_comma: str | None
    norma_specificazione_articolo: str | None
    norma_specificazione_comma: str | None
    norma_nesso: str | None
    norma_urn: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RegistryParse:
    rows: list[NormaRow]
    skipped_entries: int
    entries_without_norme: int


def registry_type_from_file_name(name: str) -> str | None:
    """``Cc_Opendata_NormeImpugnate_RegistroConflittiEnti_json.zip`` -> ``conflitti_enti``; None when the name is no registry."""
    match = _FILE_NAME.search(name)
    return _FILE_TYPES.get(match.group(1).lower()) if match else None


def try_map_urn(descrizione: str | None, data: str | None, numero: str | None, articolo: str | None) -> str | None:
    """Best-effort URN:NIR of the challenged act (rules of the legacy NormaUrnMapper, unchanged so the link to it-normativa keeps working)."""
    if not descrizione or not descrizione.strip():
        return None
    kind = _KNOWN_TYPES.get(descrizione.strip().lower())
    if kind is None or not numero or not numero.strip() or not data or not data.strip():
        return None
    try:
        date = datetime.strptime(data.strip(), "%d/%m/%Y").date()  # noqa: DTZ007 - a calendar date, no time zone involved
    except ValueError:
        return None
    urn = f"urn:nir:stato:{kind}:{date.isoformat()};{numero.strip()}"
    if articolo and articolo.strip():
        urn += f"~art{articolo.strip()}"
    return urn


def decode_json_bytes(data: bytes) -> Any:
    """The registries are UTF-8 except the conflitti-poteri file, which is Windows-1252."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    return json.loads(text)


def _text(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    text = _text(value)
    if text is None or not text.isdigit():
        return None
    return int(text)


def _field(norma: dict[str, Any], *names: str) -> str | None:
    for name in names:
        if name in norma:
            return _text(norma[name])
    return None


def _entries(root: Any) -> list[Any]:
    if isinstance(root, list):
        return root
    if isinstance(root, dict):
        # the array property varies per registry (registro_ordinanze, registro_ricorsi, confiltto_enti, conflitto_poteri): first array
        for value in root.values():
            if isinstance(value, list):
                return value
    return []


def _norme_of(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """``parti.norme`` (ordinanze, ricorsi; ``parti`` may also be a list) or ``norme`` directly under the entry (conflitti)."""
    containers: list[Any] = []
    parti = entry.get("parti")
    if isinstance(parti, dict):
        containers.append(parti)
    elif isinstance(parti, list):
        containers.extend(parti)
    containers.append(entry)
    norme: list[dict[str, Any]] = []
    for container in containers:
        raw = container.get("norme") if isinstance(container, dict) else None
        if isinstance(raw, list):
            norme.extend(n for n in raw if isinstance(n, dict))
        elif isinstance(raw, dict):
            norme.append(raw)
    return norme


def parse_registry(data: bytes, registro_tipo: str) -> RegistryParse:
    """Parses one registry JSON file. Entries without a usable year or number are skipped and counted."""
    rows: list[NormaRow] = []
    skipped = 0
    without_norme = 0
    for entry in _entries(decode_json_bytes(data)):
        anno = _int(entry.get("anno")) if isinstance(entry, dict) else None
        numero = _int(entry.get("numero")) if isinstance(entry, dict) else None
        if anno is None or numero is None:
            skipped += 1
            continue
        norme = _norme_of(entry)
        if not norme:
            without_norme += 1
        for position, norma in enumerate(norme):
            descrizione = _field(norma, "descrizione")
            numero_norma = _field(norma, "numero")
            data_norma = _field(norma, "data")
            articolo = _field(norma, "articolo")
            rows.append(
                NormaRow(
                    registro_tipo=registro_tipo,
                    registro_anno=anno,
                    registro_numero=numero,
                    ord=position,
                    norma_descrizione=descrizione,
                    norma_numero=numero_norma,
                    norma_data=data_norma,
                    norma_articolo=articolo,
                    norma_comma=_field(norma, "comma"),
                    norma_specificazione_articolo=_field(norma, "specificazione_articolo"),
                    norma_specificazione_comma=_field(norma, "specificazione_comma"),
                    norma_nesso=_field(norma, "nesso"),
                    norma_urn=try_map_urn(descrizione, data_norma, numero_norma, articolo),
                ),
            )
    return RegistryParse(rows, skipped, without_norme)
