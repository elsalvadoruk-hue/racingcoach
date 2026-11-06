"""Weather panel layout parsing, forecast stitching and the expected track temperature.

The word list mirrors real Windows OCR output of the in-sim panel (Korean OCR engine,
2026-11-04): '0C' for °C, a dropped colon in '345', and the wind arrow read as '7'.
"""
from pathlib import Path
import json
import tempfile
import unittest

from tire_reader.weather_panel import Forecast, expected_track, learned_offset, parse_panel


def panel_words(times=('3:02', '3:15', '3:30', '345', '4:00', '4:15'), temps=(19, 19, 20, 20, 21, 21), suffix='pm',
                unit='0C', speed_unit='km/h'):
    words = []
    for i, (clock, temp) in enumerate(zip(times, temps)):
        x = 235 + i * 65
        words.append((clock, x, 111, 22, 14))
        if suffix:
            words.append((suffix, x + 25, 113, 16, 12))
        words += [(str(temp), x + 2, 164, 14, 16), (unit, x + 22, 164, 18, 16), ('0%', x + 23, 189, 18, 12),
                  ('86%', x + 20, 211, 22, 12), ('SW', x + 21, 237, 18, 12), ('8', x + 2, 254, 8, 12),
                  (speed_unit, x + 13, 254, 30, 12)]
        if i in (2, 5):
            words.append(('7', x + 5, 236, 8, 12))  # the wind arrow
    words += [('Ask', 60, 300, 30, 14), ('spotter', 95, 300, 50, 14), ('WEATHER', 30, 70, 80, 16)]
    return words


class PanelTests(unittest.TestCase):
    def test_reads_six_columns_from_real_ocr_layout(self):
        panel = parse_panel(panel_words())
        columns = panel['columns']
        self.assertEqual([c['minute_of_day'] for c in columns], [902, 915, 930, 945, 960, 975])
        self.assertEqual([c['air_c'] for c in columns], [19., 19., 20., 20., 21., 21.])
        self.assertEqual({(c['rain_pct'], c['humidity_pct'], c['wind_dir'], c['wind_kph']) for c in columns},
                         {(0, 86, 'SW', 8.)})

    def test_24_hour_fahrenheit_and_mph(self):
        panel = parse_panel(panel_words(times=('15:02', '15:15', '15:30', '15:45', '16:00', '16:15'),
                                        temps=(68, 68, 70, 70, 72, 72), suffix=None, unit='°F', speed_unit='mph'))
        self.assertEqual(panel['columns'][0]['minute_of_day'], 902)
        self.assertEqual(panel['columns'][2]['air_c'], 21.1)
        self.assertEqual(panel['columns'][0]['wind_kph'], 12.9)

    def test_joined_degree_sign_and_misread_quarter_hour(self):
        # First column only: '19' and '0C' read as one word.
        words = [(t, x, y, w, h) for t, x, y, w, h in panel_words() if (t, x) != ('0C', 257)]
        words = [('190C', x, y, w + 18, h) if (t, x, y) == ('19', 237, 164) else (t, x, y, w, h) for t, x, y, w, h in words]
        words = [('3:40', x, y, w, h) if t == '3:30' else (t, x, y, w, h) for t, x, y, w, h in words]
        panel = parse_panel(words)
        self.assertEqual(panel['columns'][0]['air_c'], 19.)
        self.assertEqual(panel['columns'][2]['minute_of_day'], 930)

    def test_no_panel_without_a_row_of_times(self):
        self.assertIsNone(parse_panel([('19', 10, 10, 14, 16), ('0C', 30, 10, 18, 16), ('3:02', 10, 50, 22, 14)]))
        self.assertIsNone(parse_panel(panel_words(temps=('', '', '', '', '', ''))))


class ForecastTests(unittest.TestCase):
    def test_stitches_reads_interpolates_and_tracks_misses(self):
        forecast = Forecast()
        forecast.update('session', parse_panel(panel_words())['columns'], 902 * 60, 19.)
        later = parse_panel(panel_words(times=('4:02', '4:15', '4:30', '4:45', '5:00', '5:15'), temps=(22, 22, 23, 23, 24, 24)))
        forecast.update('session', later['columns'], 962 * 60, 21.)
        self.assertEqual(len(forecast.entries), 11)
        self.assertEqual(forecast.air_at(937.5 * 60), 20.)
        self.assertAlmostEqual(forecast.air_at(922.5 * 60), 19.5)
        self.assertIsNone(forecast.air_at(2000 * 60))
        self.assertEqual(forecast.misses, [0.0])  # 16:00 forecast 21, actual 21 (rounded to the quarter)
        forecast.update('other session', later['columns'], 962 * 60, 21.)
        self.assertEqual(len(forecast.entries), 6)

    def test_expected_track_uses_learned_offset_then_current_offset(self):
        forecast = Forecast()
        forecast.update('s', parse_panel(panel_words())['columns'], 902 * 60, 19.)
        self.assertEqual(expected_track(forecast, at_s=975 * 60, air_now=19., track_now=30., learned=9.),
                         (30., 'forecast_with_learned_offset'))
        self.assertEqual(expected_track(forecast, at_s=975 * 60, air_now=19., track_now=30., learned=None),
                         (32., 'forecast_with_current_offset'))
        self.assertEqual(expected_track(None, at_s=975 * 60, air_now=19., track_now=30., learned=None), (30., 'current'))

    def test_learned_offset_needs_enough_rows_near_that_time_of_day(self):
        from tire_reader.history import TireHistoryStore
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'history.sqlite3'
            store = TireHistoryStore(path)
            for i in range(80):
                data = {'session_time_of_day_s': 54000 + i * 30, 'air_temp_c': 20., 'track_temp_c': 28. + (i % 3)}
                store.db.execute("INSERT INTO tire_observations(created,run,game,car,track,lap,t,data,coach_run) "
                                 "VALUES(0,'r','iracing','c','track',1,0,?,'')", (json.dumps(data),))
            store.db.commit(); store.close()
            self.assertEqual(learned_offset(path, 'iracing', 'track', 55000), 9.)
            self.assertIsNone(learned_offset(path, 'iracing', 'track', 30000))  # morning: no evidence
            self.assertIsNone(learned_offset(path, 'iracing', 'other', 55000))


class ReaderWiringTests(unittest.TestCase):
    def test_forecast_reaches_the_snapshot_but_not_every_history_row(self):
        from unittest.mock import Mock, patch
        import numpy as np
        from tire_reader.screen_reader import ScreenTireReader
        from tire_reader.history import TireHistoryStore
        from tire_reader.snapshot import TireSnapshot
        from test_tire_cockpit_gate import cockpit
        now = [0.]
        sdk = Mock(is_connected=True); sdk.startup.return_value = True
        screens = Mock(); screens.monitors = [dict(left=0, top=0, width=800, height=400)] * 2
        screens.grab.return_value = np.zeros((400, 800, 4), np.uint8)
        ocr = Mock(max_dimension=2600)
        async def words(_): return panel_words()
        ocr.words = Mock(side_effect=words)
        visibility = Mock(); visibility.window_visible.return_value = True
        reader = ScreenTireReader(sdk_factory=lambda: sdk, screens_factory=lambda: screens, locator=Mock(), ocr=ocr,
                                  visibility=visibility, clock=lambda: now[0], weather=True)
        self.addCleanup(reader.close)
        context = cockpit(IsInGarage=True, IsOnTrack=False, SessionTimeOfDay=902 * 60., AirTemp=19., TrackTempCrew=30.)
        adapter = Mock(); adapter.snapshot.side_effect = lambda _: dict(context, SessionTime=now[0] + 1)
        with patch('tire_reader.screen_reader.SdkReader', return_value=adapter):
            row = reader.sample()
            now[0] = 30.; reader.sample()
        self.assertEqual(ocr.words.call_count, 1)  # not again until the reread time
        forecast = row['tire_snapshot']['weather_forecast']
        self.assertEqual([e['air_c'] for e in forecast['entries']], [19., 19., 20., 20., 21., 21.])
        with tempfile.TemporaryDirectory() as temp:
            store = TireHistoryStore(Path(temp) / 'h.sqlite3')
            try:
                store.append(TireSnapshot.from_dict(row['tire_snapshot']))
                stored = store.db.execute('SELECT data FROM tire_observations').fetchone()
                self.assertTrue(stored is None or 'weather_forecast' not in json.loads(stored[0]))
            finally:
                store.close()

    def test_controller_uses_the_forecast_for_the_advised_track_temperature(self):
        from dataclasses import replace
        from tire_overlay_control import TireOverlayController
        from test_tire_advisor import snapshot
        forecast = Forecast(); forecast.update('s', parse_panel(panel_words())['columns'], 902 * 60, 19.)
        with tempfile.TemporaryDirectory() as temp:
            controller = TireOverlayController(Path(temp) / 'overlay/iracing/tires.json')
            s = replace(snapshot(0.), weather_forecast=forecast.view(), session_time_of_day_s=902 * 60.,
                        air_temp_c=19., track_temp_c=30.)
            value, info = controller._expected_track(s, 'garage', None)
            # 15:02 + 30 min = 15:32: forecast air 20 °C + current track-air gap 11 °C.
            self.assertEqual((value, info['basis'], info['minutes_ahead']), (31., 'forecast_with_current_offset', 30))
            value, info = controller._expected_track(replace(s, weather_forecast=None), 'garage', None)
            self.assertEqual((value, info['basis']), (30., 'current'))
            # Beyond the forecast (pit far away): the current track temperature is used.
            far = replace(s, lap_last_s=120.)
            self.assertEqual(controller._expected_track(far, 'next_pit', 30)[1]['basis'], 'current')


if __name__ == '__main__':
    unittest.main()
