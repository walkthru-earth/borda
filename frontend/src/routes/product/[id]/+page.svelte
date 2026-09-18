<script lang="ts">
	import { base } from '$app/paths';
	import { page } from '$app/state';
	import PriceChart from '$lib/PriceChart.svelte';
	import ProductCard from '$lib/ProductCard.svelte';
	import { catalog, fmtPrice, groupIcon, groupLabel, sellerName } from '$lib/data.svelte';
	import { goto } from '$app/navigation';
	import { loadProductDetail, loadSeries, resolveRedirect, type Point, type ProductDetail } from '$lib/parquet';

	let id = $derived(page.params.id ?? '');
	let product = $derived(catalog.byId.get(id));
	let stats = $derived(catalog.stats.get(id));

	// merged/renamed ids (old links) are redirected to the surviving product
	$effect(() => {
		const pid = id;
		if (catalog.loading || !catalog.index || catalog.byId.has(pid)) return;
		void resolveRedirect(pid).then((target) => { if (target && pid === id) void goto(`${base}/product/${target}`, { replaceState: true }); });
	});

	let detail = $state<ProductDetail | null>(null);
	let points = $state<Point[]>([]);
	let loadingSeries = $state(true);
	$effect(() => {
		const pid = id;
		detail = null; points = []; loadingSeries = true;
		void loadProductDetail(pid).then((d) => { if (pid === id) detail = d; });
		void loadSeries([pid]).then((pts) => { if (pid === id) { points = pts; loadingSeries = false; } });
	});

	let latestBySeller = $derived.by(() => {
		const m = new Map<string, Point>();
		for (const p of points) { const cur = m.get(p.seller); if (!cur || +p.ts > +cur.ts) m.set(p.seller, p); }
		return [...m.values()].sort((a, b) => (a.price ?? Infinity) - (b.price ?? Infinity));
	});
	let similar = $derived((product?.similar ?? []).map((s) => catalog.byId.get(s)).filter((p) => !!p).slice(0, 8));
	let aka = $derived((product?.raw_names ?? []).filter((n) => n.toLowerCase() !== product?.canonical_name.toLowerCase()));
	let unpriced = $derived(Object.entries(detail?.listings ?? {}).filter(([k]) => !latestBySeller.some((o) => k.startsWith(o.seller + ':'))));
</script>

<svelte:head><title>{product?.canonical_name ?? id} – price history · EG Maker Market</title></svelte:head>

<nav class="crumbs muted small">
	<a href="{base}/">Home</a> › {#if product}<a href="{base}/?group={product.group ?? 'other'}">{groupIcon(product.group)} {groupLabel(product.group)}</a>{/if}
</nav>

{#if catalog.loading}
	<div class="skeleton" style="height: 220px"></div>
{:else if !product}
	<div class="card pad">Unknown product <code>{id}</code>. It may have been merged into another product — <a class="link" href="{base}/?q={id.split('-').slice(0, 2).join(' ')}">search for it</a>.</div>
{:else}
	<article class="hero card">
		<div class="media">
			{#if product.image}<img src={product.image} alt={product.canonical_name} />{:else}<span class="ph">{groupIcon(product.group)}</span>{/if}
		</div>
		<div class="info">
			<div class="muted small">{[product.brand, product.category].filter(Boolean).join(' · ')}</div>
			<h1>{product.canonical_name}</h1>
			{#if detail?.description}<p class="desc">{detail.description}</p>{:else if !detail}<div class="skeleton" style="height: 2.4em"></div>{/if}
			<div class="tags">{#each product.tags as t (t)}<a class="chip ghost" href="{base}/?tag={t}">#{t}</a>{/each}</div>
		</div>
		<div class="stats">
			<div class="big"><span class="muted small">best price now</span><strong>{fmtPrice(stats?.latest_min, stats?.currency)}</strong>
				{#if stats}<span class="badge {stats.in_stock_sellers ? 'ok' : 'bad'}">{stats.in_stock_sellers ? `in stock at ${stats.in_stock_sellers}/${product.sellers.length}` : 'out of stock everywhere'}</span>{/if}
			</div>
			<dl>
				<dt>min</dt><dd>{fmtPrice(stats?.min, '')}</dd>
				<dt>median</dt><dd>{fmtPrice(stats?.median, '')}</dd>
				<dt>max</dt><dd>{fmtPrice(stats?.max, '')}</dd>
				<dt>observations</dt><dd>{stats?.observations ?? 0}</dd>
			</dl>
		</div>
	</article>

	<section class="card pad">
		<h2>Price history</h2>
		{#if loadingSeries}<div class="skeleton" style="height: 200px"></div>{:else}<PriceChart {points} {stats} />{/if}
	</section>

	<section class="card pad">
		<h2>Where to buy</h2>
		<ul class="sellers">
			{#each latestBySeller as o (o.seller)}
				<li>
					<a href={o.url} target="_blank" rel="noopener nofollow" class="seller-row">
						<span class="who"><strong>{sellerName(o.seller)}</strong><span class="muted small">seen {o.ts.toLocaleDateString()}</span></span>
						<span class="badge {o.availability === 'in_stock' ? 'ok' : 'bad'}">{o.availability.replace('_', ' ')}</span>
						<span class="p">{fmtPrice(o.price, o.currency)}</span>
						<span class="go">↗</span>
					</a>
				</li>
			{/each}
			{#each unpriced as [key, url] (key)}
				<li><a href={url} target="_blank" rel="noopener nofollow" class="seller-row muted"><span class="who"><strong>{sellerName(key.split(':')[0])}</strong><span class="small">no price observed</span></span><span></span><span class="p">—</span><span class="go">↗</span></a></li>
			{/each}
		</ul>
	</section>

	{#if aka.length}
		<section class="card pad">
			<h2>Also sold as <span class="muted small">local & seller names</span></h2>
			<div class="aka">{#each aka as n (n)}<span class="chip ghost" dir="auto">{n}</span>{/each}</div>
		</section>
	{/if}

	{#if similar.length}
		<section>
			<h2 class="sec">Similar products <span class="muted small">by meaning</span></h2>
			<div class="grid">{#each similar as p (p.id)}<ProductCard product={p} />{/each}</div>
		</section>
	{/if}
{/if}

<style>
	.crumbs { margin: 0.25rem 0 0.75rem; }
	.crumbs a:hover { text-decoration: underline; }
	.link { color: var(--accent); text-decoration: underline; }
	.pad { padding: 1rem; margin-bottom: 0.75rem; }
	h2 { font-size: 1.05rem; margin-bottom: 0.6rem; }
	h2.sec { margin: 0.5rem 0 0.6rem; }
	.hero { display: grid; grid-template-columns: 200px 1fr 240px; gap: 1.25rem; padding: 1rem; margin-bottom: 0.75rem; align-items: start; }
	@media (max-width: 900px) { .hero { grid-template-columns: 1fr; } }
	.media { aspect-ratio: 1; background: white; border-radius: 12px; display: grid; place-items: center; overflow: hidden; border: 1px solid var(--line); max-width: 320px; margin: 0 auto; width: 100%; }
	.media img { width: 100%; height: 100%; object-fit: contain; padding: 0.75rem; }
	.ph { font-size: 4rem; opacity: 0.5; }
	.info h1 { font-size: 1.35rem; margin: 0.1rem 0 0.5rem; }
	.desc { margin: 0 0 0.6rem; }
	.tags { display: flex; flex-wrap: wrap; gap: 0.35rem; }
	.stats { display: flex; flex-direction: column; gap: 0.6rem; }
	.big { display: flex; flex-direction: column; gap: 0.2rem; padding: 0.75rem; border-radius: 12px; background: var(--accent-soft); }
	.big strong { font-size: 1.6rem; font-variant-numeric: tabular-nums; }
	dl { display: grid; grid-template-columns: 1fr auto; gap: 0.15rem 1rem; margin: 0; font-size: 0.9rem; }
	dt { color: var(--muted); } dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }
	.sellers { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.35rem; }
	.seller-row { display: grid; grid-template-columns: 1fr auto auto auto; gap: 0.75rem; align-items: center; padding: 0.6rem 0.75rem; border: 1px solid var(--line); border-radius: 10px; min-height: 48px; }
	.seller-row:hover { border-color: var(--accent); }
	.who { display: flex; flex-direction: column; min-width: 0; }
	.p { font-weight: 700; font-variant-numeric: tabular-nums; }
	.go { color: var(--accent); }
	.aka { display: flex; flex-wrap: wrap; gap: 0.35rem; }
</style>
