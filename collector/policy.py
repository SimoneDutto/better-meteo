"""The sole network boundary. Budgets count attempts, including failed requests."""
import contextlib
import email.utils
import fcntl
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.request

MIB = 1024 * 1024
POLICIES = {
    'openmeteo': {'requests': 24, 'bytes': 40*MIB, 'units': 1000,
        'minute_units': 400, 'hour_units': 1500, 'month_units': 30000, 'gap': 5, 'cooldown': 6*3600},
}
HOSTS = {
    'openmeteo': {'api.open-meteo.com', 'ensemble-api.open-meteo.com', 'archive-api.open-meteo.com', 'geocoding-api.open-meteo.com'},
}

class Deferred(RuntimeError):
    pass

def decode_json(body):
    """Reject streaming-error text before it can replace a valid cached response."""
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError) as exc:
        explanation = ' '.join(body[:240].decode('utf-8',errors='replace').split())[:180]
        raise ValueError(f'Open-Meteo returned invalid JSON ({len(body)} bytes): {explanation or "empty response"}') from exc
    if not isinstance(value,(dict,list)):
        raise ValueError('Open-Meteo returned an unexpected JSON type')
    if isinstance(value,dict) and value.get('error') is True:
        raise ValueError('Open-Meteo error: '+str(value.get('reason','unspecified error'))[:180])
    return value

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # redirects need review; no hidden/unbudgeted requests

@contextlib.contextmanager
def collector_lock(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root/'collector.lock').open('w') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Deferred('Another collector is already running')
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)

class Client:
    def __init__(self, root, contact='', now=time.time, sleep=time.sleep, opener=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root/'policy.sqlite')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS usage(day TEXT, provider TEXT, requests INTEGER, bytes INTEGER, units REAL, PRIMARY KEY(day,provider));
          CREATE TABLE IF NOT EXISTS provider(name TEXT PRIMARY KEY, last REAL, blocked REAL DEFAULT 0, error TEXT);
          CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, fetched REAL, etag TEXT, modified TEXT);
          CREATE TABLE IF NOT EXISTS jobs(name TEXT PRIMARY KEY, due REAL, updated REAL, error TEXT);
          CREATE TABLE IF NOT EXISTS attempts(at REAL, provider TEXT, units REAL);
        ''')
        self.now, self.sleep = now, sleep
        self.sent_requests, self.reserved_units = 0, 0
        self.archive_allowance = None
        self.opener = opener or urllib.request.build_opener(NoRedirect()).open
        self.agent = 'BetterMeteo/0.1 personal cached dashboard' + (f' ({contact})' if contact else '')

    def status(self):
        jobs = [dict(zip(('name','due','updated','error'), row)) for row in self.db.execute('SELECT * FROM jobs')]
        usage = [dict(zip(('day','provider','requests','bytes','units'), row)) for row in self.db.execute('SELECT * FROM usage ORDER BY day DESC LIMIT 21')]
        providers = [dict(zip(('name','last','blocked','error'), row)) for row in self.db.execute('SELECT * FROM provider')]
        month = time.strftime('%Y-%m',time.gmtime(self.now()))
        monthly_units = self.db.execute('SELECT COALESCE(SUM(units),0) FROM usage WHERE provider=? AND day LIKE ?',('openmeteo',month+'-%')).fetchone()[0]
        return {'jobs': jobs, 'usage': usage, 'providers': providers, 'budgets': POLICIES,
            'month':month,'monthly_units':monthly_units,'quota_note':'Units are conservative reservations, not an upstream usage report.'}

    def prune(self):
        # Preserve the 30-day geocoder cache and every active job/cooldown/budget.
        # Only immutable response bodies older than 45 days are discarded.
        rows = self.db.execute('SELECT key FROM cache WHERE fetched<?',(self.now()-45*86400,)).fetchall()
        for (key,) in rows:
            (self.root/key).unlink(missing_ok=True)
            self.db.execute('DELETE FROM cache WHERE key=?',(key,))
        self.db.execute('DELETE FROM attempts WHERE at<?',(self.now()-86400,))
        self.db.commit()

    def run(self, name, interval, action):
        row = self.db.execute('SELECT due FROM jobs WHERE name=?', (name,)).fetchone()
        if row and row[0] > self.now():
            return False
        # Reserve the next slot BEFORE work. A crash never causes an immediate retry.
        self.db.execute('INSERT INTO jobs VALUES(?,?,NULL,NULL) ON CONFLICT(name) DO UPDATE SET due=excluded.due', (name,self.now()+interval))
        self.db.commit()
        try:
            action()
        except Exception as exc:
            self.db.execute('UPDATE jobs SET error=? WHERE name=?', (str(exc),name))
            self.db.commit()
            print(f'{name}: retained previous data; {exc}')
            return False
        self.db.execute('UPDATE jobs SET updated=?,error=NULL WHERE name=?', (self.now(),name))
        self.db.commit()
        return True

    def get(self, provider, url, ttl, max_bytes, units=1, headers=None, validator=None):
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        if provider not in HOSTS or parsed.scheme != 'https' or parsed.hostname not in HOSTS[provider] or parsed.username or parsed.port not in (None,443):
            raise ValueError('Provider URL is not allowlisted')
        if not math.isfinite(units) or units<=0 or max_bytes<=0:
            raise ValueError('Requests must reserve positive quota and bytes')
        headers = headers or {}
        key = hashlib.sha256((url+json.dumps(headers,sort_keys=True)).encode()).hexdigest()
        path = self.root/key
        cached = self.db.execute('SELECT fetched,etag,modified FROM cache WHERE key=?',(key,)).fetchone()
        if cached and path.exists():
            body = path.read_bytes()
            if validator:
                try:
                    validator(body)
                except ValueError:
                    # Legacy poisoned cache: unusable, but never skip budgets,
                    # cooldowns or job schedules to replace it.
                    cached = None
            if cached and self.now()-cached[0] < ttl:
                return body
        policy = dict(POLICIES[provider])
        # Owner-approved, dated archive bootstrap only. Never changes defaults or
        # counters, and cannot affect forecasts, other hosts, or a later UTC day.
        day = time.strftime('%Y-%m-%d',time.gmtime(self.now()))
        permit = self.archive_allowance
        if permit and permit.get('day') == day and provider == 'openmeteo':
            from urllib.parse import parse_qs
            query = parse_qs(parsed.query)
            if (parsed.hostname == 'archive-api.open-meteo.com' and parsed.path == '/v1/archive'
                    and query.get('models') == ['era5']
                    and query.get('end_date', ['9999'])[0] <= '2025-12-31'):
                policy.update(requests=40, units=2500, hour_units=2500)
        state = self.db.execute('SELECT last,blocked FROM provider WHERE name=?',(provider,)).fetchone()
        if state and state[1] > self.now():
            raise Deferred(f'{provider} in cooldown until {time.strftime("%Y-%m-%d %H:%M UTC",time.gmtime(state[1]))}')
        if state:
            self.sleep(max(0,policy['gap']-(self.now()-state[0])))
        day = time.strftime('%Y-%m-%d',time.gmtime(self.now()))
        for seconds,limit,label in ((60,policy['minute_units'],'minute'),(3600,policy['hour_units'],'hour')):
            recent = self.db.execute('SELECT COALESCE(SUM(units),0) FROM attempts WHERE provider=? AND at>?',(provider,self.now()-seconds)).fetchone()[0]
            if recent+units>limit:
                raise Deferred(f'{provider} rolling {label} quota-unit budget reached; no request sent')
        monthly = self.db.execute('SELECT COALESCE(SUM(units),0) FROM usage WHERE provider=? AND day LIKE ?',(provider,day[:7]+'-%')).fetchone()[0]
        if monthly+units>policy['month_units']:
            raise Deferred(f'{provider} monthly quota-unit budget reached; no request sent')
        self.db.execute('INSERT OR IGNORE INTO usage VALUES(?,?,0,0,0)',(day,provider))
        used = self.db.execute('SELECT requests,bytes,units FROM usage WHERE day=? AND provider=?',(day,provider)).fetchone()
        if used[0]+1 > policy['requests'] or used[1]+max_bytes > policy['bytes'] or used[2]+units > policy['units']:
            raise Deferred(f'{provider} daily application budget reached; no request sent')
        self.db.execute('UPDATE usage SET requests=requests+1,bytes=bytes+?,units=units+? WHERE day=? AND provider=?',(max_bytes,units,day,provider))
        self.db.execute('INSERT INTO attempts VALUES(?,?,?)',(self.now(),provider,units))
        self.db.execute('INSERT INTO provider VALUES(?,?,0,NULL) ON CONFLICT(name) DO UPDATE SET last=excluded.last',(provider,self.now()))
        self.db.commit()
        self.sent_requests += 1
        self.reserved_units += units
        request_headers = {'User-Agent':self.agent, 'Accept-Encoding':'identity', **headers}
        if cached and path.exists():
            if cached[1]: request_headers['If-None-Match'] = cached[1]
            if cached[2]: request_headers['If-Modified-Since'] = cached[2]
        try:
            request = urllib.request.Request(url, headers=request_headers)
            try:
                response = self.opener(request,timeout=45)
            except urllib.error.HTTPError as exc:
                if exc.code != 304 or not path.exists():
                    # Open-Meteo explains malformed queries in a JSON `reason`.
                    # Capture a bounded explanation from this same response,
                    # without another request or logging an arbitrary HTML body.
                    try:
                        error = json.loads(exc.read(min(4096,max_bytes)))
                        reason = error.get('reason') if isinstance(error,dict) else None
                        if isinstance(reason,str):
                            reason = ' '.join(reason.split())[:500]
                            exc.msg = f'{exc.msg}; provider reason: {reason}'
                    except (OSError,ValueError,TypeError,AttributeError):
                        pass
                    finally:
                        exc.close()
                    raise
                response = exc
            with response:
                if response.code == 304:
                    body = path.read_bytes()
                    if validator: validator(body)
                    received = 0
                else:
                    if 'Range' in headers and response.code != 206:
                        raise RuntimeError('Server ignored byte range; full GRIB download refused')
                    if 'Range' in headers:
                        import re
                        match = re.fullmatch(r'bytes=(\d+)-(\d+)',headers['Range'])
                        content_range = response.headers.get('Content-Range','')
                        if not match or not content_range.startswith(f'bytes {match[1]}-{match[2]}/'):
                            raise RuntimeError('Server returned an unexpected byte range')
                    length = response.headers.get('Content-Length')
                    if length and int(length)>max_bytes:
                        raise RuntimeError('Download exceeds application size limit')
                    body = response.read(max_bytes+1)
                    if len(body)>max_bytes: raise RuntimeError('Download exceeds application size limit')
                    if 'Range' in headers and len(body)!=int(match[2])-int(match[1])+1:
                        raise RuntimeError('Incomplete byte-range download')
                    received = len(body)
                    if validator: validator(body)
                    temp = path.with_suffix('.tmp')
                    temp.write_bytes(body)
                    temp.replace(path)
                self.db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?,?)',(key,self.now(),response.headers.get('ETag') or (cached[1] if cached else None),response.headers.get('Last-Modified') or (cached[2] if cached else None)))
            self.db.execute('UPDATE usage SET bytes=bytes-?+? WHERE day=? AND provider=?',(max_bytes,received,day,provider))
            self.db.execute('UPDATE provider SET error=NULL WHERE name=?',(provider,))
            self.db.commit()
            return body
        except Exception as exc:
            delay = policy['cooldown']
            if isinstance(exc, urllib.error.HTTPError):
                retry = (exc.headers or {}).get('Retry-After','')
                try:
                    retry_delay = float(retry)
                except ValueError:
                    try: retry_delay = email.utils.parsedate_to_datetime(retry).timestamp()-self.now()
                    except (TypeError,ValueError,OverflowError): retry_delay = 0
                delay = max(delay,retry_delay)
            self.db.execute('UPDATE provider SET blocked=?,error=? WHERE name=?',(self.now()+delay,str(exc),provider))
            self.db.commit()
            raise
