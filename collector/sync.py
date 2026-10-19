"""Opt-in sync: python -m collector sync. No network on import, build, or page view."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode

from .policy import Client, MIB, collector_lock, decode_json
from .weather import ensemble_days, rainfall_summary

PROJECT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('BETTER_METEO_DATA', PROJECT/'data'))
CONFIG = PROJECT/'config.json'

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,allow_nan=False))
    temporary.replace(path)

def fetch_json(client, host, path, params, ttl, units=25):
    return decode_json(client.get('openmeteo',f'https://{host}{path}?'+urlencode(params),ttl,8*MIB,units,validator=decode_json))

def save_city(city, section, value):
    write_json(DATA/'cities'/city['id']/f'{section}.json', {'updated':datetime.now(timezone.utc).isoformat(),'data':value})

def forecast(client, city):
    value = fetch_json(client,'api.open-meteo.com','/v1/forecast', {
        'latitude':city['latitude'],'longitude':city['longitude'],'timezone':city['timezone'],'forecast_days':16,
        'current':'temperature_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m',
        'daily':'weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum,precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant',
        'hourly':'temperature_2m,precipitation,snowfall,wind_speed_10m,wind_direction_10m,wind_gusts_10m',
    },6*3600,50)
    if not value.get('daily',{}).get('time'): raise ValueError('Forecast has no daily data')
    save_city(city,'forecast',value)

def ensemble(client, city, model):
    payload = fetch_json(client,'ensemble-api.open-meteo.com','/v1/ensemble', {
        'latitude':city['latitude'],'longitude':city['longitude'],'timezone':city['timezone'],
        'forecast_days':15,'models':model,'hourly':'temperature_2m,precipitation',
    },6*3600)
    days = ensemble_days(payload)
    if not any(d['members'] for d in days): raise ValueError('No complete ensemble days returned')
    save_city(city,model,days)

def global_rain(client, city):
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo(city['timezone'])).date()
    payload = fetch_json(client,'archive-api.open-meteo.com','/v1/archive',{
        'latitude':city['latitude'],'longitude':city['longitude'],'timezone':city['timezone'],
        'start_date':(today-timedelta(days=365)).isoformat(),'end_date':(today-timedelta(days=1)).isoformat(),
        'daily':'precipitation_sum','models':'ecmwf_ifs',
    },86400,50)
    daily = payload['daily']
    series = dict(zip(daily['time'],daily['precipitation_sum']))
    save_city(city,'rainfall',rainfall_summary(series,today,'Open-Meteo ECMWF IFS · model estimates, not rain-gauge observations'))

def pressure(client, config):
    from .pressure import collect
    collect(client,config,fetch_json,write_json,DATA)


def climate(client, city):
    from zoneinfo import ZoneInfo
    from .climate import collect
    collect(client,city,DATA,fetch_json,write_json,save_city,datetime.now(ZoneInfo(city['timezone'])).date())


def print_plan(config):
    from .pressure import plan
    maps=plan(config)
    map_requests=len(maps['batches'])
    daily_requests=12+1+2*map_requests
    daily_units=400+50+2*maps['total_units']
    print('OFFLINE PLAN — no requests sent; all data comes from Open-Meteo.')
    print(f'Fresh full sync for one city: {5+map_requests} HTTP requests; {152+maps["total_units"]} reserved quota units.')
    print(f'Normal day for one city: {daily_requests+1} HTTP requests; {daily_units+2} reserved quota units.')
    print(f'Maps: {len(maps["points"])} points at {maps["spacing"]}°, {map_requests} requests / 12h, {maps["total_units"]} units per collection.')
    print('Forecast + two ensembles: 3 requests / 6h, 100 reserved units. Rainfall: 1 request / 24h, 50 reserved units.')
    print('Climate sync: recent ERA5 only, 1 request / 24h, 2 units. Historical files are read locally, never downloaded by sync.')
    print('Explicit climate-history command: missing historical batches only, up to 79 units each; 1991–2020 first. Shares all budgets/cooldowns; no account/key needed.')
    print('Application caps: 24 HTTP attempts/day; 400 units/minute, 1,500/hour, 1,000/day, 30,000/month; 40 MiB/day.')
    print('Open-Meteo published free allowance: 600 counted calls/minute, 5,000/hour, 10,000/day, 300,000/month; no uptime guarantee.')
    print('Reserved units are conservative estimates, not upstream-reported usage. Extra cities share these same budgets.')


def print_status(client):
    import time
    state=client.status()
    day=time.strftime('%Y-%m-%d',time.gmtime(client.now()))
    today=next((row for row in state['usage'] if row['day']==day and row['provider']=='openmeteo'),{})
    print(f'Open-Meteo today ({day} UTC): {today.get("requests",0)}/24 HTTP attempts; {today.get("units",0):g}/1000 reserved quota units; {today.get("bytes",0)/MIB:.2f}/40 MiB.')
    print(f'Month {state["month"]}: {state["monthly_units"]:g}/30000 reserved quota units. These are estimates, not an upstream usage report.')
    for provider in state['providers']:
        if provider['name']=='openmeteo' and provider['blocked']>client.now():
            print('Open-Meteo paused until '+time.strftime('%Y-%m-%d %H:%M UTC',time.gmtime(provider['blocked'])))
    blocked=max((p['blocked'] for p in state['providers'] if p['name']=='openmeteo'),default=0)
    for job in state['jobs']:
        if job['name']=='pressure' or job['name'].endswith(':rainfall'):
            print(f'Retired job {job["name"]}: disabled; history preserved')
            continue
        label=job['name']
        eligible=max(job['due'],blocked)
        due=time.strftime('%Y-%m-%d %H:%M UTC',time.gmtime(eligible))
        schedule=f'waiting until {due}' if eligible>client.now() else 'eligible on the next sync'
        print(f'{label}: {schedule}')
        if job['error']:
            print(f'  Saved failure from the previous attempt: {job["error"]}. This status line makes no request; it remains until a successful update.')
    retired=[u for u in state['usage'] if u['provider']!='openmeteo']
    if retired: print(f'Preserved {sum(u["requests"] for u in retired)} historical attempts to retired providers; those services are no longer allowlisted.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['sync','status','plan','add-city','render','climate-history'])
    parser.add_argument('--city',default='turin',help='Existing city id for sync/render; new id for add-city')
    parser.add_argument('--name',help='City search text (only add-city makes one geocoding request)')
    parser.add_argument('--match',type=int,default=None,help='Select a result from the cached geocoding response')
    parser.add_argument('--only',choices=['forecast','ensembles','rainfall','pressure','climate'])
    parser.add_argument('--json',action='store_true',help='Machine-readable status instead of the human summary')
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    if args.command=='plan':
        print_plan(config)
        return
    with collector_lock(DATA):
        client = Client(DATA/'http',config.get('contact',''))
        if args.command=='status':
            if args.json: print(json.dumps(client.status(),indent=2))
            else: print_status(client)
            return
        if args.command=='add-city':
            if not re.fullmatch(r'[a-z0-9-]{1,40}',args.city): raise ValueError('Use a short lowercase city id')
            if not args.name: parser.error('--name is required')
            result = fetch_json(client,'geocoding-api.open-meteo.com','/v1/search',{'name':args.name,'count':5,'language':'en','format':'json'},30*86400,1)
            matches = result.get('results',[])
            if args.match is None:
                for i,item in enumerate(matches): print(i,item['name'],item.get('admin1',''),item.get('country',''),item['latitude'],item['longitude'])
                print('Repeat with --match INDEX to choose from this cached search. No existing city is overwritten.'); return
            if args.match<0 or args.match>=len(matches): raise ValueError('Invalid match index')
            if any(c['id']==args.city for c in config['cities']): raise ValueError('City id already exists')
            item = matches[args.match]
            config['cities'].append({'id':args.city,'name':item['name'],'latitude':item['latitude'],'longitude':item['longitude'],'timezone':item['timezone']})
            write_json(CONFIG,config); return
        city = next((c for c in config['cities'] if c['id']==args.city),None)
        if city is None: raise ValueError('Unknown city id')
        if args.command=='render':
            from .snapshot import render_snapshot
            render_snapshot(city,DATA); return
        before=(client.sent_requests,client.reserved_units)
        if args.command=='climate-history':
            from zoneinfo import ZoneInfo
            from .climate import download_history
            import time
            permit_path = DATA/'climate-download-permit.json'
            permit = json.loads(permit_path.read_text()) if permit_path.exists() else {}
            permitted = (permit.get('day') == time.strftime('%Y-%m-%d',time.gmtime(client.now()))
                         and permit.get('city') == city['id'] and not permit.get('completed',False))
            if permitted:
                client.archive_allowance = permit
                print('Owner-approved archive allowance for today only: 40 total HTTP attempts/day, 2,500 units/day and 2,500/hour. Normal sync retains its original caps. All usage remains counted; cooldowns and Retry-After still apply.')
            try:
                remaining = download_history(client,city,DATA,fetch_json,write_json,datetime.now(ZoneInfo(city['timezone'])).date())
                if permitted and remaining == 0:
                    write_json(permit_path,{**permit,'completed':True})
            finally:
                client.archive_allowance = None
            if permitted:
                print('Normal sync caps restored. Shared usage is preserved; ordinary updates may wait until the next UTC day after this download.')
            write_json(DATA/'status.json',client.status())
            print(f'Archive run: {client.sent_requests-before[0]} HTTP requests sent; {client.reserved_units-before[1]:g} quota units reserved.')
            print_status(client)
            return
        if args.only in (None,'forecast'): client.run(f'{city["id"]}:forecast',6*3600,lambda:forecast(client,city))
        if args.only in (None,'ensembles'):
            for model in ('ecmwf_ifs025_ensemble','ncep_gefs_seamless'):
                client.run(f'{city["id"]}:{model}',6*3600,lambda m=model:ensemble(client,city,m))
        if args.only in (None,'rainfall'):
            client.run(f'{city["id"]}:rainfall:openmeteo:v1',86400,lambda:global_rain(client,city))
        if args.only in (None,'pressure'):
            client.run('pressure:openmeteo:v1',12*3600,lambda:pressure(client,config))
        if args.only in (None,'climate'):
            client.run(f'{city["id"]}:climate:era5:v1',86400,lambda:climate(client,city))
        client.prune()
        write_json(DATA/'status.json',client.status())
        requests=client.sent_requests-before[0]
        units=client.reserved_units-before[1]
        print(f'Sync finished: {requests} HTTP requests sent; {units:g} quota units reserved. Cached/skipped jobs send zero requests.')
        print_status(client)
