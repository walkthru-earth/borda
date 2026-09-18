<script lang="ts">
	import '../app.css';
	import { base } from '$app/paths';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { catalog } from '$lib/data.svelte';

	let { children } = $props();
	$effect(() => { void catalog.ensure(); });

	let q = $derived(page.url.searchParams.get('q') ?? '');
	let onHome = $derived(page.url.pathname.replace(base, '') === '/' || page.url.pathname === base);
	let timer: ReturnType<typeof setTimeout> | undefined;

	function submit(value: string) {
		clearTimeout(timer);
		timer = setTimeout(() => {
			const u = new URL(page.url);
			u.pathname = `${base}/`;
			if (value) u.searchParams.set('q', value); else u.searchParams.delete('q');
			void goto(`${u.pathname}${u.search}`, { replaceState: onHome, keepFocus: true, noScroll: true });
		}, onHome ? 120 : 0);
	}
</script>

<header class="top">
	<div class="container bar">
		<a class="brand" href="{base}/" aria-label="home">
			<span class="logo">⚡</span><span class="name">EG&nbsp;Maker&nbsp;Market</span>
		</a>
		<label class="search">
			<span class="icon" aria-hidden="true">🔍</span>
			<input
				type="search"
				enterkeyhint="search"
				autocomplete="off"
				placeholder="esp32, اردوينو اونو, hc-sr04, 10k resistor…"
				value={q}
				oninput={(e) => submit(e.currentTarget.value)}
			/>
		</label>
	</div>
</header>

<main class="container">
	{#if catalog.error}
		<p class="card notice">Could not load data: {catalog.error}</p>
	{/if}
	{@render children()}
</main>

<footer class="container muted small">
	{#if catalog.manifest}
		{catalog.manifest.products.toLocaleString()} products · {catalog.manifest.offers_total.toLocaleString()} price observations · {catalog.manifest.runs.length} monthly runs ·
		updated {new Date(catalog.manifest.generated_at).toLocaleDateString()} ·
		<a href="https://github.com/walkthru-earth/microcontroller-store" rel="noopener">source & data (Parquet)</a>
	{/if}
</footer>

<style>
	.top { position: sticky; top: 0; z-index: 10; background: color-mix(in srgb, var(--bg) 85%, transparent); backdrop-filter: saturate(160%) blur(12px); border-bottom: 1px solid var(--line); }
	.bar { display: flex; align-items: center; gap: 0.75rem; padding: 0.6rem 0; }
	.brand { display: flex; align-items: center; gap: 0.4rem; font-weight: 800; letter-spacing: -0.01em; }
	.logo { font-size: 1.2rem; }
	.search { flex: 1; display: flex; align-items: center; gap: 0.4rem; background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 0 0.7rem; min-height: 42px; }
	.search:focus-within { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
	.search input { flex: 1; min-width: 0; border: 0; outline: 0; background: transparent; color: inherit; font: inherit; font-size: 1rem; }
	.search input::-webkit-search-cancel-button { -webkit-appearance: none; }
	.icon { opacity: 0.7; }
	main { padding: 0.75rem 0 2rem; min-height: 60vh; }
	footer { padding: 1rem 0 calc(1.5rem + var(--safe-b)); }
	footer a { text-decoration: underline; }
	.notice { padding: 0.75rem 1rem; border-color: var(--bad); }
	@media (max-width: 480px) { .name { display: none; } }
</style>
