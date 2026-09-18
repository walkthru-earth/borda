<script lang="ts">
	import type { Point, Stats } from './parquet';
	import { fmtPrice, sellerName } from './data.svelte';
	import { tr, formatNumber, dateLocale } from './i18n.svelte';

	let { points }: { points: Point[]; stats?: Stats } = $props();
	const W = 720, H = 310, PAD = { l: 62, r: 28, t: 25, b: 37 };
	const palette = ['#087f73', '#5276b7', '#bb8144', '#9b6eaa', '#c5666a', '#579ba8', '#768748', '#bd728f'];
	let selectedCurrency = $state('');
	let period = $state('all');
	let hover = $state<Point | null>(null);
	let svg = $state<SVGSVGElement | null>(null);
	let valid = $derived(points.filter((point) => point.price != null && Number.isFinite(point.price) && point.price >= 0 && Number.isFinite(+point.ts)));
	let currencies = $derived([...new Set(valid.map((point) => point.currency))].sort());
	let currency = $derived(currencies.includes(selectedCurrency) ? selectedCurrency : currencies.includes('EGP') ? 'EGP' : currencies[0] ?? 'EGP');
	let currencyPoints = $derived(valid.filter((point) => point.currency === currency));
	let latest = $derived(currencyPoints.reduce((value, point) => Math.max(value, +point.ts), 0));
	let priced = $derived(currencyPoints.filter((point) => period === 'all' || +point.ts >= latest - Number(period) * 86400000).sort((a, b) => +a.ts - +b.ts));
	let sellers = $derived([...new Set(priced.map((point) => point.seller))].sort());
	let series = $derived.by(() => {
		const groups = new Map<string, Point[]>();
		for (const point of priced) {
			const key = `${point.seller}:${point.url}`;
			const group = groups.get(key) ?? [];
			group.push(point); groups.set(key, group);
		}
		return [...groups.values()];
	});
	let xs = $derived(priced.map((point) => +point.ts));
	let ys = $derived(priced.map((point) => point.price as number));
	let x0 = $derived(xs.length ? Math.min(...xs) : 0);
	let x1 = $derived(xs.length ? Math.max(...xs) : 1);
	let y0 = $derived(ys.length ? Math.max(0, Math.min(...ys) * .9) : 0);
	let y1 = $derived(ys.length ? Math.max(...ys) * 1.1 || 1 : 1);
	let median = $derived.by(() => { const sorted = [...ys].sort((a, b) => a - b); const mid = Math.floor(sorted.length / 2); return sorted.length ? (sorted[mid] + sorted[Math.floor((sorted.length - 1) / 2)]) / 2 : 0; });
	const sx = (time: number) => x1 === x0 ? (W + PAD.l - PAD.r) / 2 : PAD.l + ((time - x0) / (x1 - x0)) * (W - PAD.l - PAD.r);
	const sy = (value: number) => PAD.t + (1 - (value - y0) / (y1 - y0 || 1)) * (H - PAD.t - PAD.b);
	let yTicks = $derived(Array.from({ length: 5 }, (_, i) => y0 + (i * (y1 - y0)) / 4));
	let xTicks = $derived.by(() => { const unique = [...new Set(xs)].sort((a, b) => a - b); if (unique.length <= 4) return unique; return [...new Set([unique[0], unique[Math.floor((unique.length - 1) / 3)], unique[Math.floor(2 * (unique.length - 1) / 3)], unique[unique.length - 1]])]; });
	const fmtDate = (time: number) => new Date(time).toLocaleDateString(dateLocale(), { day: 'numeric', month: 'short', ...(x1 - x0 > 31536000000 ? { year: '2-digit' as const } : {}) });
	const fmtTick = (value: number) => value >= 1000 ? `${formatNumber(Number((value / 1000).toFixed(value >= 10000 ? 0 : 1)))}${tr('k', ' ألف')}` : formatNumber(Number(value.toFixed(value < 10 ? 1 : 0)));
	const stockLabel = (value: string) => ({ in_stock: tr("In stock", "متوفر"), out_of_stock: tr("Out of stock", "غير متوفر"), preorder: tr("Preorder", "طلب مسبق"), unknown: tr("Unconfirmed", "غير مؤكد") }[value] ?? tr("Unconfirmed", "غير مؤكد"));
	const color = (seller: string) => palette[sellers.indexOf(seller) % palette.length];
	let activePoint = $derived(hover && priced.includes(hover) ? hover : null);
	function pick(event: PointerEvent) {
		if (!svg || !priced.length) return;
		const rectangle = svg.getBoundingClientRect();
		const px = ((event.clientX - rectangle.left) / rectangle.width) * W;
		const py = ((event.clientY - rectangle.top) / rectangle.height) * H;
		let best: Point | null = null, distance = Infinity;
		for (const point of priced) { const d = (sx(+point.ts) - px) ** 2 + (sy(point.price as number) - py) ** 2; if (d < distance) { distance = d; best = point; } }
		hover = distance < 65 ** 2 ? best : null;
	}
</script>

{#if valid.length === 0}<div class="empty">{tr("No priced observations yet. Check back after the next catalog update.", "لم تُسجل أسعار بعد. يمكنك العودة بعد التحديث القادم للكتالوج.")}</div>{:else}
	<div class="chart-toolbar"><span class="unit">{tr('Prices in', 'الأسعار بعملة')} {currency === 'EGP' ? tr('EGP', 'الجنيه المصري') : currency}</span><div class="controls">{#if currencies.length > 1}<label><span class="sr-only">{tr("Chart currency", "عملة الرسم البياني")}</span><select value={currency} onchange={(event) => selectedCurrency = event.currentTarget.value} aria-label={tr("Chart currency", "عملة الرسم البياني")}>{#each currencies as unit (unit)}<option value={unit}>{unit}</option>{/each}</select></label>{/if}<label><span class="sr-only">{tr("Price history period", "فترة سجل الأسعار")}</span><select bind:value={period} aria-label={tr("Price history period", "فترة سجل الأسعار")}><option value="all">{tr("All history", "كل الفترات")}</option><option value="90">{tr("Last 90 days", "آخر ٩٠ يوماً")}</option><option value="30">{tr("Last 30 days", "آخر ٣٠ يوماً")}</option></select></label></div></div>
	<svg direction="ltr" bind:this={svg} viewBox="0 0 {W} {H}" role="img" aria-label={tr(`Recorded price history in ${currency} across ${sellers.length} sellers. A table of all displayed observations follows the chart.`, `سجل الأسعار بعملة ${currency} لدى ${formatNumber(sellers.length)} بائعين. يتبع الرسم جدول بكل البيانات المعروضة.`)} onpointermove={pick} onpointerdown={pick} onpointerleave={() => hover = null}>
		<title>{tr('Recorded prices in', 'الأسعار المسجلة بعملة')} {currency}</title><desc>{tr("Each line follows one seller listing. Filled dots indicate recorded in-stock availability. Open dots indicate other or unknown availability.", "يمثل كل خط عرضاً لدى بائع. النقاط الممتلئة تعني أن المنتج كان متوفراً، والفارغة تعني عدم توفره أو أن التوفر غير مؤكد.")}</desc>
		{#each yTicks as value (value)}<line x1={PAD.l} x2={W - PAD.r} y1={sy(value)} y2={sy(value)} class="grid-line" /><text x={PAD.l - 10} y={sy(value) + 4} class="tick" text-anchor="end">{fmtTick(value)}</text>{/each}
		{#each xTicks as time (time)}<text x={sx(time)} y={H - 10} class="tick" text-anchor="middle">{fmtDate(time)}</text>{/each}
		<line x1={PAD.l} x2={W - PAD.r} y1={sy(median)} y2={sy(median)} class="median-line" />
		{#each series as group, i (i)}<polyline fill="none" stroke={color(group[0].seller)} stroke-width="2.5" stroke-linejoin="round" points={group.map((point) => `${sx(+point.ts)},${sy(point.price as number)}`).join(' ')} />{#each group as point, j (j)}<circle cx={sx(+point.ts)} cy={sy(point.price as number)} r={activePoint === point ? 6 : 3.6} fill={point.availability === 'in_stock' ? color(point.seller) : 'var(--surface, #fff)'} stroke={color(point.seller)} stroke-width="2" />{/each}{/each}
		{#if activePoint}<line x1={sx(+activePoint.ts)} x2={sx(+activePoint.ts)} y1={PAD.t} y2={H - PAD.b} class="crosshair" />{/if}
	</svg>
	<div class="chart-readout" aria-live="polite">{#if activePoint}<strong>{sellerName(activePoint.seller)} · {fmtPrice(activePoint.price, currency)}</strong><span>{activePoint.ts.toLocaleDateString(dateLocale())} · {stockLabel(activePoint.availability)}</span>{:else}<span>{tr("Median", "الوسيط")} <strong>{fmtPrice(median, currency)}</strong></span><span>{tr(`${formatNumber(priced.length)} recorded ${priced.length === 1 ? 'price' : 'prices'}`, `${formatNumber(priced.length)} من الأسعار المسجلة`)}</span>{/if}</div>
	<ul class="legend">{#each sellers as seller (seller)}<li><span class="swatch" style:background={color(seller)}></span>{sellerName(seller)}</li>{/each}</ul>
	<p class="chart-hint">{tr('Open dots: stock unavailable or unconfirmed. Dashed line: median.', 'النقاط الفارغة: غير متوفر أو غير مؤكد. الخط المتقطع: الوسيط.')}{#if period !== 'all'} {tr('Period ends at the latest recorded observation.', 'تنتهي الفترة عند آخر رصد مسجل.')}{/if}</p>
	<details class="table-disclosure"><summary>{tr("View price history as a table", "اعرض سجل الأسعار في جدول")}</summary><div class="table-scroll"><table><caption>{tr('Displayed price observations in', 'الأسعار المعروضة بعملة')} {currency}</caption><thead><tr><th scope="col">{tr("Date", "التاريخ")}</th><th scope="col">{tr("Seller", "البائع")}</th><th scope="col">{tr("Price", "السعر")}</th><th scope="col">{tr("Availability", "التوفر")}</th></tr></thead><tbody>{#each [...priced].reverse() as point, index (index)}<tr><td>{point.ts.toLocaleDateString(dateLocale())}</td><td>{sellerName(point.seller)}</td><td>{fmtPrice(point.price, currency)}</td><td>{stockLabel(point.availability)}</td></tr>{/each}</tbody></table></div></details>
{/if}

<style>
	.chart-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:8px}.unit{font-size:10px;font-weight:600;color:var(--muted)}.controls{display:flex;gap:6px}select{background:#f6f8f9;border:1px solid var(--line);border-radius:6px;font:inherit;font-size:10px;color:var(--fg);padding:7px 8px;max-width:135px}svg{width:100%;height:auto;touch-action:pan-y;display:block;overflow:visible}.grid-line{stroke:#edf1f3;stroke-dasharray:3 4}.median-line{stroke:#9aaba8;stroke-dasharray:5 6;opacity:.65}.tick{font-size:12px;fill:var(--muted);font-variant-numeric:tabular-nums}.crosshair{stroke:#90aaa4;stroke-dasharray:3 3;pointer-events:none}.chart-readout{display:flex;align-items:center;justify-content:space-between;gap:8px;min-height:38px;background:#f6f9f8;border-radius:7px;padding:8px 10px;font-size:10px;color:var(--muted)}.chart-readout strong{color:var(--fg)}.legend{display:flex;flex-wrap:wrap;gap:10px 16px;list-style:none;padding:0;margin:16px 0 10px;font-size:10px;color:var(--muted)}.swatch{display:inline-block;width:13px;height:4px;border-radius:2px;margin-inline-end:6px;vertical-align:middle}.chart-hint{font-size:9px;color:var(--muted);line-height:1.7;margin:0}.table-disclosure{margin-top:17px;border-top:1px solid var(--line);padding-top:13px}.table-disclosure summary{font-size:10px;color:var(--accent);cursor:pointer}.table-scroll{max-height:320px;overflow:auto;margin-top:12px}table{width:100%;border-collapse:collapse;font-size:10px;text-align:start}caption{text-align:start;padding:8px 0;color:var(--muted)}th{font-weight:600;background:#f6f8f9}td,th{padding:9px 7px;border-bottom:1px solid var(--line);white-space:nowrap}.empty{padding:60px 20px;text-align:center;color:var(--muted);font-size:12px;line-height:1.7;background:#f8fafb;border-radius:10px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}select:focus-visible,summary:focus-visible{outline:3px solid #74c5b7;outline-offset:3px}@media(max-width:600px){.chart-readout{align-items:start;flex-direction:column}.tick{font-size:14px}.chart-toolbar{margin-top:4px}.unit{font-size:9px}}
</style>
