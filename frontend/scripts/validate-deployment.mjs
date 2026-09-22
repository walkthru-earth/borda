const basePath = process.env.BASE_PATH ?? '';
const siteUrl = process.env.SITE_URL ?? 'https://walkthru.earth/borda';
const siteOrigin = process.env.VITE_SITE_ORIGIN ?? 'https://walkthru.earth';

const normalizedBase = basePath && basePath !== '/' ? `/${basePath.replace(/^\/+|\/+$/g, '')}` : '';
const parsedSite = new URL(siteUrl);
const parsedOrigin = new URL(siteOrigin);
const normalizedSitePath = parsedSite.pathname === '/' ? '' : parsedSite.pathname.replace(/\/$/, '');

if (parsedOrigin.pathname !== '/' || parsedOrigin.search || parsedOrigin.hash) {
	throw new Error(`VITE_SITE_ORIGIN must contain only an origin, received ${siteOrigin}`);
}
if (parsedSite.origin !== parsedOrigin.origin) {
	throw new Error(`SITE_URL origin (${parsedSite.origin}) must match VITE_SITE_ORIGIN (${parsedOrigin.origin})`);
}
if (normalizedSitePath !== normalizedBase) {
	throw new Error(`SITE_URL path (${normalizedSitePath || '/'}) must match BASE_PATH (${normalizedBase || '/'})`);
}

console.log(`Validated deployment URL ${parsedSite.origin}${normalizedBase || '/'} (${normalizedBase || 'root'} base)`);
