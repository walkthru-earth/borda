import type { CurrentOffer, Point, Stats } from './parquet';
import { DEFAULT_PROFILE } from './profile.js';

/** Prefer the pipeline's seller-snapshot offers. An empty snapshot is authoritative:
 * historical listings must never come back as current buying recommendations.
 * Older exports lack snapshots, so retain only the last observed batch per seller
 * and expose that weaker source to the caller for explicit historical labeling.
 */
export function productOffers(stats: Stats | undefined, history: Point[], preferredCurrency?: string) {
	const source = stats?.current_offers !== undefined ? 'current' : 'history';
	let rows: CurrentOffer[];
	if (stats?.current_offers !== undefined) {
		rows = stats.current_offers;
	} else {
		const latest = new Map<string, number>();
		for (const point of history) {
			if (Number.isFinite(+point.ts)) latest.set(point.seller, Math.max(latest.get(point.seller) ?? -Infinity, +point.ts));
		}
		rows = history.filter((point) => +point.ts === latest.get(point.seller));
	}
	const listings = new Map<string, CurrentOffer>();
	for (const row of rows) {
		if (!Number.isFinite(+row.ts)) continue;
		const key = JSON.stringify([row.seller, row.url]);
		const previous = listings.get(key);
		if (!previous || +row.ts >= +previous.ts) {
			listings.set(key, { ...row, price: row.price != null && Number.isFinite(row.price) && row.price >= 0 ? row.price : null });
		}
	}
	const offers = [...listings.values()].sort((a, b) => Number(b.availability === 'in_stock') - Number(a.availability === 'in_stock') || a.currency.localeCompare(b.currency) || (a.price ?? Infinity) - (b.price ?? Infinity));
	const currency = preferredCurrency ?? stats?.currency ?? offers.find((offer) => offer.price != null)?.currency ?? DEFAULT_PROFILE.currency;
	const priced = offers.filter((offer) => offer.price != null && offer.currency === currency);
	const bestOffer = priced.filter((offer) => offer.availability === 'in_stock').reduce<CurrentOffer | null>((best, offer) => !best || offer.price! < best.price! ? offer : best, null);
	const lowestPrice = priced.reduce<number | null>((best, offer) => best == null ? offer.price : Math.min(best, offer.price!), null);
	const stockSellers = new Set(offers.filter((offer) => offer.availability === 'in_stock').map((offer) => offer.seller)).size;
	return { offers, source, currency, bestOffer, lowestPrice, stockSellers };
}
