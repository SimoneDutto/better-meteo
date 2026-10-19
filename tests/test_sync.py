"""Exercise the complete CLI against a fake transport, never the public API."""
import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs

from collector import sync
from collector.policy import Client

class Response(io.BytesIO):
    code=200
    headers={}

class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.config=json.loads(sync.CONFIG.read_text())
        self.config_path=self.root/'config.json'
        self.config_path.write_text(json.dumps(self.config))
        self.calls=[]
        self.client=Client(self.root/'http',sleep=lambda seconds:None,opener=self.open)
        self.addCleanup(self.client.db.close)
        self.addCleanup(patch.stopall)
        patch('collector.sync.CONFIG',self.config_path).start()
        patch('collector.sync.DATA',self.root).start()
        patch('collector.sync.Client',return_value=self.client).start()
        patch('collector.maps.validate_dependencies').start()
        patch('collector.maps.render_fields',side_effect=lambda fields,path,*a:path.write_bytes(b'fixture')).start()
        patch('collector.maps.render_animation',side_effect=lambda paths,path,labels:path.write_bytes(b'fixture gif')).start()

    def open(self,request,timeout):
        url=urlsplit(request.full_url)
        self.assertTrue(url.hostname.endswith('open-meteo.com'))
        self.calls.append(url)
        params={k:v[0] for k,v in parse_qs(url.query).items()}
        today=datetime.now(timezone.utc).date()
        if 'geopotential_height_500hPa' in params.get('hourly',''):
            self.assertEqual(len(params['elevation'].split(',')),len(params['latitude'].split(',')))
            reference=int(datetime.fromisoformat(params['start_date']).replace(tzinfo=timezone.utc).timestamp())
            times=[reference+s*3600 for s in self.config['map_steps']]
            value=[{'latitude':float(lat),'longitude':float(lon),
                'hourly_units':{'geopotential_height_500hPa':'m','pressure_msl':'hPa','temperature_850hPa':'°C'},
                'hourly':{'time':times,'geopotential_height_500hPa':[5500]*len(times),'pressure_msl':[1010]*len(times),'temperature_850hPa':[15]*len(times)}}
                for lat,lon in zip(params['latitude'].split(','),params['longitude'].split(','))]
        elif url.hostname=='ensemble-api.open-meteo.com':
            value={'hourly':{'time':[f'{today}T{h:02}:00' for h in range(24)],'temperature_2m':[15]*24,'precipitation':[0]*24}}
        elif params.get('models')=='era5':
            from collector.climate import FIELDS, UNITS, dates
            from datetime import date
            days=dates(date.fromisoformat(params['start_date']),date.fromisoformat(params['end_date']))
            value={'daily_units':{field:UNITS[key] for key,field in FIELDS.items()},'daily':{'time':[d.isoformat() for d in days],**{field:[1]*len(days) for field in FIELDS.values()}}}
        elif url.hostname=='archive-api.open-meteo.com':
            value={'daily':{'time':[(today-timedelta(days=i)).isoformat() for i in range(365,0,-1)],'precipitation_sum':[1]*365}}
        else:
            value={'daily':{'time':[today.isoformat()]}}
        return Response(json.dumps(value).encode())

    def run_sync(self,command='sync'):
        output=io.StringIO()
        with patch('sys.argv',['collector',command]),contextlib.redirect_stdout(output): sync.main()
        return output.getvalue()



    def test_full_sync_then_immediate_repeat_has_eight_then_zero_calls(self):
        first=self.run_sync()
        self.assertIn('8 HTTP requests sent; 314 quota units reserved',first)
        self.assertEqual(len(self.calls),8)
        self.assertTrue((self.root/'cities'/'turin'/'rainfall.json').exists())
        self.assertTrue((self.root/'pressure.json').exists())
        self.assertTrue((self.root/'cities'/'turin'/'climate.json').exists())
        second=self.run_sync()
        self.assertIn('0 HTTP requests sent; 0 quota units reserved',second)
        self.assertEqual(len(self.calls),8)
        self.assertEqual(self.client.status()['monthly_units'],314)

    def advance(self,seconds):
        self.clock+=seconds

    def test_explicit_history_stops_at_shared_budget_then_resumes_without_redownload(self):
        self.clock=self.client.now()
        self.client.now=lambda:self.clock
        self.client.sleep=self.advance
        first=self.run_sync('climate-history')
        self.assertEqual(len(self.calls),12)
        self.assertIn('12 HTTP requests sent; 948 quota units reserved',first)
        self.assertIn('10 batches remaining',first)
        climate=json.loads((self.root/'cities/turin/climate.json').read_text())
        self.assertEqual(climate['data']['metrics'][0]['years'],30)
        self.assertIsNone(climate['updated'])
        before=[u.geturl() for u in self.calls]
        self.run_sync('climate-history')
        self.assertEqual(len(self.calls),12)
        self.clock+=86401
        final=self.run_sync('climate-history')
        self.assertIn('Climate archive complete',final)
        self.assertEqual(len(self.calls),22)
        self.assertEqual(len(set(u.geturl() for u in self.calls)),22)
        self.run_sync('climate-history')
        self.assertEqual(len(self.calls),22)
        self.assertTrue(all('models=era5' in url.query for url in self.calls))

    def test_streaming_timeout_stops_archive_without_caching_bad_body(self):
        self.clock=self.client.now()
        self.client.now=lambda:self.clock
        self.client.sleep=self.advance
        def transport(request,timeout):
            url=urlsplit(request.full_url)
            if parse_qs(url.query).get('start_date')==['1964-01-01']:
                self.calls.append(url)
                return Response(b'Unexpected error while streaming data: timeoutReached')
            return self.open(request,timeout)
        self.client.opener=transport
        output=self.run_sync('climate-history')
        self.assertIn('invalid JSON',output)
        self.assertIn('timeoutReached',output)
        self.assertEqual(len(self.calls),12)
        self.assertFalse((self.root/'cities/turin/climate-archive-v1/1964-01-01_1966-12-31.json').exists())
        for (key,) in self.client.db.execute('SELECT key FROM cache'):
            self.assertNotIn(b'timeoutReached',(self.client.root/key).read_bytes())
        self.run_sync('climate-history')
        self.assertEqual(len(self.calls),12)

    def test_dated_archive_allowance_completes_once_without_changing_normal_limits(self):
        import time
        from collector.policy import Deferred, POLICIES
        self.run_sync()
        self.clock=self.client.now()+61
        self.client.now=lambda:self.clock
        self.client.sleep=self.advance
        permit=self.root/'climate-download-permit.json'
        sync.write_json(permit,{'day':time.strftime('%Y-%m-%d',time.gmtime(self.clock)), 'city':'turin', 'completed':False})
        output=self.run_sync('climate-history')
        self.assertIn('Climate archive complete',output)
        self.assertEqual(len(self.calls),30)
        self.assertEqual(self.client.status()['monthly_units'],2026)
        self.assertTrue(json.loads(permit.read_text())['completed'])
        self.assertIsNone(self.client.archive_allowance)
        self.assertEqual(POLICIES['openmeteo']['units'],1000)
        self.assertEqual(POLICIES['openmeteo']['requests'],24)
        self.run_sync('climate-history')
        self.assertEqual(len(self.calls),30)
        with self.assertRaises(Deferred):
            self.client.get('openmeteo','https://api.open-meteo.com/v1/forecast?fixture=normal',0,100,1)
        self.assertEqual(len(self.calls),30)

    def test_expired_archive_allowance_is_ignored(self):
        self.clock=self.client.now()
        self.client.now=lambda:self.clock
        self.client.sleep=self.advance
        sync.write_json(self.root/'climate-download-permit.json',{'day':'2000-01-01','city':'turin','completed':False})
        output=self.run_sync('climate-history')
        self.assertNotIn('Owner-approved archive allowance',output)
        self.assertEqual(len(self.calls),12)
        self.assertIsNone(self.client.archive_allowance)

    def test_history_preserves_recent_timestamp_and_stops_after_upstream_error(self):
        import urllib.error
        from collector.climate import download_history
        self.run_sync()
        path=self.root/'cities/turin/climate.json'
        updated=json.loads(path.read_text())['updated']
        before=len(self.calls)
        self.clock=self.client.now()+61
        self.client.now=lambda:self.clock
        self.client.sleep=self.advance
        with patch.object(self.client,'opener',side_effect=urllib.error.HTTPError('fixture',429,'Too Many Requests',{},io.BytesIO(b'{}'))):
            output=self.run_sync('climate-history')
        self.assertIn('stopped at the first deferred/failed batch',output)
        self.assertEqual(json.loads(path.read_text())['updated'],updated)
        self.assertEqual(self.client.sent_requests,before+1)
        self.run_sync('climate-history')
        self.assertEqual(self.client.sent_requests,before+1)

    def test_migration_preserves_old_cooldown_and_usage_without_using_it(self):
        self.client.db.execute('INSERT INTO provider VALUES(?,?,?,?)',('ecmwf',self.client.now(),self.client.now()+43200,'429'))
        self.client.db.execute('INSERT INTO jobs VALUES(?,?,NULL,?)',('pressure',self.client.now()+43200,'429'))
        self.client.db.execute('INSERT INTO usage VALUES(?,?,19,0,19)',('2026-10-19','ecmwf'))
        self.client.db.commit()
        output=self.run_sync()
        self.assertEqual(len(self.calls),8)
        self.assertIn('Retired job pressure: disabled; history preserved',output)
        state=self.client.status()
        self.assertEqual(next(u['requests'] for u in state['usage'] if u['provider']=='ecmwf'),19)
        self.assertGreater(next(p['blocked'] for p in state['providers'] if p['name']=='ecmwf'),self.client.now())

    def test_saved_error_is_explicit_history_and_does_not_retry(self):
        self.client.db.execute('INSERT INTO jobs VALUES(?,?,NULL,?)',('pressure:openmeteo:v1',self.client.now()+43200,'HTTP Error 400: Bad Request'))
        self.client.db.commit()
        output=io.StringIO()
        with contextlib.redirect_stdout(output): sync.print_status(self.client)
        self.assertIn('pressure:openmeteo:v1: waiting until',output.getvalue())
        self.assertIn('Saved failure from the previous attempt',output.getvalue())
        self.assertEqual(self.calls,[])

    def test_status_accounts_for_provider_cooldown_when_job_is_due(self):
        self.client.db.execute('INSERT INTO jobs VALUES(?,?,NULL,NULL)',('pressure:openmeteo:v1',self.client.now()-10))
        self.client.db.execute('INSERT INTO provider VALUES(?,?,?,?)',('openmeteo',self.client.now(),self.client.now()+21600,'429'))
        self.client.db.commit()
        output=io.StringIO()
        with contextlib.redirect_stdout(output): sync.print_status(self.client)
        self.assertIn('pressure:openmeteo:v1: waiting until',output.getvalue())
        self.assertNotIn('eligible on the next sync',output.getvalue())
        self.assertEqual(self.calls,[])

if __name__=='__main__': unittest.main()
