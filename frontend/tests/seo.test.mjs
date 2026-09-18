import assert from 'node:assert/strict';
import test from 'node:test';
import { currentOffers, productSchema, renderProductPage, renderHomePage, renderNotFoundPage, sitemapFiles, productUrl, normalizeSiteUrl, cleanShell } from '../scripts/generate-seo.mjs';

const site = 'https://walkthru.earth/borda';
const shell = '<!doctype html><html lang="en"><head><title>Old title</title><meta name="description" content="old"><meta property="og:title" content="old"><link rel="canonical" href="https://wrong.test"><script type="module" src="/borda/_app/entry.js"></script></head><body><div id="app-mount"></div></body></html>';
const product = { id: 'esp32', canonical_name: 'ESP32 board', canonical_name_ar: 'لوحة ESP32', description: 'A Wi-Fi development board.', group: 'dev-boards', image: 'https://images.test/esp32.png', sellers: ['a', 'b'], specs: ['Voltage: 3.3V'], brand: 'Example', mpn: 'ESP32' };
const offer = (url, price, extra = {}) => ({ seller: 'store-a', url, price, currency: 'EGP', availability: 'in_stock', ts: '2026-09-18T10:00:00Z', ...extra });

test('Product JSON-LD uses only current valid EGP listings, retaining truthful stock states', () => {
  const stats = { current_offers: JSON.stringify([offer('https://a.test/board', 99.5), offer('https://b.test/board', 140, { availability: 'out_of_stock' }), offer('https://c.test/board', 2, { currency: 'USD' }), offer('javascript:alert(1)', 1), offer('https://d.test', -1)]) };
  const schema = productSchema(product, stats, site);
  assert.equal(schema.offers.lowPrice, 99.5);
  assert.equal(schema.offers.highPrice, 140);
  assert.equal(schema.offers.offerCount, 2);
  assert.equal(schema.offers.offers[1].availability, 'https://schema.org/OutOfStock');
  assert.equal(schema.aggregateRating, undefined);
  assert.equal(schema.url, `${site}/product/esp32/`);
});

test('historical min price and empty or malformed current snapshots never create offers', () => {
  for (const stats of [{ min: 1, latest_min: 10 }, { current_offers: [] }, { current_offers: 'bad json' }]) {
    assert.equal(productSchema(product, stats, site).offers, undefined);
  }
});

test('newest unknown price supersedes an older priced observation of the same listing', () => {
  assert.deepEqual(currentOffers({ current_offers: [offer('https://a.test', 5, { ts: '2026-09-17' }), offer('https://a.test', null)] }), []);
});

test('product HTML exposes readable content, canonical and safe metadata without altering the SPA mount', () => {
  const html = renderProductPage(cleanShell(shell), product, { current_offers: [offer('https://a.test', 50)] }, site);
  assert.match(html, /<main id="borda-static-content">/);
  assert.match(html, /<h1>ESP32 board<\/h1>/);
  assert.match(html, /<div id="app-mount"><\/div>/);
  assert.match(html, /src="\/borda\/_app\/entry.js"/);
  assert.match(html, /href="https:\/\/walkthru.earth\/borda\/product\/esp32\/"/);
  assert.match(html, /50 EGP/);
  assert.equal((html.match(/<title\b/g) ?? []).length, 1);
  assert.equal((html.match(/rel="canonical"/g) ?? []).length, 1);
  assert.ok(html.indexOf('<main id="borda-static-content">') < html.indexOf('<div id="app-mount">'));
  assert.ok(!html.includes('<noscript>'));
});

test('untrusted product strings cannot break out of HTML or JSON-LD, including replacement metacharacters', () => {
  const dangerous = { ...product, canonical_name: '</script><script>alert(1)</script> $& $\' $`', description: '<img src=x onerror=alert(1)>', image: 'javascript:alert(1)' };
  const html = renderProductPage(cleanShell(shell), dangerous, undefined, site);
  assert.ok(!html.includes('<script>alert(1)</script>'));
  assert.ok(!html.includes('<img src=x'));
  const json = html.match(/type="application\/ld\+json">([\s\S]*?)<\/script>/)[1];
  assert.equal(JSON.parse(json).name, dangerous.canonical_name);
  assert.equal((html.match(/<body>/g) ?? []).length, 1);
  assert.equal((html.match(/id="app-mount"/g) ?? []).length, 1);
});

test('generator reruns replace static markup instead of duplicating it', () => {
  const first = renderProductPage(cleanShell(shell), product, undefined, site);
  const second = renderProductPage(cleanShell(first), product, undefined, site);
  assert.equal((second.match(/<main id="borda-static-content">/g) ?? []).length, 1);
  assert.equal((second.match(/<title\b/g) ?? []).length, 1);
  assert.equal((second.match(/id="app-mount"/g) ?? []).length, 1);
});

test('home discovery is bounded to 20 products and every sitemap chunk obeys the URL limit', () => {
  const products = Array.from({ length: 25 }, (_, i) => ({ ...product, id: `board-${i}` }));
  const html = renderHomePage(cleanShell(shell), products, { groups: { 'dev-boards': 25 }, generated_at: '2026-09-18' }, site);
  assert.equal((html.match(/<a href="https:\/\/walkthru.earth\/borda\/product\//g) ?? []).length, 20);
  const files = sitemapFiles(products, site, '2026-09-18', 10);
  assert.equal((files.get('sitemap-products.xml').match(/<url>/g) ?? []).length, 10);
  assert.equal((files.get('sitemap-products-2.xml').match(/<url>/g) ?? []).length, 10);
  assert.equal((files.get('sitemap-products-3.xml').match(/<url>/g) ?? []).length, 5);
  assert.match(files.get('sitemap.xml'), /sitemap-pages.xml/);
  assert.match(files.get('sitemap.xml'), /sitemap-products-3.xml/);
  assert.throws(() => sitemapFiles(products, site, null, 50001));
});

test('URL validation prevents build-directory traversal and malformed deployment canonicals', () => {
  for (const id of ['../escape', '..', 'a/b', 'a\\b', '\u0000']) assert.throws(() => productUrl(site, id));
  assert.equal(productUrl(site, 'a&b'), `${site}/product/a%26b/`);
  assert.equal(normalizeSiteUrl(`${site}/`), site);
  assert.throws(() => normalizeSiteUrl(`${site}?lang=ar`));
  assert.throws(() => normalizeSiteUrl('javascript:alert(1)'));
});


test('404 page preserves the SPA resolver and excludes the missing address from indexing', () => {
  const html = renderNotFoundPage(cleanShell(shell), site);
  assert.match(html, /name="robots" content="noindex,follow"/);
  assert.match(html, /src="\/borda\/_app\/entry.js"/);
  assert.match(html, /<div id="app-mount"><\/div>/);
  assert.ok(!html.includes('rel="canonical"'));
});
