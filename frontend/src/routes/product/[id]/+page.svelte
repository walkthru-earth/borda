<script lang="ts">
	import { base } from '$app/paths';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { ArrowLeft, ArrowUpRight, Check, ChevronRight, FileText, Package, RefreshCw, Store, TrendingUp } from '@lucide/svelte';
	import PriceChart from '$lib/PriceChart.svelte';
	import { productOffers } from '$lib/offers';
	import { locale, tr, formatNumber, dateLocale, groupName } from '$lib/i18n.svelte';
	import ProductCard from '$lib/ProductCard.svelte';
	import { catalog, fmtPrice, sellerName } from '$lib/data.svelte';
	import { loadListings, loadProductDetail, loadSeries, resolveRedirect, type Listing, type Point, type ProductDetail } from '$lib/parquet';

	let id = $derived(page.params.id ?? '');
	let product = $derived(catalog.byId.get(id));
	let stats = $derived(catalog.stats.get(id));
	let productName = $derived(locale.language === 'ar' ? product?.canonical_name_ar || product?.canonical_name : product?.canonical_name);
	let detail = $state<ProductDetail | null>(null);
	let description = $derived(locale.language === 'ar' ? detail?.description_ar || detail?.description : detail?.description);
	let listings = $state<Listing[]>([]);
	let points = $state<Point[]>([]);
	let loadingSeries = $state(true);
	let loadingDetail = $state(true);
	let seriesError = $state(false);
	let detailError = $state(false);
	let listingsError = $state(false);
	let imageFailed = $state(false);
	let retry = $state(0);
	let resolving = $state(false);

	$effect(() => {
		const pid = id;
		let active = true;
		if (catalog.loading || !catalog.index || catalog.byId.has(pid)) return;
		resolving = true;
		void resolveRedirect(pid).then((target) => {
			if (active && target && target !== pid) void goto(`${base}/product/${encodeURIComponent(target)}`, { replaceState: true });
		}).catch(() => undefined).finally(() => { if (active) resolving = false; });
		return () => { active = false; };
	});

	$effect(() => {
		const pid = id;
		retry;
		let active = true;
		detail = null; points = []; listings = []; loadingSeries = true; loadingDetail = true;
		seriesError = false; detailError = false; listingsError = false; imageFailed = false;
		void loadProductDetail(pid).then((value) => { if (active) detail = value; })
			.catch(() => { if (active) detailError = true; }).finally(() => { if (active) loadingDetail = false; });
		void loadListings(pid, { strict: true }).then((value) => { if (active) listings = value; })
			.catch(() => { if (active) listingsError = true; });
		void loadSeries([pid], { strict: true }).then((value) => { if (active) points = value; })
			.catch(() => { if (active) seriesError = true; }).finally(() => { if (active) loadingSeries = false; });
		return () => { active = false; };
	});

	const validUrl = (url: string | null | undefined): string | null => {
		try { const parsed = new URL(url ?? ''); return ['https:', 'http:'].includes(parsed.protocol) ? parsed.href : null; } catch { return null; }
	};
	const date = (value: Date) => value.toLocaleDateString(dateLocale(), { day: 'numeric', month: 'short', year: 'numeric' });
	const availability = (value: string) => ({ in_stock: tr("In stock", "متوفر"), out_of_stock: tr("Out of stock", "غير متوفر"), preorder: tr("Preorder", "طلب مسبق"), unknown: tr("Stock unconfirmed", "التوفر غير مؤكد") }[value] ?? tr("Stock unconfirmed", "التوفر غير مؤكد"));
	let offerSummary = $derived(productOffers(stats, points));
	let latestOffers = $derived(offerSummary.offers);
	let currency = $derived(offerSummary.currency);
	let bestOffer = $derived(offerSummary.bestOffer);
	let fallbackPrice = $derived(offerSummary.lowestPrice);
	let stockSellers = $derived(offerSummary.stockSellers);
	let historicalOffers = $derived(offerSummary.source === 'history');
	let loadingOffers = $derived(historicalOffers && loadingSeries);
	let offersUnavailable = $derived(historicalOffers && seriesError);
	let unpriced = $derived(historicalOffers ? Object.entries(detail?.listings ?? {}).filter(([, url]) => !latestOffers.some((offer) => offer.url === url)) : []);
	let similar = $derived((product?.similar ?? []).map((key) => catalog.byId.get(key)).filter((p) => !!p).slice(0, 4));
	let aka = $derived([...new Set(product?.raw_names ?? [])].filter((name) => name.toLowerCase() !== product?.canonical_name.toLowerCase()));
	let specs = $derived((locale.language === 'ar' && detail?.specs_ar?.length ? detail.specs_ar : detail?.specs ?? []).map((spec) => { const i = spec.indexOf(':'); return i > 0 ? [spec.slice(0, i).trim(), spec.slice(i + 1).trim()] : [tr('Specification', 'المواصفة'), spec]; }));
	let docLinks = $derived([...new Set(listings.flatMap((listing) => listing.links ?? []))].filter((url) => validUrl(url) && url !== detail?.datasheet_url).slice(0, 8));
	let sellerTexts = $derived(listings.filter((listing) => listing.description && listing.description.length > 40).sort((a, b) => (b.description?.length ?? 0) - (a.description?.length ?? 0)));
	let searchTerm = $derived(detail?.mpn ?? product?.canonical_name ?? '');
	const linkLabel = (url: string) => { try { const parsed = new URL(url); return parsed.hostname.replace(/^www\./, '') + (parsed.pathname.toLowerCase().endsWith('.pdf') ? ' · PDF' : tr(' · Reference', ' · مرجع')); } catch { return url; } };
</script>

<svelte:head><title>{productName ?? tr('Product', 'منتج')} · Borda | بوردة</title><meta name="description" content={tr(`Compare seller prices, availability and price history for ${productName ?? 'electronics components'} in Egypt.`, `قارن أسعار البائعين والتوفر وسجل أسعار ${productName ?? 'المكونات الإلكترونية'} في مصر.`)} /></svelte:head>

<nav class="crumbs" aria-label={tr('Breadcrumb', 'مسار التنقل')}><a href="{base}/"><ArrowLeft size={15} /> {tr("All components", "كل المكونات")}</a>{#if product}<ChevronRight size={14} /><a href="{base}/?group={encodeURIComponent(product.group ?? 'other')}">{groupName(product.group)}</a>{/if}</nav>

{#if catalog.loading || resolving}
	<div class="skeleton" style="height: 350px" aria-label={tr('Loading product', 'جارٍ تحميل المنتج')}></div>
{:else if catalog.error}
	<div class="empty panel"><Package size={32} /><h1>{tr("We couldn't load the catalog", "تعذر تحميل الكتالوج")}</h1><p>{tr("Please check your connection and try again.", "تحقق من اتصالك بالإنترنت وحاول مرة أخرى.")}</p><button class="btn" onclick={() => void catalog.ensure()}><RefreshCw size={16} /> {tr("Try again", "حاول مرة أخرى")}</button></div>
{:else if !product}
	<div class="empty panel"><Package size={32} /><h1>{tr("Product not found", "لم يتم العثور على المنتج")}</h1><p>{tr("This component may have moved or been merged with another listing.", "ربما نُقل هذا المكون أو دُمج مع منتج آخر.")}</p><a class="btn primary" href="{base}/?q={encodeURIComponent(id.split('-').slice(0, 2).join(' '))}">{tr("Search the catalog", "ابحث في الكتالوج")}</a></div>
{:else}
	<article class="hero panel">
		<div class="media">{#if validUrl(product.image) && !imageFailed}<img src={product.image!} alt={productName} onerror={() => imageFailed = true} />{:else}<Package size={70} strokeWidth={1} />{/if}<span class="category">{groupName(product.group)}</span></div>
		<div class="info"><div class="eyebrow">{product.brand ?? tr("Component spotlight", "تعرف على المكون")}</div><h1 dir="auto">{productName}</h1>{#if locale.language === 'ar' && product.canonical_name_ar}<p class="original-name" dir="ltr">{product.canonical_name}</p>{/if}
			{#if description}<p class="desc" dir="auto">{description}</p>{:else if loadingDetail}<div class="skeleton" style="height: 3em"></div>{:else}<p class="desc">{tr("Compare this component across Egyptian electronics stores and explore its recorded price history.", "قارن هذا المكون بين متاجر الإلكترونيات المصرية وتابع سجل أسعاره.")}</p>{/if}
			<div class="product-meta"><span><Store size={15} /> {formatNumber(product.sellers.length)} {product.sellers.length === 1 ? tr("seller", "بائع") : tr("sellers", "بائعين")}</span>{#if detail?.mpn}<span>{tr("Part no.", "رقم القطعة")} <strong dir="ltr">{detail.mpn}</strong></span>{/if}</div>
			<div class="tags">{#each [...new Set(product.tags)] as tag (tag)}<a class="product-tag" href="{base}/?tag={encodeURIComponent(tag)}">{tag}</a>{/each}</div>
			<div class="docs">{#if validUrl(detail?.datasheet_url)}<a class="doc-link" href={detail!.datasheet_url!} target="_blank" rel="noopener noreferrer nofollow"><FileText size={16} /> {tr("View datasheet", "ورقة البيانات")} <ArrowUpRight size={14} /></a>{:else if searchTerm}<a class="doc-link" href="https://www.alldatasheet.com/view.jsp?Searchword={encodeURIComponent(searchTerm)}" target="_blank" rel="noopener noreferrer nofollow"><FileText size={16} /> {tr("Find datasheet", "ابحث عن ورقة البيانات")} <ArrowUpRight size={14} /></a>{/if}</div>
		</div>
		<aside class="price-summary"><div class="eyebrow">{historicalOffers ? (bestOffer ? tr("Lowest recorded in-stock price", "أقل سعر مسجل عند التوفر") : tr("Lowest recorded price", "أقل سعر مسجل")) : bestOffer ? tr("Lowest in-stock price", "أقل سعر متوفر") : tr("Latest listed price", "آخر سعر مدرج")}</div>
			{#if loadingOffers}<div class="skeleton" style="height: 40px"></div>{:else}<strong class="headline-price">{fmtPrice(bestOffer?.price ?? fallbackPrice, currency)}</strong>{/if}
			{#if !loadingOffers && !offersUnavailable}<span class="stock-note" class:available={stockSellers > 0}>{#if stockSellers}<Check size={14} /> {historicalOffers ? tr("Previously in stock at", "كان متوفراً لدى") : tr("Reported in stock at", "متوفر حسب آخر رصد لدى")} {formatNumber(stockSellers)} {stockSellers === 1 ? tr("store", "متجر") : tr("stores", "متاجر")}{:else}{historicalOffers ? tr("No recorded in-stock offers", "لا توجد عروض متوفرة مسجلة") : tr("No confirmed in-stock offers", "لا توجد عروض مؤكدة التوفر")}{/if}</span>{/if}
			<a class="compare-button" href="#offers">{tr("Compare seller offers", "قارن عروض البائعين")} <ArrowUpRight size={17} /></a><p>{tr(`Prices in ${currency}. Shipping may cost extra. Confirm price and stock with the seller.`, `الأسعار بعملة ${currency === 'EGP' ? 'الجنيه المصري' : currency}. قد تُضاف رسوم شحن. تأكد من السعر والتوفر مع البائع.`)}</p>
			{#if bestOffer}<div class="last-seen">{historicalOffers ? tr("Historical offer recorded", "تاريخ رصد العرض") : tr("Best offer checked", "آخر تحقق من أفضل عرض")} {date(bestOffer.ts)}</div>{/if}
		</aside>
	</article>

	{#if detailError || seriesError || listingsError}<div class="notice" role="status"><span>{tr("Some product information couldn't load. You can still explore the available details.", "تعذر تحميل بعض معلومات المنتج. يمكنك استعراض التفاصيل المتاحة.")}</span><button class="btn" onclick={() => retry += 1}><RefreshCw size={15} /> {tr("Retry", "إعادة المحاولة")}</button></div>{/if}

	<div class="detail-columns">
		<section id="offers" class="panel section-panel"><div class="section-heading"><div><div class="eyebrow">{tr("Find your next part", "اختر مكونك القادم")}</div><h2>{tr("Compare seller offers", "قارن عروض البائعين")}</h2></div><Store size={22} /></div><p class="section-note">{historicalOffers ? tr("Historical observations; these listings may no longer be available. Stock reflects the date shown.", "بيانات سابقة؛ قد لا تكون هذه العروض متاحة الآن. حالة التوفر حسب التاريخ الموضح.") : tr("Offers from each seller’s latest catalog snapshot. Availability reflects the date shown.", "عروض من أحدث رصد لكتالوج كل بائع. حالة التوفر حسب التاريخ الموضح.")}</p>
			{#if loadingOffers}<div class="skeleton" style="height: 180px"></div>{:else if latestOffers.length || unpriced.length}<ul class="sellers">
				{#each latestOffers as offer (offer.seller + offer.url)}<li class="seller-row" class:best={offer === bestOffer}><div class="seller-identity"><strong>{sellerName(offer.seller)}</strong><span>{tr("Checked", "آخر تحقق")} {date(offer.ts)}</span></div><div class="offer-info"><strong>{fmtPrice(offer.price, offer.currency)}</strong><span class="availability" class:available={offer.availability === 'in_stock'}>{availability(offer.availability)}</span></div>{#if validUrl(offer.url)}<a class="visit" href={offer.url} target="_blank" rel="noopener noreferrer nofollow" aria-label={tr(`Visit ${sellerName(offer.seller)} for ${productName}`, `زيارة ${sellerName(offer.seller)} لشراء ${productName}`)}>{tr("Visit store", "زيارة المتجر")} <ArrowUpRight size={16} /></a>{/if}{#if offer === bestOffer}<span class="best-label">{historicalOffers ? tr("Lowest recorded in-stock price", "أقل سعر مسجل عند التوفر") : tr("Best in-stock price", "أفضل سعر متوفر")} · {currency}</span>{/if}</li>{/each}
				{#each unpriced as [key, url] (key)}<li class="seller-row"><div class="seller-identity"><strong>{sellerName(key.split(':')[0])}</strong><span>{tr("Unverified seller listing", "رابط بائع غير مؤكد")}</span></div><div class="offer-info"><span class="availability">{tr("Check with seller", "تحقق مع البائع")}</span></div>{#if validUrl(url)}<a class="visit" href={url} target="_blank" rel="noopener noreferrer nofollow">{tr("Visit store", "زيارة المتجر")} <ArrowUpRight size={16} /></a>{/if}</li>{/each}
			</ul>{:else}<div class="quiet-empty">{offersUnavailable ? tr("Seller prices are temporarily unavailable.", "أسعار البائعين غير متاحة مؤقتاً.") : historicalOffers ? tr("No seller prices have been recorded yet.", "لم تُسجل أسعار البائعين بعد.") : tr("No offers in the latest seller snapshots.", "لا توجد عروض في أحدث بيانات البائعين.")}</div>{/if}
		</section>
		<section class="panel section-panel"><div class="section-heading"><div><div class="eyebrow">{tr("Make an informed choice", "اختر بناءً على المعلومات")}</div><h2>{tr("Price history", "سجل الأسعار")}</h2></div><TrendingUp size={22} /></div><p class="section-note">{tr("Recorded prices over time, before shipping.", "الأسعار المسجلة مع مرور الوقت، دون الشحن.")}</p>{#if loadingSeries}<div class="skeleton" style="height: 240px"></div>{:else if seriesError}<div class="quiet-empty">{tr("Price history couldn't load. Try again above.", "تعذر تحميل سجل الأسعار. أعد المحاولة من الزر أعلاه.")}</div>{:else}<PriceChart {points} {stats} />{/if}</section>
	</div>
	{#if specs.length}<section class="panel section-panel specs-panel"><div class="section-heading"><div><div class="eyebrow">{tr("The details", "التفاصيل")}</div><h2>{tr("Technical specifications", "المواصفات الفنية")}</h2></div></div><dl class="specs">{#each specs as [key, value], index (index)}<div><dt dir="auto">{key}</dt><dd dir="auto">{value}</dd></div>{/each}</dl></section>{/if}
	{#if docLinks.length || sellerTexts.length}<section class="panel section-panel"><div class="section-heading"><div><div class="eyebrow">{tr("Dig a little deeper", "مزيد من المعلومات")}</div><h2>{tr("Seller notes & documents", "ملاحظات البائعين والمستندات")}</h2></div></div>{#if docLinks.length}<ul class="links">{#each docLinks as url (url)}<li><a class="doc-link" href={url} target="_blank" rel="noopener noreferrer nofollow"><FileText size={15} />{linkLabel(url)}<ArrowUpRight size={14} /></a></li>{/each}</ul>{/if}{#each sellerTexts.slice(0, 4) as listing (listing.listing_key)}<details class="seller-text"><summary><strong>{sellerName(listing.seller)}</strong><span dir="auto">{listing.raw_name}</span></summary><p dir="auto">{listing.description}</p></details>{/each}</section>{/if}
	{#if aka.length}<section class="panel section-panel"><h2>{tr("Also listed as", "أسماء أخرى للمنتج")}</h2><p class="section-note">{tr("Names used by local sellers for this component.", "أسماء يستخدمها البائعون المحليون لهذا المكون.")}</p><div class="tags">{#each aka as name (name)}<span class="product-tag" dir="auto">{name}</span>{/each}</div></section>{/if}
	{#if similar.length}<section class="related"><div class="section-heading"><div><div class="eyebrow">{tr("Keep exploring", "واصل الاستكشاف")}</div><h2>{tr("Related components", "مكونات ذات صلة")}</h2></div><a class="doc-link" href="{base}/?group={encodeURIComponent(product.group ?? 'other')}">{tr("Browse category", "تصفح الفئة")} <ArrowUpRight size={16} /></a></div><div class="grid">{#each similar as related (related.id)}<ProductCard product={related} />{/each}</div></section>{/if}
{/if}

<style>
	.crumbs { display:flex; align-items:center; flex-wrap:wrap; gap:10px; font-size:12px; color:var(--muted); margin:8px 0 24px; }.crumbs a{display:flex;align-items:center;gap:7px}.crumbs a:hover,.doc-link:hover{color:var(--accent)}
	.panel{background:var(--surface,#fff);border:1px solid var(--line);border-radius:18px}.hero{display:grid;grid-template-columns:minmax(190px, .85fr) minmax(260px,1.5fr) minmax(220px,.9fr);gap:30px;padding:30px;margin-bottom:24px;align-items:start}.media{position:relative;aspect-ratio:1;display:grid;place-items:center;background:#f8fafb;border-radius:13px;color:#a4b5ba;overflow:hidden}.media img{width:100%;height:100%;object-fit:contain;mix-blend-mode:multiply;padding:24px 24px 45px}.category{position:absolute;bottom:14px;inset-inline-start:14px;background:white;border:1px solid var(--line);padding:5px 10px;border-radius:6px;font-size:10px;color:var(--muted)}
	.eyebrow{text-transform:uppercase;letter-spacing:.12em;font-size:10px;font-weight:700;color:var(--accent);margin-bottom:8px}.info h1{font-size:clamp(23px,2.1vw,31px);letter-spacing:-.035em;line-height:1.22;margin:0 0 14px;overflow-wrap:anywhere}.desc{font-size:13px;line-height:1.8;color:var(--muted);margin:0 0 18px}.product-meta{display:flex;flex-wrap:wrap;gap:12px;font-size:11px;color:var(--muted);margin-bottom:16px}.product-meta span{display:flex;align-items:center;gap:6px}.tags{display:flex;flex-wrap:wrap;gap:6px}.product-tag{background:#f4f7f8;color:#577078;border:1px solid #e8edef;padding:5px 9px;border-radius:6px;font-size:11px;overflow-wrap:anywhere}a.product-tag:hover{border-color:var(--accent);color:var(--accent)}.docs{margin-top:20px}.doc-link{display:inline-flex;align-items:center;gap:7px;font-size:12px;font-weight:600;color:var(--accent)}
	.price-summary{background:#f1f8f6;padding:22px;border:1px solid #dcece7;border-radius:13px;display:flex;flex-direction:column;gap:9px}.price-summary .eyebrow{color:#55766e;margin:0}.headline-price{font-size:30px;letter-spacing:-1px;line-height:1.25;font-variant-numeric:tabular-nums}.stock-note{display:flex;gap:5px;align-items:center;color:var(--muted);font-size:11px;line-height:1.5}.available{color:#087f63!important}.compare-button{display:flex;align-items:center;justify-content:center;gap:10px;margin:10px 0 1px;background:#087f73;color:white;padding:12px 10px;border-radius:8px;font-size:12px;font-weight:650}.compare-button:hover{background:#06685e}.price-summary p{font-size:10px;color:#647c75;line-height:1.7;margin:0}.last-seen{padding-top:12px;border-top:1px solid #dcece7;font-size:10px;color:#647c75}
	.detail-columns{display:grid;grid-template-columns:1.05fr 1fr;gap:24px;align-items:start}.section-panel{padding:26px;margin-bottom:24px;min-width:0;scroll-margin-top:24px}.section-heading{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:9px}.section-heading> :global(svg){color:#8aa49e}h2{font-size:19px;letter-spacing:-.025em;margin:0}.section-note{color:var(--muted);font-size:11px;line-height:1.6;margin:0 0 21px}.sellers{list-style:none;padding:0;margin:0;display:grid;gap:10px}.seller-row{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:13px;align-items:center;padding:17px 13px;border:1px solid var(--line);border-radius:10px}.seller-row.best{border-color:#a8d5c9;background:#fbfefd}.seller-identity,.offer-info{display:flex;flex-direction:column;gap:6px}.seller-identity strong{font-size:12px}.seller-identity span{font-size:10px;color:var(--muted)}.offer-info{text-align:end}.offer-info strong{font-size:13px;white-space:nowrap}.availability{font-size:10px;color:var(--muted)}.visit{display:flex;align-items:center;gap:3px;font-size:10px;font-weight:600;color:var(--accent);padding:7px;border-radius:6px;background:#edf6f3;white-space:nowrap}.visit:hover{background:#dcefe9}.best-label{grid-column:1/-1;font-size:9px;font-weight:600;color:#087f63;border-top:1px solid #e1f0eb;padding-top:10px}.quiet-empty{padding:45px 15px;text-align:center;background:#f8fafb;border-radius:10px;font-size:13px;color:var(--muted)}
	.specs{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));margin:20px 0 0;gap:0 30px}.specs div{padding:13px 0;border-top:1px solid var(--line)}.specs dt{font-size:11px;color:var(--muted);margin-bottom:5px}.specs dd{font-size:13px;font-weight:600;margin:0;overflow-wrap:anywhere}.links{list-style:none;display:flex;flex-wrap:wrap;gap:12px 24px;padding:10px 0;margin:0 0 14px}.seller-text{border-top:1px solid var(--line);padding:16px 0}.seller-text summary{cursor:pointer;font-size:12px}.seller-text summary span{display:block;margin:7px 0 0;margin-inline-start:16px;color:var(--muted);font-size:11px}.seller-text p{white-space:pre-line;overflow-wrap:anywhere;font-size:12px;line-height:1.9;color:var(--muted);margin:16px 0 0}.related{margin:14px 0 35px}.related .section-heading{margin-bottom:20px}.notice{display:flex;align-items:center;justify-content:space-between;gap:16px;border:1px solid #e7d9b1;background:#fffbef;border-radius:10px;padding:15px 18px;margin-bottom:24px;font-size:12px}.empty{padding:60px 20px;text-align:center;display:flex;align-items:center;flex-direction:column;gap:15px}.empty h1{font-size:24px;margin:0}.empty p{color:var(--muted);margin:0 0 5px}a:focus-visible,button:focus-visible,summary:focus-visible{outline:3px solid #74c5b7;outline-offset:4px}
	@media(max-width:1100px){.hero{grid-template-columns:180px 1fr;gap:24px}.price-summary{grid-column:1/-1;display:grid;grid-template-columns:1fr 1fr;align-items:center}.price-summary .eyebrow,.headline-price,.stock-note{grid-column:1}.compare-button{grid-column:2;grid-row:1/3}.price-summary p{grid-column:2;grid-row:3/5}.last-seen{border:0;padding-top:4px}.detail-columns{grid-template-columns:1fr}.seller-row{grid-template-columns:minmax(0,1fr) auto 100px}.visit{justify-content:center}}
	@media(max-width:600px){.crumbs{font-size:11px;margin-bottom:18px}.hero{grid-template-columns:1fr;padding:19px;gap:22px}.media{max-height:270px;aspect-ratio:1.25}.price-summary{display:flex;grid-column:auto;padding:19px}.info h1{font-size:25px}.section-panel{padding:19px;margin-bottom:18px}.detail-columns{gap:0}.seller-row{grid-template-columns:minmax(0,1fr) auto;gap:12px;padding:14px}.visit{grid-column:1/-1;justify-content:center;padding:9px}.specs{grid-template-columns:1fr 1fr;gap:0 18px}.related .section-heading{align-items:start}.related .doc-link{font-size:10px}.notice{align-items:start;flex-direction:column}.headline-price{font-size:31px}}
	.original-name{font-size:12px;color:var(--muted);margin:-6px 0 16px;text-align:start}.product-meta strong{unicode-bidi:isolate}.hero,.section-panel{min-width:0}:global([dir="rtl"]) .eyebrow,:global([dir="rtl"]) .info h1,:global([dir="rtl"]) h2,:global([dir="rtl"]) .headline-price{letter-spacing:0}:global([dir="rtl"]) .crumbs :global(svg){transform:scaleX(-1)}:global([dir="rtl"]) .eyebrow{font-size:11px}:global([dir="rtl"]) .info h1{line-height:1.55}:global([dir="rtl"]) .section-note{line-height:1.9}:global([dir="rtl"]) .seller-row{gap:10px}:global([dir="rtl"]) .visit{white-space:normal;text-align:center}
</style>
