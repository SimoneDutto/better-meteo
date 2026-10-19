"""Pure weather transformations. Missing members/days never become zero rain."""
from collections import defaultdict
from datetime import date, datetime, timedelta
import math
import statistics

def quantile(values, fraction):
    values = sorted(v for v in values if v is not None and math.isfinite(v))
    if not values: return None
    position = (len(values)-1)*fraction
    lo, hi = math.floor(position), math.ceil(position)
    return values[lo] + (values[hi]-values[lo])*(position-lo)

def ensemble_days(payload):
    hourly = payload['hourly']
    groups = defaultdict(list)
    for i,t in enumerate(hourly['time']): groups[t[:10]].append(i)
    # Open-Meteo returns member 0 under the unsuffixed name.
    members = [k.removeprefix('temperature_2m') for k in hourly if k=='temperature_2m' or k.startswith('temperature_2m_member')]
    output = []
    for day,indices in groups.items():
        # UTC hourly sampling covers a full day only with 24 valid values.
        maxima, minima, rains = [], [], []
        for suffix in members:
            temps = [hourly['temperature_2m'+suffix][i] for i in indices]
            precip = [hourly.get('precipitation'+suffix, [None]*len(hourly['time']))[i] for i in indices]
            if len(temps)==24 and all(v is not None for v in temps):
                maxima.append(max(temps)); minima.append(min(temps))
            if len(precip)==24 and all(v is not None for v in precip): rains.append(sum(precip))
        low,median,high = (quantile(maxima,p) for p in (.1,.5,.9))
        spread = high-low if high is not None and low is not None else None
        output.append({'date':day,'low':low,'median':median,'high':high,'night':quantile(minima,.5),
            'members':len(maxima),'rain_members':len(rains),
            'rain_probability':round(100*sum(p>=1 for p in rains)/len(rains)) if rains else None,
            'rain_median':quantile(rains,.5),
            'agreement':None if spread is None else 'High' if spread<=3 else 'Medium' if spread<=6 else 'Low'})
    return output

def rainfall_summary(series, today, source):
    """Latest 15 completed local dates, plus a fixed 1991–2020 same-window baseline."""
    end = today-timedelta(days=1)
    start = end-timedelta(days=14)
    days = [{'date':(start+timedelta(days=i)).isoformat(), 'mm':series.get((start+timedelta(days=i)).isoformat())} for i in range(15)]
    complete = all(d['mm'] is not None for d in days)
    normal = []
    # Match end month/day and work backwards; handles year boundaries and leap years.
    for year in range(1991,2021):
        try: historic_end = end.replace(year=year)
        except ValueError: historic_end = date(year,2,28)
        vals = [series.get((historic_end-timedelta(days=i)).isoformat()) for i in range(15)]
        if all(v is not None for v in vals): normal.append(sum(vals))
    baseline = statistics.mean(normal) if len(normal)>=20 else None
    total = sum(d['mm'] for d in days) if complete else None
    periods = []
    for length in (30,90,180,365):
        vals = [series.get((end-timedelta(days=i)).isoformat()) for i in range(length)]
        periods.append({'days':length, 'mm':sum(vals) if all(v is not None for v in vals) else None})
    return {'source':source,'days':days,'total':total,'complete_days':sum(d['mm'] is not None for d in days),
        'normal':baseline,'normal_years':len(normal),'anomaly_percent':(100*(total/baseline-1)) if total is not None and baseline else None,
        'last_available':max(series,default=None),'periods':periods}
