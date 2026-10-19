"""Consistent ERA5 history and pure, conservative climate comparisons."""
from datetime import date, timedelta
import math
import statistics

FIELDS = {'high': 'temperature_2m_max', 'low': 'temperature_2m_min',
          'precipitation': 'precipitation_sum', 'rain': 'rain_sum', 'snow': 'snowfall_sum'}
UNITS = {'high': '°C', 'low': '°C', 'precipitation': 'mm', 'rain': 'mm', 'snow': 'cm'}


def dates(start, end):
    return [start + timedelta(days=i) for i in range((end-start).days+1)]


def batches(today):
    # Prioritise the modern normal; fixed blocks remain reusable next year.
    available = today-timedelta(days=6)
    last_year = available.year if available.month == 12 and available.day == 31 else available.year-1
    starts = list(range(1991, 2021, 3)) + list(range(1961, 1991, 3)) + list(range(2021, last_year+1, 3))
    return [(date(y, 1, 1), date(min(y+2, last_year), 12, 31)) for y in starts]


def reservation(start, end):
    # Round the published duration multiplier up; five variables (<10), one location/model.
    return math.ceil(((end-start).days+1)/14)


def parse_daily(payload, start, end):
    daily = payload['daily']
    times = daily['time']
    if times != [d.isoformat() for d in dates(start, end)]:
        raise ValueError('Climate response has missing, duplicate or unexpected dates')
    for key, field in FIELDS.items():
        if payload.get('daily_units', {}).get(field) != UNITS[key]:
            raise ValueError(f'Unexpected climate unit for {field}')
        values = daily[field]
        if len(values) != len(times):
            raise ValueError('Climate response has inconsistent array lengths')
        for v in values:
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                                  or (key in ('precipitation', 'rain', 'snow') and v < 0)):
                raise ValueError('Invalid climate value')
    return {day: {key: daily[field][i] for key, field in FIELDS.items()} for i, day in enumerate(times)}


def aggregate(series, days, key):
    values = [series.get(d.isoformat(), {}).get(key) for d in days]
    if not values or any(v is None for v in values):
        return None
    return statistics.mean(values) if key in ('high', 'low') else sum(values)


def window_end(series, target):
    """Use the latest genuinely complete 15-day window; never bridge missing days."""
    for offset in range(14):
        end = target-timedelta(days=offset)
        window = dates(end-timedelta(days=14), end)
        if all(aggregate(series, window, key) is not None for key in FIELDS):
            return end
    return target


def summary(series, end, today, completed, total_batches):
    start = end-timedelta(days=14)
    window = dates(start, end)
    metrics = []
    for key in FIELDS:
        actual = aggregate(series, window, key)
        available = [series.get(d.isoformat(), {}).get(key) for d in window]
        available = [v for v in available if v is not None]
        partial = (statistics.mean(available) if key in ('high', 'low') else sum(available)) if available else None
        reference = []
        for year in range(1991, 2021):
            # Fixed 15-day window ending on the same month/day; Feb 29 maps to Feb 28.
            try:
                historic_end = end.replace(year=year)
            except ValueError:
                historic_end = date(year, 2, 28)
            value = aggregate(series, dates(historic_end-timedelta(days=14), historic_end), key)
            if value is not None:
                reference.append(value)
        # Require 24/30 complete windows; never treat missing days as dry days.
        normal = statistics.mean(reference) if len(reference) >= 24 else None
        metrics.append({'key': key, 'unit': UNITS[key], 'actual': actual, 'normal': normal,
                        'available_days': len(available), 'available_value': partial,
                        'years': len(reference), 'difference': actual-normal if actual is not None and normal is not None else None,
                        'percent': 100*(actual/normal-1) if actual is not None and normal else None,
                        'p10': percentile(reference, .1) if normal is not None else None,
                        'p90': percentile(reference, .9) if normal is not None else None})
    annual = []
    for year in range(1961, today.year):
        days = dates(date(year, 1, 1), date(year, 12, 31))
        annual.append({'year': year, **{key: aggregate(series, days, key) for key in FIELDS}})
    changes = []
    for key in FIELDS:
        groups = [[row[key] for row in annual if lo <= row['year'] <= hi and row[key] is not None]
                  for lo, hi in ((1961, 1990), (1991, 2020))]
        means = [statistics.mean(g) if len(g) >= 24 else None for g in groups]
        changes.append({'key': key, 'unit': UNITS[key], 'old': means[0], 'modern': means[1],
                        'old_years': len(groups[0]), 'modern_years': len(groups[1]),
                        'difference': means[1]-means[0] if all(v is not None for v in means) else None})
    return {'source': 'Open-Meteo ERA5 · reanalysis estimates · approximately 25 km',
            'start': start.isoformat(), 'end': end.isoformat(), 'lag_days': (today-end).days,
            'metrics': metrics, 'annual': annual, 'changes': changes,
            'days': [{'date': d.isoformat(), **{k: series.get(d.isoformat(), {}).get(k) for k in FIELDS}} for d in window],
            'completed_batches': completed, 'total_batches': total_batches}


def percentile(values, fraction):
    values = sorted(values)
    position = (len(values)-1)*fraction
    lo, hi = math.floor(position), math.ceil(position)
    return values[lo]+(values[hi]-values[lo])*(position-lo)


def archive_root(city, data, write):
    import json
    root = data/'cities'/city['id']/'climate-archive-v1'
    root.mkdir(parents=True, exist_ok=True)
    identity = {k: city[k] for k in ('latitude', 'longitude', 'timezone')}
    path = root/'location.json'
    if path.exists() and json.loads(path.read_text()) != identity:
        raise ValueError('Climate location changed; use a new city id to preserve consistent history')
    write(path, identity)
    return root, identity


def request_days(client, identity, fetch, start, end, ttl):
    params = {**identity, 'timezone': 'GMT', 'models': 'era5', 'daily': ','.join(FIELDS.values()),
              'temperature_unit': 'celsius', 'precipitation_unit': 'mm',
              'start_date': start.isoformat(), 'end_date': end.isoformat()}
    payload = fetch(client, 'archive-api.open-meteo.com', '/v1/archive', params,
                    ttl, reservation(start, end))
    return parse_daily(payload, start, end)


def saved_summary(root, today):
    import json
    recent_path = root/'recent.json'
    recent = json.loads(recent_path.read_text()) if recent_path.exists() else {}
    series = {}
    completed = 0
    blocks = batches(today)
    for a, b in blocks:
        path = root/f'{a}_{b}.json'
        if path.exists():
            series.update(json.loads(path.read_text()))
            completed += 1
    series.update(recent)
    target = date.fromisoformat(max(recent)) if recent else today-timedelta(days=6)
    return summary(series, window_end(recent, target), today, completed, len(blocks))


def collect(client, city, data, fetch, write, save, today):
    """Normal sync updates recent weather only; history is strictly read from disk."""
    root, identity = archive_root(city, data, write)
    end = today-timedelta(days=6)
    recent = request_days(client, identity, fetch, end-timedelta(days=27), end, 86400)
    write(root/'recent.json', recent)
    save(city, 'climate', saved_summary(root, today))


def download_history(client, city, data, fetch, write, today):
    """Explicit one-time archive fill. Existing files, limits and failures stop requests."""
    import json
    from .policy import POLICIES
    root, identity = archive_root(city, data, write)
    missing = [(a, b) for a, b in batches(today) if not (root/f'{a}_{b}.json').exists()]
    print(f'Climate archive: {len(missing)} missing batches; {sum(reservation(a,b) for a,b in missing)} estimated quota units. Normal sync does not download history.')
    climate_path = root.parent/'climate.json'
    previous = json.loads(climate_path.read_text()) if climate_path.exists() else {}
    try:
        for start, end in missing:
            units = reservation(start, end)
            # Wait BEFORE attempting a request if the rolling minute is full.
            # This is pacing, not retrying an error; the Client still enforces every cap.
            now = client.now()
            attempts = client.db.execute('SELECT at,units FROM attempts WHERE provider=? AND at>? ORDER BY at',
                                         ('openmeteo', now-60)).fetchall()
            excess = sum(u for _, u in attempts)+units-POLICIES['openmeteo']['minute_units']
            if excess > 0:
                for at, used in attempts:
                    excess -= used
                    if excess <= 0:
                        wait = max(0, at+60-now+.05)
                        print(f'Climate archive: pacing for {wait:.1f}s before the next batch.')
                        client.sleep(wait)
                        break
            def download(a=start, b=end):
                values = request_days(client, identity, fetch, a, b, 86400)
                if any(v is None for row in values.values() for v in row.values()):
                    raise ValueError('Historical batch has missing values; previous archive retained')
                write(root/f'{a}_{b}.json', values)
            if not client.run(f'{city["id"]}:climate-history:era5:{start}:{end}',86400,download):
                print('Climate archive stopped at the first deferred/failed batch; no further requests sent.')
                break
            print(f'Climate archive saved: {start} through {end}')
    finally:
        # Archive work never makes old recent-weather data look freshly collected.
        write(climate_path, {**previous, 'updated': previous.get('updated'), 'data': saved_summary(root, today)})
    remaining = sum(not (root/f'{a}_{b}.json').exists() for a,b in batches(today))
    print(f'Climate archive: {remaining} batches remaining.' if remaining else 'Climate archive complete. All historical data is local; normal sync will not redownload it.')
    return remaining
