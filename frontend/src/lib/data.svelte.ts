/** App-wide catalog cache (Svelte 5 runes). Loaded once, shared by list and product pages. */
import { loadCatalog, loadManifest, loadStats, type Manifest, type Product, type Stats } from './parquet';
import { buildIndex, type Index } from './search';

class Catalog {
	products = $state<Product[]>([]);
	stats = $state<Map<string, Stats>>(new Map());
	manifest = $state<Manifest | null>(null);
	index = $state<Index | null>(null);
	error = $state<string | null>(null);
	loading = $state(false);

	byId = $derived(new Map(this.products.map((p) => [p.id, p])));
	sellers = $derived([...new Set(this.products.flatMap((p) => p.sellers))].sort());
	tagCounts = $derived.by(() => {
		const m = new Map<string, number>();
		for (const p of this.products) for (const t of p.tags) m.set(t, (m.get(t) ?? 0) + 1);
		return [...m.entries()].sort((a, b) => b[1] - a[1]);
	});

	async ensure(): Promise<void> {
		if (this.products.length || this.loading) return;
		this.loading = true;
		try {
			const [products, stats, manifest] = await Promise.all([loadCatalog(), loadStats(), loadManifest()]);
			this.products = products;
			this.stats = stats;
			this.manifest = manifest;
			this.index = buildIndex(products);
		} catch (e) {
			this.error = e instanceof Error ? e.message : String(e);
		} finally {
			this.loading = false;
		}
	}
}

export const catalog = new Catalog();

export const fmtPrice = (v: number | null | undefined, cur = 'EGP'): string =>
	v == null ? '—' : `${v.toLocaleString('en-EG', { maximumFractionDigits: 2 })} ${cur}`;

export const sellerName = (slug: string): string =>
	slug.replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
