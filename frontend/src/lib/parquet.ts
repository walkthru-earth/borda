/**
 * Data layer: reads the pipeline's Parquet files in the browser with hyparquet.
 *
 * Strategy (mirrors src/borda/storage/parquet.py):
 *  - manifest.json first (never cached): per-file sha256 / byte length / footer size.
 *    Every Parquet URL is versioned with the sha, so it is immutable and safely cacheable.
 *  - catalog and stats are read with HTTP range requests and *column projection*: the footer
 *    in ONE exact read (the manifest tells us its size), then one coalesced range per row
 *    group covering only the columns the page needs (see projection.ts). The list view
 *    therefore downloads ~2.6 MB instead of the ~7 MB of both whole files; the product page
 *    adds one range for its row group of detail columns instead of re-reading the catalog.
 *  - every fetched range is persisted in the Cache API under its immutable versioned URL,
 *    so repeat visits are served from disk; stale versions are pruned on manifest load.
 *  - small whole-table files (redirects) and the lazily used embeddings are still single GETs.
 *  - files are zstd-compressed (≈40% smaller than snappy); hyparquet-compressors decodes them.
 *  - series/listings use hyparquet's own pruning: only the row groups whose statistics /
 *    Bloom filter can match `{product_id: {$eq}}` – a product's history costs ~2 requests.
 */
import {
	asyncBufferFromUrl,
	cachedAsyncBuffer,
	parquetMetadataAsync,
	parquetMetadata,
	parquetReadObjects,
	type AsyncBuffer,
	type FileMetaData,
	type ParquetQueryFilter
} from 'hyparquet';
import { compressors } from 'hyparquet-compressors'; // zstd (+ WASM snappy) for the browser
import { base } from '$app/paths';
import type { MarketProfile } from './profile.js';
import { candidateRowGroups, DETAIL_COLUMNS, LIST_COLUMNS, planColumnRanges, prefetched, STATS_COLUMNS, type ByteRange } from './projection';
import { arabicText } from './search';

export const DATA_BASE: string = ((import.meta.env.VITE_DATA_BASE as string | undefined) ?? `${base}/data`).replace(/\/$/, '');
const CACHE_NAME = 'borda-parquet-v2';

export interface FileInfo { sha256: string; bytes: number; footer: number | null; rows: number | null; row_groups: number | null }
export interface Manifest {
	profile?: MarketProfile;
	schema_version: number;
	pipeline_version: string;
	parquet_format: string;
	generated_at: string;
	/** When the exports were last written (run or rebuild); missing in older manifests. */
	exported_at?: string;
	products: number;
	offers_total: number;
	groups: Record<string, number>;
	files: Record<string, FileInfo>;
	runs: { run_id: string; ts: string; products: number; offers: number; new_products: number; stores_ok: number; stores_failed: number }[];
}

/** List-view projection of catalog.parquet (see LIST_COLUMNS). */
export interface Product {
	id: string;
	canonical_name: string;
	canonical_name_ar?: string | null;
	raw_names: string[];
	tags: string[];
	brand: string | null;
	category: string | null;
	group: string | null;
	image: string | null;
	sellers: string[];
	enriched: boolean;
	/** Manufacturer part number; searchable, optional in older snapshots. */
	mpn?: string | null;
}
/** Product-page projection of catalog.parquet (see DETAIL_COLUMNS); merge with the list `Product`. */
export interface ProductDetail {
	id: string;
	description: string | null;
	description_ar?: string | null;
	specs: string[];
	specs_ar?: string[];
	datasheet_url: string | null;
	listings: Record<string, string>;
	/** Nearest neighbours by embedding, best first. */
	similar: string[];
	/** JSON: `{"description_source": "seller"}` marks a summary distilled from seller text. */
	extra_metadata?: string | null;
}
export interface Listing { product_id: string; listing_key: string; seller: string; url: string; raw_name: string; description: string | null; links: string[] }
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
	/** Latest observed listings; undefined for older data exports. */
	current_offers?: CurrentOffer[];
}
export type Availability = 'in_stock' | 'out_of_stock' | 'preorder' | 'unknown';
export interface CurrentOffer { ts: Date; seller: string; price: number | null; currency: string; availability: Availability; url: string }
export interface Point extends CurrentOffer { product_id: string }

let manifestPromise: Promise<Manifest> | null = null;
export function loadManifest(): Promise<Manifest> {
	manifestPromise ??= fetch(`${DATA_BASE}/manifest.json`, { cache: 'no-cache' }).then(async (r) => {
		if (!r.ok) throw new Error(`manifest.json: HTTP ${r.status}`);
		const manifest = (await r.json()) as Manifest;
		void pruneCache(manifest);
		return manifest;
	}).catch((error) => { manifestPromise = null; throw error; });
	return manifestPromise;
}

async function versioned(path: string): Promise<{ url: string; info: FileInfo | undefined }> {
	const m = await loadManifest();
	const info = m.files[path];
	return { url: `${DATA_BASE}/${path}${info ? `?v=${info.sha256.slice(0, 12)}` : ''}`, info };
}

// ---------------------------------------------------------------------------- transport + cache

let cachePromise: Promise<Cache | null> | undefined;
function openCache(): Promise<Cache | null> {
	cachePromise ??= typeof caches === 'undefined' ? Promise.resolve(null) : caches.open(CACHE_NAME).catch(() => null);
	return cachePromise;
}

/** Drop cached ranges/files of versions the current manifest no longer references. */
async function pruneCache(manifest: Manifest): Promise<void> {
	const cache = await openCache();
	if (!cache) return;
	const live = new Set(Object.values(manifest.files).map((info) => info.sha256.slice(0, 12)));
	try {
		for (const request of await cache.keys()) {
			const version = new URL(request.url).searchParams.get('v');
			if (version && !live.has(version)) void cache.delete(request).catch(() => undefined);
		}
	} catch { /* Cache enumeration is best effort. */ }
}

async function cacheGet(key: string): Promise<ArrayBuffer | undefined> {
	const cache = await openCache();
	const hit = await cache?.match(key).catch(() => undefined);
	return hit ? hit.arrayBuffer() : undefined;
}
function cachePut(key: string, body: ArrayBuffer): void {
	// Cache.put refuses 206 responses, so the range is stored as a plain 200 under its own key.
	void openCache().then((cache) => cache?.put(key, new Response(body.slice(0), { headers: { 'Content-Type': 'application/octet-stream' } }))).catch(() => undefined);
}

/** One HTTP range request `[start, end)`, persisted in the Cache API. */
async function fetchRange(url: string, path: string, start: number, end: number): Promise<ArrayBuffer> {
	const key = `${url}${url.includes('?') ? '&' : '?'}range=${start}-${end - 1}`;
	const cached = await cacheGet(key);
	if (cached && cached.byteLength === end - start) return cached;
	const res = await fetch(url, { headers: { Range: `bytes=${start}-${end - 1}` } });
	let body: ArrayBuffer;
	if (res.status === 206) body = await res.arrayBuffer();
	else if (res.status === 200) body = (await res.arrayBuffer()).slice(start, end); // server ignored Range
	else throw new Error(`${path}: HTTP ${res.status}`);
	if (body.byteLength !== end - start) throw new Error(`${path}: short range read (${body.byteLength} of ${end - start} bytes)`);
	cachePut(key, body);
	return body;
}

/** Whole-file fetch with Cache API persistence (immutable, versioned URL). */
async function wholeFile(path: string): Promise<ArrayBuffer> {
	const { url } = await versioned(path);
	const cached = await cacheGet(url);
	if (cached) return cached;
	const res = await fetch(url);
	if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
	const body = await res.arrayBuffer();
	cachePut(url, body);
	return body;
}

const wholeBuffers = new Map<string, Promise<ArrayBuffer>>();
function whole(path: string): Promise<ArrayBuffer> {
	let p = wholeBuffers.get(path);
	if (!p) {
		p = wholeFile(path).catch((error) => { wholeBuffers.delete(path); throw error; });
		wholeBuffers.set(path, p);
	}
	return p;
}

/** Range-request reader: exact footer fetch (size from the manifest), persisted + in-memory range cache. */
interface Remote { file: AsyncBuffer; metadata: FileMetaData }
const remotes = new Map<string, Promise<Remote>>();
function remote(path: string): Promise<Remote> {
	let p = remotes.get(path);
	if (!p) {
		p = (async () => {
			const { url, info } = await versioned(path);
			const raw: AsyncBuffer = info?.bytes
				? { byteLength: info.bytes, slice: (start, end = info.bytes) => fetchRange(url, path, start, end) }
				: await asyncBufferFromUrl({ url });
			const file = cachedAsyncBuffer(raw);
			const metadata = await parquetMetadataAsync(file, { initialFetchSize: info?.footer || undefined });
			return { file, metadata };
		})().catch((error) => { remotes.delete(path); throw error; });
		remotes.set(path, p);
	}
	return p;
}

function availableColumns(metadata: FileMetaData, columns: string[]): string[] {
	// Optional additive columns must not make pre-translation snapshots unreadable.
	const available = new Set(metadata.schema.map((column) => column.name));
	return columns.filter((column) => available.has(column));
}

/**
 * Read `columns` of the row groups that can hold `key` (all of them without a key) through
 * coalesced range requests: the selected chunks of each row group are fetched as one run,
 * then hyparquet decodes from memory.
 */
async function readProjected<T>(path: string, columns: string[], key?: { column: string; value: string }): Promise<T[]> {
	const { file, metadata } = await remote(path);
	const selected = availableColumns(metadata, key ? [...new Set([...columns, key.column])] : columns);
	const rowGroups = key ? candidateRowGroups(metadata, key.column, key.value) : undefined;
	if (rowGroups && rowGroups.length === 0) return [];
	const ranges = planColumnRanges(metadata, selected, { rowGroups });
	// Runs go through the in-memory range cache too, so a re-read of the same projection is free.
	const source = prefetched(file, ranges, (range: ByteRange) => Promise.resolve(file.slice(range.start, range.end)));
	const filter: ParquetQueryFilter | undefined = key ? { [key.column]: { $eq: key.value } } : undefined;
	// Statistics already pruned the row groups; skip Bloom/page-index round trips.
	return (await parquetReadObjects({ file: source, metadata, columns: selected, filter, compressors, useBloomFilters: false, usePageIndex: false })) as T[];
}

async function readWhole<T>(path: string, columns?: string[], filter?: ParquetQueryFilter): Promise<T[]> {
	const file = await whole(path);
	const metadata = parquetMetadata(file);
	const selectedColumns = columns ? availableColumns(metadata, columns) : undefined;
	return (await parquetReadObjects({ file, metadata, columns: selectedColumns, filter, compressors })) as T[];
}

// ---------------------------------------------------------------------------- public API

/** Older snapshots may carry non-Arabic text in Arabic fields; normalise once at load time. */
function withArabicFallback<T extends { canonical_name_ar?: string | null; description_ar?: string | null }>(row: T): T {
	if ('canonical_name_ar' in row) row.canonical_name_ar = arabicText(row.canonical_name_ar);
	if ('description_ar' in row) row.description_ar = arabicText(row.description_ar);
	return row;
}

export async function loadCatalog(): Promise<Product[]> {
	return (await readProjected<Product>('catalog.parquet', LIST_COLUMNS)).map(withArabicFallback);
}

export async function loadProductDetail(id: string): Promise<ProductDetail | null> {
	const rows = await readProjected<Omit<ProductDetail, 'listings'> & { listings: string | null }>('catalog.parquet', DETAIL_COLUMNS, { column: 'id', value: id });
	const r = rows[0];
	return r ? withArabicFallback({ ...r, similar: r.similar ?? [], listings: JSON.parse(r.listings || '{}') as Record<string, string> }) : null;
}

export async function loadStats(): Promise<Map<string, Stats>> {
	// Optional columns (current_offers) are dropped from the projection when a snapshot lacks them.
	const rows = await readProjected<Omit<Stats, 'current_offers'> & { current_offers?: string | null }>('stats.parquet', STATS_COLUMNS);
	return new Map(rows.map((row) => {
		const { current_offers, ...stats } = row;
		const offers: CurrentOffer[] | undefined = current_offers == null ? undefined : JSON.parse(current_offers);
		return [row.product_id, { ...stats, current_offers: offers?.map((offer) => ({ ...offer, ts: new Date(offer.ts) })) }];
	}));
}

/** Price history for one or more products via pruned range reads on series.parquet. */
export async function loadSeries(ids: string[], options: { strict?: boolean } = {}): Promise<Point[]> {
	if (ids.length === 0) return [];
	try {
		const { file, metadata } = await remote('series.parquet');
		const filter: ParquetQueryFilter = ids.length === 1 ? { product_id: { $eq: ids[0] } } : { product_id: { $in: ids } };
		const rows = (await parquetReadObjects({ file, metadata, filter, compressors, useBloomFilters: true })) as Point[];
		return rows.sort((a, b) => +a.ts - +b.ts);
	} catch (e) {
		if (options.strict) throw e;
		console.warn('series unavailable', e);
		return [];
	}
}

/** Seller descriptions + documentation links for one product (pruned range reads). */
export async function loadListings(productId: string, options: { strict?: boolean } = {}): Promise<Listing[]> {
	try {
		if (!(await loadManifest()).files['listings.parquet']) return [];
		const { file, metadata } = await remote('listings.parquet');
		return (await parquetReadObjects({ file, metadata, compressors, filter: { product_id: { $eq: productId } }, useBloomFilters: true })) as Listing[];
	} catch (error) {
		if (options.strict) throw error;
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

/**
 * int8 embedding matrix (n x 384) + ids, for client-side semantic search.
 * `model` is the encoder the pipeline recorded in the file's key/value metadata (`null` for
 * snapshots written before it was stored); callers must refuse a mismatch, otherwise query
 * vectors and product vectors live in different spaces.
 */
export async function loadEmbeddings(): Promise<{ ids: string[]; dim: number; vectors: Int8Array; model: string | null }> {
	const file = await whole('embeddings.parquet');
	const metadata = parquetMetadata(file);
	const model = metadata.key_value_metadata?.find((entry) => entry.key === 'model')?.value ?? null;
	const rows = (await parquetReadObjects({ file, metadata, columns: ['product_id', 'vec_i8'], compressors })) as { product_id: string; vec_i8: Uint8Array }[];
	const dim = rows[0]?.vec_i8.length ?? 384;
	const vectors = new Int8Array(rows.length * dim);
	if (dim !== 384 || rows.some((row) => row.vec_i8.length !== dim)) {
		throw new Error('The AI search index is incompatible with the query model. Refresh the catalog and try again.');
	}
	rows.forEach((r, i) => vectors.set(new Int8Array(r.vec_i8.buffer, r.vec_i8.byteOffset, dim), i * dim));
	return { ids: rows.map((r) => r.product_id), dim, vectors, model };
}
