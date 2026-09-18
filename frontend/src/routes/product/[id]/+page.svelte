<script lang="ts">
	import { base } from '$app/paths';
	import { page } from '$app/state';
	import PriceChart from '$lib/PriceChart.svelte';
	import ProductCard from '$lib/ProductCard.svelte';
	import { catalog, fmtPrice, groupIcon, groupLabel, sellerName } from '$lib/data.svelte';
	import { goto } from '$app/navigation';
	import { loadListings, loadProductDetail, loadSeries, resolveRedirect, type Listing, type Point, type ProductDetail } from '$lib/parquet';

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
	let listings = $state<Listing[]>([]);
	let points = $state<Point[]>([]);
	let loadingSeries = $state(true);
	$effect(() => {
		const pid = id;
		detail = null; points = []; listings = []; loadingSeries = true;
		void loadProductDetail(pid).then((d) => { if (pid === id) detail = d; });
		void loadListings(pid).then((l) => { if (pid === id) listings = l; });
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
	let specs = $derived((detail?.specs ?? []).map((s) => { const i = s.indexOf(':'); return i > 0 ? [s.slice(0, i).trim(), s.slice(i + 1).trim()] : ['', s]; }));
	let docLinks = $derived([...new Set(listings.flatMap((l) => l.links ?? []))].filter((u) => u !== detail?.datasheet_url).slice(0, 8));
	let sellerTexts = $derived(listings.filter((l) => l.description && l.description.length > 40).sort((a, b) => (b.description?.length ?? 0) - (a.description?.length ?? 0)));
	let searchTerm = $derived(detail?.mpn ?? product?.canonical_name ?? '');
	const linkLabel = (u: string) => { try { const url = new URL(u); const last = decodeURIComponent(url.pathname.split('/').filter(Boolean).pop() ?? ''); return `${url.hostname.replace(/^www\./, '')}${last ? ' · ' + last.slice(0, 40) : ''}`; } catch { return u; } };
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
			{#if specs.length}
				<dl class="specs">{#each specs as [k, v] (k + v)}<div><dt>{k}</dt><dd>{v}</dd></div>{/each}</dl>
			{/if}
			<div class="docs">
				{#if detail?.datasheet_url}
					<a class="btn primary" href={detail.datasheet_url} target="_blank" rel="noopener nofollow">📄 Datasheet ↗</a>
				{:else if searchTerm}
					<a class="btn" href="https://www.alldatasheet.com/view.jsp?Searchword={encodeURIComponent(searchTerm)}" target="_blank" rel="noopener nofollow">📄 Find datasheet{detail?.mpn ? ` · ${detail.mpn}` : ''} ↗</a>
				{/if}
				{#if detail?.mpn}<a class="btn" href="https://octopart.com/search?q={encodeURIComponent(detail.mpn)}" target="_blank" rel="noopener nofollow">🔎 {detail.mpn} on Octopart ↗</a>{/if}
			</div>
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

	{#if docLinks.length || sellerTexts.length}
		<section class="card pad">
			<h2>From the sellers <span class="muted small">descriptions & documents on their pages</span></h2>
			{#if docLinks.length}
				<ul class="links">{#each docLinks as u (u)}<li><a href={u} target="_blank" rel="noopener nofollow">🔗 {linkLabel(u)}</a></li>{/each}</ul>
			{/if}
			{#each sellerTexts.slice(0, 4) as l (l.listing_key)}
				<details class="seller-text">
					<summary><strong>{sellerName(l.seller)}</strong> <span class="muted small" dir="auto">— {l.raw_name}</span></summary>
					<p dir="auto">{l.description}</p>
				</details>
			{/each}
		</section>
	{/if}

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
	.specs { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 0.4rem; margin: 0 0 0.75rem; }
	.specs div { background: var(--bg); border: 1px solid var(--line); border-radius: 10px; padding: 0.4rem 0.6rem; }
	.specs dt { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }
	.specs dd { margin: 0; font-weight: 600; font-size: 0.9rem; }
	.docs { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.75rem; }
	.links { list-style: none; margin: 0 0 0.5rem; padding: 0; display: flex; flex-wrap: wrap; gap: 0.4rem 1rem; font-size: 0.9rem; }
	.links a { color: var(--accent); }
	.seller-text { border-top: 1px solid var(--line); padding: 0.5rem 0; }
	.seller-text summary { cursor: pointer; }
	.seller-text p { white-space: pre-line; margin: 0.4rem 0 0; font-size: 0.9rem; max-height: 14em; overflow: auto; }
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
