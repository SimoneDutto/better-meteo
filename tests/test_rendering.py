"""Synthetic fields only. These never contact ECMWF or ARPA."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HAS_PLOTS = all(importlib.util.find_spec(m) for m in ('numpy','matplotlib','PIL'))

@unittest.skipUnless(HAS_PLOTS,'Install plotting requirements to check PNG rendering')
class RenderingTests(unittest.TestCase):
    def test_forecast_gif_has_distinct_frames_and_loops(self):
        from PIL import Image
        from collector.maps import render_animation
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            paths=[]
            for i,colour in enumerate(('white','black')):
                path=root/f'frame-{i}.png'
                Image.new('RGB',(800,200),colour).save(path)
                paths.append(path)
            output=root/'loop.gif'
            render_animation(paths,output,['DAY 01/15 | 19 Oct 2026 | 12:00 UTC','DAY 02/15 | 20 Oct 2026 | 12:00 UTC'])
            with Image.open(output) as gif:
                self.assertEqual(gif.n_frames,2)
                self.assertEqual(gif.size,(800,296))
                self.assertLess(gif.convert('L').crop((0,0,800,90)).getextrema()[0],255)
                self.assertEqual(gif.info['loop'],0)
                self.assertEqual(gif.info['duration'],1200)
                self.assertEqual(gif.convert('RGB').getpixel((0,100)),(255,255,255))
                gif.seek(1)
                self.assertEqual(gif.info['duration'],2400)
                self.assertEqual(gif.convert('RGB').getpixel((0,100)),(0,0,0))

    def test_invalid_animation_preserves_previous_file(self):
        from PIL import Image
        from collector.maps import render_animation
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            output=root/'loop.gif'
            output.write_bytes(b'previous animation')
            for i,size in enumerate(((40,20),(50,20))): Image.new('RGB',size).save(root/f'{i}.png')
            with self.assertRaises(ValueError): render_animation([root/'0.png',root/'1.png'],output,['19 Oct 2026','20 Oct 2026'])
            self.assertEqual(output.read_bytes(),b'previous animation')

    def test_pressure_png_from_three_synthetic_fields(self):
        import numpy as np
        from PIL import Image
        from collector.maps import render_fields
        x,y=np.arange(-20,36),np.arange(30,66)
        xx,yy=np.meshgrid(x,y)
        fields={
            'height':(x,y,5500+8*(yy-30)+3*(xx+20)),
            'pressure':(x,y,1000+.4*(yy-30)+.1*(xx+20)),
            'temperature':(x,y,8+.3*(yy-30)+.1*(xx+20))}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.png'
            render_fields(fields,path,[-20,35,30,65],datetime(2026,10,19,tzinfo=timezone.utc))
            with Image.open(path) as image:
                self.assertEqual(image.size,(1680,840))
                array=np.asarray(image.convert('RGB'))
                self.assertTrue(np.array_equal(array[:,:,0],array[:,:,1]))

    def test_kindle_snapshot_dimensions_and_missing_values(self):
        from PIL import Image
        from collector.snapshot import render_snapshot
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            target=root/'cities'/'turin'; target.mkdir(parents=True)
            payload={'updated':'2026-10-19T00:00:00+00:00','data':{'daily':{
                'time':['2026-10-19'],'temperature_2m_min':[None],
                'temperature_2m_max':[20],'precipitation_sum':[0]}}}
            (target/'forecast.json').write_text(json.dumps(payload))
            render_snapshot({'id':'turin','name':'Torino'},root)
            with Image.open(root/'media'/'turin-dashboard.png') as image: self.assertEqual(image.size,(600,800))

    def test_missing_forecast_cannot_create_fictional_snapshot(self):
        from collector.snapshot import render_snapshot
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'No forecast cache'):
                render_snapshot({'id':'turin'},Path(directory))

if __name__=='__main__': unittest.main()
