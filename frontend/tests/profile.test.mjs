import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, readdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { DEFAULT_PROFILE, resolveProfile, profileLocale, profileNumber, profileCurrencyLabel } from '../src/lib/profile.js';
import { buildIndex, search, matchingPrice, productMatchesFilters } from '../src/lib/search.ts';
import { productOffers } from '../src/lib/offers.ts';
import { productSchema, renderHomePage, renderProductPage } from '../scripts/generate-seo.mjs';

const profile = { id:'uae', country_code:'AE', country_name:'United Arab Emirates', country_name_ar:'الإمارات العربية المتحدة', currency:'AED', locale:'en-AE', locale_ar:'ar-AE', timezone:'Asia/Dubai', languages:['en','ar'] };
const product = { id:'board', canonical_name:'Development board', raw_names:[], tags:[], sellers:['store-a'], group:'dev-boards' };
const offer = (currency, price, availability='in_stock') => ({ seller:'store-a', url:`https://store.test/${currency}`, currency, price, availability, ts:new Date('2026-09-18T12:00:00Z') });
const stats = { currency:'AED', latest_min:50, in_stock_sellers:1, current_offers:[offer('AED',50), offer('EGP',1)] };
const shell = '<html lang="en"><head></head><body><div id="app-mount"></div></body></html>';
const site = 'https://example.test/borda';

test('frontend compatibility default exactly matches the packaged public Egypt profile', () => {
 const backend = JSON.parse(readFileSync(new URL('../../src/borda/profiles/egypt.json',import.meta.url),'utf8'));
 const expected = Object.fromEntries(Object.keys(DEFAULT_PROFILE).map(key => [key,backend[key]]));
 assert.deepEqual(DEFAULT_PROFILE,expected);
 assert.deepEqual(resolveProfile(undefined),expected);
 assert.equal('stores' in DEFAULT_PROFILE,false);
});

test('a different profile controls currency filtering, price recommendation and number formatting', () => {
 const filters = { currency:profile.currency, inStock:true, maxPrice:60 };
 assert.equal(matchingPrice(stats,filters),50);
 assert.equal(productMatchesFilters(product,stats,filters),true);
 assert.equal(productMatchesFilters(product,stats,{...filters,maxPrice:10}),false);
 assert.equal(productOffers(stats,[],profile.currency).bestOffer.price,50);
 assert.equal(productOffers(undefined,[],profile.currency).currency,'AED');
 assert.equal(profileLocale(profile,'ar'),'ar-AE');
 assert.equal(profileNumber(1200.5,profile,'en'),new Intl.NumberFormat('en-AE',{maximumFractionDigits:2}).format(1200.5));
 assert.equal(profileCurrencyLabel('AED',profile,'en'),'AED');
 assert.notEqual(profileCurrencyLabel('AED',profile,'ar'),profileCurrencyLabel('EGP',profile,'ar'));
 const legacy = { currency:'EGP', latest_min:1, in_stock_sellers:1 };
 assert.equal(productMatchesFilters(product,legacy,{currency:'AED',maxPrice:60}),false);
});

test('custom-profile static SEO uses the matching country and currency without mixing snapshots', () => {
 const schema = productSchema(product,stats,site,profile);
 assert.equal(schema.offers.priceCurrency,'AED');
 assert.equal(schema.offers.lowPrice,50);
 assert.equal(schema.offers.offerCount,1);
 const detail = renderProductPage(shell,product,stats,site,[],profile);
 const home = renderHomePage(shell,[product],{profile,groups:{'dev-boards':1}},site);
 assert.match(detail,/50 AED/);
 assert.match(detail,/prices in United Arab Emirates/);
 assert.match(home,/components in United Arab Emirates/);
 assert.match(home,/الإمارات العربية المتحدة/);
 assert.match(home,/content="en_AE"/);
 assert.doesNotMatch(detail+home,/Egypt|Egyptian|EGP|مصر/);
 const englishOnly = renderHomePage(shell,[product],{profile:{...profile,languages:['en']},groups:{}},site);
 assert.doesNotMatch(englishOnly,/use the Arabic interface|lang="ar"/);
});

test('sync-data honors an explicit snapshot over BORDA_DATA_DIR and excludes nested profiles/history', () => {
 const folder = mkdtempSync(join(tmpdir(),'borda-profile-sync-'));
 try {
  const primary = join(folder,'primary'), alternate = join(folder,'alternate');
  for(const source of [primary,alternate]) {
   mkdirSync(join(source,'another-market'),{recursive:true});
   mkdirSync(join(source,'offers'),{recursive:true});
   writeFileSync(join(source,'manifest.json'),JSON.stringify({profile:source===primary?DEFAULT_PROFILE:profile}));
   writeFileSync(join(source,'catalog.parquet'),'fixture');
   writeFileSync(join(source,'stats.parquet'),'fixture');
   writeFileSync(join(source,'another-market','catalog.parquet'),'do not publish');
   writeFileSync(join(source,'offers','history.parquet'),'do not publish');
  }
  const script=fileURLToPath(new URL('../scripts/sync-data.mjs',import.meta.url));
  const env={...process.env,BORDA_DATA_DIR:alternate};
  execFileSync(process.execPath,[script],{cwd:folder,env});
  const destination=join(folder,'static','data');
  assert.equal(JSON.parse(readFileSync(join(destination,'manifest.json'),'utf8')).profile.id,'uae');
  assert.deepEqual(readdirSync(destination).sort(),['catalog.parquet','manifest.json','stats.parquet']);
  execFileSync(process.execPath,[script,primary],{cwd:folder,env});
  assert.equal(JSON.parse(readFileSync(join(destination,'manifest.json'),'utf8')).profile.id,'egypt');
 } finally { rmSync(folder,{recursive:true,force:true}); }
});


test('alternate-profile browse results and card prices honor same-offer seller, stock and budget constraints', () => {
 const board = { ...product, sellers:['store-a','store-b'] };
 const index = buildIndex([board]);
 const observed = { ...stats, current_offers:[offer('AED',10,'out_of_stock'), {...offer('AED',80),seller:'store-b'},offer('EGP',1)] };
 const browse = (filters) => search(index,'board',Infinity).filter(row => productMatchesFilters(row,observed,filters));
 assert.equal(browse({currency:'AED',seller:'store-a',inStock:true,maxPrice:100}).length,0);
 assert.equal(browse({currency:'AED',inStock:true,maxPrice:20}).length,0);
 const filters = {currency:'AED',seller:'store-b',inStock:true,maxPrice:100};
 assert.deepEqual(browse(filters).map(row=>row.id),['board']);
 assert.equal(matchingPrice(observed,filters),80);
});
