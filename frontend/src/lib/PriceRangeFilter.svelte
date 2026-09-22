<script lang="ts">
	// Price-range filter: a LayerChart histogram of the current result set's prices (log-spaced
	// bins – electronics prices span 0.5 to 50,000) with a two-thumb slider in the shadcn style
	// (native range inputs, so keyboard and screen readers work), plus exact min/max fields.
	import { BarChart } from 'layerchart';
	import { ArrowRight } from '@lucide/svelte';
	import 'layerchart/shadcn-svelte.css';
	import { tr, formatNumber, currencyLabel } from './i18n.svelte';

	let { prices, min = 0, max = 0, onapply }: { prices: number[]; min?: number; max?: number; onapply: (min: number, max: number) => void } = $props();

	const STEPS = 200, BINS = 28;
	const nice = (value: number) => { if (value <= 0) return 0; const magnitude = 10 ** Math.floor(Math.log10(value)); const unit = value / magnitude; return Math.round(unit * (unit < 3 ? 10 : unit < 10 ? 2 : 1)) / (unit < 3 ? 10 : unit < 10 ? 2 : 1) * magnitude; };
	let valid = $derived(prices.filter((price) => Number.isFinite(price) && price > 0).sort((a, b) => a - b));
	// The slider covers the 1st–99th percentile; the ends mean "no minimum" / "no maximum".
	let lo = $derived(valid.length ? Math.max(0.01, nice(valid[Math.floor(valid.length * 0.01)])) : 1);
	let hi = $derived.by(() => { const top = valid.length ? nice(valid[Math.min(valid.length - 1, Math.floor(valid.length * 0.99))]) : 1000; return top > lo ? top : lo * 10; });
	const toPrice = (step: number) => nice(lo * (hi / lo) ** (step / STEPS));
	const toStep = (price: number) => Math.round(Math.max(0, Math.min(1, Math.log(price / lo) / Math.log(hi / lo))) * STEPS);
	let bins = $derived.by(() => {
		const counts = Array.from({ length: BINS }, (_, index) => ({ index, from: lo * (hi / lo) ** (index / BINS), to: lo * (hi / lo) ** ((index + 1) / BINS), count: 0 }));
		for (const price of valid) counts[Math.max(0, Math.min(BINS - 1, Math.floor((Math.log(price / lo) / Math.log(hi / lo)) * BINS)))].count += 1;
		return counts;
	});

	let minStep = $state(0), maxStep = $state(STEPS);
	let minDraft = $state(''), maxDraft = $state('');
	let error = $state('');
	$effect(() => { minStep = min ? toStep(min) : 0; maxStep = max ? toStep(max) : STEPS; minDraft = min ? String(min) : ''; maxDraft = max ? String(max) : ''; error = ''; });
	let draftMin = $derived(minStep > 0 ? toPrice(minStep) : 0);
	let draftMax = $derived(maxStep < STEPS ? toPrice(maxStep) : 0);
	let chartData = $derived(bins.map((bin) => ({ ...bin, state: (draftMin && bin.to <= draftMin) || (draftMax && bin.from >= draftMax) ? 'out' : 'in' })));
	let dirty = $derived(draftMin !== (min || 0) || draftMax !== (max || 0));

	function slide(which: 'min' | 'max', value: number) {
		if (which === 'min') minStep = Math.min(value, maxStep - 1); else maxStep = Math.max(value, minStep + 1);
		minDraft = draftMin ? String(draftMin) : ''; maxDraft = draftMax ? String(draftMax) : ''; error = '';
	}
	function commitSlider() { onapply(draftMin, draftMax); }
	function submit(event: SubmitEvent) {
		event.preventDefault();
		const from = Number(minDraft || 0), to = Number(maxDraft || 0);
		if (!Number.isFinite(from) || !Number.isFinite(to) || from < 0 || to < 0 || (to > 0 && from > to)) { error = tr('Enter a minimum below the maximum.', 'أدخل حدًا أدنى أقل من الحد الأقصى.'); return; }
		error = ''; onapply(from, to);
	}
	const label = (value: number, fallback: string) => value ? formatNumber(value) : fallback;
</script>

<div class="price-filter">
	{#if valid.length > 1}
		<div class="histogram" dir="ltr" aria-hidden="true">
			<BarChart data={chartData} x="index" y="count" c="state" cDomain={['in', 'out']} cRange={['var(--accent)', '#d5dde1']} axis={false} grid={false} rule={false} tooltipContext={false} padding={0} bandPadding={0.25} props={{ bars: { radius: 2, strokeWidth: 0, motion: 'none' } }} />
		</div>
		<div class="slider" style:--lo="{(minStep / STEPS) * 100}%" style:--hi="{(maxStep / STEPS) * 100}%" dir="ltr">
			<div class="track"><div class="range"></div></div>
			<input type="range" min="0" max={STEPS} step="1" value={minStep} aria-label={tr('Minimum price', 'الحد الأدنى للسعر')} aria-valuetext={label(draftMin, tr('No minimum', 'بدون حد أدنى'))} oninput={(event) => slide('min', Number(event.currentTarget.value))} onchange={commitSlider} />
			<input type="range" min="0" max={STEPS} step="1" value={maxStep} aria-label={tr('Maximum price', 'الحد الأقصى للسعر')} aria-valuetext={label(draftMax, tr('No maximum', 'بدون حد أقصى'))} oninput={(event) => slide('max', Number(event.currentTarget.value))} onchange={commitSlider} />
		</div>
		<div class="slider-labels" aria-hidden="true"><span>{label(draftMin, formatNumber(0))}</span><span>{label(draftMax, tr('Any', 'بدون حد'))}</span></div>
	{/if}
	<form onsubmit={submit}>
		<div class="price-inputs"><label class="field"><span>{tr('Min', 'من')}</span><input type="number" inputmode="decimal" min="0" step="any" placeholder="0" bind:value={minDraft} /></label><span class="range-dash">–</span><label class="field"><span>{tr('Max', 'إلى')}</span><input type="number" inputmode="decimal" min="0" step="any" placeholder={tr('Any', 'بدون حد')} bind:value={maxDraft} /></label></div>
		{#if error}<p class="price-error" role="alert">{error}</p>{/if}
		<button class="apply-price" type="submit" class:dirty>{tr('Apply price range', 'تطبيق نطاق السعر')} <span class="unit">{currencyLabel()}</span><ArrowRight size={14} /></button>
	</form>
</div>

<style>
	.price-filter{display:flex;flex-direction:column;gap:8px}
	.histogram{height:56px;margin:0 8px -2px;--primary:var(--accent)}
	/* shadcn Slider look: 6px track, accent range, white thumbs with an accent ring */
	.slider{position:relative;height:20px;margin:0 8px}.track{position:absolute;inset:8px 0;border-radius:999px;background:#e3e8ec}.range{position:absolute;top:0;bottom:0;left:var(--lo);right:calc(100% - var(--hi));background:var(--accent);border-radius:999px}
	.slider input{position:absolute;inset:0;width:100%;margin:0;background:none;-webkit-appearance:none;appearance:none;pointer-events:none;height:20px}
	.slider input::-webkit-slider-runnable-track{background:none}.slider input::-moz-range-track{background:none}
	.slider input::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;pointer-events:auto;width:18px;height:18px;border-radius:50%;background:#fff;border:2px solid var(--accent);box-shadow:0 1px 3px #172b3a2e;cursor:grab;transition:box-shadow .15s}.slider input::-moz-range-thumb{pointer-events:auto;width:14px;height:14px;border-radius:50%;background:#fff;border:2px solid var(--accent);box-shadow:0 1px 3px #172b3a2e;cursor:grab}
	.slider input:active::-webkit-slider-thumb{cursor:grabbing;box-shadow:0 0 0 5px var(--accent-soft)}.slider input:focus-visible{outline:none}.slider input:focus-visible::-webkit-slider-thumb{box-shadow:0 0 0 4px var(--accent-soft),0 0 0 6px var(--accent)}.slider input:focus-visible::-moz-range-thumb{box-shadow:0 0 0 4px var(--accent-soft),0 0 0 6px var(--accent)}
	.slider-labels{display:flex;justify-content:space-between;font-size:.75rem;color:var(--muted);font-variant-numeric:tabular-nums;margin:-4px 2px 2px}
	/* `.field` itself comes from app.css (shared with the store select) */
	.price-inputs{display:flex;align-items:flex-end;gap:8px}.field{flex:1;min-width:0;font-size:.8125rem}.field input{min-width:0;font-size:.9375rem;font-variant-numeric:tabular-nums}.field input:focus-visible{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}.range-dash{color:var(--muted);padding-bottom:11px}
	.price-error{color:var(--bad);font-size:.8125rem;margin:0}
	.apply-price{display:flex;align-items:center;justify-content:center;gap:6px;width:100%;margin-top:10px;padding:9px 10px;border:1px solid var(--line);border-radius:8px;background:var(--surface);font-size:.875rem;font-weight:600;color:var(--fg)}.apply-price .unit{font-weight:500;color:var(--muted)}.apply-price:hover,.apply-price.dirty{border-color:var(--accent);color:var(--accent)}.apply-price.dirty{background:var(--accent-soft)}
</style>
