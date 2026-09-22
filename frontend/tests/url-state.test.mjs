import assert from 'node:assert/strict';
import test from 'node:test';

import { CATALOG_PAGE_SIZE, DEFAULT_CATALOG_SORT, FEATURED_GROUPS, PRICE_PRESETS, parseCatalogSort, parsePositiveNumber } from '../src/lib/catalog-config.ts';
import { localUrl, patchSearchParams } from '../src/lib/url-state.ts';

test('query patches preserve unrelated state and normalize removals and booleans', () => {
	const source = new URL('https://example.test/borda/?q=esp32&lang=ar&stock=1#results');
	const next = patchSearchParams(source, { q: 'uno', stock: false, min: 100, ai: true, seller: null });

	assert.equal(localUrl(next), '/borda/?q=uno&lang=ar&min=100&ai=1#results');
	assert.equal(source.searchParams.get('q'), 'esp32', 'the input URL stays immutable');
});

test('catalog configuration validates URL values and owns reusable display presets', () => {
	assert.equal(parseCatalogSort('price-asc'), 'price-asc');
	assert.equal(parseCatalogSort('unexpected'), DEFAULT_CATALOG_SORT);
	assert.equal(parsePositiveNumber('250.5'), 250.5);
	assert.equal(parsePositiveNumber('-1'), 0);
	assert.equal(parsePositiveNumber('nope'), 0);
	assert.equal(CATALOG_PAGE_SIZE, 24);
	assert.ok(FEATURED_GROUPS.includes('dev-boards'));
	assert.deepEqual([...PRICE_PRESETS], [100, 500, 1000]);
});
