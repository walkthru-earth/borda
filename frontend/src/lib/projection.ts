/**
 * Column projection over HTTP range requests.
 *
 * hyparquet fetches one byte range per column chunk when `columns` is given (11 row groups x
 * 13 columns = 143 requests for the catalog list). This module plans the byte ranges of the
 * selected column chunks itself, merges neighbouring chunks into one request per contiguous
 * run, and exposes the fetched runs as an `AsyncBuffer` so hyparquet's per-chunk slices are
 * served from memory. Pure functions, no SvelteKit imports: exercised by `tests/projection.test.mjs`.
 *
 * The Python exporter (`src/borda/storage/parquet.py`) writes the columns each page needs
 * first in schema order, so one range per row group covers a projection; any other column
 * order still works, just with more requests.
 */
import type { AsyncBuffer, ColumnChunk, FileMetaData } from 'hyparquet';

/** Half-open byte interval `[start, end)`. */
export interface ByteRange { start: number; end: number }

/** Columns the catalog list, search index and product cards need (`Product`). */
export const LIST_COLUMNS = ['id', 'canonical_name', 'canonical_name_ar', 'raw_names', 'tags', 'mpn', 'brand', 'category', 'group', 'image', 'sellers', 'enriched'];
/** Columns only the product page needs (`ProductDetail`) plus the `id` join key; `similar` alone is ~18% of the file. */
export const DETAIL_COLUMNS = ['id', 'similar', 'description', 'description_ar', 'specs', 'specs_ar', 'datasheet_url', 'listings', 'extra_metadata'];
/** stats.parquet also repeats name / image / tags for the SEO generator; the browser has those in the catalog. */
export const STATS_COLUMNS = ['product_id', 'currency', 'min', 'max', 'median', 'latest_min', 'latest_ts', 'in_stock_sellers', 'observations', 'current_offers'];

/**
 * Unread bytes tolerated between two chunks before they are fetched separately. On a mobile
 * connection one extra round trip costs about as much as ~32 KB of payload.
 */
export const DEFAULT_GAP = 32 * 1024;

function chunkRange(chunk: ColumnChunk): ByteRange | null {
	const meta = chunk.meta_data;
	if (!meta) return null;
	const start = Number(meta.dictionary_page_offset || meta.data_page_offset);
	return { start, end: start + Number(meta.total_compressed_size) };
}

/** Merge sorted-or-unsorted ranges whose gap is at most `gap` bytes. Never drops bytes. */
export function coalesce(ranges: ByteRange[], gap = DEFAULT_GAP): ByteRange[] {
	const sorted = ranges.filter((r) => r.end > r.start).map((r) => ({ ...r })).sort((a, b) => a.start - b.start || a.end - b.end);
	const merged: ByteRange[] = [];
	for (const range of sorted) {
		const last = merged[merged.length - 1];
		if (last && range.start - last.end <= gap) last.end = Math.max(last.end, range.end);
		else merged.push(range);
	}
	return merged;
}

/**
 * Byte ranges covering the column chunks of `columns` (top-level names) in `rowGroups`
 * (all groups when omitted), one range per contiguous run.
 */
export function planColumnRanges(
	metadata: FileMetaData,
	columns: string[],
	options: { rowGroups?: number[]; gap?: number } = {}
): ByteRange[] {
	const wanted = new Set(columns);
	const groups = options.rowGroups ?? metadata.row_groups.map((_, index) => index);
	const ranges: ByteRange[] = [];
	for (const index of groups) {
		const group = metadata.row_groups[index];
		if (!group) continue;
		for (const chunk of group.columns) {
			if (!wanted.has(chunk.meta_data?.path_in_schema[0] ?? '')) continue;
			const range = chunkRange(chunk);
			if (range) ranges.push(range);
		}
	}
	return coalesce(ranges, options.gap);
}

/**
 * Row groups whose `column` statistics admit `value` (superset of the true matches; groups
 * without statistics are kept). Files are sorted by their key column, so this is normally
 * a single group.
 */
export function candidateRowGroups(metadata: FileMetaData, column: string, value: string): number[] {
	const out: number[] = [];
	metadata.row_groups.forEach((group, index) => {
		if (Number(group.num_rows) === 0) return;
		const chunk = group.columns.find((c) => c.meta_data?.path_in_schema[0] === column);
		const stats = chunk?.meta_data?.statistics;
		const min = stats?.min_value ?? stats?.min;
		const max = stats?.max_value ?? stats?.max;
		if (typeof min === 'string' && value < min) return;
		if (typeof max === 'string' && value > max) return;
		out.push(index);
	});
	return out;
}

/** Number of requests + bytes a plan costs; handy for diagnostics and tests. */
export function planCost(ranges: ByteRange[]): { requests: number; bytes: number } {
	return { requests: ranges.length, bytes: ranges.reduce((sum, r) => sum + r.end - r.start, 0) };
}

/**
 * An `AsyncBuffer` that answers slices inside `ranges` from `load` (fetched eagerly, once)
 * and falls back to `file` for anything else.
 */
export function prefetched(file: AsyncBuffer, ranges: ByteRange[], load: (range: ByteRange) => Promise<ArrayBuffer>): AsyncBuffer {
	const buffers = ranges.map((range) => load(range));
	return {
		byteLength: file.byteLength,
		slice(start, end = file.byteLength) {
			const index = ranges.findIndex((r) => r.start <= start && end <= r.end);
			if (index < 0) return file.slice(start, end);
			const { start: base } = ranges[index];
			return buffers[index].then((buffer) => (base === start && buffer.byteLength === end - start ? buffer : buffer.slice(start - base, end - base)));
		}
	};
}
