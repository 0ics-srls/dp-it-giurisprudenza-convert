"""CGT (Corti di giustizia tributaria, MEF DocTrib ambito G): search/detail parsing and records (port of the legacy DocTribParser).

The search answer is an HTML page (ISO-8859-1) that carries the XML inside a JavaScript string (``var xmlResult = '...'``) with escaped quotes and
slashes. The list mixes bodies: only the merit courts (Corte di giustizia tributaria di primo/secondo grado) are kept; Cassazione (a corpus of its
own), pre-reform Commissioni tributarie and other bodies are discarded AND counted, so the completeness invariant ``served == kept + discarded``
stays checkable. The detail page carries the full text inside ``var xmlDettaglio`` (CDATA with the portal markup).
"""
from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

from defusedxml import ElementTree as SafeElementTree

DETAIL_URL = "https://def.finanze.it/DocTribFrontend/getGiurisprudenzaDetail.do?id="

_XML_RESULT = re.compile(r"var xmlResult\s*=\s*'(.*?)';", re.DOTALL)
_XML_DETAIL = re.compile(r"var xmlDettaglio\s*=\s*'(.*?)';", re.DOTALL)
# Estremi layout: tipo, "del" date, optional "n." number, "-" body, optional "- Sezione/Collegio" section
_ESTREMI = re.compile(
    r"^(?P<tipo>.+?)\s+del\s+(?P<data>\d{2}/\d{2}/\d{4})(?:\s+n\.\s*(?P<num>\S+))?\s*-\s*(?P<ente>.+?)(?:\s*-\s*Sezione/Collegio\s*(?P<sez>\S+))?$",
)
# the place comes with its own preposition: di Ragusa, del Piemonte, della Campania, delle Marche, dell'Emilia Romagna
_CGT_ENTE = re.compile(
    r"^Corte di giustizia tributaria di (?P<grado>primo|secondo) grado\s+(?:dell'|delle\s|della\s|del\s|di\s)\s*(?P<luogo>.+)$",
    re.IGNORECASE,
)
_XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")
_JS_SIMPLE = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_HEX = re.compile(r"[0-9a-fA-F]+")
_BLOCK_TAGS = frozenset({"p", "div", "br", "li", "tr", "h1", "h2", "h3"})


@dataclass(frozen=True)
class CgtItem:
    """One merit-court decision of a search list (no text yet)."""

    guid: str
    tipo: str
    data: str
    numero: str | None
    grado: int
    luogo: str
    sezione: str | None
    estremi: str


@dataclass(frozen=True)
class SearchSlice:
    declared: int
    items: list[CgtItem]
    discarded_cassazione: int
    discarded_commissioni: int
    discarded_altri: int


@dataclass(frozen=True)
class CgtRecord:
    guid: str
    tipo: str
    data: str
    grado: int
    luogo: str
    sezione: str | None
    numero: str | None
    anno: int
    estremi: str
    text_raw_original: str
    source_url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CgtSkipped:
    """A document that has no text at the source: it goes to ``cgt_skipped`` for the honest coverage count."""

    guid: str
    estremi: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def js_unescape(literal: str) -> str:
    """Decodes the body of a JavaScript single-quoted string literal (``\\uXXXX``, ``\\xHH``, ``\\n``..., any other ``\\c`` is ``c``)."""
    out: list[str] = []
    i = 0
    size = len(literal)
    while i < size:
        char = literal[i]
        if char != "\\" or i + 1 >= size:
            out.append(char)
            i += 1
            continue
        nxt = literal[i + 1]
        if nxt == "u" and _HEX.fullmatch(literal[i + 2 : i + 6]) and len(literal[i + 2 : i + 6]) == 4:  # noqa: PLR2004
            out.append(chr(int(literal[i + 2 : i + 6], 16)))
            i += 6
        elif nxt == "x" and _HEX.fullmatch(literal[i + 2 : i + 4]) and len(literal[i + 2 : i + 4]) == 2:  # noqa: PLR2004
            out.append(chr(int(literal[i + 2 : i + 4], 16)))
            i += 4
        elif nxt in _JS_SIMPLE:
            out.append(_JS_SIMPLE[nxt])
            i += 2
        elif nxt == "\n":
            i += 2  # line continuation
        else:
            out.append(nxt)  # identity: \' \" \\ \/ and "\a-grave"
            i += 2
    return "".join(out).encode("utf-16", "surrogatepass").decode("utf-16")  # joins surrogate pairs written as two \uXXXX


def _extract_xml(response: bytes, carrier: re.Pattern[str]) -> Any:
    page = response.decode("iso-8859-1")
    match = carrier.search(page)
    if match is None:
        return None
    xml = _XML_DECLARATION.sub("", js_unescape(match.group(1)))
    return SafeElementTree.fromstring(xml.encode("utf-8"))


def classify_ente(ente: str) -> str:
    """Who issued the decision: ``cgt``, ``cassazione``, ``commissione`` (pre-reform) or ``altro``."""
    if _CGT_ENTE.match(ente):
        return "cgt"
    if "corte di cassazione" in ente.lower():
        return "cassazione"
    lowered = ente.lower()
    if lowered.startswith(("commissione tributaria", "comm. trib.")):
        return "commissione"
    return "altro"


def parse_cgt_ente(ente: str) -> tuple[int, str]:
    """``(grado, luogo)`` of a merit court, the place without its preposition."""
    match = _CGT_ENTE.match(ente)
    if match is None:
        message = f"not a Corte di giustizia tributaria: {ente!r}"
        raise ValueError(message)
    return (1 if match.group("grado").lower() == "primo" else 2), match.group("luogo").strip()


def _iso(date_text: str) -> str:
    return datetime.strptime(date_text, "%d/%m/%Y").date().isoformat()  # noqa: DTZ007 - a calendar date, no time zone involved


def parse_search(response: bytes) -> SearchSlice:
    """Reads one search answer: the declared total of the slice and the served decisions, merit courts kept, the rest counted."""
    root = _extract_xml(response, _XML_RESULT)
    if root is None:
        return SearchSlice(0, [], 0, 0, 0)
    counter = root.find(".//contatoreGiurisprudenza")
    declared = int(counter.text) if counter is not None and counter.text and counter.text.strip().isdigit() else 0
    items: list[CgtItem] = []
    discarded = {"cassazione": 0, "commissione": 0, "altro": 0}
    for provvedimento in root.iter("Provvedimento"):
        guid = provvedimento.get("idProvvedimento")
        estremi_node = provvedimento.find("estremi")
        estremi = (estremi_node.text or "").strip() if estremi_node is not None else ""
        if not guid or not estremi:
            continue
        match = _ESTREMI.match(estremi)
        if match is None:
            message = f"estremi giurisprudenza non riconosciuti: {estremi!r}"
            raise ValueError(message)
        ente = match.group("ente").strip()
        kind = classify_ente(ente)
        if kind != "cgt":
            discarded[kind] += 1
            continue
        grado, luogo = parse_cgt_ente(ente)
        items.append(CgtItem(guid, match.group("tipo").strip(), _iso(match.group("data")), match.group("num"), grado, luogo, match.group("sez"), estremi))
    return SearchSlice(declared, items, discarded["cassazione"], discarded["commissione"], discarded["altro"])


class _TextExtractor(HTMLParser):
    """Portal HTML -> text; block elements become line boundaries (otherwise "...processo" and "M.F. ha..." would be glued)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: ARG002
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(markup: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(markup)
    extractor.close()
    text = re.sub(r"[ \t]+", " ", "".join(extractor.parts))
    text = re.sub(r"[ ]*\n[ \n]*", "\n", text).strip()
    # the source is double-encoded (&amp;#768; in the CDATA): the parser resolved the first layer, resolve the second, recompose accents
    return unicodedata.normalize("NFC", html.unescape(text))


def parse_detail(response: bytes) -> str:
    """The full text of a decision from its detail page (element ``testo`` of ``var xmlDettaglio``); empty when the page has none."""
    root = _extract_xml(response, _XML_DETAIL)
    if root is None:
        return ""
    node = root.find(".//testo")
    markup = (node.text or "") if node is not None else ""
    return html_to_text(markup) if markup.strip() else ""


def build_record(item: CgtItem, detail_response: bytes) -> CgtRecord | CgtSkipped:
    """A kept decision plus its detail page: a record with its text, or a ``no_text`` skip (counted by the coverage figures)."""
    text = parse_detail(detail_response)
    if not text.strip():
        return CgtSkipped(item.guid, item.estremi, "no_text")
    return CgtRecord(
        guid=item.guid,
        tipo=item.tipo,
        data=item.data,
        grado=item.grado,
        luogo=item.luogo,
        sezione=item.sezione,
        numero=item.numero,
        anno=int(item.data[:4]),
        estremi=item.estremi,
        text_raw_original=text,
        source_url=DETAIL_URL + item.guid,
    )
