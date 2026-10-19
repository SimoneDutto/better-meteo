/** Offline HTML export: no server, browser JavaScript, or provider access. */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { basePath, pagePath, pageStates } from '../src/lib/links.mjs';

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
process.chdir(project);
const base = basePath(process.env.PAGES_BASE || '/');
const output = path.resolve(process.env.PAGES_OUTPUT || 'dist-pages');
const data = path.resolve(process.env.BETTER_METEO_DATA || 'data');
const marker = '.better-meteo-export';
if (fs.existsSync(output) && !fs.existsSync(path.join(output, marker))) throw Error(`Refusing to replace an unrecognised directory: ${output}`);
const temporary = fs.mkdtempSync(path.join(path.dirname(output), '.meteo-pages-'));
function read(name) { try { return JSON.parse(fs.readFileSync(path.join(data, name), 'utf8')); } catch { return null; } }
const config = JSON.parse(fs.readFileSync('config.json', 'utf8'));
const pressure = read('pressure.json');
process.env.BETTER_METEO_STATIC = '1';
process.env.PAGES_BASE = base;
process.env.ASTRO_NODE_AUTOSTART = 'disabled';
try {
  await import('../dist/server/entry.mjs');
  const name = fs.readdirSync('dist/server').find(n=>n.startsWith('manifest_')&&n.endsWith('.mjs'));
  const { manifest } = await import(`../dist/server/${name}`);
  const { NodeApp } = await import('astro/app/node');
  const app = new NodeApp(manifest);
  const states = pageStates(config, pressure, (city, section)=>read(`cities/${city}/${section}.json`));
  const files = new Map();
  for (const state of states) {
    const response = await app.render(new Request('http://localhost/?'+new URLSearchParams(state)));
    if (response.status !== 200) throw Error(`Could not render ${pagePath(state)}: ${response.status}`);
    let html = await response.text();
    if (/<script\b/i.test(html)) throw Error('Static dashboard must not require browser JavaScript');
    html = html.replaceAll('href="/_astro/', `href="${base}_astro/`);
    const file = pagePath(state)+'index.html';
    if (files.has(file)) throw Error(`Duplicate static page: ${file}`);
    files.set(file, html);
  }
  const home = files.get(pagePath({city:config.cities[0].id,tab:'forecast'})+'index.html');
  files.set('index.html',home);
  files.set('404.html',`<!doctype html><html lang="en"><meta charset="UTF-8"><meta name="viewport" content="width=device-width"><title>Page not found</title><h1>Page not found</h1><p><a href="${base}">Open the weather dashboard</a></p></html>`);
  for (const [file,html] of files) {
    const target=path.join(temporary,file);
    fs.mkdirSync(path.dirname(target),{recursive:true}); fs.writeFileSync(target,html);
  }
  const clientAssets = path.resolve('dist/client');
  if(fs.existsSync(clientAssets)) fs.cpSync(clientAssets,temporary,{recursive:true});
  // Copy only media referenced by the current manifest plus the device PNG option.
  const media = new Set();
  for (const frame of pressure?.data?.frames || []) media.add(frame.image);
  if(pressure?.data?.animation) media.add(pressure.data.animation);
  for (const city of config.cities) {
    const name=`${city.id}-dashboard.png`;
    if(fs.existsSync(path.join(data,'media',name))) media.add(name);
  }
  fs.mkdirSync(path.join(temporary,'media'),{recursive:true});
  for (const name of media) {
    if(!/^[a-zA-Z0-9-]+\.(png|gif)$/.test(name)) throw Error('Invalid media filename');
    const source = path.join(data,'media',name);
    if (!fs.existsSync(source)) throw Error(`Referenced map is missing: ${name}. Previous export retained.`);
    fs.copyFileSync(source,path.join(temporary,'media',name));
  }
  fs.writeFileSync(path.join(temporary,'.nojekyll'),'');
  fs.writeFileSync(path.join(temporary,marker),'Better Meteo generated site\n');
  fs.writeFileSync(path.join(temporary,'publication.json'),JSON.stringify({generated:new Date().toISOString(),base,cities:config.cities.map(c=>c.id),pages:files.size},null,2)+'\n');
  // Every local link and asset must exist, including rotation and paginated records.
  for (const [file,html] of files) {
    const references=[...html.matchAll(/(?:href|src)="([^"]+)"/g)].map(m=>m[1]);
    const refresh=html.match(/http-equiv="refresh" content="\d+;url=([^"]+)"/);
    if(refresh) references.push(refresh[1]);
    for(const reference of references) {
      if(/^(?:https?:|#|mailto:)/.test(reference)) continue;
      if(!reference.startsWith(base)||reference.includes('?')) throw Error(`Non-static local link in ${file}: ${reference}`);
      const relative=reference.slice(base.length);
      const target=path.join(temporary,relative.endsWith('/')?relative+'index.html':relative);
      if(!fs.existsSync(target)) throw Error(`Broken local link in ${file}: ${reference}`);
    }
  }
  // Replacement occurs only after a completely rendered and validated export.
  const previous=output+'.previous';
  if(fs.existsSync(previous)) throw Error(`Previous export recovery directory exists: ${previous}`);
  if(fs.existsSync(output)) fs.renameSync(output,previous);
  try {fs.renameSync(temporary,output);} catch(error) {if(fs.existsSync(previous))fs.renameSync(previous,output);throw error;}
  if(fs.existsSync(previous))fs.rmSync(previous,{recursive:true});
  console.log(`Static site ready: ${output} · ${files.size} HTML pages · ${media.size} media files · base ${base}`);
  console.log('Export read local caches only. No weather requests sent.');
} finally {
  if(fs.existsSync(temporary))fs.rmSync(temporary,{recursive:true});
}
