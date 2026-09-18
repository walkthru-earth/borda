import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
export default {
	preprocess: vitePreprocess(),
	kit: {
		// SPA build: product pages are dynamic (ids come from catalog.parquet), so we ship a
		// single fallback and read Parquet in the browser with hyparquet.
		adapter: adapter({ fallback: 'index.html', strict: false }),
		paths: { base: process.env.BASE_PATH ?? '' }
	}
};
