"""Official turn numbers: map fitting, suggestions, confirmation, and their use in analysis and on the radio."""
from dataclasses import replace
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from test_corner_comparison import api, telemetry
from analysis_support import CornerDefinitionStore
import corner_names
from live_corners import LiveCornerCoach, describe, lap_answer
from live_telemetry import LiveTelemetry
import radio_phrases

LAT0, LON0 = 50.4, 5.97


def circuit(count=1200):
    """An unsymmetric closed lap in metres (east, north), so only one rotation fits."""
    angle = np.linspace(0, 2 * math.pi, count, endpoint=False)
    radius = 300 + 80 * np.cos(angle) + 60 * np.sin(angle) + 50 * np.sin(2 * angle) + 40 * np.sin(3 * angle)
    return np.c_[radius * np.cos(angle), radius * np.sin(angle)]


def gps(points):
    lat = LAT0 + np.degrees(points[:, 1] / 6371000.)
    lon = LON0 + np.degrees(points[:, 0] / (6371000. * math.cos(math.radians(LAT0))))
    return lat, lon


def to_map(points, degrees=40., scale=.5, shift=(900., 500.)):
    angle = math.radians(degrees)
    rotation = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
    return scale * np.c_[points[:, 0], -points[:, 1]] @ rotation.T + np.array(shift)


def write_map(root, track_id, lap, names, mirror=False, start=None):
    folder = Path(root) / str(track_id)
    folder.mkdir(parents=True)
    drawn = to_map(np.c_[-lap[:, 0], lap[:, 1]] if mirror else lap)
    if start is not None:
        x, y = drawn[int(start * len(drawn))]
        (folder / 'start-finish.svg').write_text(f'<svg><path d="M{x - 3:.1f},{y - 3:.1f} l6,0 l0,6 l-6,0 z"/></svg>', encoding='utf-8')
    (folder / 'active.svg').write_text('<svg><polygon points="' + ' '.join(f'{x:.2f},{y:.2f}' for x, y in drawn) + '"/></svg>', encoding='utf-8')
    centre = drawn.mean(0)
    texts = []
    for position, text in names:
        point = drawn[int(position * len(drawn))]
        beside = point + 14 * (point - centre) / np.linalg.norm(point - centre)   # labels sit beside the track
        texts.append(f'<text class="a" transform="translate({beside[0] - len(text) * 18 * .28:.2f} {beside[1] + 18 * .35:.2f})">{text}</text>')
    (folder / 'turns.svg').write_text('<svg><style>.a{font-size:18px;}</style>' + ''.join(texts) + '</svg>', encoding='utf-8')


def segments(*positions):
    return [{'id': f'C{n}', 'entry_pct': p - .03, 'apex_pct': p, 'exit_pct': p + .03} for n, p in enumerate(positions, 1)]


class MapFitTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.lap = circuit()
        self.pct = np.arange(len(self.lap)) / len(self.lap)
        self.lat, self.lon = gps(self.lap)

    def test_path_sampling_follows_lines_and_curves(self):
        points = np.array(corner_names.sample_path('M10,10 l10,0 V30 H10 z M50,50 C60,50 70,60 70,70 s10,20 20,20'))
        self.assertEqual(points[:5].tolist(), [[10, 10], [20, 10], [20, 30], [10, 30], [10, 10]])
        self.assertEqual(points[5].tolist(), [50, 50])
        self.assertTrue(np.allclose(points[10], [70, 70]) and np.allclose(points[-1], [90, 90]))

    def test_numbers_are_suggested_for_the_segments_they_stand_beside(self):
        write_map(self.work.name, 7, self.lap, [(.10, '1'), (.13, '2'), (.40, '3'), (.70, '4'), (.70, 'Hairpin'), (.85, '5a')])
        definitions = segments(.11, .25, .40, .70, .85)
        result = corner_names.suggest(definitions, self.pct, self.lat, self.lon, 7, root=self.work.name)
        self.assertEqual((result['status'], result['kind']), ('SUGGESTED', 'numbered'))
        self.assertLess(result['fit_median_m'], 3)
        self.assertEqual({key: value['number'] for key, value in result['corners'].items()}, {'C1': '1-2', 'C3': '3', 'C4': '4', 'C5': '5a'})
        self.assertEqual(result['corners']['C4']['name'], 'Hairpin')
        self.assertNotIn('C2', result['corners'])  # no official label there: left for the driver

    def test_named_layouts_suggest_names(self):
        write_map(self.work.name, 8, self.lap, [(.10, 'Hatzenbach'), (.40, 'Flugplatz'), (.70, 'Schwedenkreuz'), (.85, 'T13')])
        result = corner_names.suggest(segments(.10, .40, .70, .85), self.pct, self.lat, self.lon, 8, root=self.work.name)
        self.assertEqual(result['kind'], 'named')
        self.assertEqual({key: (value['number'], value['name']) for key, value in result['corners'].items()},
                         {'C1': (None, 'Hatzenbach'), 'C2': (None, 'Flugplatz'), 'C3': (None, 'Schwedenkreuz')})

    def test_a_drawing_that_does_not_match_the_lap_gives_no_suggestion(self):
        write_map(self.work.name, 9, self.lap, [(.1, '1'), (.4, '2'), (.7, '3')])
        with patch.object(corner_names, 'FIT_LIMIT_M', -1.):   # any fit worse than the limit is refused
            result = corner_names.suggest(segments(.1, .4, .7), self.pct, self.lat, self.lon, 9, root=self.work.name)
        self.assertEqual((result['status'], result['corners']), ('MAP_FIT_FAILED', {}))
        self.assertEqual(corner_names.suggest(segments(.1), self.pct, self.lat, self.lon, 404, root=self.work.name)['status'], 'NO_OFFICIAL_MAP')
        write_map(self.work.name, 10, self.lap, [(.1, '1'), (.4, '2'), (.7, '3')])
        no_gps = corner_names.suggest(segments(.1), self.pct, np.full(len(self.pct), np.nan), self.lon, 10, root=self.work.name)
        self.assertEqual(no_gps['status'], 'NO_GPS')

    def test_an_oval_is_turned_the_right_way_by_its_start_finish_marker(self):
        angle = np.linspace(0, 2 * math.pi, 1200, endpoint=False)
        oval = np.c_[400 * np.cos(angle), 200 * np.sin(angle)]   # fits itself turned by 180 degrees
        lat, lon = gps(oval)
        definitions, turns = segments(.2, .45, .7, .95), [(.2, '1'), (.45, '2'), (.7, '3'), (.95, '4')]
        write_map(self.work.name, 20, oval, turns, start=0.)
        right = corner_names.suggest(definitions, self.pct, lat, lon, 20, root=self.work.name)
        self.assertEqual({key: value['number'] for key, value in right['corners'].items()}, {'C1': '1', 'C2': '2', 'C3': '3', 'C4': '4'})
        # The same drawing with the line on the other straight: the lap must be laid the other way round.
        write_map(self.work.name, 21, oval, turns, start=.5)
        turned = corner_names.suggest(definitions, self.pct, lat, lon, 21, root=self.work.name)
        self.assertEqual({key: value['number'] for key, value in turned['corners'].items()}, {'C1': '3', 'C2': '4', 'C3': '1', 'C4': '2'})

    def test_spoken_call_prefers_own_wording_then_number_then_name(self):
        self.assertEqual(corner_names.spoken_call('5', 'Pouhon'), '5번 코너')
        self.assertEqual(corner_names.spoken_call('1-2', None), '1번 코너')
        self.assertEqual(corner_names.spoken_call(None, 'Flugplatz'), 'Flugplatz')
        self.assertEqual(corner_names.spoken_call(None, 'Flugplatz', '플룩플라츠'), '플룩플라츠')
        self.assertIsNone(corner_names.spoken_call(None, None))


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.path = Path(self.work.name) / 'corners.json'
        self.store = CornerDefinitionStore(self.path)
        api._CORNER_DEFINITIONS.clear()

    def test_confirmed_names_follow_the_layout_across_cars_and_restarts(self):
        self.assertEqual(self.store.names_for(segments(.1, .4), '523', ''), {})
        self.store.put(('car-1', 'p', 'n', '523', 'tp', 'tn', '', 'v1', 7000), segments(.1, .4))
        self.store.set_track_names('523', '', [{'pct': .1, 'number': '1', 'name': 'La Source', 'spoken': None},
                                               {'pct': .4, 'number': None, 'name': 'Raidillon', 'spoken': '라디용'}])
        restarted = CornerDefinitionStore(self.path)
        # Another car split the lap differently: the saved positions land on its own segments.
        other = segments(.105, .25, .395)
        self.assertEqual(restarted.names_for(other, '523', ''), {'C1': {'number': '1', 'name': 'La Source', 'spoken': None},
                                                                  'C3': {'number': None, 'name': 'Raidillon', 'spoken': '라디용'}})
        self.assertEqual(restarted.names_for(other, '523', 'Endurance'), {})   # another layout
        self.assertEqual(restarted.find('car-1', '523', ''), segments(.1, .4))  # segment definitions were kept
        with self.assertRaises(ValueError):
            self.store.set_track_names(None, '', [])

    def late_corner(self):
        data = telemetry()
        distance = np.linspace(.0003, .9997, len(data.channels['LapDistPct']))
        braking = (distance > .96) & (distance < .985)
        data.channels.update({'LapDistPct': distance, 'SessionTime': 1000 + distance * 100, 'LapCurrentLapTime': distance * 100,
                              'Brake': np.where(braking, .8, 0.), 'Throttle': np.where(distance < .96, 1., 0.),
                              'Speed': np.where(distance < .96, 50., np.where(distance < .985, 50 - (distance - .96) * 1200, 20.))})
        data.metadata.update(track_platform_id='900', track_config='GP', car_platform_id='', track_name='Test Ring')
        file = Path(self.work.name) / 'x.ibt'
        file.write_bytes(b'')
        return replace(data, path=file)

    def test_analysis_and_api_use_the_confirmed_call_and_keep_automatic_labels_otherwise(self):
        data = self.late_corner()
        with patch.object(api, '_CORNER_DEFINITION_STORE', self.store), patch.object(api, 'resolve_telemetry_file', return_value=Path('x.ibt')), \
             patch.object(api.repository, 'load', return_value=data):
            before = api.TelemetryAnalyzer(data).analyze(api.AnalyzeRequest(include_ai=False))
            self.assertEqual(before['performance']['corners'][0]['label'], '자동 구간 1')
            self.assertFalse(before['performance']['corner_names_confirmed'])
            from fastapi.testclient import TestClient
            with TestClient(api.app) as client:
                listed = client.get('/api/corner-names', params={'filename': 'x.ibt'}).json()
                self.assertEqual((listed['status'], listed['confirmed'], len(listed['corners'])), ('NO_GPS', False, 1))
                saved = client.put('/api/corner-names', json={'filename': 'x.ibt', 'corners': [
                    {'id': 'C1', 'number': ' 14 ', 'name': 'Last', 'spoken': ''}, {'id': 'C9', 'number': '2'}]}).json()
            self.assertEqual((saved['status'], saved['confirmed']), ('CONFIRMED', True))
            self.assertEqual((saved['corners'][0]['number'], saved['corners'][0]['call']), ('14', '14번 코너'))
            after = api.TelemetryAnalyzer(data).analyze(api.AnalyzeRequest(include_ai=False))
            corner = after['performance']['corners'][0]
            self.assertEqual((corner['label'], corner['official_turn_number'], corner['turn_name'], corner['id']), ('14번 코너', '14', 'Last', 'C1'))
            self.assertTrue(after['performance']['corner_names_confirmed'])
            live = api.live_corner_definitions({'car_id': '', 'track_id': '900', 'track_config': 'GP'})
            self.assertIsNone(live)  # segment definitions of a car without an id are not stored


CORNER = {'id': 'C1', 'entry_pct': .30, 'apex_pct': .35, 'exit_pct': .40, 'call': '5번 코너'}


def drive(sink, laps):
    clock = 0.
    for lap, extra in enumerate(laps, 1):
        pct = 0.
        while pct < 1:
            inside = CORNER['entry_pct'] <= pct <= CORNER['exit_pct']
            sink({'distance_pct': pct, 'session_time': clock, 'lap_number': lap, 'speed_mps': (27. if extra else 30.) if inside else 60.,
                  'brake': .8 if .31 <= pct < .34 else 0., 'throttle': 1. if pct < .31 or pct >= .36 else 0., 'on_track': True, 'is_replay': False,
                  'on_pit_road': False, 'incidents': 0, 'session_flags': 0, 'car_left_right': 1, 'lap_last_s': 80., 'session_num': 0,
                  'session_state': 4, 'steering_rad': 0.})
            clock, pct = clock + 1 / 30, pct + (1 / 80.) * (8. / (8. + extra) if inside else 1.) / 30


class RadioCallTests(unittest.TestCase):
    def test_confirmed_corner_is_called_by_number_in_answers_and_before_the_corner(self):
        now = [0.]
        live = LiveTelemetry(clock=lambda: now[0])
        live.corners.lookup = lambda identity: [CORNER]
        spoken = []

        def feed(sample):
            now[0] = sample['session_time']
            live.ingest({'car_id': '1', 'track_id': '9', 'track_config': ''}, sample)
            spoken.extend(e['speech_text'] for e in live.radio.view(now[0])['events'] if e['key'] == 'corner_focus' and e['speech_text'] not in spoken)
        drive(feed, [0., 0., .4, 0.])
        self.assertEqual(spoken, ['다음 5번 코너. 지난 랩에 0.4초 잃었습니다. 최저속도가 낮았습니다.'])
        answer = lap_answer(live.corners.view())
        self.assertIn('5번 코너에서 구간 최고보다 0.40초 느렸습니다', answer)
        self.assertNotIn('번 구간', answer)

    def test_unconfirmed_corner_keeps_the_neutral_wording(self):
        coach = LiveCornerCoach(lambda identity: [{**CORNER, 'call': None}])
        calls = []

        def feed(sample):
            coach.ingest(sample, {'car_id': '1', 'track_id': '9'}, sample['session_time'], 4000.)
        drive(feed, [0., 0., .4, 0.])
        self.assertIsNone(coach.focus['call'])
        self.assertIn('1번 구간에서', describe(coach.review['losses'][0]))

    def test_number_calls_are_prepared_and_names_are_spoken_on_first_use(self):
        prepared = {text for _, text in radio_phrases.catalog()}
        self.assertIn('다음 5번 코너. 지난 랩에 0.4초 잃었습니다. 제동이 일렀습니다.', prepared)
        self.assertEqual(radio_phrases.build('corner_call_time', 0, turn='플룩플라츠', loss=.3)['text'], '다음 플룩플라츠. 지난 랩에 0.3초 잃었습니다.')
        self.assertFalse([text for text in prepared if '플룩플라츠' in text])


if __name__ == '__main__':
    unittest.main()
