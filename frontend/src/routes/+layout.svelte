<script lang="ts">
	import '../app.css';
	import { base } from '$app/paths';
	import { catalog } from '$lib/data.svelte';

	let { children } = $props();
	$effect(() => { void catalog.ensure(); });
</script>

<header class="top">
	<div class="in">
		<h1><a href="{base}/">Egypt Maker Market</a></h1>
		<span class="muted">official names · local aliases · monthly price history from Egyptian stores</span>
		{#if catalog.manifest}
			<span class="muted" style="margin-left:auto">
				{catalog.manifest.products.toLocaleString()} products · {catalog.manifest.runs.length} runs · updated {new Date(catalog.manifest.generated_at).toLocaleDateString()}
			</span>
		{/if}
	</div>
</header>
<main>
	{#if catalog.error}
		<p class="card" style="border-color: var(--bad)">Could not load data: {catalog.error}</p>
	{/if}
	{@render children()}
</main>
