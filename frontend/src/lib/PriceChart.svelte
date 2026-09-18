<script lang="ts">
	/** Dependency-free SVG price-history chart: one line per seller + min/median/max guides. */
	import type { Point, Stats } from './parquet';
	import { fmtPrice, sellerName } from './data.svelte';

	let { points, stats }: { points: Point[]; stats: Stats | undefined } = $props();

	const W = 720, H = 280, PAD = { l: 64, r: 16, t: 12, b: 28 };
	const palette = ['#2563eb', '#dc2626', '#16a34a', '#d97706', '#7c3aed', '#0891b2', '#db2777', '#65a30d', '#ea580c', '#4b5563'];

	let priced = $derived(points.filter((p) => p.price != null));
	let sellers = $derived([...new Set(priced.map((p) => p.seller))].sort());
	let xs = $derived(priced.map((p) => +p.ts));
	let ys = $derived(priced.map((p) => p.price as number));
	let x0 = $derived(Math.min(...xs)), x1 = $derived(Math.max(...xs));
	let y0 = $derived(Math.min(...ys) * 0.95), y1 = $derived(Math.max(...ys) * 1.05 || 1);

	const sx = (t: number) => (x1 === x0 ? (W - PAD.l - PAD.r) / 2 + PAD.l : PAD.l + ((t - x0) / (x1 - x0)) * (W - PAD.l - PAD.r));
	const sy = (v: number) => PAD.t + (1 - (v - y0) / (y1 - y0 || 1)) * (H - PAD.t - PAD.b);

	let yTicks = $derived.by(() => {
		const n = 4, step = (y1 - y0) / n;
		return Array.from({ length: n + 1 }, (_, i) => y0 + i * step);
	});
	let xTicks = $derived.by(() => {
		const uniq = [...new Set(xs)].sort((a, b) => a - b);
		const every = Math.max(1, Math.ceil(uniq.length / 6));
		return uniq.filter((_, i) => i % every === 0 || i === uniq.length - 1);
	});
	const fmtDate = (t: number) => new Date(t).toLocaleDateString('en-GB', { month: 'short', year: '2-digit' });

	let hover = $state<Point | null>(null);
</script>

{#if priced.length === 0}
	<p class="muted">No priced observations yet.</p>
{:else}
	<svg viewBox="0 0 {W} {H}" role="img" aria-label="price history chart">
		{#each yTicks as v}
			<line x1={PAD.l} x2={W - PAD.r} y1={sy(v)} y2={sy(v)} class="grid" />
			<text x={PAD.l - 6} y={sy(v) + 4} class="tick" text-anchor="end">{Math.round(v).toLocaleString()}</text>
		{/each}
		{#each xTicks as t}
			<text x={sx(t)} y={H - 8} class="tick" text-anchor="middle">{fmtDate(t)}</text>
		{/each}
		{#if stats?.median != null}
			<line x1={PAD.l} x2={W - PAD.r} y1={sy(stats.median)} y2={sy(stats.median)} class="median" />
			<text x={W - PAD.r} y={sy(stats.median) - 4} class="tick" text-anchor="end">median</text>
		{/if}
		{#each sellers as s, i}
			{@const pts = priced.filter((p) => p.seller === s)}
			<polyline
				fill="none"
				stroke={palette[i % palette.length]}
				stroke-width="2"
				points={pts.map((p) => `${sx(+p.ts)},${sy(p.price as number)}`).join(' ')}
			/>
			{#each pts as p}
				<circle
					cx={sx(+p.ts)}
					cy={sy(p.price as number)}
					r={hover === p ? 6 : 4}
					fill={p.availability === 'in_stock' ? palette[i % palette.length] : 'white'}
					stroke={palette[i % palette.length]}
					stroke-width="2"
					role="presentation"
					onmouseenter={() => (hover = p)}
					onmouseleave={() => (hover = null)}
				/>
			{/each}
		{/each}
		{#if hover}
			{@const tx = Math.min(sx(+hover.ts) + 8, W - 190)}
			<g transform="translate({tx},{Math.max(PAD.t, sy(hover.price as number) - 44)})">
				<rect width="180" height="40" rx="4" class="tip" />
				<text x="8" y="16" class="tipt">{sellerName(hover.seller)} · {fmtPrice(hover.price, hover.currency)}</text>
				<text x="8" y="32" class="tipt muted">{hover.ts.toLocaleDateString()} · {hover.availability.replace('_', ' ')}</text>
			</g>
		{/if}
	</svg>
	<ul class="legend">
		{#each sellers as s, i}
			<li><span class="swatch" style:background={palette[i % palette.length]}></span>{sellerName(s)}</li>
		{/each}
		<li class="muted">hollow dot = out of stock</li>
	</ul>
{/if}

<style>
	svg { width: 100%; height: auto; background: var(--card); border-radius: 8px; }
	.grid { stroke: var(--line); }
	.median { stroke: #9ca3af; stroke-dasharray: 4 4; }
	.tick { font-size: 11px; fill: var(--muted); }
	.tip { fill: var(--bg); stroke: var(--line); }
	.tipt { font-size: 12px; fill: var(--fg); }
	.legend { display: flex; flex-wrap: wrap; gap: 0.75rem; list-style: none; padding: 0; margin: 0.5rem 0 0; font-size: 0.85rem; }
	.swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; }
	.muted { color: var(--muted); }
</style>
