/** Shared, persisted interface language. Technical identifiers stay unchanged. */
export const locale = $state<{ language: 'en' | 'ar' }>({ language: 'en' });
export const tr = (english: string, arabic: string): string => locale.language === 'ar' ? arabic : english;
export const dateLocale = (): string => locale.language === 'ar' ? 'ar-EG' : 'en-GB';
export const formatNumber = (value: number): string => value.toLocaleString(locale.language === 'ar' ? 'ar-EG' : 'en-EG', { maximumFractionDigits: 2 });
const groups: Record<string, [string,string]> = {
 'dev-boards':['Dev boards','لوحات التطوير'], 'microcontrollers-ics':['MCUs & ICs','متحكمات ودوائر متكاملة'], sensors:['Sensors','حساسات'], 'wireless-iot':['Wireless & IoT','اتصالات وإنترنت الأشياء'], 'displays-leds':['Displays & LEDs','شاشات وإضاءة'], 'motors-drivers':['Motors & drivers','محركات ودرايفرات'], power:['Power','مصادر الطاقة'], 'passive-components':['Passives','مقاومات ومكثفات'], semiconductors:['Semiconductors','أشباه الموصلات'], 'connectors-cables':['Connectors & cables','موصلات وكابلات'], prototyping:['Prototyping','نماذج وتجارب'], 'tools-instruments':['Tools & instruments','أدوات وأجهزة قياس'], '3d-printing-cnc':['3D printing & CNC','طباعة ثلاثية الأبعاد وCNC'], 'robotics-kits':['Robotics & kits','روبوتات وأطقم'], other:['Other','مكونات أخرى']
};
export function groupName(group?: string | null): string { const pair = groups[group ?? 'other']; return pair ? tr(...pair) : group ?? tr('Other','أخرى'); }
