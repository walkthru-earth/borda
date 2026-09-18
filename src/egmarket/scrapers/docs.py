"""Pull datasheet / documentation links out of seller description HTML."""

from __future__ import annotations

import re
from urllib.parse import urljoin

_HREF = re.compile(r"""<a\s[^>]*?href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_DOC_HINT = re.compile(
    r"datasheet|data sheet|\.pdf(\?|$)|manual|schematic|pinout|wiki|docs?\b|documentation|"
    r"reference|specification|guide|tutorial|github\.com|arduino\.cc|espressif|raspberrypi|"
    r"adafruit|sparkfun|ti\.com|st\.com|microchip\.com|nxp\.com|analog\.com|onsemi|infineon",
    re.I,
)
_SKIP = re.compile(
    r"facebook|instagram|twitter|youtube\.com/(channel|@)|whatsapp|tiktok|/cart|/checkout|mailto:|tel:",
    re.I,
)


def extract_doc_links(fragment: str | None, base_url: str) -> list[str]:
    """Return absolute links whose anchor text or href looks like documentation.
    Links with 'datasheet' + .pdf sort first so the best candidate is at index 0."""
    if not fragment:
        return []
    found: list[tuple[int, str]] = []
    for href, inner in _HREF.findall(fragment):
        text = _TAGS.sub(" ", inner)
        url = urljoin(base_url, href.strip())
        if not url.startswith("http") or _SKIP.search(url):
            continue
        blob = f"{text} {url}"
        if not _DOC_HINT.search(blob):
            continue
        score = 0
        if re.search(r"datasheet|data sheet", blob, re.I):
            score -= 4
        if re.search(r"\.pdf(\?|$)", url, re.I):
            score -= 2
        if re.search(r"manual|schematic|pinout|specification", blob, re.I):
            score -= 1
        found.append((score, url))
    out: list[str] = []
    for _, url in sorted(found):
        if url not in out:
            out.append(url)
    return out[:10]


def rank_datasheet(links: list[str]) -> str | None:
    """Best datasheet candidate among collected links (None if nothing looks like one)."""
    best: tuple[int, str] | None = None
    for u in links:
        score = 0
        if re.search(r"datasheet|data sheet", u, re.I):
            score -= 4
        if re.search(r"\.pdf(\?|$)", u, re.I):
            score -= 2
        if re.search(
            r"ti\.com|st\.com|microchip|nxp|analog\.com|espressif|onsemi|infineon|"
            r"raspberrypi\.com/documentation|docs\.arduino\.cc",
            u,
            re.I,
        ):
            score -= 1
        if score < 0 and (best is None or score < best[0]):
            best = (score, u)
    return best[1] if best else None
