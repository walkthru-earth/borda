/**
 * Client-side semantic search.
 *
 * The query is embedded in the browser with transformers.js using the *same* ONNX file the
 * pipeline used (Xenova/all-MiniLM-L6-v2, q8) – so query and product vectors share one
 * space. Product vectors come from embeddings.parquet as int8 (384 B each); scoring is a
 * dot product over the whole matrix (20k products ≈ 8M MACs, well under a frame).
 * WebGPU is used for the encoder when available, WASM otherwise. Everything is lazy: nothing
 * is downloaded until the user asks for semantic results.
 */
import { loadEmbeddings } from './parquet';

export const MODEL_ID = 'Xenova/all-MiniLM-L6-v2';

type Extractor = (text: string, opts: { pooling: 'mean'; normalize: boolean }) => Promise<{ data: Float32Array }>;

let extractorPromise: Promise<Extractor> | null = null;
let matrixPromise: ReturnType<typeof loadEmbeddings> | null = null;

export type Progress = { status: string; progress?: number; file?: string };

export function semanticReady(): boolean {
	return extractorPromise !== null && matrixPromise !== null;
}

export async function loadEncoder(onProgress?: (p: Progress) => void): Promise<Extractor> {
	extractorPromise ??= (async () => {
		const tf = await import('@huggingface/transformers');
		const webgpu = typeof navigator !== 'undefined' && 'gpu' in navigator;
		const pipe = await tf.pipeline('feature-extraction', MODEL_ID, {
			dtype: 'q8',
			device: webgpu ? 'webgpu' : 'wasm',
			progress_callback: onProgress as never
		});
		return pipe as unknown as Extractor;
	})();
	return extractorPromise;
}

export function loadMatrix() {
	matrixPromise ??= loadEmbeddings();
	return matrixPromise;
}

export interface Hit { id: string; score: number }

export async function semanticSearch(query: string, k = 60, onProgress?: (p: Progress) => void): Promise<Hit[]> {
	const [extract, { ids, dim, vectors }] = await Promise.all([loadEncoder(onProgress), loadMatrix()]);
	const out = await extract(query, { pooling: 'mean', normalize: true });
	const q = new Int8Array(dim);
	for (let d = 0; d < dim; d++) q[d] = Math.max(-127, Math.min(127, Math.round(out.data[d] * 127)));

	const n = ids.length;
	const scores = new Float32Array(n);
	for (let i = 0, off = 0; i < n; i++, off += dim) {
		let dot = 0;
		for (let d = 0; d < dim; d++) dot += vectors[off + d] * q[d];
		scores[i] = dot / (127 * 127);
	}
	const order = Array.from(scores.keys()).sort((a, b) => scores[b] - scores[a]).slice(0, k);
	return order.filter((i) => scores[i] > 0.25).map((i) => ({ id: ids[i], score: scores[i] }));
}

/** Reciprocal-rank fusion of lexical and semantic rankings. */
export function fuse(lexical: string[], semantic: Hit[], k = 60): string[] {
	const score = new Map<string, number>();
	lexical.forEach((id, r) => score.set(id, (score.get(id) ?? 0) + 1 / (k + r)));
	semantic.forEach((h, r) => score.set(h.id, (score.get(h.id) ?? 0) + 1 / (k + r)));
	return [...score.entries()].sort((a, b) => b[1] - a[1]).map(([id]) => id);
}
