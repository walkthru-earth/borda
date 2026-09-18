import type { Product, Stats } from './parquet';
import { DEFAULT_PROFILE, type MarketProfile } from './profile.js';

export const SITE_ORIGIN = ((import.meta.env.VITE_SITE_ORIGIN as string | undefined) ?? 'https://walkthru.earth').replace(/\/$/, '');
export const safeJsonLd = (value: unknown): string => JSON.stringify(value).replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026');

/** Only observed, same-currency offers are eligible for structured comparison prices. */
export function productSchema(product: Product, stats: Stats | undefined, url: string, profile: MarketProfile = DEFAULT_PROFILE) {
 const priced = (stats?.current_offers ?? []).filter(o => o.currency === profile.currency && o.price != null && Number.isFinite(o.price) && o.price >= 0);
 return {
  '@context':'https://schema.org', '@type':'Product', name:product.canonical_name, url,
  ...(product.image && /^https?:\/\//i.test(product.image) ? {image:product.image} : {}),
  ...(product.brand ? {brand:{'@type':'Brand',name:product.brand}} : {}),
  ...(product.category ? {category:product.category} : {}),
  ...(priced.length ? {offers:{'@type':'AggregateOffer', priceCurrency:profile.currency, lowPrice:Math.min(...priced.map(o => o.price!)), highPrice:Math.max(...priced.map(o => o.price!)), offerCount:priced.length, url}} : {})
 };
}
