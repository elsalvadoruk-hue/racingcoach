"""Individual preferences must also cover queued and joined announcements."""
import unittest
import ast
from pathlib import Path
from unittest.mock import MagicMock
from race_radio import RaceRadio
from voice_alerts import CATALOG, category, reports_enabled
from live_coaching import LiveCoaching


class VoiceAlertTests(unittest.TestCase):
    def radio(self):
        radio = RaceRadio()
        radio.run_id = 'synthetic'
        radio.latest = {'on_track': True, 'is_replay': False}
        radio.last_sample = 10
        return radio

    def test_disabled_flag_never_queues_and_reenable_does_not_replay(self):
        radio = self.radio()
        radio.configure_alerts({'yellow': False})
        radio.emit('yellow', 10)
        self.assertEqual([], radio.view(10)['events'])
        radio.configure_alerts({'yellow': True})
        self.assertEqual([], radio.view(10)['events'])

    def test_disable_discards_pending_and_inflight_render_source(self):
        radio = self.radio()
        radio.emit('yellow', 10)
        event = radio.view(10)['events'][0]
        radio.configure_alerts({'yellow': False})
        self.assertIsNone(radio.get(event['id'], 10))
        radio.configure_alerts({'yellow': True})
        self.assertIsNone(radio.get(event['id'], 10))

    def test_lap_parts_follow_each_switch_independently(self):
        radio = self.radio()
        radio.configure_alerts({'lap_time': False, 'position': True}, {'battle': False})
        radio.emit('lap_time', 10, '기록 페이스 순위', speech_text='기록 페이스 순위',
                   speech_parts=['기록', '페이스', '순위'], screen_parts=['기록', '페이스', '순위'],
                   part_categories=['spotter.lap_time', 'pitwall.battle', 'spotter.position'])
        event = radio.view(10)['events'][0]
        self.assertEqual('순위', event['speech_text'])
        self.assertEqual('순위', event['text'])
        self.assertEqual(['spotter.position'], event['alert_categories'])

    def test_actual_lap_pipeline_removes_pace_and_position_sentences(self):
        radio = self.radio()
        radio.configure_alerts({'position': False}, {'battle': False})
        radio.metadata = {'session_type': 'Race'}
        radio.laps.extend([{'lap': n, 'time': 100} for n in (1, 2, 3)])
        radio.lap_position = 5
        radio.pending_lap = {'lap': 4, 'at': 10, 'old_last': 100, 'old_best': 99, 'clean': True}
        sample = {'lap_completed': 4, 'lap_last_s': 102, 'lap_best_s': 99, 'class_position': 4,
                  'session_state': 4, 'on_pit_road': False}
        radio.latest.update(sample)
        radio._laps(sample, sample, 10)
        event = radio.get(radio.pending[0]['id'], 10)
        self.assertEqual(['spotter.lap_time'], event['alert_categories'])
        self.assertNotIn('클래스', event['speech_text'])
        self.assertNotIn('느', event['speech_text'])

    def test_best_and_ordinary_lap_choices_are_separate(self):
        radio = self.radio()
        radio.configure_alerts({'lap_time': False, 'best': True})
        radio.emit('lap_time', 10, '일반 랩', speech_text='일반 랩')
        radio.emit('personal_best', 10, '최고 기록', speech_text='최고 기록')
        self.assertEqual(['personal_best'], [e['key'] for e in radio.view(10)['events']])

    def test_optional_rejoin_report_sentence_has_its_own_switch(self):
        report = dict(key='pit_two', speech_parts=['피트 준비', '복귀 순위'], speech_specs=[('phrase','pit_two',{}), ('phrase','pit_rejoin',{})],
                      part_categories=['pitwall.pit_prepare','pitwall.rejoin'])
        filtered = reports_enabled([report], {'rejoin': False})[0]
        self.assertEqual(['피트 준비'], filtered['speech_parts'])
        self.assertEqual('피트 준비', filtered['spoken_text'])
        self.assertEqual(['피트 준비','복귀 순위'], report['speech_parts'])
        self.assertEqual([], reports_enabled([report], {'pit_prepare': False, 'rejoin': False}))

    def test_disabled_control_does_not_starve_enabled_control(self):
        live = MagicMock()
        live.radio = self.radio()
        live.radio.configure_alerts(pitwall={'abs': False, 'tc': True})
        live.corners.focus = None
        live.controls.view.return_value = {'ready': True, 'advice': [dict(call='control_abs_more'), dict(call='control_tc_more')]}
        live.setup_advisor.view.return_value = {'advice': []}
        live.controls.laps = []
        live._control_lap = None
        live._control_checked_at = 0
        live.control_recommendations.choose.return_value = None
        LiveCoaching._coach_corners(live, {}, {}, 100)
        choices = live.control_recommendations.choose.call_args.args[0]
        self.assertEqual(['control_tc_more'], [a['call'] for a in choices])

    def test_static_and_literal_automatic_event_keys_have_a_control(self):
        from race_radio import STATIC_CALLS
        from setup_advisor import PHRASES
        from spotter import PHRASES as OVERLAP
        keys = set(STATIC_CALLS) | set(PHRASES) | set(OVERLAP)
        for name in ('race_radio.py', 'live_coaching.py', 'live_reporting.py'):
            tree = ast.parse(Path(__file__).with_name(name).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ('emit','say')
                        and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                    keys.add(node.args[0].value)
        # straight_brief receives the selected option's category; test/explicit drills are driver requests.
        self.assertEqual([], sorted(k for k in keys if k not in ('test', 'straight_brief') and not k.startswith('drill_') and not category(k)))
        for scope, groups in CATALOG.items():
            ids = [i['id'] for g in groups for i in g['items']]
            self.assertEqual(len(ids), len(set(ids)), scope)


if __name__ == '__main__':
    unittest.main()
