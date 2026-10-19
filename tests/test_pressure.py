"""Pressure collection is tested against a synthetic multi-location JSON API."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from collector.pressure import collect, plan, VARIABLES
from collector.sync import write_json

CONFIG={'map_extent':[-20,35,30,65],'map_grid_degrees':4,'map_steps':list(range(12,349,24))}
NOW=datetime(2026,10,19,10,tzinfo=timezone.utc)
REFERENCE=NOW.replace(hour=0)

class PlanTests(unittest.TestCase):
    def test_default_grid_cost_is_bounded_and_documented(self):
        value=plan(CONFIG)
        self.assertEqual(len(value['points']),150)
        self.assertEqual([len(b) for b in value['batches']],[50,50,50])
        self.assertEqual(value['units'],[54,54,54])
        self.assertEqual(value['total_units'],162)

    def test_overlarge_or_too_fine_maps_rejected_before_fetch(self):
        for changes in ({'map_grid_degrees':.25},{'map_extent':[-180,180,-90,90]},{'map_steps':[360]}):
            with self.assertRaises(ValueError): plan({**CONFIG,**changes})

    def test_endpoints_are_included_and_point_order_is_stable(self):
        value=plan(CONFIG)
        self.assertEqual(value['points'][0],(30,-20))
        self.assertEqual(value['points'][-1],(65,35))
        self.assertEqual(len(set(value['points'])),150)

@unittest.skipUnless(importlib.util.find_spec('numpy') and importlib.util.find_spec('matplotlib'),'Plot dependencies unavailable')
class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)
        self.fetch=Mock(side_effect=self.response)
        self.render=patch('collector.maps.render_fields',side_effect=lambda fields,path,*args:path.write_bytes(b'fixture png')).start()
        patch('collector.maps.render_animation',side_effect=lambda paths,path,labels:path.write_bytes(b'fixture gif')).start()
        self.addCleanup(patch.stopall)
        self.addCleanup(self.temporary.cleanup)
        self.missing=False

    def response(self,client,host,path,params,ttl,units):
        self.assertEqual(host,'api.open-meteo.com')
        self.assertEqual(path,'/v1/forecast')
        self.assertEqual(params['models'],'ecmwf_ifs025')
        self.assertEqual(params['temporal_resolution'],'hourly_6')
        self.assertEqual(params['end_date'],'2026-11-02')
        self.assertEqual(ttl,43200)
        self.assertEqual(units,54)
        self.assertEqual(params['elevation'].split(','),['nan']*len(params['latitude'].split(',')))
        payload=[]
        for lat,lon in zip(map(float,params['latitude'].split(',')),map(float,params['longitude'].split(','))):
            times=[int(REFERENCE.timestamp())+step*3600 for step in CONFIG['map_steps']]
            values={'time':times,VARIABLES[0]:[5500+lat]*len(times),VARIABLES[1]:[1010+lon/10]*len(times),VARIABLES[2]:[20-lat/10]*len(times)}
            if self.missing: values[VARIABLES[0]][-1]=None
            payload.append({'latitude':lat,'longitude':lon,'hourly_units':dict(zip(VARIABLES,['m','hPa','°C'])),'hourly':values})
        return payload

    def test_three_requests_render_fifteen_frames_and_preserve_units(self):
        collect(None,CONFIG,self.fetch,write_json,self.root,now=NOW)
        self.assertEqual(self.fetch.call_count,3)
        self.assertEqual(self.render.call_count,15)
        manifest=json.loads((self.root/'pressure.json').read_text())['data']
        self.assertEqual(manifest['quota_units'],162)
        self.assertEqual([f['day'] for f in manifest['frames']],list(range(1,16)))
        self.assertEqual(manifest['provider'],'Open-Meteo')
        self.assertNotIn('run',manifest)  # collection/reference time is not model initialisation time
        fields=self.render.call_args_list[0].args[0]
        self.assertEqual(fields['height'][2][0,0],5530)
        self.assertEqual(fields['pressure'][2][0,0],1008)
        self.assertEqual(fields['temperature'][2][0,0],17)

    def test_missing_last_day_is_explicit_not_extrapolated(self):
        self.missing=True
        collect(None,CONFIG,self.fetch,write_json,self.root,now=NOW)
        manifest=json.loads((self.root/'pressure.json').read_text())['data']
        self.assertEqual(len(manifest['frames']),14)
        self.assertEqual(manifest['unavailable'][0]['day'],15)
        self.assertEqual(self.render.call_count,14)

    def test_failed_batch_keeps_previous_manifest_and_never_retries(self):
        previous={'data':{'frames':['previous']}}
        write_json(self.root/'pressure.json',previous)
        self.fetch.side_effect=OSError('offline')
        with self.assertRaises(OSError): collect(None,CONFIG,self.fetch,write_json,self.root,now=NOW)
        self.assertEqual(self.fetch.call_count,1)
        self.render.assert_not_called()
        self.assertEqual(json.loads((self.root/'pressure.json').read_text()),previous)

    def test_response_order_mismatch_refused(self):
        def wrong(*args):
            payload=self.response(*args)
            return list(reversed(payload))
        self.fetch.side_effect=wrong
        with self.assertRaisesRegex(ValueError,'coordinates'): collect(None,CONFIG,self.fetch,write_json,self.root,now=NOW)
        self.assertFalse((self.root/'pressure.json').exists())

    def test_unexpected_temperature_units_refused(self):
        def wrong(*args):
            payload=self.response(*args)
            payload[0]['hourly_units'][VARIABLES[2]]='K'
            return payload
        self.fetch.side_effect=wrong
        with self.assertRaisesRegex(ValueError,'units'): collect(None,CONFIG,self.fetch,write_json,self.root,now=NOW)

if __name__=='__main__': unittest.main()
