/** App-wide catalog cache (Svelte 5 runes). Loaded once, shared by list and product pages. */
import { loadCatalog, loadManifest, loadStats, type Manifest, type Product, type Stats } from './parquet';
import { buildIndex, type Index } from './search';

export const GROUP_META: Record<string, { label: string; icon: string }> = {
	'dev-boards': { label: 'Dev boards', icon: '🧩' },
	'microcontrollers-ics': { label: 'MCUs & ICs', icon: '🔲' },
	sensors: { label: 'Sensors', icon: '📡' },
	'wireless-iot': { label: 'Wireless & IoT', icon: '📶' },
	'displays-leds': { label: 'Displays & LEDs', icon: '💡' },
	'motors-drivers': { label: 'Motors & drivers', icon: '⚙️' },
	power: { label: 'Power', icon: '🔋' },
	'passive-components': { label: 'Passives', icon: '〰️' },
	semiconductors: { label: 'Semiconductors', icon: '🔺' },
	'connectors-cables': { label: 'Connectors & cables', icon: '🔌' },
	prototyping: { label: 'Prototyping', icon: '🛠️' },
	'tools-instruments': { label: 'Tools & instruments', icon: '🔧' },
	'3d-printing-cnc': { label: '3D printing & CNC', icon: '🖨️' },
	'robotics-kits': { label: 'Robotics & kits', icon: '🤖' },
	other: { label: 'Other', icon: '📦' }
};

class Catalog {
	products = $state<Product[]>([]);
	stats = $state<Map<string, Stats>>(new Map());
	manifest = $state<Manifest | null>(null);
	index = $state<Index | null>(null);
	error = $state<string | null>(null);
	loading = $state(false);

	byId = $derived(new Map(this.products.map((p) => [p.id, p])));
	sellers = $derived([...new Set(this.products.flatMap((p) => p.sellers))].sort());
	groups = $derived.by(() => {
		const m = new Map<string, number>();
		for (const p of this.products) m.set(p.group ?? 'other', (m.get(p.group ?? 'other') ?? 0) + 1);
		return [...m.entries()].sort((a, b) => b[1] - a[1]);
	});
	tagCounts = $derived.by(() => {
		const m = new Map<string, number>();
		for (const p of this.products) for (const t of p.tags) m.set(t, (m.get(t) ?? 0) + 1);
		return [...m.entries()].sort((a, b) => b[1] - a[1]);
	});

	async ensure(): Promise<void> {
		if (this.products.length || this.loading) return;
		this.loading = true;
		try {
			const manifest = await loadManifest();
			const [products, stats] = await Promise.all([loadCatalog(), loadStats()]);
			this.manifest = manifest;
			this.products = products;
			this.stats = stats;
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
	v == null ? '—' : `${Math.round(v).toLocaleString('en-EG')} ${cur}`;

export const sellerName = (slug: string): string =>
	slug.replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

export const groupLabel = (g: string | null | undefined): string => GROUP_META[g ?? 'other']?.label ?? g ?? 'Other';
export const groupIcon = (g: string | null | undefined): string => GROUP_META[g ?? 'other']?.icon ?? '📦';
