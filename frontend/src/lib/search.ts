/** Local, deterministic search across official names, seller names, brands, part numbers and taxonomy. */
import type { CurrentOffer, Product, Stats } from './parquet';
import { DEFAULT_PROFILE } from './profile.js';

const SPLIT = /[^\p{L}\p{N}.+-]+/u;

// Keep aligned with src/borda/normalize/names.py:_ARABIC_GLOSSARY.
// These deterministic aliases work even when sellers provide only English names.
const ARABIC_GLOSSARY: Record<string, string> = {
	"اردوينو": "arduino",
	"أردوينو": "arduino",
	"اونو": "uno",
	"أونو": "uno",
	"ميجا": "mega",
	"نانو": "nano",
	"راسبيري باي": "raspberry pi",
	"راسبري باي": "raspberry pi",
	"بيكو": "pico",
	"حساس": "sensor",
	"سنسور": "sensor",
	"موديول": "module",
	"موديل": "module",
	"وحدة": "module",
	"بورد": "board",
	"بوردة": "board",
	"لوحة": "board",
	"ريلاي": "relay",
	"ريليه": "relay",
	"موتور": "motor",
	"محرك": "motor",
	"سيرفو": "servo",
	"ستيبر": "stepper",
	"درايفر": "driver",
	"بطارية": "battery",
	"شاحن": "charger",
	"محول": "converter",
	"منظم": "regulator",
	"شاشة": "display",
	"كابل": "cable",
	"كيبل": "cable",
	"سلك": "wire",
	"اسلاك": "wire",
	"جمبر": "jumper",
	"مقاومة": "resistor",
	"مكثف": "capacitor",
	"ترانزستور": "transistor",
	"دايود": "diode",
	"ليد": "led",
	"بلوتوث": "bluetooth",
	"واي فاي": "wifi",
	"وايفاي": "wifi",
	"الترا سونيك": "ultrasonic",
	"التراسونيك": "ultrasonic",
	"مسافة": "distance",
	"حرارة": "temperature",
	"رطوبة": "humidity",
	"ضغط": "pressure",
	"غاز": "gas",
	"افوميتر": "multimeter",
	"أفوميتر": "multimeter",
	"كاوية": "soldering iron",
	"لحام": "soldering",
	"مفتاح": "switch",
	"زرار": "button",
	"بريد بورد": "breadboard",
	"كاميرا": "camera",
	"مايك": "microphone",
	"سماعة": "speaker",
	"بازر": "buzzer",
	"طاقة": "power",
	"مصدر": "supply",
	"شريحة": "ic",
	"متحكم": "microcontroller",
	"مبرمج": "programmer",
	"قارئ": "reader",
	"كارت": "card",
	"ذاكرة": "memory"
};

const glossary = new Map(Object.entries(ARABIC_GLOSSARY).map(([arabic, english]) => [normalizeSearch(arabic), english]));
const glossaryPattern = new RegExp(`(?<![\\p{L}\\p{N}])(?:${[...glossary.keys()].sort((a, b) => b.length - a.length).map((phrase) => phrase.replace(/ /g, '\\s+')).join('|')})(?![\\p{L}\\p{N}])`, 'gu');

function expandArabic(text: string): string {
	return normalizeSearch(text).replace(glossaryPattern, (phrase) => glossary.get(phrase.replace(/\s+/g, ' '))!);
}

/**
 * Arabic translation fields must contain Arabic script; models occasionally echo or reorder the
 * English name instead. Such values are not translations: return null so the interface falls
 * back to English instead of showing the same Latin name twice. Mirrors borda.models.
 */
export function arabicText(value: string | null | undefined): string | null {
	return value && /\p{Script=Arabic}/u.test(value) ? value : null;
}

/** Fold accents, Arabic diacritics / alef variants and Arabic-Indic digits. */
export function normalizeSearch(text: string): string {
	return text.toLowerCase().normalize('NFKD')
		.replace(/\p{M}/gu, '')
		.replace(/ـ/g, '')
		.replace(/[أإآٱ]/g, 'ا')
		.replace(/ى/g, 'ي')
		.replace(/[٠-٩۰-۹]/g, (digit) => String(digit.charCodeAt(0) - (digit <= '٩' ? 0x660 : 0x6f0)));
}

function terms(text: string): string[] {
	return normalizeSearch(text).split(SPLIT).map((token) => token.replace(/^[.+-]+|[.+-]+$/g, '')).filter(Boolean);
}

export function tokenize(...texts: (string | null | undefined)[]): Set<string> {
	const out = new Set<string>();
	for (const text of texts) {
		if (!text) continue;
		// Preserve literal seller aliases as well as their English equivalents.
		for (const token of new Set([...terms(text), ...terms(expandArabic(text))])) {
			out.add(token);
			if (token.includes('-')) {
				out.add(token.replace(/-/g, ''));
				for (const part of token.split('-')) if (part) out.add(part);
			}
		}
	}
	return out;
}

export interface Index {
	products: Product[];
	tokens: Map<string, number[]>;
	sortedTokens: string[];
	nameTokens: Set<string>[];
}

export function buildIndex(products: Product[]): Index {
	const tokens = new Map<string, number[]>();
	products.forEach((product, i) => {
		for (const token of tokenize(product.canonical_name, product.canonical_name_ar, product.brand, product.mpn, product.category, product.group, ...product.raw_names, ...product.tags)) {
			const positions = tokens.get(token);
			if (positions) positions.push(i);
			else tokens.set(token, [i]);
		}
	});
	return { products, tokens, sortedTokens: [...tokens.keys()].sort(), nameTokens: products.map((p) => tokenize(p.canonical_name, p.canonical_name_ar)) };
}

function prefixHits(index: Index, query: string): Set<number> {
	const hits = new Set<number>();
	let lo = 0, hi = index.sortedTokens.length;
	while (lo < hi) {
		const mid = (lo + hi) >> 1;
		if (index.sortedTokens[mid] < query) lo = mid + 1;
		else hi = mid;
	}
	for (let i = lo; i < index.sortedTokens.length && index.sortedTokens[i].startsWith(query); i++) {
		for (const pos of index.tokens.get(index.sortedTokens[i]) ?? []) hits.add(pos);
	}
	return hits;
}

function termHits(index: Index, term: string): Set<number> {
	// Exact matches must not suppress longer part numbers sharing that prefix.
	const hits = prefixHits(index, term);
	if (term.includes('-')) {
		for (const pos of prefixHits(index, term.replace(/-/g, ''))) hits.add(pos);
		const parts = term.split('-').filter(Boolean).map((part) => prefixHits(index, part));
		for (const pos of parts[0] ?? []) if (parts.every((part) => part.has(pos))) hits.add(pos);
	}
	return hits;
}

/** Every query term must match. Pass Infinity before applying filters to avoid truncation. */
export function search(index: Index, query: string, limit = 60): Product[] {
	const count = Number.isNaN(limit) ? 0 : Math.max(0, limit);
	// Translate each known phrase once: Arabic and English are alternatives, not
	// additional AND constraints. Multiword translations still require every term.
	const expandedQuery = expandArabic(query);
	const queryTerms = [...new Set(terms(expandedQuery))];
	if (queryTerms.length === 0) return query.trim() ? [] : index.products.slice(0, count);
	const perTerm = queryTerms.map((term) => termHits(index, term)).sort((a, b) => a.size - b.size);
	const matches = [...perTerm[0]].filter((i) => perTerm.every((set) => set.has(i)));
	const normalizedQuery = expandedQuery.trim();
	const scored = matches.map((i) => {
		const product = index.products[i];
		const name = normalizeSearch(product.canonical_name);
		const nameTokens = [...index.nameTokens[i]];
		const exact = queryTerms.filter((term) => index.nameTokens[i].has(term)).length;
		const prefixes = queryTerms.filter((term) => nameTokens.some((token) => token.startsWith(term))).length;
		return { product, score: (name === normalizedQuery ? 1000 : name.startsWith(normalizedQuery) ? 100 : 0) + exact * 20 + prefixes * 10 + Math.min(product.sellers.length, 9) };
	});
	return scored.sort((a, b) => b.score - a.score || a.product.canonical_name.localeCompare(b.product.canonical_name))
		.slice(0, count).map(({ product }) => product);
}

export interface ProductFilters {
	currency?: string;
	group?: string;
	tag?: string;
	seller?: string;
	inStock?: boolean;
	minPrice?: number;
	maxPrice?: number;
}

function validPrice(price: number | null | undefined): price is number {
	return price != null && Number.isFinite(price) && price >= 0;
}

/** All offer constraints apply to the same listing, never different sellers. */
export function matchingOffers(stats: Stats | undefined, filters: ProductFilters = {}): CurrentOffer[] {
	const currency = filters.currency ?? stats?.currency ?? DEFAULT_PROFILE.currency;
	return (stats?.current_offers ?? []).filter((offer) =>
		offer.currency === currency
		&& (!filters.seller || offer.seller === filters.seller)
		&& (!filters.inStock || offer.availability === 'in_stock')
		&& (!(filters.minPrice != null && filters.minPrice > 0) || (validPrice(offer.price) && offer.price >= filters.minPrice))
		&& (!(filters.maxPrice != null && filters.maxPrice > 0) || (validPrice(offer.price) && offer.price <= filters.maxPrice))
	);
}

/** Lowest eligible listed price, or null when no price is known. */
export function matchingPrice(stats: Stats | undefined, filters: ProductFilters = {}): number | null {
	if (stats?.current_offers !== undefined) {
		const prices = matchingOffers(stats, filters).map((offer) => offer.price).filter(validPrice);
		return prices.length ? Math.min(...prices) : null;
	}
	// Older snapshots cannot identify the price or availability of a particular seller.
	if (filters.seller || filters.inStock) return null;
	return stats?.currency === (filters.currency ?? stats?.currency ?? DEFAULT_PROFILE.currency) && validPrice(stats.latest_min) ? stats.latest_min : null;
}

export function productMatchesFilters(product: Product, stats: Stats | undefined, filters: ProductFilters): boolean {
	if (filters.group && (product.group ?? 'other') !== filters.group) return false;
	if (filters.tag && !product.tags.includes(filters.tag)) return false;
	if (filters.seller && !product.sellers.includes(filters.seller)) return false;
	const hasPrice = (filters.minPrice ?? 0) > 0 || (filters.maxPrice ?? 0) > 0;
	if (!filters.seller && !filters.inStock && !hasPrice) return true;
	if (stats?.current_offers !== undefined) return matchingOffers(stats, filters).length > 0;
	if (filters.seller && (filters.inStock || hasPrice)) return false;
	if (filters.inStock && !(stats && stats.in_stock_sellers > 0)) return false;
	if (hasPrice) {
		// Without listing data a stock+price conjunction cannot be verified either.
		if (filters.inStock) return false;
		const price = matchingPrice(stats, { currency: filters.currency });
		if (price == null || price < (filters.minPrice ?? 0) || ((filters.maxPrice ?? 0) > 0 && price > filters.maxPrice!)) return false;
	}
	return true;
}
