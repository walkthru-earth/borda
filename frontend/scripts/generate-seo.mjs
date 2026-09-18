/** Build crawlable HTML alongside the client-rendered Svelte app.
 * Google recommends initial-HTML Product structured data and prerendering:
 * https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics
 * https://developers.google.com/search/docs/appearance/structured-data/product-snippet
 * This is identical content for all visitors, not user-agent-based dynamic rendering.
 */
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parquetReadObjects } from 'hyparquet';
import { compressors } from 'hyparquet-compressors';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const BRAND = 'Borda | بوردة';
const HOMEPAGE_DESCRIPTION = 'Find electronic components across Egyptian stores. Compare recorded prices and availability, explore price history, and search in English or Arabic.';
const GROUPS = { 'dev-boards': 'Development boards', 'microcontrollers-ics': 'Microcontrollers & ICs', sensors: 'Sensors', 'wireless-iot': 'Wireless & IoT', 'displays-leds': 'Displays & LEDs', 'motors-drivers': 'Motors & drivers', power: 'Power', 'passive-components': 'Passive components', semiconductors: 'Semiconductors', 'connectors-cables': 'Connectors & cables', prototyping: 'Prototyping', 'tools-instruments': 'Tools & instruments', '3d-printing-cnc': '3D printing & CNC', 'robotics-kits': 'Robotics & kits', other: 'Other components' };
export const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
const escapeJson = (value) => JSON.stringify(value).replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026').replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
const readable = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
const category = (product) => GROUPS[product.group] ?? product.category ?? 'Electronic components';
const safeUrl = (value) => { try { const parsed = new URL(value); return /^https?:$/.test(parsed.protocol) ? parsed.href : null; } catch { return null; } };
const sellerName = (slug) => String(slug ?? '').replace(/-/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
const price = (value) => `${Number(value).toLocaleString('en-GB', { maximumFractionDigits: 2 })} EGP`;
const validDate = (value) => { if (value == null || value === '') return null; const date = new Date(value); return Number.isFinite(+date) ? date.toISOString() : null; };
const dateText = (value) => validDate(value)?.slice(0, 10) ?? 'Date unavailable';
const availabilityLabel = (value) => ({ in_stock: 'In stock', out_of_stock: 'Out of stock', preorder: 'Preorder', unknown: 'Stock unconfirmed' })[value] ?? 'Stock unconfirmed';
const availabilitySchema = { in_stock: 'https://schema.org/InStock', out_of_stock: 'https://schema.org/OutOfStock', preorder: 'https://schema.org/PreOrder' };

export function normalizeSiteUrl(value = 'https://walkthru.earth/borda') {
  const url = new URL(value);
  if (!/^https?:$/.test(url.protocol) || url.search || url.hash || url.username || url.password) throw new Error('SITE_URL must be an HTTP(S) URL without credentials, query or fragment');
  return url.href.replace(/\/+$/, '');
}

export function productUrl(siteUrl, id) {
  if (typeof id !== 'string' || !id || id === '.' || id === '..' || /[/\\\x00-\x1f]/.test(id)) throw new Error(`Unsafe product id: ${JSON.stringify(id)}`);
  return `${siteUrl}/product/${encodeURIComponent(id)}/`;
}

/** Never manufacture current offers from historical min/max or chart observations. */
export function currentOffers(stats) {
  let rows = stats?.current_offers;
  if (typeof rows === 'string') { try { rows = JSON.parse(rows); } catch { return []; } }
  if (!Array.isArray(rows)) return [];
  const listings = new Map();
  for (const row of rows) {
    if (!row || typeof row.seller !== 'string' || !safeUrl(row.url) || !validDate(row.ts)) continue;
    const key = JSON.stringify([row.seller, row.url]);
    const previous = listings.get(key);
    if (!previous || +new Date(row.ts) >= +new Date(previous.ts)) listings.set(key, row);
  }
  return [...listings.values()].filter((row) => row.currency === 'EGP' && typeof row.price === 'number' && Number.isFinite(row.price) && row.price >= 0);
}

export function productSchema(product, stats, siteUrl) {
  const url = productUrl(siteUrl, product.id);
  const schema = { '@context': 'https://schema.org', '@type': 'Product', '@id': `${url}#product`, name: product.canonical_name, url, category: category(product) };
  if (product.description) schema.description = readable(product.description);
  const image = safeUrl(product.image);
  if (image) schema.image = [image];
  if (product.brand) schema.brand = { '@type': 'Brand', name: product.brand };
  if (product.mpn) schema.mpn = product.mpn;
  const offers = currentOffers(stats);
  if (offers.length) {
    const prices = offers.map((offer) => offer.price);
    schema.offers = { '@type': 'AggregateOffer', priceCurrency: 'EGP', lowPrice: Math.min(...prices), highPrice: Math.max(...prices), offerCount: offers.length, offers: offers.map((offer) => ({ '@type': 'Offer', url: safeUrl(offer.url), price: offer.price, priceCurrency: 'EGP', seller: { '@type': 'Organization', name: sellerName(offer.seller) }, ...(availabilitySchema[offer.availability] ? { availability: availabilitySchema[offer.availability] } : {}) })) };
  }
  return schema;
}

/** Preserve Svelte's bootstrap/assets while making this pass safe to rerun. */
export function cleanShell(shell) {
  return shell
    .replace(/<!-- borda-static-seo:start -->[\s\S]*?<!-- borda-static-seo:end -->/g, '')
    .replace(/<!-- borda-static-content:start -->[\s\S]*?<!-- borda-static-content:end -->/g, '')
    .replace(/<title\b[^>]*>[\s\S]*?<\/title>/gi, '')
    .replace(/<meta\b(?=[^>]*(?:name\s*=\s*["'](?:description|robots|twitter:[^"']+)["']|property\s*=\s*["']og:[^"']+["']))[^>]*>/gi, '')
    .replace(/<link\b(?=[^>]*rel\s*=\s*["']canonical["'])[^>]*>/gi, '');
}

const fallbackStyle = `<style data-borda-static-seo>#borda-static-content{max-width:1080px;margin:32px auto;padding:28px;color:#172b3a;background:#fff;border:1px solid #e2e8eb;border-radius:18px;font:16px/1.7 system-ui,sans-serif}#borda-static-content h1{font-size:clamp(24px,4vw,38px);line-height:1.3}#borda-static-content a{color:#087f73}#borda-static-content img{max-width:260px;width:100%;height:220px;object-fit:contain}#borda-static-content table{border-collapse:collapse;width:100%;font-size:14px}#borda-static-content th,#borda-static-content td{text-align:start;padding:12px;border-bottom:1px solid #e2e8eb}#borda-static-content .table-wrap{overflow:auto}#borda-static-content .discovery{display:flex;flex-wrap:wrap;gap:8px 24px;list-style:none;padding:0}#borda-static-content .notice{color:#526a75;font-size:14px}@media(max-width:600px){#borda-static-content{margin:12px;padding:20px}}</style>`;
// The fallback is a sibling of the Svelte mount, so client startup never hydrates it.
// Remove it only after the app actually renders; a failed JS import leaves usable HTML.
const fallbackCleanup = `<script data-borda-static-cleanup>(()=>{const fallback=document.getElementById('borda-static-content');if(!fallback)return;const cleanup=()=>{if(document.getElementById('main-content')){fallback.remove();observer.disconnect();return true}return false};const observer=new MutationObserver(cleanup);if(!cleanup())observer.observe(document.body,{childList:true,subtree:true})})();</script>`;

function renderPage(shell, { title, description, canonical, image, schema, body }) {
  const tags = [
    `<title data-borda-static-seo>${escapeHtml(title)}</title>`,
    `<meta data-borda-static-seo name="description" content="${escapeHtml(description)}">`,
    `<link data-borda-static-seo rel="canonical" href="${escapeHtml(canonical)}">`,
    `<meta data-borda-static-seo name="robots" content="index,follow,max-image-preview:large">`,
    `<meta data-borda-static-seo property="og:site_name" content="${escapeHtml(BRAND)}">`,
    `<meta data-borda-static-seo property="og:type" content="website">`,
    `<meta data-borda-static-seo property="og:title" content="${escapeHtml(title)}">`,
    `<meta data-borda-static-seo property="og:description" content="${escapeHtml(description)}">`,
    `<meta data-borda-static-seo property="og:url" content="${escapeHtml(canonical)}">`,
    `<meta data-borda-static-seo property="og:image" content="${escapeHtml(image)}">`,
    `<meta data-borda-static-seo name="twitter:card" content="summary_large_image">`,
    `<meta data-borda-static-seo name="twitter:title" content="${escapeHtml(title)}">`,
    `<meta data-borda-static-seo name="twitter:description" content="${escapeHtml(description)}">`,
    `<meta data-borda-static-seo name="twitter:image" content="${escapeHtml(image)}">`,
    `<script data-borda-static-seo type="application/ld+json">${escapeJson(schema)}</script>`, fallbackStyle
  ].join('\n');
  return shell.replace('</head>', () => `<!-- borda-static-seo:start -->\n${tags}\n<!-- borda-static-seo:end -->\n</head>`)
    .replace(/(<body\b[^>]*>)/i, (_, bodyTag) => `${bodyTag}\n<!-- borda-static-content:start --><main id="borda-static-content">${body}</main>${fallbackCleanup}<!-- borda-static-content:end -->`);
}

export function renderProductPage(shell, product, stats, siteUrl, relatedProducts = []) {
  const url = productUrl(siteUrl, product.id);
  const name = readable(product.canonical_name);
  const description = readable(product.description) || `Compare ${name} prices and recorded availability across Egyptian electronics stores. Explore specifications and price history on Borda.`;
  const offers = currentOffers(stats);
  const image = safeUrl(product.image);
  const related = relatedProducts.filter((row) => row.id !== product.id).slice(0, 8);
  const body = `<nav aria-label="Breadcrumb"><a href="${escapeHtml(siteUrl)}/">Borda | بوردة</a> / ${escapeHtml(category(product))}</nav>
<h1>${escapeHtml(name)}</h1>${product.canonical_name_ar ? `<p lang="ar" dir="rtl">${escapeHtml(product.canonical_name_ar)}</p>` : ''}
${image ? `<img src="${escapeHtml(image)}" alt="${escapeHtml(name)}" loading="eager" width="260" height="220">` : ''}
<p>${escapeHtml(description)}</p>${product.description_ar ? `<p lang="ar" dir="rtl">${escapeHtml(product.description_ar)}</p>` : ''}
<p>Category: ${escapeHtml(category(product))}${product.brand ? ` · Brand: ${escapeHtml(product.brand)}` : ''}${product.mpn ? ` · Part number: ${escapeHtml(product.mpn)}` : ''}</p>
${product.specs?.length ? `<h2>Technical specifications</h2><ul>${product.specs.map((spec) => `<li>${escapeHtml(spec)}</li>`).join('')}</ul>` : ''}
<section><h2>Compare seller offers</h2><p class="notice">Prices and stock reflect the recorded dates below. Confirm details with the seller; shipping may cost extra.</p>
${offers.length ? `<div class="table-wrap"><table><thead><tr><th scope="col">Seller</th><th scope="col">Price</th><th scope="col">Recorded availability</th><th scope="col">Checked</th></tr></thead><tbody>${offers.map((offer) => `<tr><td><a href="${escapeHtml(safeUrl(offer.url))}" rel="nofollow noopener">${escapeHtml(sellerName(offer.seller))}</a></td><td>${escapeHtml(price(offer.price))}</td><td>${escapeHtml(availabilityLabel(offer.availability))}</td><td>${escapeHtml(dateText(offer.ts))}</td></tr>`).join('')}</tbody></table></div>` : '<p>No current priced offers are available in this catalog snapshot.</p>'}</section>
${related.length ? `<h2>Related components</h2><ul>${related.map((row) => `<li><a href="${escapeHtml(productUrl(siteUrl, row.id))}">${escapeHtml(row.canonical_name)}</a></li>`).join('')}</ul>` : ''}
<p class="notice">The interactive catalog adds search, filtering, Arabic translation and price-history charts when JavaScript is enabled.</p><p><a href="${escapeHtml(siteUrl)}/">Explore all components</a></p>`;
  return renderPage(shell, { title: `${name} — prices in Egypt · ${BRAND}`, description: description.slice(0, 170), canonical: url, image: image ?? `${siteUrl}/brand/borda-social.png`, schema: productSchema(product, stats, siteUrl), body });
}

export function renderHomePage(shell, products, manifest, siteUrl) {
  const groups = Object.entries(manifest.groups ?? {}).sort((a, b) => b[1] - a[1]);
  const popular = [...products].sort((a, b) => (b.sellers?.length ?? 0) - (a.sellers?.length ?? 0) || a.canonical_name.localeCompare(b.canonical_name)).slice(0, 20);
  const body = `<header><p>Borda | بوردة — Find. Compare. Build.</p><h1>Find electronic components in Egypt</h1><p lang="ar" dir="rtl">بوردة — ابحث عن المكونات الإلكترونية وقارن أسعار المتاجر المصرية.</p><p>${HOMEPAGE_DESCRIPTION}</p></header><p>Explore ${products.length.toLocaleString('en-GB')} components across ${groups.length} categories. Catalog updated ${escapeHtml(dateText(manifest.generated_at))}.</p><h2>Browse categories</h2><ul class="discovery">${groups.map(([group, count]) => `<li><a href="${escapeHtml(siteUrl)}/?group=${encodeURIComponent(group)}">${escapeHtml(GROUPS[group] ?? group)} (${Number(count).toLocaleString('en-GB')})</a></li>`).join('')}</ul><h2>Components from multiple stores</h2><ul>${popular.map((product) => `<li><a href="${escapeHtml(productUrl(siteUrl, product.id))}">${escapeHtml(product.canonical_name)}</a></li>`).join('')}</ul><p class="notice">Enable JavaScript to search, filter by price and seller, compare availability, and use the Arabic interface. Prices reflect recorded observations and exclude shipping.</p>`;
  return renderPage(shell, { title: `Electronic components & prices in Egypt · ${BRAND}`, description: HOMEPAGE_DESCRIPTION, canonical: `${siteUrl}/`, image: `${siteUrl}/brand/borda-social.png`, schema: { '@context': 'https://schema.org', '@type': 'WebSite', '@id': `${siteUrl}/#website`, name: BRAND, url: `${siteUrl}/`, description: HOMEPAGE_DESCRIPTION, inLanguage: ['en', 'ar'] }, body });
}

export function renderNotFoundPage(shell, siteUrl) {
  const head = `<title data-borda-static-seo>Page not found · ${escapeHtml(BRAND)}</title><meta data-borda-static-seo name="robots" content="noindex,follow">${fallbackStyle}`;
  const body = `<h1>Looking for a component?</h1><p>This address is not in the current static catalog. If the product was renamed or merged, the application will try to find its new address.</p><p><a href="${escapeHtml(siteUrl)}/">Search the Borda catalog</a></p>`;
  return shell.replace('</head>', () => `<!-- borda-static-seo:start -->${head}<!-- borda-static-seo:end --></head>`)
    .replace(/(<body\b[^>]*>)/i, (_, bodyTag) => `${bodyTag}<!-- borda-static-content:start --><main id="borda-static-content">${body}</main>${fallbackCleanup}<!-- borda-static-content:end -->`);
}

export function sitemapFiles(products, siteUrl, generatedAt, limit = 50000) {
  if (!Number.isInteger(limit) || limit < 1 || limit > 50000) throw new Error('Sitemap URL limit must be between 1 and 50000');
  const files = new Map();
  const lastmod = validDate(generatedAt);
  const entry = (url) => `<url><loc>${escapeHtml(url)}</loc>${lastmod ? `<lastmod>${lastmod}</lastmod>` : ''}</url>`;
  files.set('sitemap-pages.xml', `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${entry(`${siteUrl}/`)}</urlset>`);
  for (let start = 0; start < products.length; start += limit) {
    const filename = start === 0 ? 'sitemap-products.xml' : `sitemap-products-${Math.floor(start / limit) + 1}.xml`;
    files.set(filename, `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${products.slice(start, start + limit).map((product) => entry(productUrl(siteUrl, product.id))).join('')}</urlset>`);
  }
  files.set('sitemap.xml', `<?xml version="1.0" encoding="UTF-8"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${[...files.keys()].map((filename) => `<sitemap><loc>${escapeHtml(`${siteUrl}/${filename}`)}</loc>${lastmod ? `<lastmod>${lastmod}</lastmod>` : ''}</sitemap>`).join('')}</sitemapindex>`);
  return files;
}

async function parquetRows(path) {
  const buffer = await readFile(path);
  return parquetReadObjects({ file: buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength), compressors });
}

export async function generateSeo({ buildDir = join(ROOT, 'build'), dataDir = join(buildDir, 'data'), siteUrl = process.env.SITE_URL ?? 'https://walkthru.earth/borda' } = {}) {
  siteUrl = normalizeSiteUrl(siteUrl);
  const [html, products, statsRows, manifestText] = await Promise.all([readFile(join(buildDir, 'index.html'), 'utf8'), parquetRows(join(dataDir, 'catalog.parquet')), parquetRows(join(dataDir, 'stats.parquet')), readFile(join(dataDir, 'manifest.json'), 'utf8')]);
  const shell = cleanShell(html);
  if (!shell.includes('</head>') || !/<body\b/i.test(shell)) throw new Error('Build index.html is not a valid HTML shell');
  const manifest = JSON.parse(manifestText);
  const byId = new Map(products.map((product) => [product.id, product]));
  const stats = new Map(statsRows.map((row) => [row.product_id, row]));
  const seen = new Set();
  for (const product of products) {
    productUrl(siteUrl, product.id);
    if (seen.has(product.id)) throw new Error(`Duplicate product id: ${product.id}`);
    seen.add(product.id);
  }
  // Bound filesystem concurrency rather than opening 20,000 files together.
  let next = 0;
  await Promise.all(Array.from({ length: 16 }, async () => {
    while (next < products.length) {
      const product = products[next++];
      const folder = join(buildDir, 'product', product.id);
      const related = (product.similar ?? []).map((id) => byId.get(id)).filter(Boolean);
      await mkdir(folder, { recursive: true });
      await writeFile(join(folder, 'index.html'), renderProductPage(shell, product, stats.get(product.id), siteUrl, related));
    }
  }));
  await writeFile(join(buildDir, 'index.html'), renderHomePage(shell, products, manifest, siteUrl));
  await writeFile(join(buildDir, '404.html'), renderNotFoundPage(shell, siteUrl));
  for (const [filename, content] of sitemapFiles(products, siteUrl, manifest.generated_at)) await writeFile(join(buildDir, filename), content);
  await writeFile(join(buildDir, 'robots.txt'), `User-agent: *\nAllow: /\n\nSitemap: ${siteUrl}/sitemap.xml\n`);
  console.log(`Generated SEO HTML for ${products.length.toLocaleString('en-GB')} products, homepage, sitemaps and robots.txt (${siteUrl}).`);
  return { products: products.length, siteUrl };
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  generateSeo({ buildDir: resolve(process.argv[2] ?? join(ROOT, 'build')), ...(process.argv[3] ? { dataDir: resolve(process.argv[3]) } : {}) }).catch((error) => { console.error(`SEO generation failed: ${error.message}`); process.exitCode = 1; });
}
