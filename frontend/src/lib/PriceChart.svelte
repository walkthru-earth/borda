<script lang="ts">
	// Price history rendered with LayerChart – the library behind shadcn-svelte's chart
	// components – using its shadcn design tokens (`layerchart/shadcn-svelte.css`) so the
	// chart matches that look without pulling Tailwind into this plain-CSS app.
	import { LineChart, Points, Tooltip } from 'layerchart';
	import { scaleTime } from 'd3-scale';
	import 'layerchart/shadcn-svelte.css';
	import type { Point, Stats } from './parquet';
	import { fmtPrice, sellerName } from './data.svelte';
	import { tr, formatNumber, dateLocale, dateOptions, market, currencyName } from './i18n.svelte';

	type Row = Point & { listing: string };

	let { points }: { points: Point[]; stats?: Stats } = $props();
	// shadcn chart palette (--chart-1 … --chart-5 in oklch), extended for markets with many sellers.
	const palette = ['oklch(0.646 0.222 41.116)', 'oklch(0.6 0.118 184.704)', 'oklch(0.398 0.07 227.392)', 'oklch(0.828 0.189 84.429)', 'oklch(0.769 0.188 70.08)', 'oklch(0.55 0.2 300)', 'oklch(0.62 0.19 20)', 'oklch(0.5 0.12 150)'];
	let selectedCurrency = $state('');
	let period = $state('all');
	let valid = $derived(points.filter((point) => point.price != null && Number.isFinite(point.price) && point.price >= 0 && Number.isFinite(+point.ts)));
	let currencies = $derived([...new Set(valid.map((point) => point.currency))].sort());
	let currency = $derived(currencies.includes(selectedCurrency) ? selectedCurrency : currencies.includes(market.profile.currency) ? market.profile.currency : currencies[0] ?? market.profile.currency);
	let currencyPoints = $derived(valid.filter((point) => point.currency === currency));
	let latest = $derived(currencyPoints.reduce((value, point) => Math.max(value, +point.ts), 0));
	let priced = $derived(currencyPoints.filter((point) => period === 'all' || +point.ts >= latest - Number(period) * 86400000).sort((a, b) => +a.ts - +b.ts));
	let sellers = $derived([...new Set(priced.map((point) => point.seller))].sort());
	// One line per seller listing (a seller can list the same product twice); colour per seller.
	let rows = $derived<Row[]>(priced.map((point) => ({ ...point, listing: `${point.seller}\u0000${point.url}` })));
	let listings = $derived([...new Set(rows.map((row) => row.listing))]);
	let listingColors = $derived(listings.map((listing) => color(listing.split('\u0000')[0])));
	let ys = $derived(priced.map((point) => point.price as number));
	let xs = $derived(priced.map((point) => +point.ts));
	let x0 = $derived(xs.length ? Math.min(...xs) : 0);
	let x1 = $derived(xs.length ? Math.max(...xs) : 1);
	let y0 = $derived(ys.length ? Math.max(0, Math.min(...ys) * .9) : 0);
	let y1 = $derived(ys.length ? Math.max(...ys) * 1.1 || 1 : 1);
	// A single observation date has no time extent: pad the domain by a day so the point is centred.
	let xDomain = $derived<[Date, Date]>(x1 === x0 ? [new Date(x0 - 43200000), new Date(x1 + 43200000)] : [new Date(x0), new Date(x1)]);
	let median = $derived.by(() => { const sorted = [...ys].sort((a, b) => a - b); const mid = Math.floor(sorted.length / 2); return sorted.length ? (sorted[mid] + sorted[Math.floor((sorted.length - 1) / 2)]) / 2 : 0; });
	const fmtDate = (value: Date | number) => new Date(value).toLocaleDateString(dateLocale(), { ...dateOptions(), day: 'numeric', month: 'short', ...(x1 - x0 > 31536000000 ? { year: '2-digit' as const } : {}) });
	const fmtTick = (value: number) => value >= 1000 ? `${formatNumber(Number((value / 1000).toFixed(value >= 10000 ? 0 : 1)))}${tr('k', ' ألف')}` : formatNumber(Number(value.toFixed(value < 10 ? 1 : 0)));
	const stockLabel = (value: string) => ({ in_stock: tr("In stock", "متوفر"), out_of_stock: tr("Out of stock", "غير متوفر"), preorder: tr("Preorder", "طلب مسبق"), unknown: tr("Unconfirmed", "غير مؤكد") }[value] ?? tr("Unconfirmed", "غير مؤكد"));
	const color = (seller: string) => palette[sellers.indexOf(seller) % palette.length];
	const rowColor = (row: Row) => color(row.seller);
	// Filled dot = recorded in stock; open dot = out of stock / unconfirmed (matches the hint below).
	const pointFill = (row: Row) => row.availability === 'in_stock' ? rowColor(row) : 'var(--surface, #fff)';
</script>

{#if valid.length === 0}<div class="empty">{tr("No priced observations yet. Check back after the next catalog update.", "لم تُسجل أسعار بعد. يمكنك العودة بعد التحديث القادم للكتالوج.")}</div>{:else}
	<div class="chart-toolbar"><span class="unit">{tr('Prices in', 'الأسعار بعملة')} {currencyName(currency)}</span><div class="controls">{#if currencies.length > 1}<label><span class="sr-only">{tr("Chart currency", "عملة الرسم البياني")}</span><select value={currency} onchange={(event) => selectedCurrency = event.currentTarget.value} aria-label={tr("Chart currency", "عملة الرسم البياني")}>{#each currencies as unit (unit)}<option value={unit}>{unit}</option>{/each}</select></label>{/if}<label><span class="sr-only">{tr("Price history period", "فترة سجل الأسعار")}</span><select bind:value={period} aria-label={tr("Price history period", "فترة سجل الأسعار")}><option value="all">{tr("All history", "كل الفترات")}</option><option value="90">{tr("Last 90 days", "آخر ٩٠ يوماً")}</option><option value="30">{tr("Last 30 days", "آخر ٣٠ يوماً")}</option></select></label></div></div>
	<figure class="chart" dir="ltr" aria-label={tr(`Recorded price history in ${currency} across ${sellers.length} sellers. A table of all displayed observations follows the chart.`, `سجل الأسعار بعملة ${currency} لدى ${formatNumber(sellers.length)} بائعين. يتبع الرسم جدول بكل البيانات المعروضة.`)}>
		{#key `${currency}:${period}`}
		<LineChart
			data={rows}
			x="ts"
			y="price"
			c="listing"
			cDomain={listings}
			cRange={listingColors}
			xScale={scaleTime()}
			{xDomain}
			yDomain={[y0, y1]}
			seriesLayout="overlap"
			padding={{ top: 12, right: 14, bottom: 30, left: 52 }}
			grid={{ x: false, y: true }}
			highlight={{ lines: true, points: { r: 6, strokeWidth: 2 } }}
			tooltipContext={{ mode: 'quadtree' }}
			annotations={[{ type: 'line', y: median, label: tr('Median', 'الوسيط'), labelPlacement: 'top-right', props: { line: { class: 'median-line' } } }]}
			props={{
				spline: { strokeWidth: 2.25 },
				xAxis: { format: fmtDate, ticks: 4, rule: true },
				yAxis: { format: fmtTick, ticks: 5, rule: false },
			}}
		>
			{#snippet points()}
				<!-- Per-point fill (filled = in stock, open = other) needs the raw points; `Points`
				     only takes one fill for the whole mark. -->
				<Points r={3.5}>
					{#snippet children({ points: dots })}
						{#each dots as dot, index (index)}<circle class="lc-point" cx={dot.x} cy={dot.y} r={dot.r} fill={pointFill(dot.data)} stroke={rowColor(dot.data)} stroke-width="2" />{/each}
					{/snippet}
				</Points>
			{/snippet}
			{#snippet tooltip({ context })}
				<Tooltip.Root {context} x="data" y="data" anchor="bottom" yOffset={12} variant="default">
					{#snippet children({ data })}
						<Tooltip.Header color={rowColor(data)}>{sellerName(data.seller)}</Tooltip.Header>
						<Tooltip.List>
							<Tooltip.Item label={tr('Price', 'السعر')} value={fmtPrice(data.price, currency)} />
							<Tooltip.Item label={tr('Date', 'التاريخ')} value={data.ts.toLocaleDateString(dateLocale(), dateOptions())} />
							<Tooltip.Item label={tr('Stock', 'التوفر')} value={stockLabel(data.availability)} />
						</Tooltip.List>
					{/snippet}
				</Tooltip.Root>
			{/snippet}
		</LineChart>
		{/key}
	</figure>
	<div class="chart-readout"><span>{tr("Median", "الوسيط")} <strong>{fmtPrice(median, currency)}</strong></span><span>{tr(`${formatNumber(priced.length)} recorded ${priced.length === 1 ? 'price' : 'prices'}`, `${formatNumber(priced.length)} من الأسعار المسجلة`)}</span></div>
	<ul class="legend">{#each sellers as seller (seller)}<li><span class="swatch" style:background={color(seller)}></span>{sellerName(seller)}</li>{/each}</ul>
	<p class="chart-hint">{tr('Open dots: stock unavailable or unconfirmed. Dashed line: median.', 'النقاط الفارغة: غير متوفر أو غير مؤكد. الخط المتقطع: الوسيط.')}{#if period !== 'all'} {tr('Period ends at the latest recorded observation.', 'تنتهي الفترة عند آخر رصد مسجل.')}{/if}</p>
	<details class="table-disclosure"><summary>{tr("View price history as a table", "اعرض سجل الأسعار في جدول")}</summary><div class="table-scroll"><table><caption>{tr('Displayed price observations in', 'الأسعار المعروضة بعملة')} {currency}</caption><thead><tr><th scope="col">{tr("Date", "التاريخ")}</th><th scope="col">{tr("Seller", "البائع")}</th><th scope="col">{tr("Price", "السعر")}</th><th scope="col">{tr("Availability", "التوفر")}</th></tr></thead><tbody>{#each [...priced].reverse() as point, index (index)}<tr><td>{point.ts.toLocaleDateString(dateLocale(),dateOptions())}</td><td>{sellerName(point.seller)}</td><td>{fmtPrice(point.price, currency)}</td><td>{stockLabel(point.availability)}</td></tr>{/each}</tbody></table></div></details>
{/if}

<style>
	.chart-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:8px}.unit{font-size:.8125rem;font-weight:600;color:var(--muted)}.controls{display:flex;gap:6px}select{background:#f6f8f9;border:1px solid var(--line);border-radius:6px;font:inherit;font-size:.8125rem;color:var(--fg);padding:7px 8px;max-width:135px}
	/* shadcn chart tokens consumed by layerchart/shadcn-svelte.css, mapped onto this app's palette */
	.chart{position:relative;height:300px;margin:0 0 10px;--primary:var(--accent);--card-background:var(--surface,#fff);--card-muted:#f6f8f9;--card-foreground:var(--fg);--color-surface-content:var(--fg);font-size:.75rem;font-variant-numeric:tabular-nums}
	.chart :global(.lc-axis-tick-label){fill:var(--muted);font-size:.75rem}.chart :global(.lc-axis-rule){stroke:var(--line)}.chart :global(.lc-grid-line){stroke:#edf1f3;stroke-dasharray:3 4}.chart :global(.lc-highlight-line){stroke:#90aaa4;stroke-dasharray:3 3}.chart :global(.median-line){stroke:#9aaba8;stroke-dasharray:5 6;opacity:.75}.chart :global(.lc-annotation-line-label){fill:var(--muted);font-size:.6875rem}
	:global(.lc-tooltip-root){font-size:.8125rem;--color-surface-100:#fff;--color-surface-content:#172b3a}:global(.lc-tooltip-container){border:1px solid var(--line,#e3e8ec);border-radius:8px;box-shadow:0 8px 24px #172b3a1a}
	.chart-readout{display:flex;align-items:center;justify-content:space-between;gap:8px;min-height:38px;background:#f6f9f8;border-radius:7px;padding:8px 10px;font-size:.8125rem;color:var(--muted)}.chart-readout strong{color:var(--fg)}.legend{display:flex;flex-wrap:wrap;gap:10px 16px;list-style:none;padding:0;margin:16px 0 10px;font-size:.8125rem;color:var(--muted)}.swatch{display:inline-block;width:13px;height:4px;border-radius:2px;margin-inline-end:6px;vertical-align:middle}.chart-hint{font-size:.75rem;color:var(--muted);line-height:1.7;margin:0}.table-disclosure{margin-top:17px;border-top:1px solid var(--line);padding-top:13px}.table-disclosure summary{font-size:.8125rem;color:var(--accent);cursor:pointer}.table-scroll{max-height:320px;overflow:auto;margin-top:12px}table{width:100%;border-collapse:collapse;font-size:.8125rem;text-align:start}caption{text-align:start;padding:8px 0;color:var(--muted)}th{font-weight:600;background:#f6f8f9}td,th{padding:9px 7px;border-bottom:1px solid var(--line);white-space:nowrap}.empty{padding:60px 20px;text-align:center;color:var(--muted);font-size:.9375rem;line-height:1.7;background:#f8fafb;border-radius:10px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}select:focus-visible,summary:focus-visible{outline:3px solid #74c5b7;outline-offset:3px}
	@media(max-width:600px){.chart{height:240px}.chart-toolbar{flex-wrap:wrap}}
</style>
