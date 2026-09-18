/** One published catalog belongs to one market; this is a compatibility default, not a selector. */
/** @typedef {{id:string,country_code:string,country_name:string,country_name_ar:string,currency:string,locale:string,locale_ar:string,timezone:string,languages:string[]}} MarketProfile */

/** @type {MarketProfile} */
export const DEFAULT_PROFILE = Object.freeze({
 id: 'egypt', country_code: 'EG', country_name: 'Egypt', country_name_ar: 'مصر',
 currency: 'EGP', locale: 'en-EG', locale_ar: 'ar-EG', timezone: 'Africa/Cairo', languages: ['en', 'ar']
});

/** @param {Partial<MarketProfile> | null | undefined} profile @returns {MarketProfile} */
export function resolveProfile(profile) {
 return { ...DEFAULT_PROFILE, ...profile, languages: [...(profile?.languages ?? DEFAULT_PROFILE.languages)] };
}

/** @param {MarketProfile} profile @param {'en'|'ar'} language */
export function profileLocale(profile, language) {
 return language === 'ar' ? profile.locale_ar : profile.locale;
}

/** @param {number} value @param {MarketProfile} profile @param {'en'|'ar'} language */
export function profileNumber(value, profile, language) {
 return value.toLocaleString(profileLocale(profile, language), { maximumFractionDigits: 2 });
}

/** Use ISO codes in English and the locale's own currency label in Arabic. */
/** @param {string} currency @param {MarketProfile} profile @param {'en'|'ar'} language */
export function profileCurrencyLabel(currency, profile, language) {
 if (!currency || language === 'en') return currency;
 return new Intl.NumberFormat(profileLocale(profile, language), { style: 'currency', currency })
  .formatToParts(0).find((part) => part.type === 'currency')?.value ?? currency;
}
