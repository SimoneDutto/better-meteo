import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import vm from 'node:vm';
import { basePath, pagePath, pageUrl, pageStates } from '../src/lib/links.mjs';

const project=path.resolve(import.meta.dirname,'..');

test('optional display diagnostic reports CSS viewport separately from screen pixels',()=>{
  const html=fs.readFileSync(path.join(project,'public/viewport.html'),'utf8');
  const source=html.match(/<script>([\s\S]*?)<\/script>/)[1];
  assert.ok(!/\b(?:const|let)\b|=>/.test(source),'Diagnostic must use legacy-compatible JavaScript');
  const cells={}, events={};
  const window={innerWidth:800,innerHeight:600,devicePixelRatio:2,screen:{width:1200,height:1600},addEventListener:(event,callback)=>events[event]=callback};
  const root={clientWidth:800,clientHeight:600};
  const document={documentElement:root,getElementById:id=>cells[id]||(cells[id]={}),createElement:()=>({style:{setProperty:function(k,v){this[k]=v;},getPropertyValue:function(k){return this[k]||'';}}})};
  new vm.Script(source).runInNewContext({window,document,navigator:{userAgent:'offline fixture'}});
  assert.equal(cells.window.textContent,'800 × 600');
  assert.equal(cells.document.textContent,'800 × 600');
  assert.equal(cells.screen.textContent,'1200 × 1600');
  window.innerWidth=600;window.innerHeight=800;events.orientationchange();
  assert.equal(cells.window.textContent,'600 × 800');
});

test('static links handle project prefixes, climate pagination, animation and rotation',()=>{
  assert.equal(pageUrl('/weather/',{city:'turin',tab:'climate',view:'records',page:'2',cycle:'1'}),'/weather/turin/climate/records/page-2/rotate/');
  assert.equal(pageUrl('/',{city:'turin',tab:'pressure',step:'36',motion:'on'}),'/turin/pressure/at-36/loop/');
  assert.equal(pageUrl('/weather',{city:'turin',tab:'climate',view:'overview'}),'/weather/turin/climate/');
  assert.throws(()=>basePath('/../secrets/'));
  assert.throws(()=>pagePath({city:'../secrets',tab:'forecast'}));
});

test('all cities and complete archive record pages have unique URLs',()=>{
  const states=pageStates({cities:[{id:'turin'},{id:'cuneo'}]}, {data:{frames:[{step:12},{step:36}],animation:'loop.gif'}},()=>({data:{annual:Array.from({length:65},()=>({high:20}))}}));
  const paths=states.map(pagePath);
  assert.equal(new Set(paths).size,paths.length);
  for(const city of ['turin','cuneo']){
    assert.ok(paths.includes(`${city}/climate/records/page-6/`));
    assert.ok(paths.includes(`${city}/pressure/at-36/loop/rotate/`));
    assert.ok(paths.includes(`${city}/sources/`));
  }
});

test('offline export resolves every link and keeps private caches out of the site',{skip:!fs.existsSync(path.join(project,'dist/server/entry.mjs'))},()=>{
  const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'better-meteo-static-test-'));
  try{
    const city=JSON.parse(fs.readFileSync(path.join(project,'config.json'),'utf8')).cities[0].id;
    const data=path.join(temporary,'data'), output=path.join(temporary,'site');
    function json(file,value){const target=path.join(data,file);fs.mkdirSync(path.dirname(target),{recursive:true});fs.writeFileSync(target,JSON.stringify(value));}
    const metrics=['high','low','precipitation','rain','snow'].map(key=>({key,unit:key==='snow'?'cm':key==='high'||key==='low'?'°C':'mm',actual:20,normal:18,difference:2,years:30,available_days:15}));
    json(`cities/${city}/climate.json`,{updated:'2026-10-19T12:00:00Z',data:{start:'2026-09-28',end:'2026-10-12',lag_days:7,metrics,changes:metrics.map(m=>({...m,old:18,modern:20,old_years:30,modern_years:30})),completed_batches:22,total_batches:22,annual:Array.from({length:65},(_,i)=>({year:1961+i,high:20+i/30,low:10,precipitation:800,rain:750,snow:5}))}});
    json('pressure.json',{updated:'2026-10-19T12:00:00Z',data:{provider:'Open-Meteo',frames:[{step:12,day:1,valid:'2026-10-19T12:00:00Z',image:'test-map-a.png'},{step:36,day:2,valid:'2026-10-20T12:00:00Z',image:'test-map-b.png'}],animation:'test-loop.gif'}});
    fs.mkdirSync(path.join(data,'media'),{recursive:true});
    for(const name of ['test-map-a.png','test-map-b.png','test-loop.gif','old-map.png',`${city}-dashboard.png`])fs.writeFileSync(path.join(data,'media',name),'offline fixture');
    json('http/policy.sqlite',{private:'must never be exported'});
    const env={...process.env,BETTER_METEO_DATA:data,PAGES_OUTPUT:output,PAGES_BASE:'/weather-test/'};
    const exportSite=()=>spawnSync(process.execPath,['scripts/export-pages.mjs'],{cwd:project,env,encoding:'utf8'});
    let result=exportSite();assert.equal(result.status,0,result.stderr);
    const climate=fs.readFileSync(path.join(output,`${city}/climate/index.html`),'utf8');
    assert.ok(climate.includes('/weather-test/'));
    assert.ok(climate.includes('Saved 19 Oct 2026'));
    assert.ok(!climate.includes('<script'));
    assert.ok(!climate.includes('<form'));
    assert.ok(!climate.includes('?tab='));
    assert.ok(fs.existsSync(path.join(output,`${city}/pressure/at-36/loop/rotate/index.html`)));
    const records=fs.readFileSync(path.join(output,`${city}/climate/records/page-0/index.html`),'utf8');
    assert.equal([...records.matchAll(/<tr>/g)].length,11);
    assert.ok(fs.existsSync(path.join(output,'media',`${city}-dashboard.png`)));
    assert.ok(!fs.existsSync(path.join(output,'http')));
    assert.ok(!fs.existsSync(path.join(output,'media/old-map.png')));
    assert.ok(fs.existsSync(path.join(output,'.nojekyll')));
    assert.ok(fs.existsSync(path.join(output,'viewport.html')));
    // The same export also supports a user site/custom domain at the root.
    env.PAGES_BASE='/';result=exportSite();assert.equal(result.status,0,result.stderr);
    const published=fs.readFileSync(path.join(output,'publication.json'),'utf8');
    // A missing current asset must leave the previous complete export intact.
    fs.unlinkSync(path.join(data,'media/test-map-a.png'));
    result=exportSite();assert.notEqual(result.status,0);
    assert.equal(fs.readFileSync(path.join(output,'publication.json'),'utf8'),published);
  }finally{fs.rmSync(temporary,{recursive:true,force:true});}
});
