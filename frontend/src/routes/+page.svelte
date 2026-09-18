<script lang="ts">
	import { base } from '$app/paths';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { catalog, fmtPrice, sellerName } from '$lib/data.svelte';
	import { search } from '$lib/search';

	// URL is the source of truth for filters so searches are shareable.
	let q = $derived(page.url.searchParams.get('q') ?? '');
	let tag = $derived(page.url.searchParams.get('tag') ?? '');
	let seller = $derived(page.url.searchParams.get('seller') ?? '');
	let stock = $derived(page.url.searchParams.get('stock') === '1');
	let sort = $derived(page.url.searchParams.get('sort') ?? 'relevance');

	function setParam(key: string, value: string | null) {
		const u = new URL(page.url);
		if (value) u.searchParams.set(key, value);
		else u.searchParams.delete(key);
		void goto(`${u.pathname}${u.search}`, { replaceState: true, keepFocus: true, noScroll: true });
	}

	let results = $derived.by(() => {
		if (!catalog.index) return [];
		let list = search(catalog.index, q, 5000);
		if (tag) list = list.filter((p) => p.tags.includes(tag));
		if (seller) list = list.filter((p) => p.sellers.includes(seller));
		if (stock) list = list.filter((p) => (catalog.stats.get(p.id)?.in_stock_sellers ?? 0) > 0);
		if (sort === 'price-asc' || sort === 'price-desc') {
			const dir = sort === 'price-asc' ? 1 : -1;
			list = [...list].sort((a, b) => {
				const pa = catalog.stats.get(a.id)?.latest_min ?? Infinity;
				const pb = catalog.stats.get(b.id)?.latest_min ?? Infinity;
				return (pa - pb) * dir;
			});
		} else if (sort === 'sellers') {
			list = [...list].sort((a, b) => b.sellers.length - a.sellers.length);
		}
		return list;
	});
	let shown = $derived(results.slice(0, 200));
</script>

<svelte:head><title>Egypt Maker Market – search</title></svelte:head>

<section class="controls">
	<input
		type="search"
		placeholder="Search official or local names, part numbers, Arabic… e.g. esp32, اردوينو, hc-sr04"
		value={q}
		oninput={(e) => setParam('q', e.currentTarget.value)}
		style="flex:1; min-width: 260px"
	/>
	<select value={seller} onchange={(e) => setParam('seller', e.currentTarget.value)}>
		<option value="">all sellers</option>
		{#each catalog.sellers as s}<option value={s}>{sellerName(s)}</option>{/each}
	</select>
	<select value={sort} onchange={(e) => setParam('sort', e.currentTarget.value)}>
		<option value="relevance">relevance</option>
		<option value="price-asc">price ↑</option>
		<option value="price-desc">price ↓</option>
		<option value="sellers">most sellers</option>
	</select>
	<label><input type="checkbox" checked={stock} onchange={(e) => setParam('stock', e.currentTarget.checked ? '1' : null)} /> in stock</label>
</section>

<section class="tags">
	{#each catalog.tagCounts.slice(0, 40) as [t, n]}
		<button class="tag" class:on={t === tag} onclick={() => setParam('tag', t === tag ? null : t)}>{t} <span class="muted">{n}</span></button>
	{/each}
</section>

{#if catalog.loading}
	<p class="muted">Loading catalog.parquet…</p>
{:else if catalog.index}
	<p class="muted">{results.length.toLocaleString()} products{results.length > shown.length ? ` (showing ${shown.length})` : ''}</p>
	<table>
		<thead>
			<tr><th></th><th>product</th><th>tags</th><th class="num">best price now</th><th class="num">min / median / max</th><th class="num">sellers</th></tr>
		</thead>
		<tbody>
			{#each shown as p (p.id)}
				{@const s = catalog.stats.get(p.id)}
				<tr>
					<td>{#if p.image}<img class="thumb" src={p.image} alt="" loading="lazy" />{/if}</td>
					<td>
						<a href="{base}/product/{p.id}"><strong>{p.canonical_name}</strong></a>
						{#if p.brand}<span class="muted"> · {p.brand}</span>{/if}
						{#if p.raw_names.length && p.raw_names[0] !== p.canonical_name}
							<div class="muted small" title="local / seller names">aka {p.raw_names.slice(0, 2).join(' · ')}{p.raw_names.length > 2 ? ` +${p.raw_names.length - 2}` : ''}</div>
						{/if}
					</td>
					<td>{#each p.tags.slice(0, 5) as t}<button class="tag" onclick={() => setParam('tag', t)}>{t}</button>{/each}</td>
					<td class="num">
						{fmtPrice(s?.latest_min, s?.currency)}
						{#if s}<div><span class="pill {s.in_stock_sellers ? 'ok' : 'bad'}">{s.in_stock_sellers ? `${s.in_stock_sellers} in stock` : 'out of stock'}</span></div>{/if}
					</td>
					<td class="num muted">{s ? `${s.min?.toLocaleString()} / ${s.median?.toLocaleString()} / ${s.max?.toLocaleString()}` : '—'}</td>
					<td class="num">{p.sellers.length}</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}

<style>
	.controls { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; margin-bottom: 0.75rem; }
	.tags { margin-bottom: 0.75rem; }
	.tags .tag, td .tag { cursor: pointer; font: inherit; font-size: 0.75rem; }
	.small { font-size: 0.8rem; }
</style>
