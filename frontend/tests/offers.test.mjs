import assert from 'node:assert/strict';
import test from 'node:test';
import { productOffers } from '../src/lib/offers.ts';

const offer = (url, price, extra = {}) => ({ seller: 'store-a', url, price, currency: 'EGP', availability: 'in_stock', ts: new Date('2026-09-18'), ...extra });
const history = (row) => ({ product_id: 'board', ...row });

test('current seller snapshots exclude cheaper removed historical listings', () => {
  const current = offer('https://store-a.test/current', 120);
  const removed = history(offer('https://store-a.test/removed', 5));
  const result = productOffers({ currency: 'EGP', current_offers: [current] }, [removed]);
  assert.deepEqual(result.offers.map((row) => row.url), [current.url]);
  assert.equal(result.bestOffer.price, 120);
  assert.equal(result.source, 'current');
});

test('empty current snapshot never falls back to historical in-stock prices', () => {
  const result = productOffers({ current_offers: [] }, [history(offer('https://store-a.test/removed', 5))]);
  assert.deepEqual(result.offers, []);
  assert.equal(result.bestOffer, null);
  assert.equal(result.lowestPrice, null);
  assert.equal(result.stockSellers, 0);
  assert.equal(result.source, 'current');
});

test('legacy fallback keeps concurrent listings only from each seller’s last observed batch', () => {
  const rows = [
    history(offer('old', 5, { ts: new Date('2026-09-17') })),
    history(offer('current-a', 120)),
    history(offer('current-b', 100)),
    history(offer('untouched-store', 90, { seller: 'store-b', ts: new Date('2026-09-16') }))
  ];
  const result = productOffers(undefined, rows);
  assert.equal(result.source, 'history');
  assert.deepEqual(new Set(result.offers.map((row) => row.url)), new Set(['current-a', 'current-b', 'untouched-store']));
  assert.equal(result.bestOffer.price, 90);
});

test('lowest in-stock recommendation compares valid prices within one currency', () => {
  const rows = [offer('out', 1, { availability: 'out_of_stock' }), offer('foreign', 2, { currency: 'USD' }), offer('bad', -10), offer('nan', NaN), offer('unknown', null), offer('valid', 125.5)];
  const result = productOffers({ currency: 'EGP', current_offers: rows }, []);
  assert.equal(result.bestOffer.url, 'valid');
  assert.equal(result.bestOffer.price, 125.5);
  assert.equal(result.lowestPrice, 1);
  assert.equal(result.stockSellers, 1);
  assert.equal(result.offers.find((row) => row.url === 'bad').price, null);
});

test('listing deduplication preserves the newest availability and never mutates inputs', () => {
  const current = offer('same', null, { availability: 'unknown' });
  const rows = [offer('same', 10, { ts: new Date('2026-09-17') }), current];
  const result = productOffers({ current_offers: rows }, []);
  assert.equal(result.offers.length, 1);
  assert.equal(result.bestOffer, null);
  assert.equal(result.stockSellers, 0);
  assert.equal(rows[0].price, 10);
  assert.equal(current.availability, 'unknown');
});
