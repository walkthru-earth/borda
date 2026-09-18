/**
 * Read the pipeline's Parquet files directly in the browser with hyparquet.
 * Layout mirrors src/egmarket/storage/parquet.py; files are snappy + Parquet v2, so no
 * extra decompressors are needed. Range requests keep product-page loads small.
 */
import { asyncBufferFromUrl, parquetReadObjects, type AsyncBuffer } from 'hyparquet';
import { base } from '$app/paths';

/** Where the `data/` directory is served from. Override for GitHub Pages / raw hosting. */
export const DATA_BASE: string =
	(import.meta.env.VITE_DATA_BASE as string | undefined) ?? `${base}/data`;

export interface Product {
	id: string;
	canonical_name: string;
	raw_names: string[];
	tags: string[];
	description: string | null;
	category: string | null;
	brand: string | null;
	image: string | null;
	sellers: string[];
	listings: Record<string, string>;
	enriched: boolean;
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

export interface Point {
	product_id: string;
	ts: Date;
	seller: string;
	price: number | null;
	currency: string;
	availability: 'in_stock' | 'out_of_stock' | 'preorder' | 'unknown';
	url: string;
}

export interface Manifest {
	schema_version: number;
	pipeline_version: string;
	parquet_format: string;
	generated_at: string;
	products: number;
	offers_total: number;
	files: Record<string, string>;
	runs: { run_id: string; ts: string; products: number; offers: number; stores_ok: number; stores_failed: number }[];
}

const buffers = new Map<string, Promise<AsyncBuffer>>();

function file(path: string): Promise<AsyncBuffer> {
	const url = `${DATA_BASE}/${path}`;
	let p = buffers.get(url);
	if (!p) {
		p = asyncBufferFromUrl({ url });
		buffers.set(url, p);
	}
	return p;
}

async function read<T>(path: string, columns?: string[]): Promise<T[]> {
	const rows = await parquetReadObjects({ file: await file(path), columns });
	return rows as T[];
}

export async function loadManifest(): Promise<Manifest> {
	const r = await fetch(`${DATA_BASE}/manifest.json`);
	if (!r.ok) throw new Error(`manifest.json: HTTP ${r.status}`);
	return (await r.json()) as Manifest;
}

export async function loadCatalog(): Promise<Product[]> {
	const rows = await read<Omit<Product, 'listings'> & { listings: string }>('catalog.parquet');
	return rows.map((r) => ({ ...r, listings: JSON.parse(r.listings || '{}') as Record<string, string> }));
}

export async function loadStats(): Promise<Map<string, Stats>> {
	const rows = await read<Stats>('stats.parquet', [
		'product_id', 'currency', 'min', 'max', 'median', 'latest_min', 'latest_ts', 'in_stock_sellers', 'observations'
	]);
	return new Map(rows.map((r) => [r.product_id, r]));
}

/** Time series for one product: only its 2-char bucket file is fetched. */
export async function loadSeries(productId: string): Promise<Point[]> {
	const bucket = productId.slice(0, 2);
	try {
		const rows = await read<Point>(`series/bucket=${bucket}/points.parquet`);
		return rows.filter((r) => r.product_id === productId).sort((a, b) => +a.ts - +b.ts);
	} catch {
		return []; // product exists in catalog but has no chartable points yet
	}
}
