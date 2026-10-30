from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
import xml.etree.ElementTree as ET

from fastapi import FastAPI
from fastapi.testclient import TestClient
from overlay_control import OverlayController,routes
from overlay_preview import OverlayPreview
from overlay_studio import defaults
ROOT=Path(__file__).resolve().parents[1]


def body(game='iracing'):
    config=defaults()
    if game=='lmu':config['source']='personal'
    return dict(config=config,session='practice',scene='after',availability='g61' if game=='iracing' else 'personal',
                appearance=dict(width=131,height=107,content_opacity=100,background_opacity=85),pixel_ratio=1.25)


class PreviewRouteTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.controller=OverlayController(Path(self.folder.name)/'settings.json')
        app=FastAPI();app.include_router(routes(self.controller));self.client=TestClient(app)

    def test_preview_never_selects_controller_or_writes_preferences_or_starts_live_child(self):
        response={'svg':'<svg/>','renderer':'native-tk-v1','texts':[],'ids':[],'width':390,'height':150}
        with patch('overlay_control.OverlayPreview.render',return_value=response) as renderer,patch.object(self.controller,'select_game',side_effect=AssertionError('preview changes game')):
            self.assertEqual(self.client.post('/api/overlay/studio/render?game=iracing',json=body()).status_code,200)
        self.assertEqual(renderer.call_args.args[0]['game'],'iracing')
        self.assertIsNone(self.controller.process)
        self.assertFalse(list(Path(self.folder.name).rglob('*.json')))

    def test_invalid_inputs_cannot_launch_renderer(self):
        with patch('overlay_control.OverlayPreview.render') as renderer:
            for changes in ({'pixel_ratio':9},{'appearance':{'width':1000}},{'scene':'file://secret'},{'extra':'no'}):
                self.assertEqual(self.client.post('/api/overlay/studio/render',json={**body(),**changes}).status_code,422)
            invalid=body('lmu');invalid['config']['source']='garage61'
            self.assertEqual(self.client.post('/api/overlay/studio/render?game=lmu',json=invalid).status_code,422)
        renderer.assert_not_called()

    def test_cached_request_reuses_result_and_cache_is_bounded(self):
        renderer=OverlayPreview();value=body();value['game']='iracing'
        completed=subprocess.CompletedProcess([],0,json.dumps({'svg':'<svg/>','renderer':'native-tk-v1'}).encode(),b'')
        with patch('overlay_preview.subprocess.run',return_value=completed) as run:
            renderer.render(value);renderer.render(value)
            self.assertEqual(run.call_count,1)
            for width in range(75,115):
                request=deepcopy(value);request['appearance']['width']=width;renderer.render(request)
        self.assertEqual(len(renderer.cache),32)

    def test_timeout_releases_worker_lock_and_allows_retry(self):
        renderer=OverlayPreview()
        with patch('overlay_preview.subprocess.run',side_effect=subprocess.TimeoutExpired('synthetic',5)):
            with self.assertRaises(RuntimeError):renderer.render(body())
        self.assertTrue(renderer.lock.acquire(blocking=False));renderer.lock.release()

    def test_large_previews_are_evicted_before_retained_strings_exceed_eight_mib(self):
        renderer = OverlayPreview()
        svg = '<svg><!--' + 'x' * 1_000_000 + '--></svg>'
        completed = subprocess.CompletedProcess([], 0, json.dumps({'svg': svg, 'renderer': 'native-tk-v1'}).encode(), b'')
        with patch('overlay_preview.subprocess.run', return_value=completed) as run:
            for slot in range(16):
                renderer.render({'slot': slot})
            self.assertLessEqual(sum(sys.getsizeof(row['svg']) for row in renderer.cache.values()), 8 * 1024 * 1024)
            renderer.render({'slot': 15})
            self.assertEqual(run.call_count, 16)
            renderer.render({'slot': 0})
            self.assertEqual(run.call_count, 17)


@unittest.skipUnless(sys.platform=='win32','Actual Windows native fonts')
class NativeRenderTests(unittest.TestCase):
    def test_actual_native_renderer_outputs_pedal_image_and_matching_coach_values_in_both_games(self):
        renderer=OverlayPreview();ns={'s':'http://www.w3.org/2000/svg'}
        for game in ('iracing','lmu'):
            value=body(game);value['game']=game
            for mode in ('inputs','coach'):
                value['config']['modes']['practice']=mode
                result=renderer.render(value);svg=ET.fromstring(result['svg'])
                self.assertEqual((result['width'],result['height']),(511.2,160.8))
                self.assertEqual(svg.attrib['width'],'639')
                if mode=='inputs':
                    self.assertEqual(len(svg.findall('.//s:image',ns)),1)
                    self.assertIn('4',result['texts'])
                else:
                    self.assertIn('-0.120',result['texts'])
                    self.assertIn('1:37.607' if game=='iracing' else '1:49.286',result['texts'])
                if game=='lmu':self.assertNotIn('Garage61',result['svg'])


if __name__=='__main__':unittest.main()
