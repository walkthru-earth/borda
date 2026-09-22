export type SearchParamPatch = Record<string, string | number | boolean | null | undefined>;

/** Return a new URL with a normalized query-string patch applied. */
export function patchSearchParams(url: URL, patch: SearchParamPatch): URL {
	const next = new URL(url);
	for (const [key, value] of Object.entries(patch)) {
		if (value === null || value === undefined || value === '' || value === false) {
			next.searchParams.delete(key);
		} else {
			next.searchParams.set(key, value === true ? '1' : String(value));
		}
	}
	return next;
}

/** SvelteKit's goto accepts same-origin paths; keep origin details out of navigation calls. */
export function localUrl(url: URL): string {
	return `${url.pathname}${url.search}${url.hash}`;
}
