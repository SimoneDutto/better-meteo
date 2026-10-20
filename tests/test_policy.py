import io
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch
import urllib.error

from collector.policy import Client, Deferred, NoRedirect, POLICIES, collector_lock, decode_json

URL = 'https://api.open-meteo.com/v1/forecast?latitude=45&longitude=7'

class Response(io.BytesIO):
    def __init__(self,body=b'{}',code=200,headers=None):
        super().__init__(body)
        self.code = code
        self.headers = headers or {}

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = 1792400000.
        self.opener = Mock(side_effect=lambda *a,**k:Response())
        self.client = Client(self.temp.name,now=lambda:self.clock,sleep=self.advance,opener=self.opener)

    def tearDown(self):
        self.client.db.close()
        self.temp.cleanup()

    def advance(self,seconds): self.clock += seconds

    def test_manual_archive_can_retry_transient_failure_with_spacing_and_shared_quota(self):
        url='https://archive-api.open-meteo.com/v1/archive?models=era5'
        self.opener.side_effect=[Response(b'Unexpected error while streaming data: timeoutReached'),Response()]
        with self.client.manual_history():
            with self.assertRaises(ValueError):
                self.client.get('openmeteo',url,0,1024,units=79,validator=decode_json)
        with self.assertRaises(Deferred): self.get()
        with self.client.manual_history():
            # The manual exception cannot apply to forecast requests.
            with self.assertRaises(Deferred): self.get()
            self.assertEqual(self.client.get('openmeteo',url,0,1024,units=79,validator=decode_json),b'{}')
        self.assertEqual(self.opener.call_count,2)
        self.assertEqual(self.client.reserved_units,158)
        self.assertEqual(self.clock,1792400005.)
        day=self.client.status()['usage'][0]['day']
        self.client.db.execute('UPDATE usage SET units=1000 WHERE day=?',(day,))
        self.client.db.commit()
        with self.client.manual_history():
            with self.assertRaisesRegex(Deferred,'daily'):
                self.client.get('openmeteo',url+'&batch=2',0,1024,units=79)
        self.assertEqual(self.opener.call_count,2)

    def test_manual_archive_respects_persisted_rate_limit_and_retry_after(self):
        url='https://archive-api.open-meteo.com/v1/archive?models=era5'
        for code,headers in [(429,Message()),(503,Message())]:
            with self.subTest(code=code):
                self.advance(21601)
                if code==503: headers['Retry-After']='43200'
                self.opener.side_effect=urllib.error.HTTPError(url,code,'Unavailable',headers,io.BytesIO(b'{}'))
                with self.client.manual_history():
                    with self.assertRaises(urllib.error.HTTPError): self.client.get('openmeteo',url,0,1024)
                other=Client(self.temp.name,now=lambda:self.clock,opener=self.opener)
                try:
                    before=self.opener.call_count
                    with other.manual_history():
                        with self.assertRaises(Deferred): other.get('openmeteo',url,0,1024)
                    self.assertEqual(self.opener.call_count,before)
                finally:
                    other.db.close()

    def get(self,**kwargs): return self.client.get('openmeteo',URL,3600,1024,**kwargs)

    def test_streaming_error_is_not_cached_or_retried_and_preserves_previous_body(self):
        self.get(validator=decode_json)
        key=self.client.db.execute('SELECT key FROM cache').fetchone()[0]
        original=(self.client.root/key).read_bytes()
        fetched=self.client.db.execute('SELECT fetched FROM cache').fetchone()[0]
        self.advance(3601)
        self.opener.side_effect=lambda *a,**k:Response(b'Unexpected error while streaming data: timeoutReached')
        with self.assertRaisesRegex(ValueError,'timeoutReached'):
            self.get(validator=decode_json)
        self.assertEqual((self.client.root/key).read_bytes(),original)
        self.assertEqual(self.client.db.execute('SELECT fetched FROM cache').fetchone()[0],fetched)
        state=self.client.status()
        self.assertGreaterEqual(state['providers'][0]['blocked'],self.clock+21600)
        self.assertEqual(state['usage'][0]['requests'],2)
        with self.assertRaises(Deferred):
            self.get(validator=decode_json)
        self.assertEqual(self.opener.call_count,2)

    def test_legacy_invalid_cache_cannot_bypass_cooldown_and_is_replaced_when_allowed(self):
        self.opener.side_effect=lambda *a,**k:Response(b'Unexpected error while streaming data: timeoutReached')
        self.get()  # Simulate the old collector accepting HTTP 200 as success.
        self.client.db.execute('UPDATE provider SET blocked=?',(self.clock+21600,))
        self.client.db.commit()
        with self.assertRaises(Deferred):self.get(validator=decode_json)
        self.assertEqual(self.opener.call_count,1)
        self.advance(21601)
        self.opener.side_effect=lambda *a,**k:Response(b'{"ok":true}')
        self.assertEqual(self.get(validator=decode_json),b'{"ok":true}')
        self.assertEqual(self.opener.call_count,2)

    def test_repeated_views_of_fresh_cache_make_one_request(self):
        for _ in range(100): self.assertEqual(self.get(),b'{}')
        self.assertEqual(self.opener.call_count,1)

    def test_cache_persists_across_collector_restarts(self):
        self.get()
        other = Client(self.temp.name,now=lambda:self.clock,opener=self.opener)
        self.assertEqual(other.get('openmeteo',URL,3600,1024),b'{}')
        other.db.close()
        self.assertEqual(self.opener.call_count,1)

    def test_pruning_does_not_erase_budgets_or_schedules(self):
        self.get()
        self.client.run('forecast',21600,lambda:None)
        self.advance(46*86400)
        self.client.prune()
        self.assertEqual(self.client.db.execute('SELECT COUNT(*) FROM cache').fetchone()[0],0)
        self.assertEqual(len(self.client.status()['usage']),1)
        self.assertEqual(len(self.client.status()['jobs']),1)

    def test_failed_attempt_has_no_automatic_retry_and_provider_cooldown(self):
        self.opener.side_effect = OSError('offline')
        with self.assertRaises(OSError): self.get()
        with self.assertRaises(Deferred): self.client.get('openmeteo',URL+'&other=1',0,1024)
        self.assertEqual(self.opener.call_count,1)
        self.assertEqual(self.client.status()['usage'][0]['requests'],1)

    def test_bad_request_reason_is_persisted_without_retry_or_reset(self):
        body=io.BytesIO(b'{"error":true,"reason":"Coordinates and elevation count must be the same"}')
        self.opener.side_effect=urllib.error.HTTPError(URL,400,'Bad Request',Message(),body)
        with self.assertRaisesRegex(urllib.error.HTTPError,'Coordinates and elevation count'):
            self.get()
        state=self.client.status()
        self.assertIn('Coordinates and elevation count',state['providers'][0]['error'])
        self.assertGreaterEqual(state['providers'][0]['blocked'],self.clock+21600)
        self.assertEqual(state['usage'][0]['requests'],1)
        with self.assertRaises(Deferred): self.get()
        self.assertEqual(self.opener.call_count,1)

    def test_non_json_error_does_not_hide_http_error(self):
        self.opener.side_effect=urllib.error.HTTPError(URL,503,'Unavailable',Message(),io.BytesIO(b'<html>Unavailable</html>'))
        with self.assertRaisesRegex(urllib.error.HTTPError,'503'): self.get()
        self.assertNotIn('<html>',self.client.status()['providers'][0]['error'])

    def test_rate_limit_retry_after_seconds_survives_restart(self):
        headers = Message(); headers['Retry-After']='86400'
        self.opener.side_effect = urllib.error.HTTPError(URL,429,'limit',headers,None)
        with self.assertRaises(urllib.error.HTTPError): self.get()
        self.advance(7*3600)
        other = Client(self.temp.name,now=lambda:self.clock,opener=self.opener)
        with self.assertRaises(Deferred): other.get('openmeteo',URL,0,1024)
        other.db.close()
        self.assertEqual(self.opener.call_count,1)

    def test_retry_after_http_date_is_respected(self):
        from email.utils import formatdate
        headers = Message(); headers['Retry-After']=formatdate(self.clock+90000,usegmt=True)
        self.opener.side_effect=urllib.error.HTTPError(URL,503,'busy',headers,None)
        with self.assertRaises(urllib.error.HTTPError): self.get()
        blocked=self.client.status()['providers'][0]['blocked']
        self.assertGreaterEqual(blocked,self.clock+90000)

    def test_daily_request_budget_shared_across_endpoints(self):
        policy = {**POLICIES['openmeteo'],'requests':2}
        with patch.dict(POLICIES,openmeteo=policy):
            self.client.get('openmeteo',URL,0,1024)
            self.client.get('openmeteo',URL+'&city=2',0,1024)
            with self.assertRaises(Deferred): self.client.get('openmeteo',URL+'&city=3',0,1024)
        self.assertEqual(self.opener.call_count,2)

    def test_weighted_usage_budget_cannot_be_bypassed(self):
        with patch.dict(POLICIES,openmeteo={**POLICIES['openmeteo'],'units':25}):
            self.get(units=25)
            with self.assertRaises(Deferred): self.client.get('openmeteo',URL+'&x=1',0,1024)
        self.assertEqual(self.opener.call_count,1)

    def test_download_budget_checks_before_network(self):
        with patch.dict(POLICIES,openmeteo={**POLICIES['openmeteo'],'bytes':512}):
            with self.assertRaises(Deferred): self.get()
        self.opener.assert_not_called()

    def test_oversized_response_does_not_replace_good_cache(self):
        self.get(); self.advance(3601)
        self.opener.side_effect=lambda *a,**k:Response(b'x'*1025)
        with self.assertRaises(RuntimeError): self.get()
        self.assertEqual(self.client.get('openmeteo',URL,7200,1024),b'{}')

    def test_full_grib_refused_if_range_ignored(self):
        with self.assertRaisesRegex(RuntimeError,'ignored byte range'):
            self.client.get('openmeteo','https://api.open-meteo.com/test',0,1024,headers={'Range':'bytes=0-1'})
        self.assertEqual(self.opener.call_count,1)

    def test_range_response_must_match_requested_bytes(self):
        self.opener.side_effect=lambda *a,**k:Response(b'ab',206,{'Content-Range':'bytes 0-1/99'})
        self.assertEqual(self.client.get('openmeteo','https://api.open-meteo.com/test',0,1024,headers={'Range':'bytes=0-1'}),b'ab')

    def test_partial_range_refused(self):
        self.opener.side_effect=lambda *a,**k:Response(b'a',206,{'Content-Range':'bytes 0-1/99'})
        with self.assertRaisesRegex(RuntimeError,'Incomplete'):
            self.client.get('openmeteo','https://api.open-meteo.com/test',0,1024,headers={'Range':'bytes=0-1'})

    def test_conditional_get_keeps_body_on_304(self):
        self.opener.side_effect=lambda *a,**k:Response(b'{"ok":1}',200,{'ETag':'v1'})
        self.get(); self.advance(3601)
        headers=Message(); headers['ETag']='v1'
        self.opener.side_effect=urllib.error.HTTPError(URL,304,'not modified',headers,None)
        self.assertEqual(self.get(),b'{"ok":1}')
        self.assertEqual(self.opener.call_args.args[0].get_header('If-none-match'),'v1')

    def test_jobs_reserve_slot_before_failure(self):
        action=Mock(side_effect=OSError('offline'))
        self.assertFalse(self.client.run('forecast',21600,action))
        for _ in range(20): self.assertFalse(self.client.run('forecast',21600,action))
        self.assertEqual(action.call_count,1)
        self.advance(21601)
        self.client.run('forecast',21600,action)
        self.assertEqual(action.call_count,2)




    def test_provider_minimum_spacing(self):
        self.get(); first=self.clock
        self.client.get('openmeteo',URL+'&x=1',0,1024)
        self.assertGreaterEqual(self.clock-first,5)

    def test_retired_services_are_rejected_before_any_request(self):
        for provider,url in [('ecmwf','https://data.ecmwf.int/test'),('arpa','https://www.arpa.piemonte.it/test')]:
            with self.assertRaises(ValueError): self.client.get(provider,url,0,1024)
        self.opener.assert_not_called()

    def test_rolling_minute_quota_is_shared_across_hosts(self):
        self.get(units=250)
        with self.assertRaises(Deferred):
            self.client.get('openmeteo','https://ensemble-api.open-meteo.com/v1/ensemble',0,1024,units=200)
        self.assertEqual(self.opener.call_count,1)
        self.advance(61)
        self.client.get('openmeteo','https://ensemble-api.open-meteo.com/v1/ensemble',0,1024,units=200)
        self.assertEqual(self.opener.call_count,2)

    def test_monthly_cap_uses_persisted_daily_usage(self):
        day='2026-10-01'
        self.client.db.execute('INSERT INTO usage VALUES(?,?,1,0,30000)',(day,'openmeteo'))
        self.client.db.commit()
        with self.assertRaisesRegex(Deferred,'monthly'): self.get()
        self.opener.assert_not_called()

    def test_hourly_cap_enforced_across_midnight(self):
        self.clock=1792454401.0
        self.client.db.execute('INSERT INTO attempts VALUES(?,?,?)',(self.clock-1800,'openmeteo',1500))
        self.client.db.commit()
        with self.assertRaisesRegex(Deferred,'hour'): self.get()
        self.opener.assert_not_called()

    def test_allowlist_rejects_wrong_scheme_host_and_credentials(self):
        for url in ('http://api.open-meteo.com','https://example.com','https://user:pass@api.open-meteo.com','https://api.open-meteo.com:444'):
            with self.assertRaises(ValueError): self.client.get('openmeteo',url,0,1024)
        self.opener.assert_not_called()

    def test_redirects_are_not_followed(self):
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,'',{},'https://example.com'))

    def test_overlapping_collector_is_rejected(self):
        with collector_lock(self.temp.name):
            with self.assertRaises(Deferred):
                with collector_lock(self.temp.name): self.fail('Acquired duplicate lock')

    def test_no_network_in_frontend(self):
        project=Path(__file__).parents[1]
        for path in (project/'src').rglob('*'):
            if path.is_file():
                self.assertNotIn('fetch(',path.read_text(),str(path))

if __name__=='__main__': unittest.main()
