import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
export default {
	preprocess: vitePreprocess(),
	kit: {
		// SPA build: product pages are dynamic (ids come from catalog.parquet), so we ship a
		// single fallback and read Parquet in the browser with hyparquet.
		adapter: adapter({ fallback: 'index.html', strict: true }),
		// Absolute asset URLs so PostHog session replays can resolve stylesheets/images
		// (https://posthog.com/docs/libraries/svelte); base-aware, so /borda/_app/… in production.
		paths: { base: process.env.BASE_PATH ?? '', relative: false },
		// One .env at the repository root serves the Python pipeline and the frontend; only
		// PUBLIC_* variables are ever exposed to the browser bundle.
		env: { dir: '..' }
	}
};
