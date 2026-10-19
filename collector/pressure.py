"""A deliberately coarse Open-Meteo map grid with bounded, precomputed cost."""
from datetime import datetime, timedelta, timezone
import math

VARIABLES = ('geopotential_height_500hPa','pressure_msl','temperature_850hPa')
BATCH_SIZE = 50

def axis(start, stop, step):
    result = [round(start+i*step,6) for i in range(math.floor((stop-start)/step)+1)]
    if result[-1]!=stop: result.append(stop)
    return result

def plan(config):
    west,east,south,north = config['map_extent']
    spacing = config.get('map_grid_degrees',4)
    if not all(math.isfinite(v) for v in (west,east,south,north,spacing)) or not (-180<=west<east<=180 and -90<=south<north<=90) or spacing<4:
        raise ValueError('Use a valid extent and map spacing >=4° to bound API usage')
    x,y = axis(west,east,spacing),axis(south,north,spacing)
    if len(x)*len(y)>200 or min(len(x),len(y))<2:
        raise ValueError('Map grid must contain 2–200 points with at least two per axis; no request sent')
    points = [(lat,lon) for lat in y for lon in x]
    batches = [points[i:i+BATCH_SIZE] for i in range(0,len(points),BATCH_SIZE)]
    # Pricing weights locations and windows beyond 14 days. Three fields <10.
    units = [math.ceil(len(batch)*15/14) for batch in batches]
    steps = config['map_steps']
    if not steps or len(steps)>15 or any(not isinstance(s,int) or s<0 or s>354 or s%6 for s in steps) or len(set(steps))!=len(steps) or steps!=sorted(steps):
        raise ValueError('Choose up to fifteen unique, ordered six-hour snapshots within the 15-day window (0–354h)')
    return {'x':x,'y':y,'points':points,'batches':batches,'units':units,'total_units':sum(units),'spacing':spacing}

def collect(client, config, fetch_json, save, root, now=None):
    from .maps import render_fields, render_animation, validate_dependencies
    validate_dependencies()
    import numpy as np
    grid = plan(config)
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
    samples = []
    for batch,units in zip(grid['batches'],grid['units']):
        payload = fetch_json(client,'api.open-meteo.com','/v1/forecast',{
            'latitude':','.join(str(p[0]) for p in batch),
            'longitude':','.join(str(p[1]) for p in batch),
            'models':'ecmwf_ifs025','hourly':','.join(VARIABLES),
            'timezone':'GMT','timeformat':'unixtime','temporal_resolution':'hourly_6',
            'start_date':reference.date().isoformat(),
            'end_date':(reference+timedelta(days=14)).date().isoformat(),
            # Unlike timezone, elevation is NOT broadcast over multiple locations.
            # Open-Meteo requires one value per coordinate, including `nan`.
            'cell_selection':'nearest','elevation':','.join('nan' for _ in batch),
        },12*3600,units)
        if isinstance(payload,dict) and len(batch)==1: payload=[payload]
        if not isinstance(payload,list) or len(payload)!=len(batch):
            raise ValueError('Pressure response location count mismatch')
        for item,(lat,lon) in zip(payload,batch):
            returned_lon=(item['longitude']+180)%360-180
            if abs(item['latitude']-lat)>.4 or abs(returned_lon-lon)>.4:
                raise ValueError('Pressure response grid order/coordinates mismatch')
            units_returned = item.get('hourly_units',{})
            if units_returned.get(VARIABLES[0])!='m' or units_returned.get(VARIABLES[1],'').lower()!='hpa' or units_returned.get(VARIABLES[2])!='°C':
                raise ValueError('Unexpected pressure variable units')
            hourly=item['hourly']
            if any(len(hourly[key])!=len(hourly['time']) for key in VARIABLES):
                raise ValueError('Pressure response time/value length mismatch')
            samples.append({t:tuple(hourly[key][i] for key in VARIABLES) for i,t in enumerate(hourly['time'])})
    frames,unavailable = [],[]
    for step in config['map_steps']:
        valid = reference+timedelta(hours=step)
        day = step//24+1
        values = [sample.get(int(valid.timestamp())) for sample in samples]
        if any(row is None or any(v is None or not math.isfinite(v) for v in row) for row in values):
            unavailable.append({'day':day,'valid':valid.isoformat(),'reason':'Incomplete model coverage'})
            continue
        values = np.asarray(values)
        shape = (len(grid['y']),len(grid['x']))
        fields = {key:(grid['x'],grid['y'],values[:,i].reshape(shape)) for i,key in enumerate(('height','pressure','temperature'))}
        filename=f'pressure-om-{reference:%Y%m%d}-{int(datetime.now(timezone.utc).timestamp())}-{step}.png'
        path=root/'media'/filename
        path.parent.mkdir(parents=True,exist_ok=True)
        render_fields(fields,path,config['map_extent'],valid)
        frames.append({'step':step,'day':day,'valid':valid.isoformat(),'image':filename})
    if not frames: raise ValueError('No complete pressure snapshots returned; retaining previous maps')
    animation=None
    if len(frames)>1:
        import hashlib
        digest=hashlib.sha256(('dated-daily-v1,'+','.join(f['image'] for f in frames)).encode()).hexdigest()[:12]
        animation=f'pressure-loop-{digest}.gif'
        labels=[f'DAY {f["day"]:02}/15  |  {datetime.fromisoformat(f["valid"]).astimezone(timezone.utc):%a %d %b %Y  |  %H:%M UTC}' for f in frames]
        render_animation([root/'media'/f['image'] for f in frames],root/'media'/animation,labels)
    save(root/'pressure.json',{'updated':datetime.now(timezone.utc).isoformat(),'data':{
        'provider':'Open-Meteo','model':'ECMWF IFS 0.25°','reference':reference.isoformat(),
        'grid_degrees':grid['spacing'],'grid_points':len(grid['points']),
        'quota_units':grid['total_units'],'frames':frames,'animation':animation,'unavailable':unavailable,'extent':config['map_extent']}})
    for path in (root/'media').glob('pressure-*'):
        if path.suffix in ('.png','.gif') and path.name not in {animation,*(f['image'] for f in frames)} and path.stat().st_mtime<datetime.now().timestamp()-7*86400:
            path.unlink()
