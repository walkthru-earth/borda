<script lang="ts">
 import { onMount } from 'svelte';
 import { page } from '$app/state';
 import { goto } from '$app/navigation';
 import { Search, SlidersHorizontal, X, Sparkles, ArrowRight, Check, Store, Layers, ArrowUpDown, ChevronDown, RotateCcw, CircuitBoard, Cpu, Zap, Radio, PackageSearch, LoaderCircle, Info } from '@lucide/svelte';
 import ProductCard from '$lib/ProductCard.svelte';
 import CategoryIcon from '$lib/CategoryIcon.svelte';
 import PriceRangeFilter from '$lib/PriceRangeFilter.svelte';
 import { locale, tr, formatNumber, market, currencyLabel, groupName as groupLabel } from '$lib/i18n.svelte';
 import { catalog, sellerName } from '$lib/data.svelte';
 import { search, productMatchesFilters, matchingPrice } from '$lib/search';
 import { fuse, semanticSearch, type Hit, type Progress } from '$lib/semantic';
 import type { Product } from '$lib/parquet';
 import { trackCatalogView, trackAiDiscoveryToggle, trackFiltersReset } from '$lib/analytics';
 let q = $derived(page.url.searchParams.get('q') ?? '');
 let group = $derived(page.url.searchParams.get('group') ?? '');
 let tag = $derived(page.url.searchParams.get('tag') ?? '');
 let seller = $derived(page.url.searchParams.get('seller') ?? '');
 let stock = $derived(page.url.searchParams.get('stock') === '1');
 const sorts = ['relevance', 'price-asc', 'price-desc', 'sellers', 'name'];
 let sort = $derived(sorts.includes(page.url.searchParams.get('sort') ?? '') ? page.url.searchParams.get('sort')! : 'relevance');
 function priceParam(key: string) { const n = Number(page.url.searchParams.get(key)); return Number.isFinite(n) && n > 0 ? n : 0; }
 let minPrice = $derived(priceParam('min'));
 let maxPrice = $derived(priceParam('max'));
 let semantic = $derived(page.url.searchParams.get('ai') === '1');
 let filtersOpen = $state(false);
 let categoryExpanded = $state(false);
 function setParams(patch: Record<string, string | null>) {
  const u = new URL(page.url);
  for (const [k,v] of Object.entries(patch)) v ? u.searchParams.set(k,v) : u.searchParams.delete(k);
  void goto(`${u.pathname}${u.search}`, { replaceState:true, keepFocus:true, noScroll:true });
 }
 const applyPrice = (min: number, max: number) => setParams({ min:min > 0 ? String(min) : null, max:max > 0 ? String(max) : null });
 function resetFilters() { trackFiltersReset('filters'); setParams({ group:null, tag:null, seller:null, stock:null, min:null, max:null }); }
 let semHits = $state<Hit[]>([]);
 let semBusy = $state(false);
 let semProgress = $state<Progress | null>(null);
 let semError = $state<string | null>(null);
 $effect(() => {
  const query = q, enabled = semantic;
  semHits = []; semBusy = false; semProgress = null; semError = null;
  if (!enabled || query.trim().length < 2) return;
  let cancelled = false; semBusy = true;
  const t = setTimeout(() => {
   semanticSearch(query,120,(p) => { if (!cancelled) semProgress = p; })
    .then(h => { if (!cancelled) semHits = h; })
    .catch(e => { if (!cancelled) semError = e instanceof Error ? e.message : String(e); })
    .finally(() => { if (!cancelled) { semBusy = false; semProgress = null; } });
  },300);
  return () => { cancelled = true; clearTimeout(t); };
 });
 let offerFilters = $derived({ currency:market.profile.currency, seller, inStock:stock, minPrice, maxPrice });
 let queryResults = $derived.by((): Product[] => {
  if (!catalog.index) return [];
  let list = search(catalog.index,q,Infinity);
  if (semantic && semHits.length) list = fuse(list.map(p => p.id),semHits).map(id => catalog.byId.get(id)).filter((p): p is Product => !!p);
  return list;
 });
 let categoryCounts = $derived.by(() => {
  const m = new Map<string,number>();
  for (const p of queryResults.filter(p => productMatchesFilters(p,catalog.stats.get(p.id),offerFilters))) { const g = p.group ?? 'other'; m.set(g,(m.get(g) ?? 0)+1); }
  return m;
 });
 let categoryOptions = $derived(catalog.groups.filter(([g]) => (categoryCounts.get(g) ?? 0) > 0 || g === group).sort(([a],[b]) => (q || seller || stock || minPrice || maxPrice) ? (categoryCounts.get(b) ?? 0)-(categoryCounts.get(a) ?? 0) : (featuredGroups.indexOf(a) < 0 ? 99 : featuredGroups.indexOf(a))-(featuredGroups.indexOf(b) < 0 ? 99 : featuredGroups.indexOf(b))));
 let results = $derived.by(() => {
  let list = queryResults.filter(p => (!group || (p.group ?? 'other') === group) && (!tag || p.tags.includes(tag)) && productMatchesFilters(p,catalog.stats.get(p.id),offerFilters));
  const price = (p: Product) => matchingPrice(catalog.stats.get(p.id),offerFilters);
  if (sort === 'price-asc' || sort === 'price-desc') list = [...list].sort((a,b) => {
   const ap = price(a), bp = price(b);
   if (ap == null || !Number.isFinite(ap)) return bp == null || !Number.isFinite(bp) ? 0 : 1;
   if (bp == null || !Number.isFinite(bp)) return -1;
   return sort === 'price-asc' ? ap-bp : bp-ap;
  });
  else if (sort === 'sellers') list = [...list].sort((a,b) => b.sellers.length-a.sellers.length);
  else if (sort === 'name') list = [...list].sort((a,b) => a.canonical_name.localeCompare(b.canonical_name));
  else if (!q) list = [...list].sort((a,b) => Number((catalog.stats.get(b.id)?.in_stock_sellers ?? 0)>0)-Number((catalog.stats.get(a.id)?.in_stock_sellers ?? 0)>0) || b.sellers.length-a.sellers.length || Number(!!b.image)-Number(!!a.image) || a.canonical_name.localeCompare(b.canonical_name));
  return list;
 });
 let limit = $state(24);
 $effect(() => { void [q,group,tag,seller,stock,sort,minPrice,maxPrice,semantic]; limit = 24; });
 // One `catalog_view` per settled state (typing and filter clicks are debounced), with the
 // result count – this is the search/filter leg of the journey funnel.
 $effect(() => {
  const snapshot = { query:q.trim(), group, tag, seller, in_stock_only:stock, sort, min_price:minPrice, max_price:maxPrice, ai_discovery:semantic, results:results.length };
  if (catalog.loading || !catalog.index || semBusy) return;
  const t = setTimeout(() => trackCatalogView(snapshot), 900);
  return () => clearTimeout(t);
 });
 let shown = $derived(results.slice(0,limit));
 let loadMoreSentinel = $state<HTMLDivElement | undefined>();
 let loadObserver = $state<IntersectionObserver | null>(null);
 let loadPending = $state(false);
 onMount(() => {
  const observer = new IntersectionObserver((entries) => {
   if (!entries.some((entry) => entry.isIntersecting) || loadPending || shown.length >= results.length) return;
   loadPending = true;
   limit += 24;
   window.setTimeout(() => {
    loadPending = false;
    if (loadMoreSentinel) {
     observer.unobserve(loadMoreSentinel);
     observer.observe(loadMoreSentinel);
    }
   }, 250);
  }, { rootMargin: '480px 0px' });
  loadObserver = observer;
  return () => observer.disconnect();
 });
 $effect(() => {
  const observer = loadObserver;
  const sentinel = loadMoreSentinel;
  if (!observer || !sentinel) return;
  observer.observe(sentinel);
  return () => observer.unobserve(sentinel);
 });
 let activeFilters = $derived([group,tag,seller,stock,minPrice,maxPrice].filter(Boolean).length);
 // Price distribution for the range filter: everything the other filters allow, at the price
 // the same seller/stock conditions would match (so the histogram shows what the slider selects).
 let priceDistribution = $derived.by(() => {
  const filters = { currency:market.profile.currency, seller, inStock:stock };
  const prices: number[] = [];
  for (const p of queryResults) {
   if ((group && (p.group ?? 'other') !== group) || (tag && !p.tags.includes(tag))) continue;
   const stats = catalog.stats.get(p.id);
   if (!productMatchesFilters(p,stats,filters)) continue;
   const price = matchingPrice(stats,filters);
   if (price != null && Number.isFinite(price) && price > 0) prices.push(price);
  }
  return prices;
 });
 let tagsForGroup = $derived.by(() => {
  const m = new Map<string,number>();
  for (const p of queryResults.filter(p => productMatchesFilters(p,catalog.stats.get(p.id),offerFilters))) if (!group || (p.group ?? 'other') === group) for (const t of p.tags) m.set(t,(m.get(t) ?? 0)+1);
  return [...m.entries()].sort((a,b) => b[1]-a[1]).slice(0,8);
 });
 const featuredGroups = ['dev-boards','sensors','wireless-iot','motors-drivers','power','tools-instruments'];
</script>

<svelte:head><title>{q ? `${q} · ` : ''}{tr(`Borda — Electronic components in ${market.profile.country_name}`,`بوردة — مكونات الإلكترونيات في ${market.profile.country_name_ar}`)}</title></svelte:head>

<section class="discovery">
 <div class="hero-copy"><div class="eyebrow"><span class="dot"></span> {tr(`THE MAKER’S COMPANION IN ${market.profile.country_name.toUpperCase()}`,`رفيقك في عالم الإلكترونيات في ${market.profile.country_name_ar}`)}</div><h1>{tr('Less searching.','تدوير أقل.')}<br/>{tr('More ','')}<span>{tr('making.','ابتكار أكتر.')}</span></h1><p>{tr('All the parts for your next big idea.','كل قطع مشروعك الجاي في مكان واحد.')}<br class="mobile-break"/> {tr(`Find and compare components across ${market.profile.country_name}.`,`دور وقارن بين محلات الإلكترونيات في ${market.profile.country_name_ar}.`)}</p><div class="hero-stats"><span><Layers size={15}/><strong>{catalog.products.length ? formatNumber(catalog.products.length) : '—'}</strong> {tr('components','قطعة')}</span><i></i><span><Store size={15}/><strong>{catalog.sellers.length ? formatNumber(catalog.sellers.length) : '—'}</strong> {tr('local stores','محلات محلية')}</span><span class="hero-note"><Check size={15}/> {tr('One place to look','مكان واحد للبحث')}</span></div></div>
 <div class="hero-art" aria-hidden="true"><div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><div class="floating sensor"><Radio size={26}/><span>SENSORS</span></div><div class="board"><div class="board-top"><span>MADE TO BUILD</span><span>01</span></div><div class="board-traces"><div class="chip-core"><Cpu size={53} strokeWidth={1}/></div><div class="trace-line"></div><div class="board-led"></div></div><div class="board-bottom"><CircuitBoard size={20}/><span>IDEA → REALITY</span></div><div class="pins"></div></div><div class="floating power"><Zap size={24}/><span>POWER YOUR IDEAS</span></div><div class="found"><span><Check size={13}/></span> Your next project starts here</div></div>
</section>

<nav class="quick-categories scroll-x" aria-label={tr('Popular categories','الفئات الشائعة')}><button class:on={!group} aria-pressed={!group} onclick={() => setParams({group:null,tag:null})}><CategoryIcon/>{tr('All components','كل المكونات')}</button>{#each featuredGroups as g}<button class:on={group===g} aria-pressed={group===g} onclick={() => setParams({group:group===g ? null : g,tag:null})}><CategoryIcon group={g}/>{groupLabel(g)}</button>{/each}</nav>

<div class="catalog-layout">
 <aside class:expanded={filtersOpen} id="catalog-filters" aria-label={tr('Filter components','فلترة المكونات')}>
  <div class="filter-heading"><h2><SlidersHorizontal size={17}/> {tr('Filters','الفلاتر')} {#if activeFilters}<span class="filter-count">{activeFilters}</span>{/if}</h2><button class="reset" onclick={resetFilters} disabled={!activeFilters}>{tr('Reset','مسح')}</button></div>
  <section class="filter-section"><h3>{tr('Category','الفئة')}</h3><div class="category-list"><button class:chosen={!group} aria-pressed={!group} onclick={() => setParams({group:null,tag:null})}><span><CategoryIcon size={16}/> {tr('All components','كل المكونات')}</span><small>{formatNumber([...categoryCounts.values()].reduce((a,b) => a+b,0))}</small></button>{#each (categoryExpanded ? categoryOptions : categoryOptions.slice(0,8)) as [g]}<button class:chosen={group===g} aria-pressed={group===g} onclick={() => setParams({group:group===g ? null : g,tag:null})}><span><CategoryIcon group={g} size={16}/>{groupLabel(g)}</span><small>{formatNumber(categoryCounts.get(g) ?? 0)}</small></button>{/each}</div>{#if categoryOptions.length>8}<button class="text-action" onclick={() => categoryExpanded = !categoryExpanded}>{categoryExpanded ? tr('Show fewer categories','فئات أقل') : tr(`All ${categoryOptions.length} categories`,`كل الفئات (${categoryOptions.length})`)}<ChevronDown size={14} class={categoryExpanded ? 'rotate' : ''}/></button>{/if}</section>
  <section class="filter-section"><h3>{tr('Availability','التوفر')}</h3><label class="stock-check"><input type="checkbox" checked={stock} onchange={e => setParams({stock:e.currentTarget.checked ? '1' : null})}/><span>{tr('In stock only','المتوفر فقط')}<small>{tr('At the latest store check','حسب آخر رصد للمحل')}</small></span><span class="status-dot"></span></label></section>
  <section class="filter-section"><h3>{tr('Price range','نطاق السعر')} <span>{currencyLabel()}</span></h3><PriceRangeFilter prices={priceDistribution} min={minPrice} max={maxPrice} onapply={applyPrice} /><div class="price-presets">{#each [100,500,1000] as n}<button class:chosen={maxPrice===n && !minPrice} onclick={() => setParams({min:null,max:String(n)})}>{tr('Under','أقل من')} {formatNumber(n)}</button>{/each}</div></section>
  <section class="filter-section"><label class="field seller-field"><span>{tr('Store','المحل')}</span><select value={seller} onchange={e => setParams({seller:e.currentTarget.value || null})}><option value="">{tr(`All stores in ${market.profile.country_name}`,`كل المحلات في ${market.profile.country_name_ar}`)}</option>{#each catalog.sellers as s}<option value={s}>{sellerName(s)}</option>{/each}</select></label></section>
  <div class="filter-tip"><Info size={16}/><p>{tr('Compare before you build. Prices and stock reflect the latest recorded store check.','قارن قبل ما تبدأ. الأسعار والتوفر حسب آخر رصد لكل محل.')}</p></div>
  <button class="btn primary mobile-done" onclick={() => { filtersOpen = false; document.getElementById('results-heading')?.scrollIntoView({block:'center'}); }}>{tr('Show','عرض')} {formatNumber(results.length)} {tr('results','نتيجة')}<ArrowRight size={15}/></button>
 </aside>
 <section class="results" aria-label={tr('Component results','نتائج المكونات')}>
  <div class="results-header"><div><div class="section-kicker">{tr('THE COMPONENT CATALOG','دليل المكونات')}</div><h2 id="results-heading">{q ? tr(`Results for “${q}”`,`نتائج البحث عن «${q}»`) : group ? groupLabel(group) : tr('Find your next component','لاقي القطعة اللي محتاجها')}<span>{catalog.loading ? tr('Loading…','جاري التحميل…') : tr(`${formatNumber(results.length)} products`,`${formatNumber(results.length)} قطعة`)}</span></h2></div><label class="sort-control"><ArrowUpDown size={15}/><span class="sr-only">{tr('Sort products','ترتيب المكونات')}</span><select value={sort} onchange={e => setParams({sort:e.currentTarget.value==='relevance' ? null : e.currentTarget.value})}><option value="relevance">{q ? tr('Best match','الأكثر صلة') : tr('Recommended','مقترحة لك')}</option><option value="price-asc">{tr('Price: low to high','السعر: من الأقل للأعلى')}</option><option value="price-desc">{tr('Price: high to low','السعر: من الأعلى للأقل')}</option><option value="sellers">{tr('Most stores','الأكثر انتشارًا')}</option><option value="name">{tr('Name: A to Z','الاسم: أبجديًا')}</option></select></label></div>
  <div class="search-tools"><button class="chip mobile-filter" class:on={filtersOpen} aria-controls="catalog-filters" aria-expanded={filtersOpen} onclick={() => filtersOpen = !filtersOpen}><SlidersHorizontal size={15}/>{tr('Filters','الفلاتر')} {activeFilters || ''}</button><button class="ai-toggle" class:enabled={semantic} aria-pressed={semantic} onclick={() => { trackAiDiscoveryToggle(!semantic); setParams({ai:semantic ? null : '1'}); }}><Sparkles size={15}/><strong>{tr('AI discovery','اكتشاف ذكي')}</strong><span class="toggle-track"><span></span></span></button><span class="ai-explainer">{semantic ? tr('Adds related matches · best with English descriptions','نتائج مشابهة · يعمل أفضل بالوصف الإنجليزي') : tr('Find parts by what they do','دور على القطع حسب وظيفتها')}</span><span class="private-note">{semantic ? tr('Model downloads on first search','تحميل النموذج عند أول بحث') : tr('English & Arabic name search','بحث بالأسماء العربية والإنجليزية')}</span></div>
  {#if activeFilters || q}<div class="active-filters" aria-label={tr('Active filters','الفلاتر المطبقة')}>{#if q}<button class="chip on" onclick={() => setParams({q:null})}>{tr('Search:','بحث:')} {q}<X size={13}/></button>{/if}{#if group}<button class="chip on" onclick={() => setParams({group:null})}>{groupLabel(group)}<X size={13}/></button>{/if}{#if tag}<button class="chip on" onclick={() => setParams({tag:null})}>{tag}<X size={13}/></button>{/if}{#if seller}<button class="chip on" onclick={() => setParams({seller:null})}>{sellerName(seller)}<X size={13}/></button>{/if}{#if stock}<button class="chip on" onclick={() => setParams({stock:null})}>{tr('In stock','متوفر')}<X size={13}/></button>{/if}{#if minPrice || maxPrice}<button class="chip on" onclick={() => setParams({min:null,max:null})}>{currencyLabel()} {formatNumber(minPrice)} – {maxPrice ? formatNumber(maxPrice) : tr('any','بدون حد')}<X size={13}/></button>{/if}<button class="clear-all" onclick={() => { setParams({q:null,group:null,tag:null,seller:null,stock:null,min:null,max:null}); }}>{tr('Clear all','مسح الكل')}</button></div>{/if}
  {#if tagsForGroup.length && !tag}<div class="tag-suggestions scroll-x"><span>{tr('Explore','اكتشف')}</span>{#each tagsForGroup as [t]}<button onclick={() => setParams({tag:t})}>{t}</button>{/each}</div>{/if}
  {#if semBusy}<p class="ai-status" role="status"><LoaderCircle size={15} class="spin"/>{semProgress?.progress != null ? tr(`Preparing AI discovery · ${Math.round(semProgress.progress)}%`,`تجهيز البحث الذكي · ${Math.round(semProgress.progress)}%`) : tr('Finding related components…','جاري البحث عن قطع مشابهة…')}<span>{tr('Your regular results are ready below.','نتائج البحث العادي جاهزة بالأسفل.')}</span></p>{/if}
  {#if semError}<p class="ai-status ai-error" role="status"><Info size={16}/>{tr('AI discovery is unavailable. Showing regular search results.','البحث الذكي غير متاح. بنعرض نتائج البحث العادي.')}<button onclick={() => setParams({ai:null})}>{tr('Turn off','إيقاف')}</button></p>{/if}
  {#if catalog.loading}<div class="grid" aria-label={tr('Loading products','تحميل المكونات')} aria-busy="true">{#each Array(9) as _,i (i)}<div class="skeleton" style="height:320px"></div>{/each}</div>
  {:else if !catalog.error && results.length===0}<div class="empty card"><div class="empty-icon"><PackageSearch size={38} strokeWidth={1.4}/></div><h3>{tr('No components found','مفيش نتائج للبحث ده')}</h3><p>{tr('Try a shorter part number, another spelling,','جرّب رقم قطعة أقصر أو كتابة مختلفة،')}<br/>{tr('or remove a filter to widen your search.','أو امسح فلتر عشان تظهر نتائج أكتر.')}</p><button class="btn primary" onclick={() => { trackFiltersReset('search_and_filters'); setParams({q:null,group:null,tag:null,seller:null,stock:null,min:null,max:null}); }}><RotateCcw size={15}/>{tr('Reset search & filters','مسح البحث والفلاتر')}</button><div class="example-searches"><span>{tr('Try','جرّب')}</span>{#each ['ESP32','Arduino','اردوينو'] as example}<button onclick={() => setParams({q:example,group:null,tag:null,seller:null,stock:null,min:null,max:null})}>{example}</button>{/each}</div></div>
  {:else}<div class="grid">{#each shown as p (p.id)}<ProductCard product={p} {offerFilters}/>{/each}</div><div class="more"><span aria-live="polite">{tr(`Showing ${formatNumber(shown.length)} of ${formatNumber(results.length)} components`,`عرض ${formatNumber(shown.length)} من ${formatNumber(results.length)} قطعة`)}</span>{#if shown.length<results.length}<div bind:this={loadMoreSentinel} class="load-more-sentinel" role="status" aria-live="polite">{#if loadPending}{tr('Loading more components…','جاري تحميل مكونات أكتر…')}{/if}</div>{/if}</div>{/if}
 </section>
</div>
<style>
 .discovery { display:flex; justify-content:space-between; align-items:center; overflow:hidden; border:1px solid #deebe5; background:linear-gradient(110deg,#edf5ef,#f0f6f1 65%,#e6f1ed); border-radius:20px; min-height:278px; padding:34px 42px; position:relative; }.hero-copy { z-index:1; }.eyebrow { display:flex; align-items:center; gap:8px; font-size:.8125rem; font-weight:700; letter-spacing:1.8px; color:#427064; margin-bottom:16px; }.dot { width:6px; height:6px; border-radius:50%; background:var(--accent); }h1 { font-size:2.75rem; line-height:1.04; font-weight:750; letter-spacing:-1.9px; }h1 span { color:var(--accent); }.hero-copy>p { color:#596f68; margin-top:14px; font-size:1rem; }.mobile-break { display:none; }.hero-stats { display:flex; align-items:center; gap:15px; margin-top:24px; font-size:.875rem; color:#647970; }.hero-stats>span { display:flex; align-items:center; gap:6px; }.hero-stats strong { color:#2b4f43; }.hero-stats i { height:13px; width:1px; background:#cfdfd4; }.hero-note { margin-left:8px; }.hero-art { width:410px; height:255px; position:relative; margin:-25px 20px -25px 0; flex-shrink:0; }.orbit { position:absolute; border:1px solid #c8ddd180; border-radius:50%; }.orbit-one { width:290px; height:290px; left:55px; top:-15px; }.orbit-two { width:390px; height:390px; left:5px; top:-65px; }.board { position:absolute; width:188px; height:198px; top:20px; left:116px; padding:16px; border-radius:16px; background:#176458; transform:rotate(-13deg); box-shadow:0 18px 25px #24594229,inset 0 0 0 5px #397d62,inset 0 0 0 6px #a1b68b; color:#c6dec5; }.board::before,.board::after { content:''; position:absolute; width:7px; height:7px; background:#d9e5c1; border:2px solid #95ab7b; border-radius:50%; bottom:11px; }.board::before { left:11px; }.board::after { right:11px; }.board-top,.board-bottom { display:flex; align-items:center; justify-content:space-between; font-family:monospace; font-size:7px; letter-spacing:1px; }.board-traces { height:120px; display:grid; place-items:center; background:repeating-linear-gradient(90deg,transparent 0,transparent 18px,#80a98850 19px,transparent 20px); }.chip-core { background:#243b37; color:#cad9b5; padding:4px; border:3px solid #92a482; box-shadow:0 0 0 5px #244f41; }.board-led { position:absolute; background:#dcefa5; width:5px; height:9px; right:23px; bottom:42px; box-shadow:0 0 8px #dcefa5; }.pins { position:absolute; left:30px; right:30px; height:8px; bottom:-7px; background:repeating-linear-gradient(90deg,#bda878 0,#bda878 5px,transparent 5px,transparent 10px); }.floating { position:absolute; background:#ffffffec; border:1px solid #d9e5dd; border-radius:13px; display:flex; flex-direction:column; align-items:center; gap:7px; padding:15px; font-size:7px; letter-spacing:1px; color:#528579; box-shadow:0 10px 20px #24594207; }.sensor { left:38px; top:28px; transform:rotate(-6deg); }.power { right:9px; top:123px; transform:rotate(7deg); color:#998651; }.found { position:absolute; bottom:5px; left:90px; display:flex; align-items:center; gap:8px; background:white; border-radius:8px; padding:9px 12px; font-size:10px; box-shadow:0 5px 16px #24594212; color:#4f685e; }.found>span { display:grid; place-items:center; background:var(--accent-soft); border-radius:50%; width:20px; height:20px; color:var(--accent); }
 .quick-categories { margin:22px 0 30px; gap:10px; }.quick-categories button { display:flex; align-items:center; gap:9px; padding:12px 16px; border:1px solid var(--line); background:white; border-radius:10px; white-space:nowrap; font-size:.9375rem; font-weight:500; }.quick-categories button.on { border-color:var(--accent); background:var(--accent); color:white; }.quick-categories button:hover { border-color:var(--accent); }
 .catalog-layout { display:grid; grid-template-columns:232px minmax(0,1fr); gap:30px; }aside { align-self:start; }.filter-heading { display:flex; align-items:center; justify-content:space-between; padding-bottom:20px; border-bottom:1px solid var(--line); }.filter-heading h2 { display:flex; align-items:center; gap:8px; font-size:1.125rem; }.filter-count { background:var(--accent-soft); color:var(--accent); padding:2px 6px; font-size:.8125rem; border-radius:4px; }.reset,.clear-all { font-size:.875rem; color:var(--accent); }.reset:disabled { opacity:.4; cursor:default; }.filter-section { padding:20px 0; border-bottom:1px solid var(--line); }.filter-section h3 { font-size:.9375rem; font-weight:650; margin-bottom:12px; display:flex; justify-content:space-between; }.filter-section h3>span { font-size:.8125rem; color:var(--muted); font-weight:400; }.category-list { display:flex; flex-direction:column; gap:3px; }.category-list>button { display:flex; align-items:center; justify-content:space-between; gap:8px; padding:9px 8px; border-radius:7px; font-size:.875rem; color:#5b6874; text-align:start; }.category-list>button>span { display:flex; align-items:center; gap:8px; }.category-list small { font-size:.8125rem; color:#7a8791; }.category-list>button:hover { background:#edf0f2; }.category-list>button.chosen { background:var(--accent-soft); color:var(--accent); font-weight:600; }.text-action { color:var(--accent); display:flex; align-items:center; gap:6px; font-size:.875rem; padding:12px 8px 0; }.stock-check { display:flex; align-items:center; gap:10px; font-size:.9375rem; cursor:pointer; }.stock-check small { display:block; color:var(--muted); font-size:.8125rem; margin-top:2px; }.status-dot { width:6px; height:6px; border-radius:50%; background:#438763; margin-inline-start:auto; }.price-presets { display:flex; gap:5px; flex-wrap:wrap; }.price-presets button { border:1px solid var(--line); font-size:.75rem; padding:5px 7px; border-radius:5px; color:var(--muted); }.price-presets button.chosen { border-color:var(--accent); color:var(--accent); }.seller-field>span { color:var(--fg); font-weight:650; }.seller-field select { font-size:.875rem; }.filter-tip { display:flex; align-items:flex-start; gap:8px; padding:16px 0; color:#83908d; }.filter-tip :global(svg) { flex-shrink:0; }.filter-tip p { font-size:.8125rem; line-height:1.65; }.mobile-done,.mobile-filter { display:none; }
 .results { min-width:0; }.results-header { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:17px; }.section-kicker { font-size:.75rem; letter-spacing:1.6px; font-weight:600; color:#86929c; margin-bottom:7px; }.results-header h2 { font-size:1.375rem; overflow-wrap:anywhere; }.results-header h2>span { display:inline-block; vertical-align:middle; font-size:.8125rem; letter-spacing:0; font-weight:400; color:var(--muted); margin-inline-start:12px; }.sort-control { display:flex; align-items:center; gap:8px; padding:9px 10px; background:white; border:1px solid var(--line); border-radius:8px; color:var(--muted); flex-shrink:0; }.sort-control select { background:none; border:0; color:var(--fg); font-size:.875rem; max-width:155px; }.search-tools { display:flex; align-items:center; gap:10px; border-top:1px solid var(--line); border-bottom:1px solid var(--line); padding:11px 0; margin-bottom:14px; }.ai-toggle { display:flex; align-items:center; gap:7px; font-size:.875rem; color:#657082; }.ai-toggle.enabled { color:var(--accent); }.toggle-track { display:flex; align-items:center; width:27px; height:16px; background:#d9dfe3; padding:2px; border-radius:20px; margin-left:3px; }.toggle-track>span { height:12px; width:12px; border-radius:50%; background:white; box-shadow:0 1px 2px #0002; }.enabled .toggle-track { background:var(--accent); }.enabled .toggle-track>span { transform:translateX(11px); }.ai-explainer { color:var(--muted); font-size:.8125rem; }.private-note { color:#859099; margin-inline-start:auto; font-size:.8125rem; }.active-filters { display:flex; align-items:center; flex-wrap:wrap; gap:7px; margin-bottom:14px; }.active-filters .chip { font-size:.8125rem; }.tag-suggestions { margin:0 0 16px; align-items:center; gap:8px; }.tag-suggestions>span { font-size:.8125rem; color:var(--muted); margin-inline-end:3px; }.tag-suggestions button { font-size:.8125rem; white-space:nowrap; color:#687782; border:1px solid #e1e7eb; padding:4px 9px; border-radius:5px; }.tag-suggestions button:hover { color:var(--accent); border-color:var(--accent); }.ai-status { display:flex; align-items:center; flex-wrap:wrap; gap:8px; padding:12px; background:var(--accent-soft); border-radius:8px; margin-bottom:14px; color:var(--accent); font-size:.9375rem; }.ai-status>span { color:var(--muted); }.ai-error { background:var(--warn-soft); color:#765f26; }.ai-error button { text-decoration:underline; }.empty { text-align:center; padding:60px 16px; }.empty-icon { color:var(--accent); background:var(--accent-soft); width:76px; height:76px; display:grid; place-items:center; border-radius:22px; margin:0 auto 22px; }.empty h3 { font-size:1.4375rem; }.empty p { color:var(--muted); font-size:1rem; margin:12px 0 22px; }.example-searches { display:flex; gap:12px; justify-content:center; margin-top:22px; font-size:.9375rem; }.example-searches span { color:var(--muted); }.example-searches button { color:var(--accent); text-decoration:underline; }.more { display:flex; flex-direction:column; align-items:center; gap:15px; padding:30px 0 10px; color:var(--muted); font-size:.875rem; }.load-more-sentinel { min-height:18px; width:100%; text-align:center; color:var(--accent); }
 @media(min-width:1350px) { .results :global(.grid) { grid-template-columns:repeat(4,minmax(0,1fr)); } }
 @media(max-width:1100px) { .catalog-layout { grid-template-columns:210px minmax(0,1fr); gap:22px; }.hero-art { margin-right:-30px; transform:scale(.9); }.hero-note { display:none!important; }.discovery { padding:30px; }.private-note { display:none; }.results-header h2>span { display:block; margin:7px 0 0; } }
 @media(max-width:850px) { .catalog-layout { grid-template-columns:minmax(0,1fr); }aside { display:none; }aside.expanded { display:block; background:white; padding:20px; border:1px solid var(--line); border-radius:12px; }.mobile-done { display:flex; width:100%; }.mobile-filter { display:inline-flex; }.hero-art { transform:scale(.8); margin-left:-40px; margin-right:-60px; }.hero-copy { flex-shrink:0; }h1 { font-size:2.375rem; }.quick-categories { margin:18px 0 24px; } }
 @media(max-width:600px) { .discovery { display:block; padding:25px 22px 0; min-height:0; text-align:center; }.hero-copy { display:flex; flex-direction:column; align-items:center; }.hero-art { display:block; width:min(100%,330px); height:205px; margin:2px auto -6px; transform:scale(.74); transform-origin:top center; }.orbit-one { left:20px; top:-40px; }.orbit-two { left:-30px; top:-90px; }.board { top:0; left:78px; }.sensor { left:18px; top:22px; }.power { right:2px; top:94px; }.found { left:48px; bottom:1px; }.eyebrow { justify-content:center; font-size:.75rem; letter-spacing:1.3px; }h1 { font-size:2.4375rem; }.hero-stats { justify-content:center; gap:12px; font-size:.8125rem; }.hero-copy>p { font-size:.9375rem; }.mobile-break { display:block; }.hero-stats { margin-top:20px; }.quick-categories button { padding:10px 12px; font-size:.875rem; }.results-header h2 { font-size:1.1875rem; }.sort-control { gap:5px; padding:8px; }.sort-control select { max-width:115px; font-size:.8125rem; }.section-kicker { font-size:.75rem; }.ai-explainer { display:none; }.search-tools { justify-content:space-between; }.results :global(.grid) { gap:10px; } }
</style>
