// Interactive SPA backed by Parquet. The build adds crawlable static product HTML.
export const ssr = false;
export const prerender = false;

export const trailingSlash = 'always';

import { initAnalytics } from '$lib/analytics';
// PostHog starts here so the first `$pageview` fires before any component mounts
// (https://posthog.com/docs/libraries/svelte). No-op without PUBLIC_POSTHOG_KEY.
export const load = () => { initAnalytics(); };
