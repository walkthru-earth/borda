import assert from 'node:assert/strict';
import test from 'node:test';
import { existsSync, readFileSync } from 'node:fs';
import { parquetMetadata } from 'hyparquet';
import { candidateRowGroups, coalesce, DETAIL_COLUMNS, LIST_COLUMNS, planColumnRanges, planCost, prefetched, STATS_COLUMNS } from '../src/lib/projection.ts';

/** Minimal FileMetaData: row groups laid out back to back, chunks in schema order. */
function fakeMetadata(layout, statsByGroup = []) {
	let offset = 4;
	const row_groups = layout.map((sizes, g) => {
		const columns = Object.entries(sizes).map(([name, size]) => {
			const chunk = { meta_data: { path_in_schema: [name], dictionary_page_offset: BigInt(offset), data_page_offset: BigInt(offset + 10), total_compressed_size: BigInt(size), statistics: statsByGroup[g]?.[name] } };
			offset += size;
			return chunk;
		});
		return { columns, num_rows: 2048n };
	});
	return { row_groups, schema: [] };
}

test('coalesce merges touching and near ranges but never drops bytes', () => {
	const merged = coalesce([{ start: 100, end: 200 }, { start: 0, end: 100 }, { start: 210, end: 300 }, { start: 5000, end: 6000 }, { start: 50, end: 60 }], 16);
	assert.deepEqual(merged, [{ start: 0, end: 300 }, { start: 5000, end: 6000 }]);
	assert.deepEqual(coalesce([{ start: 0, end: 100 }, { start: 200, end: 300 }], 50), [{ start: 0, end: 100 }, { start: 200, end: 300 }]);
	assert.deepEqual(coalesce([{ start: 5, end: 5 }]), []);
});

test('contiguous list columns cost one request per row group; interleaved layouts still read only what they need', () => {
	const ordered = fakeMetadata([{ id: 100, name: 100, image: 100, description: 50000, similar: 30000 }, { id: 100, name: 100, image: 100, description: 50000, similar: 30000 }]);
	const plan = planColumnRanges(ordered, ['id', 'name', 'image']);
	assert.deepEqual(planCost(plan), { requests: 2, bytes: 600 });
	assert.deepEqual(plan, [{ start: 4, end: 304 }, { start: 80304, end: 80604 }]);

	const interleaved = fakeMetadata([{ id: 100, description: 100000, name: 100, image: 100 }]);
	const runs = planColumnRanges(interleaved, ['id', 'name', 'image']);
	assert.deepEqual(planCost(runs), { requests: 2, bytes: 300 });

	// A small unread column between two wanted ones is cheaper to download than a round trip.
	const smallGap = fakeMetadata([{ id: 100, mpn: 1000, name: 100 }]);
	assert.equal(planCost(planColumnRanges(smallGap, ['id', 'name'])).requests, 1);
	assert.equal(planCost(planColumnRanges(smallGap, ['id', 'name'], { gap: 0 })).requests, 2);

	assert.deepEqual(planColumnRanges(ordered, ['id'], { rowGroups: [1] }), [{ start: 80304, end: 80404 }]);
	assert.deepEqual(planColumnRanges(ordered, ['missing']), []);
});

test('candidateRowGroups prunes by key statistics and keeps groups without them', () => {
	const stats = [{ id: { min_value: 'a', max_value: 'f' } }, { id: { min_value: 'g', max_value: 'm' } }, {}];
	const metadata = fakeMetadata([{ id: 1 }, { id: 1 }, { id: 1 }], stats);
	assert.deepEqual(candidateRowGroups(metadata, 'id', 'esp32'), [0, 2]);
	assert.deepEqual(candidateRowGroups(metadata, 'id', 'g'), [1, 2]);
	assert.deepEqual(candidateRowGroups(metadata, 'id', 'zzz'), [2]);
	metadata.row_groups[2].num_rows = 0n;
	assert.deepEqual(candidateRowGroups(metadata, 'id', 'zzz'), []);
});

test('prefetched serves sub-slices from fetched runs and falls back to the file elsewhere', async () => {
	const bytes = Uint8Array.from({ length: 64 }, (_, i) => i);
	const fileSlices = [];
	const file = { byteLength: 64, slice: (s, e = 64) => { fileSlices.push([s, e]); return bytes.buffer.slice(s, e); } };
	const loads = [];
	const source = prefetched(file, [{ start: 8, end: 24 }], (r) => { loads.push(r); return Promise.resolve(bytes.buffer.slice(r.start, r.end)); });
	assert.deepEqual([...new Uint8Array(await source.slice(10, 14))], [10, 11, 12, 13]);
	assert.deepEqual([...new Uint8Array(await source.slice(8, 24))].at(-1), 23);
	assert.deepEqual([...new Uint8Array(await source.slice(40, 42))], [40, 41]);
	assert.deepEqual(loads, [{ start: 8, end: 24 }]);
	assert.deepEqual(fileSlices, [[40, 42]]);
});

test('browser projections are disjoint except for the join keys', () => {
	const overlap = LIST_COLUMNS.filter((c) => DETAIL_COLUMNS.includes(c));
	assert.deepEqual(overlap, ['id']);
	assert.ok(!STATS_COLUMNS.includes('image') && !STATS_COLUMNS.includes('canonical_name'));
});

const snapshot = new URL('../../data/catalog.parquet', import.meta.url);
const statsSnapshot = new URL('../../data/stats.parquet', import.meta.url);
test('checked-in snapshot serves each projection as one range per row group', { skip: !existsSync(snapshot) || !existsSync(statsSnapshot) }, () => {
	const load = (url) => { const b = readFileSync(url); return parquetMetadata(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength)); };
	const catalog = load(snapshot);
	const stats = load(statsSnapshot);
	const whole = (m) => Number(m.row_groups.reduce((sum, g) => sum + g.total_byte_size, 0n));
	// `id` is the join key for the detail filter; the detail block itself must be one run.
	for (const [metadata, columns] of [[catalog, LIST_COLUMNS], [catalog, DETAIL_COLUMNS.filter((c) => c !== 'id')], [stats, STATS_COLUMNS]]) {
		const plan = planColumnRanges(metadata, columns, { gap: 0 });
		assert.equal(plan.length, metadata.row_groups.length, `${columns[0]}: ${plan.length} runs for ${metadata.row_groups.length} row groups`);
	}
	const list = planCost(planColumnRanges(catalog, LIST_COLUMNS)).bytes;
	assert.ok(list < 0.5 * whole(catalog), 'list projection should skip at least half of the catalog bytes');
	const detail = planCost(planColumnRanges(catalog, DETAIL_COLUMNS, { rowGroups: candidateRowGroups(catalog, 'id', 'esp32') }));
	assert.equal(detail.requests, 2, 'id chunk + contiguous detail block');
	assert.ok(detail.bytes < 0.1 * whole(catalog));
});
