/**
 * PostHog product analytics – the full anonymous user journey:
 *  - automatic: pageviews on every SvelteKit navigation (`history_change`), pageleaves,
 *    autocapture of clicks/submits, session replay, web vitals, dead & rage clicks, JS errors
 *  - named journey events (below) so funnels read search → filter → product → seller click-out
 *
 * Borda has no accounts, so nobody is ever `identify()`-ed: PostHog's anonymous distinct id
 * (person profiles only for identified users) is enough and keeps events cheap.
 *
 * Setup follows https://posthog.com/docs/libraries/svelte (init from the root layout `load`
 * in the browser, `defaults` pinned to the newest preset, absolute asset paths for replay).
 * Configure with PUBLIC_POSTHOG_KEY / PUBLIC_POSTHOG_HOST (repo-root .env, GitHub Actions
 * variables in production). Without a key everything here is a no-op.
 */
import posthog from 'posthog-js';
import { browser, dev } from '$app/environment';
import { env } from '$env/dynamic/public';

let enabled = false;

export function initAnalytics(): void {
	const key = env.PUBLIC_POSTHOG_KEY;
	if (!browser || !key || enabled) return;
	// Local dev never pollutes production data unless explicitly asked (PUBLIC_POSTHOG_DEBUG=1).
	if (dev && !env.PUBLIC_POSTHOG_DEBUG) return;
	posthog.init(key, {
		api_host: env.PUBLIC_POSTHOG_HOST || 'https://eu.i.posthog.com',
		defaults: '2026-08-30',
		person_profiles: 'identified_only',
		respect_dnt: true,
		// Search terms are the product – they are not personal data here (no accounts, no
		// checkout), so replays keep them visible. Anything password-like stays masked.
		session_recording: { maskAllInputs: false, maskInputOptions: { password: true } },
		capture_performance: { web_vitals: true },
		capture_dead_clicks: true,
		capture_exceptions: true,
		debug: !!env.PUBLIC_POSTHOG_DEBUG
	});
	enabled = true;
}

/** Super property on every event: which UI language the visitor is using. */
export function setAnalyticsLanguage(language: string): void {
	if (enabled) posthog.register({ ui_language: language });
}

type Props = Record<string, string | number | boolean | null | undefined>;

function capture(event: string, props: Props): void {
	if (enabled) posthog.capture(event, props);
}

/** The catalog view settled (query/filters/sort; debounced by the caller). */
export function trackCatalogView(props: {
	query: string;
	group: string;
	tag: string;
	seller: string;
	in_stock_only: boolean;
	sort: string;
	min_price: number;
	max_price: number;
	ai_discovery: boolean;
	results: number;
}): void {
	capture('catalog_view', { ...props, has_query: props.query.length > 0, has_filters: !!(props.group || props.tag || props.seller || props.in_stock_only || props.min_price || props.max_price) });
}

export function trackProductView(props: { product_id: string; name: string; group: string | null; brand: string | null; sellers: number; lowest_price: number | null; currency: string }): void {
	capture('product_view', props);
}

/** Outbound click to a seller listing – the conversion event of the whole site. */
export function trackSellerClick(props: { product_id: string; seller: string; price: number | null; currency: string | null; availability: string | null; url: string }): void {
	let host: string | null = null;
	try { host = new URL(props.url).hostname; } catch { /* unparsable seller URL – still record the click */ }
	capture('seller_click', { ...props, seller_host: host });
}

export function trackDocumentClick(props: { product_id: string; kind: 'datasheet' | 'datasheet_search' | 'seller_document'; url: string }): void {
	capture('document_click', props);
}

export function trackLanguageSwitch(from: string, to: string): void {
	capture('language_switch', { from, to });
	setAnalyticsLanguage(to);
}

export function trackAiDiscoveryToggle(enabledNow: boolean): void {
	capture('ai_discovery_toggle', { enabled: enabledNow });
}

export function trackFiltersReset(scope: 'filters' | 'search_and_filters'): void {
	capture('filters_reset', { scope });
}

export function trackCatalogError(message: string): void {
	capture('catalog_load_error', { message });
}
