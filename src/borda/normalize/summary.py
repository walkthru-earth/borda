"""Compact product info distilled from seller listing text – no model call needed.

Most products never reach the (rate-limited) LLM, yet almost every listing ships a seller
description: a few sentences of prose plus a loose "Key: value" table. `summarize()` turns
that into the same compact shape the enrichment produces – one or two neutral sentences and
up to six "Key: value" spec highlights – so a product page is useful before enrichment and
the model's answer simply replaces it later.

Everything here is deterministic and conservative: marketing, first-person seller talk,
section headings, pin-outs and prices are dropped rather than paraphrased.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import names

MAX_DESCRIPTION = 320
MAX_SPECS = 6
_MAX_KEY, _MAX_VALUE = 32, 60

_SECTION_HEADINGS = frozenset(
    {
        "feature",
        "features",
        "key features",
        "main features",
        "application",
        "applications",
        "specification",
        "specifications",
        "technical specifications",
        "technical specification",
        "specs",
        "spec",
        "description",
        "product description",
        "introduction",
        "overview",
        "note",
        "notes",
        "important notes",
        "important",
        "warning",
        "warnings",
        "package",
        "package contents",
        "package includes",
        "package included",
        "package list",
        "packing list",
        "what's in the box",
        "in the box",
        "contents",
        "includes",
        "including",
        "pin configuration",
        "pinout",
        "pin out",
        "pin description",
        "design guidelines",
        "usage",
        "how to use",
        "data sheet",
        "datasheet",
        "documents",
        "documentation",
        "resources",
        "attribute",
        "value",
        "parameter",
        "parameters",
        "item",
        "details",
        "product details",
        "highlights",
        "more info",
        "shipping",
        "warranty",
        "faq",
    }
)
# keys that never help a comparison shopper
_USELESS_KEYS = re.compile(
    r"^(?:category|categories|rohs|sku|price|stock|availability|quantity|qty|brand|manufacturer|"
    r"vendor|seller|shipping|warranty|delivery|barcode|ean|upc|tags?|product name|prodcut name|"
    r"name|title|part number|part no\.?|mpn|model no\.?|item|link|url|datasheet|note|notes|"
    r"pin ?\d+|pins? ?\d+|weight \(with packaging\)|packaging|origin|country of origin|"
    r"package contents?|package includes?|packing list|contents?|includes?|including|"
    r"applications?|features?|key features|description|introduction|overview|usage)$",
    re.I,
)
_PRIORITY_KEYS = (
    "voltage",
    "current",
    "power",
    "interface",
    "package",
    "type",
    "capacity",
    "range",
    "resolution",
    "frequency",
    "output",
    "input",
    "channels",
    "pins",
    "chip",
    "model",
    "size",
    "dimension",
    "material",
    "speed",
    "torque",
    "accuracy",
    "temperature",
    "wavelength",
    "resistance",
    "tolerance",
    "length",
    "connector",
    "protocol",
    "supply",
    "communication",
    "memory",
    "flash",
    "ram",
    "cpu",
    "core",
    "sensor",
    "display",
    "color",
)
_KV_LINE = re.compile(r"^\s*([^:：;]{1,40}?)\s*[:：]\s*(\S.{0,200}?)\s*$")
_LABEL_LINE = re.compile(r"^[A-Za-z][A-Za-z0-9 ()/+&%°Ωµ-]{1,31}$")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(])")
_FLUFF = re.compile(
    r"\b(?:you|your|yours|we|our|us|buy|order|shop|price|prices|egp|le\b|discount|offer|"
    r"shipping|delivery|warranty|whatsapp|call|contact|click|visit|guarantee|hurry|deal|"
    r"cheap|best|amazing|awesome|perfect|premium|quality|great|excellent|must-have|ideal|"
    r"experience|imagine|enjoy|love|feel|feels|thank|welcome|hello|please)\b",
    re.I,
)
_TECHNICAL = re.compile(
    r"\d|\b(?:module|sensor|board|driver|amplifier|regulator|converter|connector|cable|resistor|"
    r"capacitor|transistor|diode|led|display|motor|switch|battery|charger|tool|kit|adapter|pcb|"
    r"shield|antenna|relay|controller|microcontroller|interface|voltage|current|power|signal|"
    r"output|input|supports?|designed|operates?|provides?|features?|integrates?|includes?|"
    r"measures?|uses?|based|compatible|ic|chip|circuit|frequency|wireless|usb|i2c|spi|uart|"
    r"pwm|analog|digital|filament|printer|nozzle|screw|bearing|wire|plug|socket|terminal)\b",
    re.I,
)
_BULLET = re.compile(r"^\s*(?:[-–•*·▪●■✓✔➤►]\s*|\d{1,2}[.)]\s+)")


@dataclass
class Summary:
    description: str | None = None
    specs: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.description or self.specs)


def _lines(text: str, title: str | None) -> list[str]:
    title_key = names.clean(title) if title else None
    out: list[str] = []
    for raw in text.splitlines():
        line = _BULLET.sub("", raw).strip()
        if len(line) < 3:
            continue
        if title_key and names.clean(line) == title_key:
            continue  # sellers repeat the listing title as the first line
        out.append(line)
    return out


def _clean_value(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" ;,.-")
    return value[:_MAX_VALUE].rstrip(" ,;(-") if len(value) > _MAX_VALUE else value


def _clean_key(key: str) -> str:
    key = re.sub(r"\s+", " ", key).strip(" -*•:")
    key = re.sub(r"\s*\(.*?\)\s*$", "", key) if len(key) > _MAX_KEY else key
    return key[:_MAX_KEY].rstrip()


def _is_heading(text: str) -> bool:
    return names.clean(text).rstrip(" :") in _SECTION_HEADINGS


def _add_spec(specs: dict[str, str], key: str, value: str, *, pair: bool = False) -> None:
    key, value = _clean_key(key), _clean_value(value)
    if (
        not key
        or not value
        or (pair and _is_heading(key))  # "Key Features" / "Contains four op-amps" is not a spec
        or _USELESS_KEYS.match(key)
        or names.has_arabic(key)
        or key.lower() == value.lower()
        or not re.search(r"[A-Za-z]", key)
        or len(value) < 1
        or _FLUFF.search(value)
        or len(value.split()) > 9
    ):
        return
    specs.setdefault(key.lower(), f"{key}: {value}")


_SPEC_SECTION = re.compile(r"spec|param|detail|attribute|value|technical|data ?sheet|highlight")
_SKIP_SECTION = re.compile(
    r"application|pin|package|content|guideline|note|usage|how to|warning|shipping|warranty|"
    r"faq|document|resource|box|includ"
)


def extract_specs(lines: list[str]) -> list[str]:
    """`Key: value` lines (also `k : v;k2 : v2`) anywhere except in pin-out / package /
    application sections, plus label/value line pairs inside specification tables."""
    specs: dict[str, str] = {}
    section = ""  # "" = before the first heading
    i = 0
    while i < len(lines):
        line = lines[i]
        if _is_heading(line):
            section = names.clean(line).rstrip(" :")
            i += 1
            continue
        skip = bool(_SKIP_SECTION.search(section))
        if not skip and ";" in line and line.count(":") >= 2:
            for part in line.split(";"):
                if m := _KV_LINE.match(part):
                    _add_spec(specs, m.group(1), m.group(2))
            i += 1
            continue
        if not skip and (m := _KV_LINE.match(line)) and len(m.group(2)) <= 90:
            _add_spec(specs, m.group(1), m.group(2))
            i += 1
            continue
        # "Power (Watts)\n0.125W": table rows flattened to alternate lines
        if (
            (not section or _SPEC_SECTION.search(section))
            and i + 1 < len(lines)
            and _LABEL_LINE.match(line)
            and len(line.split()) <= 4
            and not re.search(r"\d", line)
            and len(lines[i + 1]) <= 40
            and not _is_heading(lines[i + 1])
            and not _KV_LINE.match(lines[i + 1])
        ):
            _add_spec(specs, line, lines[i + 1], pair=True)
            i += 2
            continue
        i += 1
    ordered = sorted(
        specs.values(),
        key=lambda s: next(
            (n for n, k in enumerate(_PRIORITY_KEYS) if k in s.split(":", 1)[0].lower()),
            len(_PRIORITY_KEYS),
        ),
    )
    return ordered[:MAX_SPECS]


_VERB = re.compile(
    r"\b(?:is|are|has|have|provides?|offers?|features?|supports?|allows?|enables?|delivers?|"
    r"designed|intended|used|uses?|combines?|integrates?|includes?|contains?|converts?|"
    r"measures?|controls?|drives?|operates?|works?|comes|consists|makes?|lets?|can|based)\b",
    re.I,
)


def extract_description(lines: list[str]) -> str | None:
    """The first one or two neutral, technical prose sentences – proper sentences with a verb
    first, bullet-like fragments only when nothing better exists."""
    return _extract_description(lines, require_verb=True) or _extract_description(
        lines, require_verb=False
    )


def _extract_description(lines: list[str], *, require_verb: bool) -> str | None:
    picked: list[str] = []
    total = 0
    for line in lines:
        if len(line) < 40 or _KV_LINE.match(line) or line.endswith(":") or _is_heading(line):
            continue
        if names.has_arabic(line):
            continue
        for sentence in _SENTENCE_SPLIT.split(line):
            sentence = sentence.strip()
            if len(sentence) < 40 or len(sentence) > MAX_DESCRIPTION:
                continue
            if _FLUFF.search(sentence) or not _TECHNICAL.search(sentence):
                continue
            if require_verb and (len(sentence.split()) < 6 or not _VERB.search(sentence)):
                continue
            if not re.match(r"^[A-Z0-9(\"']", sentence):
                continue
            if not sentence.endswith((".", "!", "?")):
                sentence += "."
            picked.append(sentence)
            total += len(sentence)
            if len(picked) == 2 or total >= 200:
                return " ".join(picked)[:MAX_DESCRIPTION]
        if picked:
            break  # stay within the first useful paragraph
    return " ".join(picked)[:MAX_DESCRIPTION] or None


def summarize(text: str | None, title: str | None = None) -> Summary:
    if not text:
        return Summary()
    lines = _lines(text, title)
    if not lines:
        return Summary()
    return Summary(description=extract_description(lines), specs=extract_specs(lines))
