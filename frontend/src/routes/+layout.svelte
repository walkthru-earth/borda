<script lang="ts">
 import '@fontsource/cairo/400.css';
 import '@fontsource/cairo/600.css';
 import '@fontsource/cairo/700.css';
 import '../app.css';
 import { locale, tr, dateLocale } from '$lib/i18n.svelte';
 import { base } from '$app/paths';
 import { page } from '$app/state';
 import { goto } from '$app/navigation';
 import { onDestroy, onMount } from 'svelte';
 import { Search, X, ArrowUpRight, CodeXml, RefreshCw, Languages } from '@lucide/svelte';
 import { SITE_ORIGIN, safeJsonLd, productSchema } from '$lib/seo';
 import { catalog } from '$lib/data.svelte';
 let { children } = $props();
 onMount(() => {
  document.querySelectorAll('[data-borda-static-seo]').forEach(node => {
   // Svelte updates document.title by reusing the static title element.
   if (node.tagName === 'TITLE') node.removeAttribute('data-borda-static-seo');
   else node.remove();
  });
  const queryLanguage = new URLSearchParams(location.search).get('lang');
  let saved: string | null = null;
  try { saved = localStorage.getItem('borda-language'); } catch { /* Private browsing can disable storage. */ }
  const chosen = queryLanguage ?? saved;
  if (chosen === 'ar' || chosen === 'en') locale.language = chosen;
 });
 $effect(() => { document.documentElement.lang = locale.language; document.documentElement.dir = locale.language === 'ar' ? 'rtl' : 'ltr'; });
 function toggleLanguage() {
  locale.language = locale.language === 'en' ? 'ar' : 'en';
  try { localStorage.setItem('borda-language', locale.language); } catch { /* Preference still lasts this session. */ }
  const url = new URL(page.url); url.searchParams.set('lang', locale.language);
  void goto(`${url.pathname}${url.search}`, { replaceState:true, keepFocus:true, noScroll:true });
 }
 $effect(() => { void catalog.ensure(); });
 let seoProduct = $derived(catalog.byId.get(page.params.id ?? ''));
 let canonical = $derived(`${SITE_ORIGIN}${`${page.url.pathname.replace(/\/$/, '')}/`}`);
 let filtered = $derived([...page.url.searchParams.keys()].some(key => key !== 'lang'));
 let seoTitle = $derived(seoProduct ? `${seoProduct.canonical_name} · Borda | بوردة` : tr('Borda — Electronic components in Egypt','بوردة — مكونات الإلكترونيات في مصر'));
 let seoDescription = $derived(seoProduct ? tr(`Compare ${seoProduct.canonical_name} prices and recorded availability across Egyptian electronics stores. Explore specifications, seller links and price history on Borda.`,`قارن أسعار ${seoProduct.canonical_name_ar || seoProduct.canonical_name} وتوفرها المسجل في محلات مصر. مواصفات وروابط المحلات وتاريخ الأسعار على بوردة.`) : tr('Find electronic components across Egyptian stores. Compare listed prices, browse stock observations, and search parts in English or Arabic.','دور على مكونات الإلكترونيات في محلات مصر. قارن الأسعار المسجلة والتوفر وابحث عن القطع بالعربي أو الإنجليزي.'));
 let structuredData = $derived(seoProduct ? productSchema(seoProduct,catalog.stats.get(seoProduct.id),canonical) : { '@context':'https://schema.org', '@type':'WebSite', name:'Borda | بوردة', url:`${SITE_ORIGIN}${base}/`, inLanguage:['en','ar'], description:seoDescription });
 let q = $derived(page.url.searchParams.get('q') ?? '');
 let draft = $state('');
 $effect(() => { draft = q; });
 let input: HTMLInputElement;
 let timer: ReturnType<typeof setTimeout> | undefined;
 onDestroy(() => clearTimeout(timer));
 function submit(value: string, immediate = false) {
  clearTimeout(timer);
  const navigate = () => {
   const u = new URL(page.url);
   const onHome = u.pathname === `${base}/` || u.pathname === base;
   u.pathname = `${base}/`;
   if (!onHome) u.search = '';
   if (value.trim()) u.searchParams.set('q', value.trim()); else u.searchParams.delete('q');
   void goto(`${u.pathname}${u.search}`, { replaceState: onHome, keepFocus: true, noScroll: true });
  };
  if (immediate) navigate(); else timer = setTimeout(navigate, 220);
 }
 function shortcut(e: KeyboardEvent) {
  if (e.key === '/' && !(e.target instanceof HTMLInputElement) && !(e.target instanceof HTMLTextAreaElement) && !(e.target instanceof HTMLElement && e.target.isContentEditable)) { e.preventDefault(); input?.focus(); }
 }
</script>
<svelte:window onkeydown={shortcut} />
<svelte:head>
 <link rel="icon" type="image/svg+xml" href="{base}/brand/borda-mark.svg" />
 <link rel="canonical" href={canonical} />
 <meta name="robots" content={filtered || (page.params.id && !seoProduct && !catalog.loading) ? 'noindex,follow' : 'index,follow,max-image-preview:large'} />
 <meta property="og:site_name" content="Borda | بوردة" />
 <meta property="og:type" content={seoProduct ? 'product' : 'website'} />
 <meta property="og:title" content={seoTitle} />
 <meta property="og:description" content={seoDescription} />
 <meta property="og:url" content={canonical} />
 <meta property="og:locale" content={locale.language === 'ar' ? 'ar_EG' : 'en_GB'} />
 <meta property="og:image" content={seoProduct?.image && /^https?:\/\//i.test(seoProduct.image) ? seoProduct.image : `${SITE_ORIGIN}${base}/brand/borda-social.png`} />
 <meta property="og:image:alt" content={seoProduct?.canonical_name ?? 'Borda | بوردة — قطعتك فين؟'} />
 <meta name="twitter:card" content="summary_large_image" />
 <meta name="theme-color" content="#087f73" />
 <meta name="description" content={seoDescription} />
 {@html `<script type="application/ld+json">${safeJsonLd(structuredData)}</script>`}
</svelte:head>
<a class="skip" href="#main-content">{tr('Skip to products','انتقل للمكونات')}</a>
<header class="top">
 <div class="container bar">
  <a class="brand" href="{base}/" aria-label={tr('Borda home','بوردة — الرئيسية')}><img class="brand-mark" src="{base}/brand/borda-mark.svg" alt="" width="42" height="42"/><span class="wordmark">borda<span class="brand-sub">بوردة · FIND. COMPARE. BUILD.</span></span></a>
  <form class="search" role="search" onsubmit={(e) => { e.preventDefault(); submit(draft, true); }}>
   <Search size={19} aria-hidden="true"/>
   <input bind:this={input} bind:value={draft} type="search" enterkeyhint="search" autocomplete="off" aria-label={tr('Search components','ابحث عن المكونات')} dir="auto" placeholder={tr('Search a component, part number, or اسم القطعة…','ابحث باسم القطعة أو رقمها… ESP32، أردوينو، حساس')} oninput={(e) => submit(e.currentTarget.value)} />
   {#if draft}<button type="button" aria-label={tr('Clear search','امسح البحث')} onclick={() => { draft = ''; submit('', true); input.focus(); }}><X size={17}/></button>{:else}<kbd>/</kbd>{/if}
  </form>
  <button class="language-switch" onclick={toggleLanguage} aria-label={tr("Switch to Arabic", "Switch to English")}><Languages size={16}/><span>{tr("العربية", "English")}</span></button>
  <a class="source" href="https://github.com/walkthru-earth/borda" target="_blank" rel="noopener"><CodeXml size={18}/><span>{tr('Open source','مفتوح المصدر')}</span><ArrowUpRight size={14}/></a>
 </div>
</header>
<main id="main-content" class="container">
 {#if catalog.error}<div class="card notice" role="alert"><div><strong>{tr('We couldn’t load the catalog.','تعذر تحميل المكونات.')}</strong><p class="muted small">{tr('Check your connection and try again.','تحقق من الاتصال وحاول مرة أخرى.')}</p></div><button class="btn" onclick={() => catalog.ensure()}><RefreshCw size={15}/> {tr('Try again','حاول مرة أخرى')}</button></div>{/if}
 {@render children()}
</main>
<footer class="container footer">
 <div><strong>{tr('Made for the makers.','معاك في كل مشروع.')}</strong><p>{tr('Find your next component. Bring your next idea to life.','لاقي القطعة اللي محتاجها، وحوّل فكرتك لحقيقة.')}</p></div>
 <div class="foot-meta">{#if catalog.manifest}<span>{tr('Data updated','آخر تحديث')} {new Date(catalog.manifest.generated_at).toLocaleDateString(dateLocale(), { day:'numeric',month:'short',year:'numeric' })}</span>{/if}<span>{tr('Prices are observations. Confirm price and availability with the seller.','الأسعار حسب آخر رصد. راجع السعر والتوفر مع المحل قبل الشراء.')}</span></div>
</footer>
<style>
 .language-switch { display:flex; align-items:center; gap:7px; font-size:12px; white-space:nowrap; font-weight:600; color:var(--accent); }.bar { gap:26px!important; }
 .skip { position:fixed; top:-80px; left:16px; z-index:100; padding:12px; background:var(--surface); }.skip:focus { top:8px; }
 .top { position:sticky; top:0; z-index:30; background:#fffffff5; backdrop-filter:blur(12px); border-bottom:1px solid var(--line); }.bar { display:flex; align-items:center; gap:44px; min-height:86px; }.brand { display:flex; align-items:center; gap:10px; font-size:20px; letter-spacing:-.6px; font-weight:750; flex-shrink:0; line-height:1.15; }.brand-sub { display:block; color:var(--muted); font-size:9px; letter-spacing:2.1px; font-weight:600; margin-top:5px; }.brand-mark { border-radius:12px; }.wordmark { font-size:29px; font-weight:750; letter-spacing:-1.2px; }.wordmark .brand-sub { font-size:7px; letter-spacing:1px; }.search { flex:1; display:flex; align-items:center; gap:12px; background:var(--bg); border:1px solid var(--line); border-radius:11px; padding:0 15px; height:45px; color:var(--muted); }.search:focus-within { border-color:var(--accent); box-shadow:0 0 0 3px var(--accent-soft); }.search input { width:100%; min-width:0; border:0; outline:0; background:transparent; color:var(--fg); font-size:13px; }.search input::-webkit-search-cancel-button { -webkit-appearance:none; }.search button { display:grid; place-items:center; min-width:28px; min-height:32px; }kbd { border:1px solid var(--line); background:white; padding:0 6px; border-radius:4px; }.source { display:flex; align-items:center; gap:8px; font-size:12px; font-weight:600; white-space:nowrap; }main { padding:30px 0 48px; min-height:75vh; }.footer { border-top:1px solid var(--line); display:flex; justify-content:space-between; gap:24px; padding:28px 0 calc(30px + var(--safe-b)); font-size:12px; color:var(--muted); }.footer strong { color:var(--fg); font-size:14px; }.footer p { margin-top:5px; }.foot-meta { display:flex; flex-direction:column; gap:5px; text-align:end; }.notice { padding:16px; display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; border-color:#e5bba6; }
 @media(max-width:1000px) { .bar { gap:24px; }.source span { display:none; } }
 @media(max-width:700px) { .bar { gap:12px; padding:16px 0; flex-wrap:wrap; }.brand { font-size:18px; }.source { margin-inline-start:auto; }.search { order:3; flex-basis:100%; }.bar { min-height:0; }.footer { flex-direction:column; }.foot-meta { text-align:start; }main { padding-top:20px; } }
</style>
