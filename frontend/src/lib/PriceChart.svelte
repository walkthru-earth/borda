<script lang="ts">
	/** Dependency-free, touch-friendly SVG price chart: one line per seller + median guide. */
	import type { Point, Stats } from './parquet';
	import { fmtPrice, sellerName } from './data.svelte';

	let { points, stats }: { points: Point[]; stats: Stats | undefined } = $props();

	const W = 720, H = 300, PAD = { l: 56, r: 12, t: 16, b: 30 };
	const palette = ['#2457f5', '#e11d48', '#059669', '#d97706', '#7c3aed', '#0891b2', '#db2777', '#65a30d', '#ea580c', '#64748b'];

	let priced = $derived(points.filter((p) => p.price != null));
	let sellers = $derived([...new Set(priced.map((p) => p.seller))].sort());
	let xs = $derived(priced.map((p) => +p.ts));
	let ys = $derived(priced.map((p) => p.price as number));
	let x0 = $derived(Math.min(...xs)), x1 = $derived(Math.max(...xs));
	let y0 = $derived(Math.max(0, Math.min(...ys) * 0.92)), y1 = $derived(Math.max(...ys) * 1.06 || 1);

	const sx = (t: number) => (x1 === x0 ? (W - PAD.l - PAD.r) / 2 + PAD.l : PAD.l + ((t - x0) / (x1 - x0)) * (W - PAD.l - PAD.r));
	const sy = (v: number) => PAD.t + (1 - (v - y0) / (y1 - y0 || 1)) * (H - PAD.t - PAD.b);

	let yTicks = $derived(Array.from({ length: 5 }, (_, i) => y0 + (i * (y1 - y0)) / 4));
	let xTicks = $derived.by(() => {
		const uniq = [...new Set(xs)].sort((a, b) => a - b);
		const every = Math.max(1, Math.ceil(uniq.length / 6));
		return uniq.filter((_, i) => i % every === 0 || i === uniq.length - 1);
	});
	const fmtDate = (t: number) => new Date(t).toLocaleDateString('en-GB', { month: 'short', year: '2-digit' });
	const fmtK = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k` : `${Math.round(v)}`);

	let hover = $state<Point | null>(null);
	let svg = $state<SVGSVGElement | null>(null);

	/** Nearest point to the pointer (works for touch – no tiny hit targets). */
	function pick(e: PointerEvent) {
		if (!svg || !priced.length) return;
		const r = svg.getBoundingClientRect();
		const px = ((e.clientX - r.left) / r.width) * W, py = ((e.clientY - r.top) / r.height) * H;
		let best: Point | null = null, bd = Infinity;
		for (const p of priced) {
			const d = (sx(+p.ts) - px) ** 2 + (sy(p.price as number) - py) ** 2;
			if (d < bd) { bd = d; best = p; }
		}
		hover = bd < 40 ** 2 ? best : null;
	}
</script>

{#if priced.length === 0}
	<p class="muted">No priced observations yet.</p>
{:else}
	<svg bind:this={svg} viewBox="0 0 {W} {H}" role="img" aria-label="price history" onpointermove={pick} onpointerdown={pick} onpointerleave={() => (hover = null)}>
		{#each yTicks as v (v)}
			<line x1={PAD.l} x2={W - PAD.r} y1={sy(v)} y2={sy(v)} class="grid" />
			<text x={PAD.l - 6} y={sy(v) + 4} class="tick" text-anchor="end">{fmtK(v)}</text>
		{/each}
		{#each xTicks as t (t)}
			<text x={sx(t)} y={H - 8} class="tick" text-anchor="middle">{fmtDate(t)}</text>
		{/each}
		{#if stats?.median != null}
			<line x1={PAD.l} x2={W - PAD.r} y1={sy(stats.median)} y2={sy(stats.median)} class="median" />
			<text x={W - PAD.r} y={sy(stats.median) - 4} class="tick" text-anchor="end">median {fmtK(stats.median)}</text>
		{/if}
		{#each sellers as s, i (s)}
			{@const pts = priced.filter((p) => p.seller === s)}
			<polyline fill="none" stroke={palette[i % palette.length]} stroke-width="2.5" stroke-linejoin="round" points={pts.map((p) => `${sx(+p.ts)},${sy(p.price as number)}`).join(' ')} />
			{#each pts as p (p.ts.getTime() + p.url)}
				<circle cx={sx(+p.ts)} cy={sy(p.price as number)} r={hover === p ? 7 : 4.5} fill={p.availability === 'in_stock' ? palette[i % palette.length] : 'var(--surface)'} stroke={palette[i % palette.length]} stroke-width="2.5" />
			{/each}
		{/each}
		{#if hover}
			{@const tx = Math.min(Math.max(sx(+hover.ts) - 90, PAD.l), W - PAD.r - 180)}
			<g transform="translate({tx},{Math.max(PAD.t, sy(hover.price as number) - 52)})">
				<rect width="180" height="42" rx="8" class="tip" />
				<text x="10" y="17" class="tipt">{sellerName(hover.seller)} · {fmtPrice(hover.price, hover.currency)}</text>
				<text x="10" y="33" class="tipt muted">{hover.ts.toLocaleDateString()} · {hover.availability.replace('_', ' ')}</text>
			</g>
		{/if}
	</svg>
	<ul class="legend">
		{#each sellers as s, i (s)}
			<li><span class="swatch" style:background={palette[i % palette.length]}></span>{sellerName(s)}</li>
		{/each}
		<li class="muted">◯ = out of stock</li>
	</ul>
{/if}

<style>
	svg { width: 100%; height: auto; touch-action: pan-y; display: block; }
	.grid { stroke: var(--line); }
	.median { stroke: var(--muted); stroke-dasharray: 5 5; opacity: 0.7; }
	.tick { font-size: 11px; fill: var(--muted); }
	.tip { fill: var(--surface); stroke: var(--line); filter: drop-shadow(0 4px 8px rgb(0 0 0 / 0.15)); }
	.tipt { font-size: 12px; fill: var(--fg); }
	.tipt.muted { fill: var(--muted); }
	.legend { display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; list-style: none; padding: 0; margin: 0.5rem 0 0; font-size: 0.82rem; }
	.swatch { display: inline-block; width: 12px; height: 4px; border-radius: 2px; margin-right: 6px; vertical-align: middle; }
</style>
