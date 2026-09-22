export const CATALOG_SORTS = ['relevance', 'price-asc', 'price-desc', 'sellers', 'name'] as const;
export type CatalogSort = (typeof CATALOG_SORTS)[number];

export const DEFAULT_CATALOG_SORT: CatalogSort = 'relevance';
export const CATALOG_PAGE_SIZE = 24;
export const FEATURED_GROUPS = [
	'dev-boards',
	'sensors',
	'wireless-iot',
	'motors-drivers',
	'power',
	'tools-instruments'
] as const;
export const PRICE_PRESETS = [100, 500, 1000] as const;

export function parseCatalogSort(value: string | null): CatalogSort {
	return CATALOG_SORTS.includes(value as CatalogSort) ? (value as CatalogSort) : DEFAULT_CATALOG_SORT;
}

export function parsePositiveNumber(value: string | null): number {
	const parsed = Number(value);
	return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}
