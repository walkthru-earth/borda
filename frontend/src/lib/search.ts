/**
 * Client-side inverted index built from catalog.parquet.
 * Mirrors egmarket.storage.index: tokens from the official name, *every* local/Egyptian
 * raw name (Arabic included) and tags – so a query in local spelling finds the official
 * product. Prefix matching on tokens covers partial part numbers ("esp32-w").
 */
import type { Product } from './parquet';

const SPLIT = /[^\p{L}\p{N}.+-]+/u;

export function tokenize(...texts: (string | null | undefined)[]): Set<string> {
	const out = new Set<string>();
	for (const t of texts) {
		if (!t) continue;
		for (const raw of t.toLowerCase().normalize('NFKD').split(SPLIT)) {
			const tok = raw.replace(/^[.+-]+|[.+-]+$/g, '');
			if (tok.length < 2) continue;
			out.add(tok);
			if (tok.includes('-')) for (const sub of tok.split('-')) if (sub.length >= 2) out.add(sub);
		}
	}
	return out;
}

export interface Index {
	products: Product[];
	tokens: Map<string, number[]>;
	sortedTokens: string[];
}

export function buildIndex(products: Product[]): Index {
	const tokens = new Map<string, number[]>();
	products.forEach((p, i) => {
		for (const tok of tokenize(p.canonical_name, p.brand, ...p.raw_names, ...p.tags)) {
			const arr = tokens.get(tok);
			if (arr) arr.push(i);
			else tokens.set(tok, [i]);
		}
	});
	return { products, tokens, sortedTokens: [...tokens.keys()].sort() };
}

function prefixHits(idx: Index, q: string): Set<number> {
	const hits = new Set<number>();
	// binary search into the sorted token list, then walk while prefix matches
	let lo = 0, hi = idx.sortedTokens.length;
	while (lo < hi) {
		const mid = (lo + hi) >> 1;
		if (idx.sortedTokens[mid] < q) lo = mid + 1;
		else hi = mid;
	}
	for (let i = lo; i < idx.sortedTokens.length && idx.sortedTokens[i].startsWith(q); i++) {
		for (const pos of idx.tokens.get(idx.sortedTokens[i]) ?? []) hits.add(pos);
	}
	return hits;
}

/** AND-search: every query token must match (exactly or as a prefix). */
export function search(idx: Index, query: string, limit = 60): Product[] {
	const q = [...tokenize(query)];
	if (q.length === 0) return idx.products.slice(0, limit);
	const perToken = q.map((tok) => {
		const exact = new Set<number>(idx.tokens.get(tok) ?? []);
		return exact.size ? exact : prefixHits(idx, tok);
	});
	perToken.sort((a, b) => a.size - b.size);
	const result = [...perToken[0]].filter((i) => perToken.every((set) => set.has(i)));
	const scored = result.map((i) => {
		const p = idx.products[i];
		const nameTokens = tokenize(p.canonical_name);
		const inName = q.filter((t) => [...nameTokens].some((n) => n.startsWith(t))).length;
		return { p, score: inName * 10 + p.sellers.length };
	});
	return scored.sort((a, b) => b.score - a.score || a.p.canonical_name.localeCompare(b.p.canonical_name)).slice(0, limit).map((s) => s.p);
}
