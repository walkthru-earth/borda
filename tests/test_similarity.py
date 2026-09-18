"""Taxonomy groups + embedding cache/neighbours (embedder mocked – no model download)."""

import numpy as np
import pyarrow.parquet as pq

from egmarket.enrich import embed
from egmarket.normalize import Catalog
from egmarket.normalize.categories import GROUPS, assign_group
from egmarket.storage import ParquetStore


def test_assign_group_rules():
    assert assign_group(name="Arduino Uno R3", tags=["arduino"]) == "dev-boards"
    assert assign_group(name="HC-SR04 Ultrasonic Sensor", tags=[]) == "sensors"
    assert assign_group(name="LM2596 Buck Converter Module", tags=[]) == "power"
    assert assign_group(name="Carbon Film Resistor 10K", tags=[]) == "passive-components"
    assert (
        assign_group(name="Mystery item", tags=[], store_category="Soldering Tools")
        == "tools-instruments"
    )
    assert assign_group(name="zzz", tags=[]) == "other"
    assert set(GROUPS) >= {"dev-boards", "sensors", "other"}


class FakeEmbedder:
    """Deterministic vectors: products sharing a keyword land close together."""

    def encode(self, texts):
        out = np.zeros((len(texts), embed.EMBEDDING_DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            for tok in t.lower().split():
                out[i, hash(tok) % embed.EMBEDDING_DIM] += 1
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)


def test_embeddings_cached_and_neighbours(tmp_path, make_offer, monkeypatch):
    monkeypatch.setattr(embed, "Embedder", FakeEmbedder)
    store = ParquetStore(tmp_path)
    cat = Catalog()
    for name in [
        "ESP32 DevKit V1 wifi board",
        "ESP32 CAM wifi camera board",
        "10k resistor pack",
        "100k resistor pack",
    ]:
        cat.resolve(make_offer("s1", name, 10), fuzzy_threshold=93)

    r1 = embed.embed_catalog(store, cat, top_k=2)
    assert r1.computed == 4 and r1.reused == 0 and r1.neighbours == 4
    esp = next(p for p in cat.products.values() if "devkit" in p.id)
    assert esp.similar and "cam" in esp.similar[0]

    t = pq.read_table(tmp_path / "embeddings.parquet")
    assert t.num_rows == 4 and t.schema.field("vec_i8").type.byte_width == embed.EMBEDDING_DIM
    assert t.schema.metadata[b"model"] == embed.MODEL_REPO.encode()
    v = embed.dequantize(t["vec_i8"][0].as_py())
    assert abs(float(np.linalg.norm(v)) - 1) < 0.05

    r2 = embed.embed_catalog(store, cat, top_k=2)  # unchanged text -> everything reused
    assert r2.computed == 0 and r2.reused == 4
