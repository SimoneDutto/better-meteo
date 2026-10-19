import fs from 'node:fs';
import path from 'node:path';
import { basePath } from './links.mjs';

export const root = path.resolve(process.env.BETTER_METEO_DATA || 'data');
export function read(file: string): any {
  try { return JSON.parse(fs.readFileSync(path.join(root, file), 'utf8')); }
  catch { return null; }
}
export function configuration(): any {
  return JSON.parse(fs.readFileSync(path.resolve('config.json'), 'utf8'));
}
export const number = (value: any, digits = 0): string =>
  typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
export function date(value: string, zone: string, options: Intl.DateTimeFormatOptions = {weekday:'short',day:'numeric',month:'short'}) {
  return new Intl.DateTimeFormat('en-GB', {timeZone:zone, ...options}).format(new Date(value.length===10 ? value+'T12:00:00Z' : value));
}
export function media(file: string) {
  return (process.env.BETTER_METEO_STATIC==='1' ? basePath(process.env.PAGES_BASE || '/') : '/')+'media/'+file;
}
export function age(updated: string | undefined) {
  if (!updated) return 'No data yet';
  if (process.env.BETTER_METEO_STATIC==='1') return 'Saved '+date(updated,'UTC',{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'})+' UTC';
  const hours = Math.max(0, (Date.now() - Date.parse(updated)) / 3600000);
  return hours < 1 ? 'Updated less than 1h ago' : `Updated ${Math.floor(hours)}h ago`;
}
export function weather(code: number) {
  if (code===0) return 'Clear';
  if (code<=3) return 'Cloudy';
  if (code<=48) return 'Fog';
  if (code<=57) return 'Drizzle';
  if (code<=67) return 'Rain';
  if (code<=77) return 'Snow';
  if (code<=82) return 'Showers';
  if (code<=86) return 'Snow showers';
  if (code>=95) return 'Thunderstorm';
  return 'Unavailable';
}
export function compass(degrees: number | null) {
  return degrees==null ? '—' : ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'][Math.round(degrees/22.5)%16];
}
