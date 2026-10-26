"""Every multipart builder must keep prepared sentences in its playback voice."""
import ast
import inspect
from pathlib import Path
import unittest
from unittest.mock import patch

import radio_phrases
from race_radio import RaceRadio
from race_engineer import RaceEngineer
import test_engineer_variation as radio_cases
import test_straight_brief as straight_cases
import test_live_formation as formation_cases
import test_fuel_strategy as fuel_cases


class MultipartCatalogTests(unittest.TestCase):
    def test_pace_additions_are_prepared_in_every_spotter_voice(self):
        for voice in radio_phrases.SPOTTER_VOICES:
            with patch.dict('os.environ', IRACING_TTS_SPOTTER_VOICE=radio_phrases.VOICE_PREFIX + voice):
                catalog = set(radio_phrases.catalog())
                additions = set()
                for key in ('pace_slower_by', 'pace_faster_by'):
                    for loss in radio_phrases.tenths(.1, 3.0):
                        for seed in range(len(radio_phrases.wordings(key)[1])):
                            text = radio_phrases.build(key, seed, loss=loss)['text']
                            self.assertTrue(('race', text) in catalog, text)
                            self.assertTrue(('spotter', text) in catalog, text)
                            additions.add(('spotter', text))
                self.assertEqual(len(additions), 300)

    def test_each_builder_uses_the_call_voice_catalog(self):
        expected = {
            ('race_radio.py', '_laps', 'dynamic'),
            ('live_reporting.py', '_brief_formation', 'formation_brief'),
            ('live_reporting.py', '_brief_straight', 'straight_brief'),
            ('live_coaching.py', '_track_sectors', 'repair_check'),
            ('live_coaching.py', '_track_sectors', 'damage_check'),
        }
        # New builders must add an exercised representative below. This inventory
        # also prevents silently missing a new file or a second construction site.
        inventory, assignments = set(), set()
        for path in Path(__file__).parent.glob('*.py'):
            if path.name.startswith('test_'):
                continue
            tree = ast.parse(path.read_text(encoding='utf8'))
            for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
                for node in ast.walk(function):
                    if isinstance(node, ast.Assign) and any(isinstance(target, ast.Subscript)
                            and isinstance(target.slice, ast.Constant) and target.slice.value == 'speech_parts'
                            for target in node.targets):
                        assignments.add((path.name, function.name))
                    if isinstance(node, ast.Call) and any(k.arg == 'speech_parts' for k in node.keywords):
                        key = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else 'dynamic'
                        inventory.add((path.name, function.name, key))
        self.assertEqual(inventory, expected)
        self.assertEqual(assignments, {('race_radio.py', 'emit'), ('race_engineer.py', 'candidates'), ('race_engineer.py', 'add')})
        catalog = set(radio_phrases.catalog())
        found, records, reports = set(), [], []
        emit, candidates = RaceRadio.emit, RaceEngineer.candidates
        def capture(radio, *args, **kwargs):
            caller = inspect.currentframe().f_back
            before = len(radio.pending)
            result = emit(radio, *args, **kwargs)
            if kwargs.get('speech_parts') and len(radio.pending) > before:
                event = radio.pending[-1]
                source = (Path(caller.f_code.co_filename).name, caller.f_code.co_name,
                          args[0] if caller.f_code.co_name not in ('_laps', '_results') else 'dynamic')
                found.add(source)
                records.append(event)
            return result
        def capture_reports(wall, *args, **kwargs):
            result = candidates(wall, *args, **kwargs)
            reports.extend(item for item in result if item.get('speech_parts'))
            return result
        cases = [
            radio_cases.RadioCallTests('test_numbers_that_were_only_on_screen_are_now_spoken'),
            radio_cases.RadioCallTests('test_qualifying_speaks_only_the_lap_its_result_coaching_and_warnings'),
            straight_cases.StraightBriefTests('test_two_corners_are_two_whole_sentences_in_one_call_when_the_straight_is_long_enough'),
            straight_cases.StraightBriefTests('test_the_first_clean_lap_after_a_contact_is_reported_as_one_call'),
            formation_cases.FormationRadioTests('test_a_topic_reaches_the_radio_with_its_sentences_and_is_gone_at_the_green'),
            fuel_cases.EnduranceFuelTests('test_the_pit_call_adds_where_the_stop_puts_the_car_as_a_second_sentence'),
            fuel_cases.EnduranceFuelTests('test_the_tyre_plan_is_its_own_call_near_the_stop'),
        ]
        with patch.object(RaceRadio, 'emit', capture), patch.object(RaceEngineer, 'candidates', capture_reports):
            result = unittest.TestResult()
            unittest.TestSuite(cases).run(result)
            self.assertEqual(result.errors + result.failures, [])
            fixture = straight_cases.StraightBriefTests()
            fixture.setUp()
            fixture.live.pit_stop.ingest = lambda *args: {'kind': 'optional', 'repair_s': 30., 'fuel_s': 20.}
            fixture.live._track_sectors({'car_id': '1', 'track_id': '2'}, straight_cases.sample(5.), None, 5.)
        self.assertEqual(found, expected)
        self.assertTrue(reports)  # race_engineer.candidates' nested add builder
        for event in records + reports:
            for index, sentence in enumerate(event.get('speech_parts') or [event['speech_text']]):
                pair = event['engineer'], sentence
                with self.subTest(key=event['key'], role=pair[0], sentence=sentence):
                    self.assertTrue(pair in catalog, pair)
