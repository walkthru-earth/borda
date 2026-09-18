"""Lightweight in-memory inverted index (CLI search; the frontend builds the same
structure client-side from catalog.parquet – see frontend/src/lib/search.ts).

  {"ids": ["esp32-devkit", ...],
   "docs": [["ESP32 DevKit V1", ["esp32","wifi"], 3], ...],   # name, tags, seller count
   "tokens": {"esp32": [0, 17, 42], ...}}                    # token -> doc positions
Tokens come from canonical names, *all* raw local-market names and tags, so a query
using a local spelling still finds the official product."""

from __future__ import annotations

from collections import defaultdict

from ..models import Product
from ..normalize import names

_MIN_TOKEN = 2


def tokenize(*texts: str) -> set[str]:
    out: set[str] = set()
    for t in texts:
        for tok in names.tokens(names.clean(t)):
            if len(tok) >= _MIN_TOKEN:
                out.add(tok)
                # also index sub-tokens of part numbers: "esp32-wroom-32" -> esp32, wroom, 32
                if "-" in tok:
                    out.update(x for x in tok.split("-") if len(x) >= _MIN_TOKEN)
    return out


def build_index(products: list[Product]) -> dict:
    ids, docs = [], []
    tokens: dict[str, list[int]] = defaultdict(list)
    for pos, p in enumerate(products):
        ids.append(p.id)
        docs.append([p.canonical_name, p.tags, len(p.sellers)])
        for tok in sorted(tokenize(p.canonical_name, *p.raw_names, *p.tags)):
            tokens[tok].append(pos)
    return {"ids": ids, "docs": docs, "tokens": dict(sorted(tokens.items()))}


def search(index: dict, query: str, limit: int = 20) -> list[tuple[str, str, int]]:
    """AND-search over tokens with prefix fallback; returns (id, name, score)."""
    q_tokens = tokenize(query)
    if not q_tokens:
        return []
    scores: dict[int, int] = defaultdict(int)
    for qt in q_tokens:
        hits = set(index["tokens"].get(qt, []))
        if not hits:  # prefix match
            for tok, positions in index["tokens"].items():
                if tok.startswith(qt):
                    hits.update(positions)
        for pos in hits:
            scores[pos] += 1
    ranked = sorted(
        (pos for pos, s in scores.items() if s == len(q_tokens)),
        key=lambda pos: (-index["docs"][pos][2], index["docs"][pos][0]),
    )
    return [(index["ids"][p], index["docs"][p][0], index["docs"][p][2]) for p in ranked[:limit]]
