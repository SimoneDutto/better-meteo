"""Synthetic ERA5 climate data only; no public API calls."""
from datetime import date, timedelta
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stdout
import io

from collector.climate import FIELDS, UNITS, aggregate, batches, collect, dates, download_history, parse_daily, reservation, summary, window_end


def series_between(start, end, high=20, low=10, precipitation=2, rain=1, snow=0):
    return {d.isoformat(): dict(high=high, low=low, precipitation=precipitation, rain=rain, snow=snow)
            for d in dates(start, end)}


def payload(start, end):
    return {'daily_units': {field: UNITS[key] for key, field in FIELDS.items()},
            'daily': {'time': [d.isoformat() for d in dates(start, end)],
                      **{field: [1]*len(dates(start, end)) for field in FIELDS.values()}}}


class ClimateTests(unittest.TestCase):
    def test_required_pause_explains_wait_without_fetching_or_extending_schedule(self):
        from collector.policy import Client
        from collector.sync import write_json
        from datetime import datetime, timezone
        now=datetime(2026,10,19,13,tzinfo=timezone.utc).timestamp()
        due=datetime(2026,10,20,11,36,tzinfo=timezone.utc).timestamp()
        city=dict(id='turin',latitude=45.07,longitude=7.68,timezone='Europe/Rome')
        start,end=date(1964,1,1),date(1964,1,2)
        name=f'turin:climate-history:era5:{start}:{end}'
        with tempfile.TemporaryDirectory() as temp, patch('collector.climate.batches',return_value=[(start,end)]):
            root=Path(temp);fetch=Mock(return_value=payload(start,end));sleep=Mock()
            client=Client(root/'http',now=lambda:now,sleep=sleep)
            try:
                client.db.execute('INSERT INTO jobs VALUES(?,?,NULL,?)',(name,due,'timeoutReached'))
                client.db.execute('INSERT INTO provider VALUES(?,?,?,?)',('openmeteo',now,due,'HTTP Error 429'))
                client.db.execute('INSERT INTO provider_required_pause VALUES(?,?)',('openmeteo',due))
                client.db.commit()
                output=io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(download_history(client,city,root,fetch,write_json,date(2026,10,19)),1)
                fetch.assert_not_called();sleep.assert_not_called()
                self.assertIn('2026-10-20 11:36 UTC',output.getvalue())
                self.assertIn('2026-10-20 13:36 Europe/Rome',output.getvalue())
                self.assertIn('Saved failure, not a new request: timeoutReached',output.getvalue())
                self.assertEqual(client.db.execute('SELECT due FROM jobs WHERE name=?',(name,)).fetchone()[0],due)
                now=due+1
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(download_history(client,city,root,fetch,write_json,date(2026,10,20)),0)
                    download_history(client,city,root,fetch,write_json,date(2026,10,20))
                self.assertEqual(fetch.call_count,1,'Completed archive must never be fetched again')
            finally:
                client.db.close()

    def test_manual_archive_retries_failed_batch_once_and_keeps_saved_batches(self):
        from collector.policy import Client
        from collector.sync import write_json
        city=dict(id='turin',latitude=45.07,longitude=7.68,timezone='Europe/Rome')
        start,end=date(1964,1,1),date(1964,1,2)
        with tempfile.TemporaryDirectory() as temp, patch('collector.climate.batches',return_value=[(start,end)]):
            root=Path(temp);client=Client(root/'http',now=lambda:1792480000)
            fetch=Mock(side_effect=[ValueError('timeoutReached'),payload(start,end)])
            try:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(download_history(client,city,root,fetch,write_json,date(2026,10,20)),1)
                    self.assertEqual(fetch.call_count,1,'Failure must stop this invocation without automatic retries')
                    self.assertEqual(download_history(client,city,root,fetch,write_json,date(2026,10,20)),0)
                    download_history(client,city,root,fetch,write_json,date(2026,10,20))
                self.assertEqual(fetch.call_count,2,'Only another manual invocation retries; saved batches are skipped')
                self.assertFalse(client.manual_archive_retry)
            finally:
                client.db.close()

    def test_publication_delay_chooses_latest_complete_window(self):
        target=date(2026,10,13)
        series=series_between(date(2026,9,16),target)
        series[target.isoformat()]={k:None for k in FIELDS}
        end=window_end(series,target)
        self.assertEqual(end,date(2026,10,12))
        result=summary(series,end,date(2026,10,19),1,22)
        self.assertEqual(result['start'],'2026-09-28')
        self.assertTrue(all(m['available_days']==15 and m['actual'] is not None for m in result['metrics']))
        self.assertEqual(result['lag_days'],7)

    def test_existing_fourteen_day_cache_stays_explicitly_partial(self):
        series=series_between(date(2026,9,29),date(2026,10,12))
        target=date(2026,10,13)
        self.assertEqual(window_end(series,target),target)
        result=summary(series,target,date(2026,10,19),1,22)
        rain=next(m for m in result['metrics'] if m['key']=='precipitation')
        self.assertIsNone(rain['actual'])
        self.assertIsNone(rain['difference'])
        self.assertEqual(rain['available_days'],14)
        self.assertEqual(rain['available_value'],28)
        self.assertIsNone(result['days'][-1]['precipitation'])

    def test_partial_difference_uses_identical_dates_not_a_full_window_normal(self):
        series={}
        for year in range(1991,2021):
            series.update(series_between(date(year,10,1),date(year,10,15),precipitation=2))
            # A large rainfall on the missing recent day must not enter the normal.
            series[f'{year}-10-08']['precipitation']=1000
        series.update(series_between(date(2026,10,1),date(2026,10,15),high=23,low=12,precipitation=1))
        series['2026-10-08']={k:None for k in FIELDS}
        result=summary(series,date(2026,10,15),date(2026,10,21),10,22)
        metrics={m['key']:m for m in result['metrics']}
        self.assertIsNone(metrics['precipitation']['actual'])
        self.assertEqual(metrics['precipitation']['available_value'],14)
        self.assertEqual(metrics['precipitation']['normal'],28)
        self.assertEqual(metrics['precipitation']['difference'],-14)
        self.assertEqual(metrics['precipitation']['comparison_days'],14)
        self.assertEqual(metrics['high']['difference'],3)
        self.assertEqual(metrics['low']['difference'],2)
        self.assertEqual(metrics['snow']['difference'],0)
        self.assertIsNone(metrics['snow']['percent'])

    def test_matching_window_anomalies_and_period_changes(self):
        series = series_between(date(1961,1,1), date(1990,12,31),high=18,low=8,precipitation=3)
        series.update(series_between(date(1991,1,1),date(2020,12,31)))
        series.update(series_between(date(2026,10,1),date(2026,10,15),high=23,low=12,precipitation=1))
        result=summary(series,date(2026,10,15),date(2026,10,21),20,22)
        metrics={m['key']:m for m in result['metrics']}
        self.assertEqual(metrics['high']['difference'],3)
        self.assertEqual(metrics['precipitation']['normal'],30)
        self.assertEqual(metrics['precipitation']['percent'],-50)
        self.assertEqual(metrics['high']['years'],30)
        self.assertEqual(metrics['snow']['actual'],0)
        self.assertIsNone(metrics['snow']['percent'])
        changes={m['key']:m for m in result['changes']}
        self.assertEqual(changes['high']['difference'],2)
        self.assertEqual(changes['low']['difference'],2)
        self.assertEqual(result['lag_days'],6)

    def test_missing_day_invalidates_only_affected_metric_and_year(self):
        series=series_between(date(1991,1,1),date(1991,12,31))
        series['1991-02-15']['rain']=None
        self.assertIsNone(aggregate(series,dates(date(1991,1,1),date(1991,12,31)),'rain'))
        result=summary(series,date(1991,2,20),date(1992,1,1),1,22)
        self.assertIsNone(result['annual'][30]['rain'])
        self.assertEqual(result['annual'][30]['high'],20)
        self.assertIsNone(result['metrics'][0]['normal'])

    def test_twenty_four_reference_windows_required(self):
        series={}
        for year in range(1991,2014):
            series.update(series_between(date(year,6,1),date(year,6,15)))
        result=summary(series,date(2026,6,15),date(2026,6,21),1,22)
        self.assertIsNone(result['metrics'][0]['normal'])
        series.update(series_between(date(2014,6,1),date(2014,6,15)))
        self.assertEqual(summary(series,date(2026,6,15),date(2026,6,21),1,22)['metrics'][0]['normal'],20)

    def test_leap_day_and_year_boundary_windows(self):
        series=series_between(date(1960,1,1),date(2024,12,31))
        for end in (date(2024,2,29),date(2024,1,5)):
            result=summary(series,end,date(2025,1,1),22,22)
            self.assertEqual(result['metrics'][0]['years'],30)
            self.assertEqual(result['metrics'][2]['normal'],30)

    def test_batch_coverage_and_weighted_budgets(self):
        blocks=batches(date(2026,10,19))
        self.assertEqual(len(blocks),22)
        self.assertEqual(blocks[0],(date(1991,1,1),date(1993,12,31)))
        all_days=[d for a,b in blocks for d in dates(a,b)]
        self.assertEqual(len(set(all_days)),len(all_days))
        self.assertEqual(min(all_days),date(1961,1,1))
        self.assertEqual(max(all_days),date(2025,12,31))
        self.assertLessEqual(max(reservation(a,b) for a,b in blocks),79)
        self.assertLessEqual(312+79+2,400)
        self.assertLessEqual(774+79+2,1000)
        self.assertEqual(max(b for a,b in batches(date(2026,1,2))),date(2024,12,31))
        self.assertEqual(max(b for a,b in batches(date(2026,1,6))),date(2025,12,31))

    def test_parse_rejects_units_dates_nonfinite_and_array_length(self):
        a,b=date(2026,1,1),date(2026,1,2)
        original=payload(a,b)
        self.assertEqual(len(parse_daily(original,a,b)),2)
        broken=[]
        p=copy.deepcopy(original);p['daily_units']['snowfall_sum']='mm';broken.append(p)
        p=copy.deepcopy(original);p['daily']['time'].reverse();broken.append(p)
        p=copy.deepcopy(original);p['daily']['rain_sum']=[1];broken.append(p)
        p=copy.deepcopy(original);p['daily']['rain_sum'][0]=float('nan');broken.append(p)
        p=copy.deepcopy(original);p['daily']['rain_sum'][0]=-1;broken.append(p)
        for p in broken:
            with self.assertRaises(ValueError):parse_daily(p,a,b)

    def test_sync_fetches_recent_only_and_preserves_archive(self):
        from collector.sync import write_json
        today=date(2026,10,19)
        city=dict(id='turin',latitude=45.07,longitude=7.68,timezone='Europe/Rome')
        calls=[]
        def fetch(client,host,path,params,ttl,units):
            calls.append(params)
            a,b=date.fromisoformat(params['start_date']),date.fromisoformat(params['end_date'])
            return payload(a,b)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);save=Mock()
            collect(None,city,root,fetch,write_json,save,today)
            self.assertEqual(len(calls),1)
            self.assertEqual(calls[0]['models'],'era5')
            self.assertEqual(calls[0]['timezone'],'GMT')
            self.assertEqual(save.call_args.args[2]['completed_batches'],0)
            archive=root/'cities/turin/climate-archive-v1/1991-01-01_1993-12-31.json'
            write_json(archive,series_between(date(1991,1,1),date(1993,12,31)))
            saved=archive.read_bytes()
            collect(None,city,root,fetch,write_json,save,today+timedelta(days=1))
            self.assertEqual(len(calls),2)
            self.assertEqual(archive.read_bytes(),saved)
            self.assertEqual(save.call_args.args[2]['completed_batches'],1)
            with self.assertRaisesRegex(ValueError,'location changed'):
                collect(None,{**city,'latitude':44},root,fetch,write_json,save,today)


if __name__=='__main__':unittest.main()
