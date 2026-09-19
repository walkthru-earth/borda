import assert from 'node:assert/strict';
import test from 'node:test';
import { buildIndex, search, normalizeSearch, productMatchesFilters, matchingPrice } from '../src/lib/search.ts';

const product = (id, canonical_name, extra = {}) => ({ id, canonical_name, raw_names: [], tags: [], brand: null, category: null, group: null, image: null, sellers: ['store-a'], similar: [], enriched: false, ...extra });
const ids = (index, query, limit) => search(index, query, limit).map((p) => p.id);

test('exact tokens do not hide longer part numbers, and exact names rank first', () => {
  const index = buildIndex([product('variant', 'ESP32-S3 board'), product('exact', 'ESP32')]);
  assert.deepEqual(ids(index, 'esp32'), ['exact', 'variant']);
});

test('part number punctuation and prefix variants remain searchable', () => {
  const index = buildIndex([product('hyphen', 'ESP32-WROOM-32'), product('space', 'ESP32 WROOM 32')]);
  assert.deepEqual(new Set(ids(index, 'esp32-wroom')), new Set(['hyphen', 'space']));
  assert.deepEqual(ids(index, 'esp32wroom'), ['hyphen']);
  assert.deepEqual(new Set(ids(index, 'esp32 wroom')), new Set(['hyphen', 'space']));
});

test('Arabic names match without diacritics, tatweel, alef variants or digit differences', () => {
  const index = buildIndex([product('arabic', 'Arduino', { raw_names: ['أَرْدُويـنُو ٥ فولت'] })]);
  assert.deepEqual(ids(index, 'اردوينو 5'), ['arabic']);
  assert.equal(normalizeSearch('۱۲٣'), '123');
});

test('single-digit specifications are meaningful and all query terms must match', () => {
  const index = buildIndex([product('five', 'Motor 5 V'), product('nine', 'Motor 9 V')]);
  assert.deepEqual(ids(index, 'motor 5'), ['five']);
  assert.deepEqual(ids(index, 'motor sensor'), []);
  assert.deepEqual(ids(index, '???'), []);
});

test('brands, brand alias tags and part numbers are searchable', () => {
  const index = buildIndex([
    product('tbeam', 'T-Beam ESP32 LoRa Board', { brand: 'LILYGO', tags: ['lilygo', 'ttgo'] }),
    product('hat', 'USB Monitor AIO 4 Inch', { brand: 'Waveshare' }),
    product('uno', 'Arduino Uno R3', { mpn: 'A000066' }),
    product('legacy', 'Old snapshot row without mpn column'),
  ]);
  assert.deepEqual(ids(index, 'lilygo'), ['tbeam']);
  assert.deepEqual(ids(index, 'ttgo'), ['tbeam']);
  assert.deepEqual(ids(index, 'waveshare'), ['hat']);
  assert.deepEqual(ids(index, 'a000066'), ['uno']);
});

test('unlimited results let callers filter the complete catalog', () => {
  const products = Array.from({ length: 5100 }, (_, i) => product(String(i), `Board ${i}`));
  const index = buildIndex(products);
  assert.equal(search(index, '', Infinity).length, 5100);
  assert.equal(search(index, 'board', Infinity).length, 5100);
  assert.equal(search(index, 'board', -1).length, 0);
});

const offer = (seller, price, availability = 'in_stock') => ({ seller, price, availability, currency: 'EGP', url: `https://${seller}.test`, ts: new Date() });
const stats = { currency: 'EGP', latest_min: 20, in_stock_sellers: 1, current_offers: [offer('store-a', 20, 'out_of_stock'), offer('store-b', 100)] };
const board = product('board', 'Board', { sellers: ['store-a', 'store-b'] });

test('seller, stock and budget must all match the same offer', () => {
  assert.equal(productMatchesFilters(board, stats, { seller: 'store-a', inStock: true }), false);
  assert.equal(productMatchesFilters(board, stats, { inStock: true, maxPrice: 50 }), false);
  assert.equal(productMatchesFilters(board, stats, { seller: 'store-b', inStock: true, minPrice: 80, maxPrice: 120 }), true);
  assert.equal(matchingPrice(stats, { inStock: true }), 100);
  assert.equal(matchingPrice(stats, { seller: 'store-a' }), 20);
});

test('unknown prices never meet a budget and non-EGP offers are not compared as EGP', () => {
  const unknown = { ...stats, current_offers: [offer('store-a', null), { ...offer('store-b', 1), currency: 'USD' }] };
  assert.equal(productMatchesFilters(board, unknown, { maxPrice: 100 }), false);
  assert.equal(matchingPrice(unknown), null);
});

test('legacy snapshots do not claim seller-specific availability or price', () => {
  const legacy = { currency: 'EGP', latest_min: 20, in_stock_sellers: 1 };
  assert.equal(productMatchesFilters(board, legacy, { seller: 'store-a' }), true);
  assert.equal(productMatchesFilters(board, legacy, { seller: 'store-a', inStock: true }), false);
  assert.equal(productMatchesFilters(board, legacy, { maxPrice: 30 }), true);
});

test('common Arabic electronics queries find an English-only catalog without AI', () => {
  const index = buildIndex([
    product('uno', 'Arduino Uno R3'),
    product('nano', 'Arduino Nano'),
    product('temperature', 'DHT11 Temperature Sensor'),
    product('pressure', 'BMP180 Pressure Sensor'),
    product('pico', 'Raspberry Pi Pico W'),
    product('iron', 'Soldering Iron'),
    product('paste', 'Soldering Paste'),
  ]);
  assert.deepEqual(new Set(ids(index, 'اردوينو')), new Set(['uno', 'nano']));
  assert.deepEqual(ids(index, 'أَرْدُوِينُو اونو'), ['uno']);
  assert.deepEqual(ids(index, 'اردوينو Uno'), ['uno']);
  assert.deepEqual(ids(index, 'حساس حرارة'), ['temperature']);
  assert.deepEqual(ids(index, 'راسبري   باي بيكو'), ['pico']);
  assert.deepEqual(ids(index, 'كاوية'), ['iron']);
  assert.deepEqual(ids(index, 'اردوينو حرارة'), []);
});

test('glossary expansion preserves Arabic-only seller aliases and literal prefixes', () => {
  const index = buildIndex([product('arabic', 'قطعة', { raw_names: ['حساس حرارة'] })]);
  assert.deepEqual(ids(index, 'sensor temperature'), ['arabic']);
  assert.deepEqual(ids(index, 'حساس حرارة'), ['arabic']);
  assert.deepEqual(ids(index, 'حسا'), ['arabic']);
  assert.deepEqual(ids(index, 'محساس'), []);
});

test('persisted Arabic translations are indexed alongside English names', () => {
  const index = buildIndex([product('translated', 'Optical Detector', { canonical_name_ar: 'حساس ضوئي' })]);
  assert.deepEqual(ids(index, 'حساس ضوئي'), ['translated']);
  assert.deepEqual(ids(index, 'optical'), ['translated']);
});
