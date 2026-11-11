"""Corte costituzionale open data readers (port of the legacy GiurisZipSource + GiurisReader).

``GiurisZipSource`` navigates a bundle lazily: an outer zip holds one inner zip per year, each inner zip holds one XML.
``GiurisReader`` streams one ``<pronuncia>`` at a time, cleans the text, parses dates and counts the records it cannot
build instead of aborting the year. DTDs and entity declarations are refused (defusedxml).
"""
from __future__ import annotations

import html
import io
import re
import unicodedata
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Any, BinaryIO, Self

from defusedxml import ElementTree as SafeElementTree
from defusedxml.common import DefusedXmlException

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path
    from types import TracebackType
    from xml.etree.ElementTree import Element

_WHITESPACE = re.compile(r"\s+")
_YEAR_ZIP = re.compile(r"(\d{4})\.zip$", re.IGNORECASE)
_SKIPPABLE = (ValueError, KeyError, TypeError)


class XmlRefusedError(ValueError):
    """The XML declares a DTD or an entity, which the reader never expands."""


@dataclass(frozen=True)
class Pronuncia:
    """A decision of the Corte costituzionale. ``text_raw`` is the space-joined, de-duplicated non-blank sections."""

    ecli: str
    anno: int
    numero: int
    tipologia: str
    presidente: str
    relatore: str
    redattore: str | None
    data_decisione: str | None
    data_deposito: str
    collegio: str
    epigrafe: str
    fatto: str | None
    diritto: str | None
    testo: str | None
    dispositivo: str
    text_raw: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Massima:
    """A legal principle of a decision. ``ecli`` is rebuilt from year and number (the massime XML carries none)."""

    ecli: str
    numero: int
    titolo: str
    text_raw: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decode_entities(value: str) -> str:
    """Resolve the second layer of HTML entities the source buries in its text, then recompose combining accents (NFC)."""
    decoded = html.unescape(value).replace("&greco;", "Δ").replace("&ul;", "")
    return unicodedata.normalize("NFC", decoded)


def clean(value: str) -> str:
    return _WHITESPACE.sub(" ", decode_entities(value)).strip()


def clean_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = clean(value)
    return cleaned or None


def flatten(element: Element) -> str:
    """Concatenate the descendant text nodes; a space goes between two alphanumerics at an element boundary (``seguenteSENTENZAnei``)."""
    parts: list[str] = []
    for piece in element.itertext():
        if not piece:
            continue
        if parts and parts[-1][-1].isalnum() and piece[0].isalnum():
            parts.append(" ")
        parts.append(piece)
    return "".join(parts)


def _flat_or_none(element: Element | None) -> str | None:
    return None if element is None else clean_or_none(flatten(element))


def _field(parent: Element, name: str) -> str:
    child = parent.find(name)
    if child is None or child.text is None:
        message = f"missing element '{name}'"
        raise ValueError(message)
    return clean(child.text)


def _iso_date(value: str) -> str:
    return datetime.strptime(value, "%d/%m/%Y").date().isoformat()  # noqa: DTZ007 - a calendar date, no time zone involved


def _iso_date_or_none(value: str | None) -> str | None:
    try:
        return _iso_date(clean(value or ""))
    except ValueError:
        return None


def _text_of(parent: Element, name: str) -> str | None:
    child = parent.find(name)
    return None if child is None else clean_or_none(child.text)


class GiurisReader:
    """Streaming parser of one year XML. ``skipped`` counts records that could not be built (reset on every parse call)."""

    def __init__(self) -> None:
        self.skipped = 0

    def parse_pronunce(self, xml: bytes | BinaryIO) -> Iterator[Pronuncia]:
        self.skipped = 0
        for element in self._pronuncia_elements(xml):
            pronuncia = self._try_build(element)
            if pronuncia is None:
                self.skipped += 1
                continue
            yield pronuncia

    def parse_massime(self, xml: bytes | BinaryIO) -> Iterator[Massima]:
        self.skipped = 0
        for element in self._pronuncia_elements(xml):
            yield from self._build_massime(element)

    @staticmethod
    def _pronuncia_elements(xml: bytes | BinaryIO) -> Iterator[Element]:
        stream = io.BytesIO(xml) if isinstance(xml, bytes) else xml
        try:
            for _, element in SafeElementTree.iterparse(stream, events=("end",), forbid_dtd=True, forbid_entities=True):
                if element.tag != "pronuncia":
                    continue
                yield element
                element.clear()
        except DefusedXmlException as error:
            message = f"the XML declares a DTD or an entity: {error}"
            raise XmlRefusedError(message) from error

    def _build_massime(self, element: Element) -> list[Massima]:
        result: list[Massima] = []
        testata = element.find("pronuncia_testata")
        if testata is None:
            return result
        try:
            ecli = f"ECLI:IT:COST:{int(_field(testata, 'anno_pronuncia'))}:{int(_field(testata, 'numero_pronuncia'))}"
        except _SKIPPABLE:
            return result
        container = element.find("massime")
        for massima in [] if container is None else container.findall("massima"):
            try:
                text = _flat_or_none(massima.find("testo"))
                if text is None:
                    message = "testo must not be blank"
                    raise ValueError(message)
                result.append(Massima(ecli, int(_field(massima, "numero")), _flat_or_none(massima.find("titolo")) or "", text))
            except _SKIPPABLE:
                self.skipped += 1
        return result

    @staticmethod
    def _try_build(element: Element) -> Pronuncia | None:
        try:
            testata = element.find("pronuncia_testata")
            testo_node = element.find("pronuncia_testo")
            if testata is None or testo_node is None:
                return None
            ecli = _text_of(testata, "ecli")
            if ecli is None:
                return None  # missing/blank ecli: skipped (robust ingest)
            anno = int(_field(testata, "anno_pronuncia"))
            numero = int(_field(testata, "numero_pronuncia"))
            if ecli != f"ECLI:IT:COST:{anno}:{numero}":
                return None
            tipologia = _text_of(testata, "tipologia_pronuncia") or ""
            epigrafe = _flat_or_none(testo_node.find("epigrafe"))
            testo = _flat_or_none(testo_node.find("testo"))
            # ordinanze carry the whole body in <epigrafe> and no <testo>: it is served in the body field too
            corpo = testo if testo is not None else (epigrafe if tipologia == "O" else None)
            fatto = _flat_or_none(testo_node.find("fatto"))
            diritto = _flat_or_none(testo_node.find("diritto"))
            dispositivo = _flat_or_none(testo_node.find("dispositivo")) or ""
            sections = dict.fromkeys(s for s in (epigrafe or "", fatto, diritto, corpo, dispositivo) if s and s.strip())
            return Pronuncia(
                ecli=ecli,
                anno=anno,
                numero=numero,
                tipologia=tipologia,
                presidente=_text_of(testata, "presidente") or "",
                relatore=_text_of(testata, "relatore_pronuncia") or "",
                redattore=_text_of(testata, "redattore_pronuncia"),
                data_decisione=_iso_date_or_none(_text_of(testata, "data_decisione")),
                data_deposito=_iso_date(_field(testata, "data_deposito")),
                collegio=_flat_or_none(testo_node.find("collegio")) or "",
                epigrafe=epigrafe or "",
                fatto=fatto,
                diritto=diritto,
                testo=corpo,
                dispositivo=dispositivo,
                text_raw=" ".join(sections),
            )
        except _SKIPPABLE:
            return None


@dataclass(frozen=True)
class GiurisYearEntry:
    """One year of a bundle; ``open_xml`` opens the inner zip on demand and returns the bytes of its single XML."""

    year: int
    open_xml: Callable[[], bytes]


class GiurisZipSource:
    """Lazy navigation of a bundle (outer zip of year zips); entries that are no zip named with a 4-digit year are skipped and counted."""

    def __init__(self, outer_zip_path: Path) -> None:
        self._path = outer_zip_path
        self._archive: zipfile.ZipFile | None = None
        self.ignored_files = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None) -> None:
        self.close()

    def close(self) -> None:
        if self._archive is not None:
            self._archive.close()
            self._archive = None

    def _open_archive(self) -> zipfile.ZipFile:
        if self._archive is None:
            self._archive = zipfile.ZipFile(self._path)
        return self._archive

    def enumerate_years(self) -> list[GiurisYearEntry]:
        """Reads only the outer central directory; no inner zip is opened here."""
        self.ignored_files = 0
        years: list[GiurisYearEntry] = []
        for info in self._open_archive().infolist():
            if info.is_dir():
                continue
            match = _YEAR_ZIP.search(info.filename.rsplit("/", 1)[-1])
            if match is None:
                self.ignored_files += 1
                continue
            name = info.filename
            years.append(GiurisYearEntry(int(match.group(1)), partial(self._open_inner_xml, name)))
        years.sort(key=lambda entry: entry.year)
        return years

    def _open_inner_xml(self, name: str) -> bytes:
        inner_bytes = self._open_archive().read(name)
        with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner:
            xml_name = next((n for n in inner.namelist() if n.lower().endswith(".xml")), None)
            if xml_name is None:
                message = f"no .xml entry inside inner zip: {name}"
                raise ValueError(message)
            return inner.read(xml_name)
