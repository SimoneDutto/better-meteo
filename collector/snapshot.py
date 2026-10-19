"""A device can fetch a complete 600×800 PNG without running a browser."""
import json
import os
from datetime import datetime

def render_snapshot(city, root):
    os.environ.setdefault('MPLCONFIGDIR','/tmp/better-meteo-mpl')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    path = root/'cities'/city['id']/'forecast.json'
    if not path.exists(): raise ValueError('No forecast cache; no image generated')
    value = json.loads(path.read_text())
    daily = value['data']['daily']
    fig = plt.figure(figsize=(6,8),dpi=100,facecolor='white')
    fig.text(.08,.93,city['name'].upper(),fontsize=28,weight='bold')
    fig.text(.08,.89,'FORECAST / NEXT 7 DAYS',fontsize=11)
    for i,day in enumerate(daily['time'][:7]):
        y = .81-i*.085
        display = lambda v: '—' if v is None else f'{v:.0f}'
        fig.text(.08,y,datetime.fromisoformat(day).strftime('%a %d'),fontsize=17)
        fig.text(.36,y,f'{display(daily["temperature_2m_min"][i])} / {display(daily["temperature_2m_max"][i])} °C',fontsize=17)
        fig.text(.73,y,f'{display(daily["precipitation_sum"][i])} mm',fontsize=15)
    age = (datetime.now().astimezone()-datetime.fromisoformat(value['updated'])).total_seconds()/3600
    fig.text(.08,.14,f'Cached {age:.0f}h ago · {value["updated"][:16]} UTC',fontsize=10)
    fig.text(.08,.1,'STALE: last successful forecast' if age>12 else 'Open-Meteo · CC BY 4.0',fontsize=10)
    fig.text(.08,.06,'better meteo / model forecast',fontsize=10)
    target = root/'media'/f'{city["id"]}-dashboard.png'
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary = target.with_suffix('.tmp')
    fig.savefig(temporary,format='png',facecolor='white'); plt.close(fig)
    temporary.replace(target)
