/** Offline viewport checks using installed Chrome; no npm/browser downloads or server. */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
process.chdir(project);
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'better-meteo-layout-'));
const localData = path.join(temporary, 'data');
const originalData = path.resolve(process.env.BETTER_METEO_DATA || 'data');
fs.mkdirSync(localData, { recursive: true });
const config = JSON.parse(fs.readFileSync('config.json', 'utf8'));
const city = config.cities[0];
for (const name of ['pressure.json', 'status.json', ...['forecast', 'ecmwf_ifs025_ensemble', 'ncep_gefs_seamless', 'rainfall', 'climate'].map(n => `cities/${city.id}/${n}.json`)]) {
  const source = path.join(originalData, name);
  if (fs.existsSync(source)) {
    fs.mkdirSync(path.dirname(path.join(localData, name)), { recursive: true });
    fs.copyFileSync(source, path.join(localData, name));
  }
}
process.env.BETTER_METEO_DATA = localData;
process.env.ASTRO_NODE_AUTOSTART = 'disabled';
let browser;
try {
  await import('../dist/server/entry.mjs');
  const manifestFile = fs.readdirSync('dist/server').find(n => n.startsWith('manifest_') && n.endsWith('.mjs'));
  const { manifest } = await import(`../dist/server/${manifestFile}`);
  const { NodeApp } = await import('astro/app/node');
  const app = new NodeApp(manifest);
  const readerCss = fs.readFileSync('src/styles/reader-landscape.css', 'utf8');
  const css = fs.readFileSync('src/styles/global.css', 'utf8') + '\n@media screen and (min-width:760px) and (max-height:700px){' + readerCss + '}';
  const routes = ['forecast', 'outlook', 'rainfall', 'wind', 'sources', 'pressure', 'pressure&motion=on', 'climate', ...['periods', 'charts', 'records', 'method'].map(v => `climate&view=${v}`)];
  const documents = [];
  async function saveRoute(route, prefix = '') {
    const response = await app.render(new Request(`http://localhost/?city=${city.id}&tab=${route}`));
    if (response.status !== 200) throw Error(`SSR failed: ${route}: ${response.status}`);
    let html = await response.text();
    if (html.includes('<script')) throw Error(`Unexpected browser JavaScript in ${route}`);
    html = html.replace(/<link\b[^>]*rel="stylesheet"[^>]*>/g, '').replace(/<meta\b[^>]*http-equiv="refresh"[^>]*>/g, '');
    html = html.replace('</head>', `<style>${css}${route.includes('layout=landscape')?readerCss:''}</style></head>`).replaceAll('src="/media/', `src="file://${originalData}/media/`);
    const file = path.join(temporary, `${prefix}${documents.length}.html`);
    fs.writeFileSync(file, html);
    documents.push({ route: prefix + route, file });
  }
  for (const route of routes) await saveRoute(route);
  for (const route of routes) await saveRoute(route+'&layout=landscape');
  // Full archives must fit too, not just today's partly collected climate cache.
  const climatePath = path.join(localData, `cities/${city.id}/climate.json`);
  if (fs.existsSync(climatePath)) {
    const fixture = JSON.parse(fs.readFileSync(climatePath, 'utf8'));
    for (const metric of fixture.data.metrics) Object.assign(metric, { actual: 20, normal: 18, difference: 2, available_days: 15, years: 30 });
    for (const metric of fixture.data.changes) Object.assign(metric, { old: 18, modern: 20, difference: 2, old_years: 30, modern_years: 30 });
    fixture.data.annual = Array.from({ length: 65 }, (_, i) => ({ year: 1961 + i, high: 17 + i / 30, low: 7 + i / 30, precipitation: 800 + i, rain: 700 + i, snow: 25 }));
    fs.writeFileSync(climatePath, JSON.stringify(fixture));
    for (const route of ['climate', 'climate&view=periods', 'climate&view=charts', 'climate&view=records', 'climate&view=records&page=6']) await saveRoute(route, 'full-archive-');
  }
  console.log(`Rendered ${documents.length} cached/fixture views successfully; no browser JavaScript.`);
  browser = spawn(process.env.CHROME || 'google-chrome', ['--headless', '--no-sandbox', '--disable-crashpad-for-testing', '--disable-gpu', '--disable-dev-shm-usage', '--disable-background-networking', '--no-first-run', '--remote-debugging-pipe', `--user-data-dir=${temporary}/chrome`], { stdio: ['ignore', 'ignore', 'pipe', 'pipe', 'pipe'] });
  let nextId = 0, buffer = '', stderr = '';
  const pending = new Map();
  const events = new Map();
  browser.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-2000); });
  browser.stdio[4].on('data', chunk => {
    buffer += chunk.toString();
    let end;
    while ((end = buffer.indexOf('\0')) >= 0) {
      const message = JSON.parse(buffer.slice(0, end));
      buffer = buffer.slice(end + 1);
      const eventKey = `${message.sessionId}:${message.method}`;
      if (events.has(eventKey)) { events.get(eventKey)(); events.delete(eventKey); }
      if (pending.has(message.id)) {
        const { resolve, reject, timer } = pending.get(message.id);
        clearTimeout(timer); pending.delete(message.id);
        message.error ? reject(Error(message.error.message)) : resolve(message.result);
      }
    }
  });
  const failPending = error => { for (const p of pending.values()) { clearTimeout(p.timer); p.reject(error); } pending.clear(); };
  browser.stdio[4].on('error', error => failPending(Error(`Chrome debugging pipe failed: ${error.message}. ${stderr}`)));
  browser.on('error', failPending);
  browser.on('exit', code => failPending(Error(`Chrome exited (${code}). ${stderr}`)));
  browser.stdio[3].on('error', failPending);
  function call(method, params = {}, sessionId) {
    return new Promise((resolve, reject) => {
      const id = ++nextId;
      const timer = setTimeout(() => { pending.delete(id); reject(Error(`Chrome timed out: ${method}. ${stderr}`)); }, 15000);
      pending.set(id, { resolve, reject, timer });
      browser.stdio[3].write(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }) + '\0');
    });
  }
  const { targetId } = await call('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await call('Target.attachToTarget', { targetId, flatten: true });
  const send = (method, params) => call(method, params, sessionId);
  await send('Page.enable');
  await send('Network.enable');
  await send('Network.setBlockedURLs', { urls: ['http://*', 'https://*', 'ws://*', 'wss://*'] });
  let failures = 0;
  for (const [width, height] of [[600, 800], [800, 600], [1366, 768], [1920, 1080]]) {
    await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
    for (const { route, file } of documents) {
      if (route.includes('layout=landscape') && (width!==800 || height!==600)) continue;
      const loaded = new Promise((resolve, reject) => {
        const timer = setTimeout(() => { events.delete(`${sessionId}:Page.loadEventFired`); reject(Error('Page load timed out')); }, 15000);
        events.set(`${sessionId}:Page.loadEventFired`, () => { clearTimeout(timer); resolve(); });
      });
      await Promise.all([send('Page.navigate', { url: `file://${file}` }),loaded]);
      const { result, exceptionDetails } = await send('Runtime.evaluate', { awaitPromise: true, returnByValue: true, expression: `(async()=>{
        if(document.readyState!=='complete') await new Promise(r=>window.addEventListener('load',r,{once:true}));
        await document.fonts.ready; await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
        const main=document.querySelector('main'), bounds=main.getBoundingClientRect();
        const bad=[...main.querySelectorAll('*')].filter(el=>{
          if(el.closest('svg')&&el.tagName.toLowerCase()!=='svg')return false;
          const r=el.getBoundingClientRect(); if(!r.width||!r.height)return false;
          return r.bottom>bounds.bottom+2||r.right>innerWidth+2||r.left < -2;
        }).map(el=>el.tagName+'.'+el.className).slice(0,5);
        const landscapeErrors=[];
        if(innerWidth===800&&innerHeight===600){
          const header=document.querySelector('header').getBoundingClientRect(), location=document.querySelector('.location-line').getBoundingClientRect();
          if(Math.abs(header.top-location.top)>2)landscapeErrors.push('Header must share a row with location');
          const current=main.querySelector('.current'), week=main.querySelector('.forecast-week');
          if(current&&week&&current.getBoundingClientRect().right>week.getBoundingClientRect().left)landscapeErrors.push('Weekly forecast must sit beside current conditions');
          const chart=main.querySelector('.outlook-chart');
          if(chart&&!chart.getBoundingClientRect().height)landscapeErrors.push('Outlook chart must remain visible');
          const map=main.querySelector('.pressure-map');
          if(map&&map.getBoundingClientRect().height<250)landscapeErrors.push('Pressure map must use landscape space');
        }
        return {width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,bad,landscapeErrors};
      })()` });
      const metrics = result?.value;
      if (exceptionDetails || !metrics || metrics.scrollWidth > width + 2 || metrics.scrollHeight > height + 2 || metrics.bad.length || metrics.landscapeErrors.length) {
        failures++; console.error(`FAIL ${width}×${height} ${route}`, metrics || exceptionDetails);
      } else console.log(`PASS ${width}×${height} ${route}`);
    }
  }
  if (failures) throw Error(`${failures} layouts overflow. Content must fit without clipping or scrollbars.`);
} catch (error) {
  console.error(`Layout checks could not complete: ${error.message}`);
  console.error('Chrome must be installed and allowed to start in this environment.');
  process.exitCode = 1;
} finally {
  if (browser && browser.exitCode === null) browser.kill();
  fs.rmSync(temporary, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
}
