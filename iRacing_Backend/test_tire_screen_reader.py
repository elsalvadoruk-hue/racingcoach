"""App reader boundaries: fake SDK, individual screens, no desktop/game access."""
import unittest
from unittest.mock import Mock, patch
import numpy as np

from tire_reader.screen_reader import ScreenTireReader
from test_tire_cockpit_gate import cockpit


class ScreenReaderTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.context = cockpit(DisplayUnits=0)
        self.sdk = Mock(is_connected=True)
        self.sdk.startup.return_value = True
        self.screens = Mock()
        self.screens.monitors = [dict(left=-160, top=0, width=480, height=100)] + [
            dict(left=x, top=0, width=160, height=100) for x in (-160, 0, 160)]
        self.screens.grab.side_effect = lambda rect: np.zeros((100, 160, 4), dtype=np.uint8)
        self.locator, self.ocr, self.visibility = Mock(), Mock(), Mock()
        self.visibility.window_visible.return_value = True
        self.visibility.pressure_visible.return_value = True
        self.boxes = dict(FL=[0,0,60,30],FR=[80,0,140,30],RL=[0,50,60,80],RR=[80,50,140,80])
        self.location = dict(boxes=self.boxes,temperature_boxes={},temperature_kind=None)
        self.locator.locate.return_value = dict(status='located',location=self.location)
        self.profiles = []
        async def recognize(image, profile):
            self.profiles.append(profile)
            return dict(wheels={w:dict(kpa=172.3689) for w in self.boxes},temperatures={})
        self.ocr.recognize = Mock(side_effect=recognize)
        self.reader = ScreenTireReader(sdk_factory=lambda:self.sdk,
            screens_factory=lambda:self.screens,locator=self.locator,ocr=self.ocr,
            visibility=self.visibility,clock=lambda:self.now)
        self.addCleanup(self.reader.close)
        adapter = Mock()
        adapter.snapshot.side_effect = lambda _:dict(self.context,SessionTime=self.now+1)
        self.adapter_patch = patch('tire_reader.screen_reader.SdkReader', return_value=adapter)
        self.adapter_patch.start();self.addCleanup(self.adapter_patch.stop)
        self.frame_patch = patch('tire_reader.screen_reader.ocr_frame',
            return_value=(np.zeros((100,160,3),dtype=np.uint8),self.boxes,{}))
        self.frame_patch.start();self.addCleanup(self.frame_patch.stop)

    def enter(self):
        self.assertFalse(self.reader.sample()['overlay_ready'])
        self.now = 10

    def test_no_capture_or_ocr_until_ten_seconds_and_then_every_sample_reads(self):
        for self.now in (0,1,9.999):
            self.assertEqual(self.reader.sample()['status'],'cockpit_warmup')
        self.screens.grab.assert_not_called();self.ocr.recognize.assert_not_called()
        for self.now in (10,11,12):
            self.assertEqual(self.reader.sample()['status'],'reading')
        self.assertEqual(self.ocr.recognize.call_count,3)
        self.assertEqual(self.profiles[0]['temperature_unit'],'F')
        self.assertEqual(self.profiles[0]['screen_unit'],'auto')

    def test_search_each_monitor_and_retry_other_monitors_after_two_misses(self):
        self.enter()
        missing=dict(status='not_found',location=None)
        found=dict(status='located',location=self.location)
        self.locator.locate.side_effect=[missing,found,missing,missing,missing,found]
        self.assertEqual(self.reader.sample()['capture']['left'],0)
        self.now=11;self.reader.sample()
        self.now=12;self.reader.sample()
        self.now=13;self.assertEqual(self.reader.sample()['status'],'reading')
        rectangles=[call.args[0] for call in self.screens.grab.call_args_list]
        self.assertEqual([r['left'] for r in rectangles],[-160,0,0,0,0,-160])
        self.assertTrue(all(r['width']==160 for r in rectangles))

    def test_exit_hides_and_reentry_waits_ten_seconds(self):
        self.enter();self.reader.sample()
        self.context['IsOnTrack']=False;self.now=11
        self.assertFalse(self.reader.sample()['overlay_ready'])
        self.context['IsOnTrack']=True;self.now=12
        self.assertEqual(self.reader.sample()['warmup_remaining_s'],10)
        self.assertEqual(self.ocr.recognize.call_count,1)

    def test_context_change_during_ocr_discards_values(self):
        self.enter()
        async def recognize(*_):
            self.context['IsReplayPlaying']=True
            return dict(wheels={},temperatures={})
        self.ocr.recognize.side_effect=recognize
        result=self.reader.sample()
        self.assertEqual(result['status'],'context_changed_during_frame')
        self.assertFalse(result['overlay_ready'])
        self.assertNotIn('raw_kpa_FL',result)

    def test_voice_controls_use_the_latest_sdk_read_after_ocr(self):
        self.context.update(SteeringWheelAngle=0.,Throttle=1.,Brake=0.,Speed=30.)
        self.enter()
        async def recognize(*_):
            self.context.update(SteeringWheelAngle=.3,Throttle=0.,Brake=.8)
            return dict(wheels={w:dict(kpa=170.) for w in self.boxes},temperatures={})
        self.ocr.recognize.side_effect=recognize
        row=self.reader.sample()
        self.assertEqual(row['tire_snapshot']['brake'],.8)
        self.assertEqual(row['tire_snapshot']['steering'],.3)

    def test_occluded_or_expired_frame_never_yields_numbers(self):
        self.enter();self.visibility.pressure_visible.return_value=False
        result=self.reader.sample()
        self.assertEqual(result['status'],'pressure_area_occluded')
        self.assertNotIn('raw_kpa_FL',result)
        self.visibility.pressure_visible.return_value=True
        async def slow(*_):
            self.now+=2
            return dict(wheels={},temperatures={})
        self.ocr.recognize.side_effect=slow
        self.assertEqual(self.reader.sample()['status'],'frame_expired')

    def test_capture_failure_does_not_end_continuous_measurement(self):
        self.enter();self.screens.grab.side_effect=OSError('synthetic')
        self.assertEqual(self.reader.sample()['status'],'measurement_error')
        self.screens.grab.side_effect=lambda _:np.zeros((100,160,4),dtype=np.uint8)
        self.now=11
        self.assertEqual(self.reader.sample()['status'],'reading')

    def test_unknown_temperature_unit_and_unsupported_car(self):
        self.enter();self.context['DisplayUnits']=None
        self.reader.sample();self.assertEqual(self.profiles[0]['temperature_unit'],'unknown')
        self.context['car_path']='unsupported';self.now=11
        self.assertEqual(self.reader.sample()['status'],'unsupported_car')
        self.assertEqual(self.ocr.recognize.call_count,1)

    def test_cup_metric_integer_pressure_reaches_bar_display(self):
        from tire_reader.pressure_ocr import WindowsPressureOcr
        from tire_reader.pressure_display import PressureDisplay
        self.context.update(car_path='porsche9922cup', DisplayUnits=1)
        engine=object.__new__(WindowsPressureOcr)
        engine.language='synthetic';engine.languages=[];engine.max_dimension=2048
        async def text(_):return '159'
        engine._text=Mock(side_effect=text)
        self.reader.ocr=engine
        self.enter()
        row=self.reader.sample()
        self.assertEqual(row['status'],'reading')
        self.assertEqual([row['raw_kpa_'+w] for w in self.boxes],[159]*4)
        display=PressureDisplay();display.update(row)
        self.assertEqual(set(display.formatted().values()),{'1.59'})

    def test_kpa_profile_requires_verified_cup_and_metric_sdk_mode(self):
        self.enter()
        for car,mode,expected in [('porsche9922cup',1,('kPa',0,'kPa')),
                ('porsche9922cup',0,('bar',2,'auto')),
                ('porsche9922cup',None,('bar',2,'auto')),
                ('porsche9922cup',True,('bar',2,'auto')),
                ('porsche9922cup','1',('bar',2,'auto')),
                ('bmwm4gt3',1,('bar',2,'auto')),
                ('porsche992rgt3',1,('bar',2,'auto'))]:
            with self.subTest(car=car,mode=mode):
                # Each car transition starts its own ten-second gate.
                self.context.update(car_path=car,DisplayUnits=mode)
                self.reader.sample();self.now+=10
                self.assertEqual(self.reader.sample()['status'],'reading')
                profile=self.profiles[-1]
                self.assertEqual(tuple(profile[k] for k in ('unit','decimals','screen_unit')),expected)

    def test_unit_mode_change_during_ocr_discards_values(self):
        self.context.update(car_path='porsche9922cup',DisplayUnits=1)
        self.enter()
        async def recognize(*_):
            self.context['DisplayUnits']=0
            return dict(wheels={w:dict(kpa=159) for w in self.boxes},temperatures={})
        self.ocr.recognize.side_effect=recognize
        result=self.reader.sample()
        self.assertEqual(result['status'],'context_changed_during_frame')
        self.assertNotIn('raw_kpa_FL',result)
