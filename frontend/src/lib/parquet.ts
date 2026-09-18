/**
 * Data layer: reads the pipeline's Parquet files in the browser with hyparquet.
 *
 * Strategy (mirrors src/egmarket/storage/parquet.py):
 *  - manifest.json first (never cached): per-file sha256 / byte length / footer size.
 *    Every Parquet URL is versioned with the sha, so it is immutable and safely cacheable.
 *  - small, whole-table files (catalog, stats, embeddings) are fetched once as a single GET,
 *    persisted in the Cache API and parsed from memory.
 *  - series.parquet is read with range requests: the footer in ONE exact read (manifest
 *    tells us its size), then only the row groups whose statistics / Bloom filter can match
 *    `{product_id: {$eq}}` – a product's full price history costs ~2 small requests.
 */
import {
	asyncBufferFromUrl,
	cachedAsyncBuffer,
	parquetMetadataAsync,
	parquetReadObjects,
	type AsyncBuffer,
	type FileMetaData,
	type ParquetQueryFilter
} from 'hyparquet';
import { base } from '$app/paths';

export const DATA_BASE: string = (import.meta.env.VITE_DATA_BASE as string | undefined) ?? `${base}/data`;
const CACHE_NAME = 'egmarket-parquet-v1';

export interface FileInfo { sha256: string; bytes: number; footer: number | null; rows: number | null; row_groups: number | null }
export interface Manifest {
	schema_version: number;
	pipeline_version: string;
	parquet_format: string;
	generated_at: string;
	products: number;
	offers_total: number;
	groups: Record<string, number>;
	files: Record<string, FileInfo>;
	runs: { run_id: string; ts: string; products: number; offers: number; new_products: number; stores_ok: number; stores_failed: number }[];
}

export interface Product {
	id: string;
	canonical_name: string;
	raw_names: string[];
	tags: string[];
	brand: string | null;
	category: string | null;
	group: string | null;
	image: string | null;
	sellers: string[];
	similar: string[];
	enriched: boolean;
}
export interface ProductDetail extends Product {
	description: string | null;
	listings: Record<string, string>;
}
export interface Stats {
	product_id: string;
	currency: string;
	min: number | null;
	max: number | null;
	median: number | null;
	latest_min: number | null;
	latest_ts: Date | null;
	in_stock_sellers: number;
	observations: number;
}
export type Availability = 'in_stock' | 'out_of_stock' | 'preorder' | 'unknown';
export interface Point { product_id: string; ts: Date; seller: string; price: number | null; currency: string; availability: Availability; url: string }

let manifestPromise: Promise<Manifest> | null = null;
export function loadManifest(): Promise<Manifest> {
	manifestPromise ??= fetch(`${DATA_BASE}/manifest.json`, { cache: 'no-cache' }).then(async (r) => {
		if (!r.ok) throw new Error(`manifest.json: HTTP ${r.status}`);
		return (await r.json()) as Manifest;
	});
	return manifestPromise;
}

async function versioned(path: string): Promise<{ url: string; info: FileInfo | undefined }> {
	const m = await loadManifest();
	const info = m.files[path];
	return { url: `${DATA_BASE}/${path}${info ? `?v=${info.sha256.slice(0, 12)}` : ''}`, info };
}

/** Whole-file fetch with Cache API persistence (immutable, versioned URL). */
async function wholeFile(path: string): Promise<ArrayBuffer> {
	const { url } = await versioned(path);
	const cache = typeof caches !== 'undefined' ? await caches.open(CACHE_NAME).catch(() => null) : null;
	const hit = await cache?.match(url);
	if (hit) return hit.arrayBuffer();
	const res = await fetch(url);
	if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
	if (cache) void cache.put(url, res.clone()).catch(() => undefined);
	return res.arrayBuffer();
}

const wholeBuffers = new Map<string, Promise<ArrayBuffer>>();
function whole(path: string): Promise<ArrayBuffer> {
	let p = wholeBuffers.get(path);
	if (!p) { p = wholeFile(path); wholeBuffers.set(path, p); }
	return p;
}

/** Range-request reader with exact footer fetch and in-memory range cache. */
interface Remote { file: AsyncBuffer; metadata: FileMetaData }
const remotes = new Map<string, Promise<Remote>>();
function remote(path: string): Promise<Remote> {
	let p = remotes.get(path);
	if (!p) {
		p = (async () => {
			const { url, info } = await versioned(path);
			const raw = await asyncBufferFromUrl({ url, byteLength: info?.bytes });
			const file = cachedAsyncBuffer(raw);
			const metadata = await parquetMetadataAsync(file, { initialFetchSize: (info?.footer ?? 0) + 4096 || undefined });
			return { file, metadata };
		})();
		remotes.set(path, p);
	}
	return p;
}

async function readWhole<T>(path: string, columns?: string[], filter?: ParquetQueryFilter): Promise<T[]> {
	const file = await whole(path);
	return (await parquetReadObjects({ file, columns, filter })) as T[];
}

// ---------------------------------------------------------------------------- public API

const LIST_COLUMNS = ['id', 'canonical_name', 'raw_names', 'tags', 'brand', 'category', 'group', 'image', 'sellers', 'similar', 'enriched'];

export function loadCatalog(): Promise<Product[]> {
	return readWhole<Product>('catalog.parquet', LIST_COLUMNS);
}

export async function loadProductDetail(id: string): Promise<ProductDetail | null> {
	const rows = await readWhole<Omit<ProductDetail, 'listings'> & { listings: string }>(
		'catalog.parquet', [...LIST_COLUMNS, 'description', 'listings'], { id: { $eq: id } }
	);
	const r = rows[0];
	return r ? { ...r, listings: JSON.parse(r.listings || '{}') as Record<string, string> } : null;
}

export async function loadStats(): Promise<Map<string, Stats>> {
	const rows = await readWhole<Stats>('stats.parquet', ['product_id', 'currency', 'min', 'max', 'median', 'latest_min', 'latest_ts', 'in_stock_sellers', 'observations']);
	return new Map(rows.map((r) => [r.product_id, r]));
}

/** Price history for one or more products via pruned range reads on series.parquet. */
export async function loadSeries(ids: string[]): Promise<Point[]> {
	if (ids.length === 0) return [];
	try {
		const { file, metadata } = await remote('series.parquet');
		const filter: ParquetQueryFilter = ids.length === 1 ? { product_id: { $eq: ids[0] } } : { product_id: { $in: ids } };
		const rows = (await parquetReadObjects({ file, metadata, filter, useBloomFilters: true })) as Point[];
		return rows.sort((a, b) => +a.ts - +b.ts);
	} catch (e) {
		console.warn('series unavailable', e);
		return [];
	}
}

/** Old / merged product id -> surviving id (redirects.parquet is tiny; whole read). */
export async function resolveRedirect(id: string): Promise<string | null> {
	try {
		const rows = await readWhole<{ old_id: string; new_id: string }>('redirects.parquet', undefined, { old_id: { $eq: id } });
		return rows[0]?.new_id ?? null;
	} catch {
		return null;
	}
}

/** int8 embedding matrix (n x 384) + ids, for client-side semantic search. */
export async function loadEmbeddings(): Promise<{ ids: string[]; dim: number; vectors: Int8Array }> {
	const rows = await readWhole<{ product_id: string; vec_i8: Uint8Array }>('embeddings.parquet', ['product_id', 'vec_i8']);
	const dim = rows[0]?.vec_i8.length ?? 384;
	const vectors = new Int8Array(rows.length * dim);
	rows.forEach((r, i) => vectors.set(new Int8Array(r.vec_i8.buffer, r.vec_i8.byteOffset, dim), i * dim));
	return { ids: rows.map((r) => r.product_id), dim, vectors };
}
