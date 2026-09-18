/** Shared, persisted interface language. Technical identifiers stay unchanged. */
import { DEFAULT_PROFILE, profileLocale, profileNumber, profileCurrencyLabel, type MarketProfile } from './profile.js';
export const market = $state<{ profile: MarketProfile }>({ profile: DEFAULT_PROFILE });
export const locale = $state<{ language: 'en' | 'ar' }>({ language: 'en' });
export const tr = (english: string, arabic: string): string => locale.language === 'ar' ? arabic : english;
export const dateLocale = (): string => profileLocale(market.profile, locale.language);
export const dateOptions = (): Intl.DateTimeFormatOptions => ({ timeZone: market.profile.timezone });
export const countryName = (): string => locale.language === 'ar' ? market.profile.country_name_ar : market.profile.country_name;
export const currencyLabel = (currency = market.profile.currency): string => profileCurrencyLabel(currency, market.profile, locale.language);
export const currencyName = (currency = market.profile.currency): string => new Intl.DisplayNames([dateLocale()], { type: 'currency' }).of(currency) ?? currency;
export const formatNumber = (value: number): string => profileNumber(value, market.profile, locale.language);
const groups: Record<string, [string,string]> = {
 'dev-boards':['Dev boards','لوحات التطوير'], 'microcontrollers-ics':['MCUs & ICs','متحكمات ودوائر متكاملة'], sensors:['Sensors','حساسات'], 'wireless-iot':['Wireless & IoT','اتصالات وإنترنت الأشياء'], 'displays-leds':['Displays & LEDs','شاشات وإضاءة'], 'motors-drivers':['Motors & drivers','محركات ودرايفرات'], power:['Power','مصادر الطاقة'], 'passive-components':['Passives','مقاومات ومكثفات'], semiconductors:['Semiconductors','أشباه الموصلات'], 'connectors-cables':['Connectors & cables','موصلات وكابلات'], prototyping:['Prototyping','نماذج وتجارب'], 'tools-instruments':['Tools & instruments','أدوات وأجهزة قياس'], '3d-printing-cnc':['3D printing & CNC','طباعة ثلاثية الأبعاد وCNC'], 'robotics-kits':['Robotics & kits','روبوتات وأطقم'], other:['Other','مكونات أخرى']
};
export function groupName(group?: string | null): string { const pair = groups[group ?? 'other']; return pair ? tr(...pair) : group ?? tr('Other','أخرى'); }
