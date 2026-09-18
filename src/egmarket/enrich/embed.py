"""Sentence embeddings for similarity search.

The exact ONNX file transformers.js downloads for `Xenova/all-MiniLM-L6-v2`
(`onnx/model_quantized.onnx`) is run here with onnxruntime, so vectors computed in the
browser for a query live in the same space as the vectors stored in `embeddings.parquet`.

* text = official name + brand + group + tags + (first 120 chars of) description
* mean pooling over attention mask, L2 normalised (sentence-transformers convention)
* stored as int8 (`round(v * 127)`), 384 bytes/product; cosine ≈ dot / 127²
* nearest neighbours (top-k) are precomputed so product pages need no model at all
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from ..models import Product
from ..normalize import Catalog
from ..storage import ParquetStore
from ..storage.parquet import EMBEDDING_DIM

log = logging.getLogger(__name__)

MODEL_REPO = "Xenova/all-MiniLM-L6-v2"
MODEL_FILE = "onnx/model_quantized.onnx"
MAX_TOKENS = 96


def product_text(p: Product) -> str:
    bits = [p.canonical_name]
    if p.brand:
        bits.append(p.brand)
    if p.group:
        bits.append(p.group.replace("-", " "))
    if p.tags:
        bits.append(" ".join(p.tags[:8]))
    if p.description:
        bits.append(p.description[:120])
    return " . ".join(bits)


@dataclass
class EmbedReport:
    computed: int = 0
    reused: int = 0
    neighbours: int = 0
    error: str | None = None


class Embedder:
    def __init__(self) -> None:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        self.tokenizer = Tokenizer.from_file(hf_hub_download(MODEL_REPO, "tokenizer.json"))
        self.tokenizer.enable_padding()
        self.tokenizer.enable_truncation(MAX_TOKENS)
        so = ort.SessionOptions()
        so.intra_op_num_threads = 4
        self.session = ort.InferenceSession(
            hf_hub_download(MODEL_REPO, MODEL_FILE), so, providers=["CPUExecutionProvider"]
        )

    def encode(self, texts: list[str], batch: int = 64) -> np.ndarray:
        out = np.empty((len(texts), EMBEDDING_DIM), dtype=np.float32)
        for i in range(0, len(texts), batch):
            enc = self.tokenizer.encode_batch(texts[i : i + batch])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            hidden = self.session.run(
                None,
                {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)},
            )[0]
            m = mask[..., None].astype(np.float32)
            pooled = (hidden * m).sum(1) / np.maximum(m.sum(1), 1e-9)
            out[i : i + len(enc)] = pooled / np.maximum(
                np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9
            )
        return out


def quantize(v: np.ndarray) -> bytes:
    return np.clip(np.rint(v * 127), -127, 127).astype(np.int8).tobytes()


def dequantize(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.int8).astype(np.float32) / 127.0


def embed_catalog(store: ParquetStore, catalog: Catalog, *, top_k: int = 8) -> EmbedReport:
    """Compute vectors for new/changed products (cache keyed by product id + text hash),
    write embeddings.parquet and fill `Product.similar` with the top-k cosine neighbours."""
    report = EmbedReport()
    products = catalog.sorted_products()
    if not products:
        return report
    cached = store.read_embeddings()
    texts = {p.id: product_text(p) for p in products}
    hashes = {pid: _h(t) for pid, t in texts.items()}
    vectors: dict[str, tuple[bytes, str]] = {}
    todo: list[Product] = []
    for p in products:
        hit = cached.get(p.id)
        if hit and hit[1] == hashes[p.id]:
            vectors[p.id] = hit
        else:
            todo.append(p)
    report.reused = len(vectors)

    log.info("embeddings: %d cached, %d to compute", len(vectors), len(todo))
    if todo:
        try:
            embedder = Embedder()
            vecs = embedder.encode([texts[p.id] for p in todo])
        except Exception as exc:  # noqa: BLE001 - model download/runtime problems are non-fatal
            report.error = f"{type(exc).__name__}: {exc}"
            log.warning("embeddings skipped: %s", report.error)
            if not vectors:
                return report
        else:
            for p, v in zip(todo, vecs, strict=True):
                vectors[p.id] = (quantize(v), hashes[p.id])
            report.computed = len(todo)
    store.write_embeddings(vectors, MODEL_REPO)

    # nearest neighbours: dense float32 matmul in chunks (20k x 20k is fine)
    ids = [p.id for p in products if p.id in vectors]
    if len(ids) < 2:
        return report
    mat = np.stack([dequantize(vectors[i][0]) for i in ids]).astype(np.float32)
    k = min(top_k, len(ids) - 1)
    for start in range(0, len(ids), 2048):
        sims = mat[start : start + 2048] @ mat.T
        for row, sim in enumerate(sims):
            sim[start + row] = -1.0  # exclude self
            best = np.argpartition(-sim, k)[:k]
            best = best[np.argsort(-sim[best])]
            catalog.products[ids[start + row]].similar = [ids[j] for j in best if sim[j] > 0.35]
            report.neighbours += 1
    return report


def _h(text: str) -> str:
    import hashlib

    return hashlib.sha1(text.encode()).hexdigest()[:12]
