<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import ProductCard from '$lib/ProductCard.svelte';
	import { catalog, groupIcon, groupLabel, sellerName } from '$lib/data.svelte';
	import { search } from '$lib/search';
	import { fuse, semanticSearch, type Hit, type Progress } from '$lib/semantic';
	import type { Product } from '$lib/parquet';

	// URL is the source of truth for every filter, so results are shareable/bookmarkable.
	let q = $derived(page.url.searchParams.get('q') ?? '');
	let group = $derived(page.url.searchParams.get('group') ?? '');
	let tag = $derived(page.url.searchParams.get('tag') ?? '');
	let seller = $derived(page.url.searchParams.get('seller') ?? '');
	let stock = $derived(page.url.searchParams.get('stock') === '1');
	let sort = $derived(page.url.searchParams.get('sort') ?? 'relevance');
	let maxPrice = $derived(Number(page.url.searchParams.get('max') ?? 0) || 0);
	let semantic = $derived(page.url.searchParams.get('ai') === '1');

	function setParams(patch: Record<string, string | null>) {
		const u = new URL(page.url);
		for (const [k, v] of Object.entries(patch)) v ? u.searchParams.set(k, v) : u.searchParams.delete(k);
		void goto(`${u.pathname}${u.search}`, { replaceState: true, keepFocus: true, noScroll: true });
	}

	// --- semantic results (lazy: model + embeddings load only when enabled)
	let semHits = $state<Hit[]>([]);
	let semBusy = $state(false);
	let semProgress = $state<Progress | null>(null);
	let semError = $state<string | null>(null);
	$effect(() => {
		const query = q, on = semantic;
		if (!on || query.trim().length < 2) { semHits = []; return; }
		let cancelled = false;
		semBusy = true;
		const t = setTimeout(() => {
			semanticSearch(query, 80, (p) => (semProgress = p))
				.then((h) => { if (!cancelled) semHits = h; })
				.catch((e) => { if (!cancelled) semError = e instanceof Error ? e.message : String(e); })
				.finally(() => { if (!cancelled) { semBusy = false; semProgress = null; } });
		}, 250);
		return () => { cancelled = true; clearTimeout(t); };
	});

	let results = $derived.by((): Product[] => {
		if (!catalog.index) return [];
		let list = search(catalog.index, q, 5000);
		if (semantic && semHits.length) {
			const ids = fuse(list.map((p) => p.id), semHits);
			list = ids.map((id) => catalog.byId.get(id)).filter((p): p is Product => !!p);
		}
		if (group) list = list.filter((p) => (p.group ?? 'other') === group);
		if (tag) list = list.filter((p) => p.tags.includes(tag));
		if (seller) list = list.filter((p) => p.sellers.includes(seller));
		if (stock) list = list.filter((p) => (catalog.stats.get(p.id)?.in_stock_sellers ?? 0) > 0);
		if (maxPrice) list = list.filter((p) => (catalog.stats.get(p.id)?.latest_min ?? Infinity) <= maxPrice);
		const price = (p: Product) => catalog.stats.get(p.id)?.latest_min ?? Infinity;
		if (sort === 'price-asc') list = [...list].sort((a, b) => price(a) - price(b));
		else if (sort === 'price-desc') list = [...list].sort((a, b) => (price(b) === Infinity ? -1 : price(b)) - (price(a) === Infinity ? -1 : price(a)));
		else if (sort === 'sellers') list = [...list].sort((a, b) => b.sellers.length - a.sellers.length);
		else if (sort === 'name') list = [...list].sort((a, b) => a.canonical_name.localeCompare(b.canonical_name));
		return list;
	});

	// infinite list
	let limit = $state(48);
	$effect(() => { void [q, group, tag, seller, stock, sort, maxPrice]; limit = 48; });
	let shown = $derived(results.slice(0, limit));
	function sentinel(node: HTMLElement) {
		const io = new IntersectionObserver((es) => { if (es[0].isIntersecting) limit += 48; }, { rootMargin: '600px' });
		io.observe(node);
		return { destroy: () => io.disconnect() };
	}

	let filtersOpen = $state(false);
	let activeFilters = $derived([tag, seller, stock ? '1' : '', maxPrice ? '1' : '', sort !== 'relevance' ? '1' : ''].filter(Boolean).length);
	let tagsForGroup = $derived.by(() => {
		if (!group) return catalog.tagCounts.slice(0, 30);
		const m = new Map<string, number>();
		for (const p of catalog.products) if ((p.group ?? 'other') === group) for (const t of p.tags) m.set(t, (m.get(t) ?? 0) + 1);
		return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 30);
	});
</script>

<svelte:head><title>{q ? `${q} – ` : ''}EG Maker Market</title></svelte:head>

<nav class="scroll-x groups" aria-label="categories">
	<button class="chip" class:on={!group} onclick={() => setParams({ group: null, tag: null })}>All <span class="muted small">{catalog.products.length || ''}</span></button>
	{#each catalog.groups as [g, n] (g)}
		<button class="chip" class:on={g === group} onclick={() => setParams({ group: g === group ? null : g, tag: null })}>{groupIcon(g)} {groupLabel(g)} <span class="muted small">{n}</span></button>
	{/each}
</nav>

<div class="toolbar">
	<span class="muted small count">
		{#if catalog.loading}loading catalog…{:else}{results.length.toLocaleString()} products{/if}
		{#if semBusy}<span class="badge soft">{semProgress?.status === 'progress' && semProgress.progress != null ? `AI model ${Math.round(semProgress.progress)}%` : 'AI…'}</span>{/if}
	</span>
	<button class="chip" class:on={semantic} title="Semantic search: understands meaning, not just words (downloads a 23 MB model once)" onclick={() => setParams({ ai: semantic ? null : '1' })}>✨ AI search</button>
	<button class="chip" class:on={filtersOpen || activeFilters > 0} onclick={() => (filtersOpen = !filtersOpen)}>⚙ Filters{activeFilters ? ` · ${activeFilters}` : ''}</button>
</div>

{#if filtersOpen}
	<section class="card filters">
		<label class="field">Seller
			<select value={seller} onchange={(e) => setParams({ seller: e.currentTarget.value || null })}>
				<option value="">any</option>
				{#each catalog.sellers as s}<option value={s}>{sellerName(s)}</option>{/each}
			</select>
		</label>
		<label class="field">Sort
			<select value={sort} onchange={(e) => setParams({ sort: e.currentTarget.value === 'relevance' ? null : e.currentTarget.value })}>
				<option value="relevance">relevance</option>
				<option value="price-asc">price: low → high</option>
				<option value="price-desc">price: high → low</option>
				<option value="sellers">most sellers</option>
				<option value="name">name</option>
			</select>
		</label>
		<label class="field">Max price (EGP)
			<input type="number" inputmode="numeric" min="0" step="10" value={maxPrice || ''} placeholder="any" onchange={(e) => setParams({ max: e.currentTarget.value || null })} />
		</label>
		<label class="field check"><input type="checkbox" checked={stock} onchange={(e) => setParams({ stock: e.currentTarget.checked ? '1' : null })} /> in stock only</label>
		{#if activeFilters}<button class="btn" onclick={() => setParams({ tag: null, seller: null, stock: null, max: null, sort: null })}>Reset</button>{/if}
	</section>
{/if}

{#if tagsForGroup.length}
	<div class="scroll-x tags">
		{#each tagsForGroup as [t, n] (t)}
			<button class="chip ghost" class:on={t === tag} onclick={() => setParams({ tag: t === tag ? null : t })}>#{t} <span class="muted small">{n}</span></button>
		{/each}
	</div>
{/if}

{#if semError}<p class="card notice small">AI search unavailable: {semError}</p>{/if}

{#if catalog.loading}
	<div class="grid">{#each Array(12) as _, i (i)}<div class="skeleton" style="aspect-ratio: 3/4"></div>{/each}</div>
{:else if results.length === 0 && catalog.index}
	<div class="card empty">
		<p><strong>No products match.</strong></p>
		<p class="muted">Try fewer words, a part number, or turn on ✨ AI search for meaning-based results (works with Arabic queries too).</p>
	</div>
{:else}
	<div class="grid">
		{#each shown as p (p.id)}<ProductCard product={p} />{/each}
	</div>
	{#if shown.length < results.length}<div use:sentinel class="muted small more">showing {shown.length} of {results.length}…</div>{/if}
{/if}

<style>
	.groups { margin: 0.25rem 0 0.6rem; }
	.toolbar { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem; }
	.count { flex: 1; display: flex; align-items: center; gap: 0.4rem; }
	.filters { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 0.75rem; padding: 0.9rem; margin-bottom: 0.6rem; align-items: end; }
	.check { flex-direction: row; align-items: center; gap: 0.5rem; color: var(--fg); min-height: 42px; }
	.tags { margin-bottom: 0.75rem; }
	.empty { padding: 1.5rem; text-align: center; }
	.more { text-align: center; padding: 1.5rem; }
	.notice { padding: 0.6rem 0.9rem; margin-bottom: 0.6rem; border-color: var(--bad); }
</style>
