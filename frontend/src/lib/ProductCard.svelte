<script lang="ts">
	import { base } from '$app/paths';
	import { catalog, fmtPrice, groupIcon, groupLabel } from '$lib/data.svelte';
	import type { Product } from '$lib/parquet';

	let { product }: { product: Product } = $props();
	let s = $derived(catalog.stats.get(product.id));
	let aka = $derived(product.raw_names.filter((n) => n.toLowerCase() !== product.canonical_name.toLowerCase()));
	let drop = $derived(s && s.latest_min != null && s.median != null && s.median > 0 ? Math.round(((s.latest_min - s.median) / s.median) * 100) : null);
</script>

<a class="card item" href="{base}/product/{product.id}">
	<div class="img">
		{#if product.image}
			<img src={product.image} alt="" loading="lazy" decoding="async" />
		{:else}
			<span class="ph">{groupIcon(product.group)}</span>
		{/if}
		{#if s}
			<span class="badge {s.in_stock_sellers ? 'ok' : 'bad'} stock">{s.in_stock_sellers ? `${s.in_stock_sellers} in stock` : 'out of stock'}</span>
		{/if}
	</div>
	<div class="body">
		<div class="muted small group">{groupIcon(product.group)} {groupLabel(product.group)}{product.brand ? ` · ${product.brand}` : ''}</div>
		<h3 class="name">{product.canonical_name}</h3>
		{#if aka.length}<div class="muted small aka" dir="auto">aka {aka[0]}{aka.length > 1 ? ` +${aka.length - 1}` : ''}</div>{/if}
		<div class="price-row">
			<strong class="price">{fmtPrice(s?.latest_min, s?.currency)}</strong>
			{#if drop != null && Math.abs(drop) >= 5}<span class="badge {drop < 0 ? 'ok' : 'bad'}">{drop > 0 ? '+' : ''}{drop}% vs median</span>{/if}
		</div>
		<div class="muted small">{product.sellers.length} seller{product.sellers.length === 1 ? '' : 's'}{s?.min != null && s.max != null && s.min !== s.max ? ` · ${fmtPrice(s.min, '')}–${fmtPrice(s.max, s.currency)}` : ''}</div>
	</div>
</a>

<style>
	.item { display: flex; flex-direction: column; overflow: hidden; transition: transform 0.15s, box-shadow 0.15s; }
	@media (hover: hover) { .item:hover { transform: translateY(-2px); box-shadow: 0 12px 30px -18px rgb(15 23 42 / 0.5); } }
	.img { position: relative; aspect-ratio: 4 / 3; background: white; display: grid; place-items: center; border-bottom: 1px solid var(--line); }
	.img img { width: 100%; height: 100%; object-fit: contain; padding: 0.5rem; }
	.ph { font-size: 2.4rem; opacity: 0.5; }
	.stock { position: absolute; left: 0.5rem; bottom: 0.5rem; }
	.body { padding: 0.6rem 0.75rem 0.75rem; display: flex; flex-direction: column; gap: 0.2rem; }
	.name { font-size: 0.95rem; font-weight: 600; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
	.aka { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.price-row { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.15rem; }
	.price { font-size: 1.05rem; font-variant-numeric: tabular-nums; }
	@media (max-width: 480px) { .body { padding: 0.5rem; } .name { font-size: 0.85rem; } .group { display: none; } }
</style>
