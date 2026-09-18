<script lang="ts">
	import { base } from '$app/paths';
	import { page } from '$app/state';
	import PriceChart from '$lib/PriceChart.svelte';
	import { catalog, fmtPrice, sellerName } from '$lib/data.svelte';
	import { loadSeries, type Point } from '$lib/parquet';

	let id = $derived(page.params.id ?? '');
	let product = $derived(catalog.byId.get(id));
	let stats = $derived(catalog.stats.get(id));

	let points = $state<Point[]>([]);
	let loadingSeries = $state(false);
	$effect(() => {
		const pid = id;
		loadingSeries = true;
		loadSeries(pid).then((pts) => {
			if (pid === id) points = pts;
		}).finally(() => (loadingSeries = false));
	});

	/** Latest observation per seller (current offers table). */
	let latestBySeller = $derived.by(() => {
		const m = new Map<string, Point>();
		for (const p of points) {
			const cur = m.get(p.seller);
			if (!cur || +p.ts > +cur.ts) m.set(p.seller, p);
		}
		return [...m.values()].sort((a, b) => (a.price ?? Infinity) - (b.price ?? Infinity));
	});
</script>

<svelte:head><title>{product?.canonical_name ?? id} – price history</title></svelte:head>

<p><a href="{base}/">← all products</a></p>

{#if catalog.loading}
	<p class="muted">Loading…</p>
{:else if !product}
	<p class="card">Unknown product <code>{id}</code>. It may have been merged into another product; try <a href="{base}/?q={id.split('-').slice(0, 2).join(' ')}">searching</a>.</p>
{:else}
	<article class="head">
		{#if product.image}<img class="hero" src={product.image} alt={product.canonical_name} />{/if}
		<div>
			<h2>{product.canonical_name}</h2>
			{#if product.brand || product.category}
				<p class="muted">{[product.brand, product.category].filter(Boolean).join(' · ')}</p>
			{/if}
			{#if product.description}<p>{product.description}</p>{/if}
			<p>{#each product.tags as t}<a class="tag" href="{base}/?tag={t}">{t}</a>{/each}</p>
			{#if product.raw_names.length}
				<details>
					<summary class="muted">Also sold as ({product.raw_names.length} local names)</summary>
					<ul class="muted">{#each product.raw_names as n}<li>{n}</li>{/each}</ul>
				</details>
			{/if}
		</div>
		{#if stats}
			<dl class="card stats">
				<dt>best price now</dt><dd><strong>{fmtPrice(stats.latest_min, stats.currency)}</strong></dd>
				<dt>all-time min</dt><dd>{fmtPrice(stats.min, stats.currency)}</dd>
				<dt>median</dt><dd>{fmtPrice(stats.median, stats.currency)}</dd>
				<dt>all-time max</dt><dd>{fmtPrice(stats.max, stats.currency)}</dd>
				<dt>in stock at</dt><dd>{stats.in_stock_sellers} / {product.sellers.length} sellers</dd>
				<dt>observations</dt><dd>{stats.observations}</dd>
			</dl>
		{/if}
	</article>

	<h3>Price history</h3>
	{#if loadingSeries}<p class="muted">Loading series bucket…</p>{:else}<PriceChart {points} {stats} />{/if}

	<h3>Where to buy</h3>
	<table>
		<thead><tr><th>seller</th><th class="num">last price</th><th>availability</th><th>seen</th><th>link</th></tr></thead>
		<tbody>
			{#each latestBySeller as o (o.seller)}
				<tr>
					<td>{sellerName(o.seller)}</td>
					<td class="num">{fmtPrice(o.price, o.currency)}</td>
					<td><span class="pill {o.availability === 'in_stock' ? 'ok' : 'bad'}">{o.availability.replace('_', ' ')}</span></td>
					<td class="muted">{o.ts.toLocaleDateString()}</td>
					<td><a href={o.url} target="_blank" rel="noopener nofollow">open ↗</a></td>
				</tr>
			{/each}
			{#each Object.entries(product.listings).filter(([k]) => !latestBySeller.some((o) => k.startsWith(o.seller + ':'))) as [key, url] (key)}
				<tr class="muted">
					<td>{sellerName(key.split(':')[0])}</td><td class="num">—</td><td>no price observed</td><td></td>
					<td><a href={url} target="_blank" rel="noopener nofollow">open ↗</a></td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}

<style>
	.head { display: grid; grid-template-columns: auto 1fr auto; gap: 1.25rem; align-items: start; margin-bottom: 1rem; }
	@media (max-width: 800px) { .head { grid-template-columns: 1fr; } }
	.hero { width: 160px; height: 160px; object-fit: contain; background: white; border-radius: 8px; border: 1px solid var(--line); }
	h2 { margin: 0 0 0.25rem; }
	.stats { display: grid; grid-template-columns: auto auto; gap: 0.15rem 1rem; margin: 0; min-width: 240px; }
	.stats dt { color: var(--muted); font-size: 0.8rem; }
	.stats dd { margin: 0; text-align: right; }
	details ul { margin: 0.25rem 0 0; padding-left: 1.2rem; font-size: 0.85rem; }
</style>
