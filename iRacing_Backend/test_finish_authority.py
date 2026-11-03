"""Finish observations are not official classifications; live ranks may settle in cooldown."""
import unittest

from race_engineer import read_race
from race_radio import RaceRadio, STATIC_CALLS
from race_result import RaceResultTracker


class FinishAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.race = dict(game='iracing', session_type='Race', player_idx=0, cars=[
            dict(idx=0, class_id=1), dict(idx=1, class_id=1)])
        self.sample = dict(session_num=0, session_time=0., session_state=5, session_flags=1,
            is_replay=False, on_track=True, lap_completed=10, class_position=8, position=8)

    def finish(self, tracker):
        tracker.ingest(self.sample, self.race, 'one', 0.)
        self.sample.update(lap_completed=11, session_time=1.)
        tracker.ingest(self.sample, self.race, 'one', 1.)

    def test_cooldown_follows_live_rank_without_becoming_official(self):
        tracker = RaceResultTracker()
        self.finish(tracker)
        self.sample.update(session_state=6, class_position=8)
        tracker.ingest(self.sample, self.race, 'one', 2.)
        self.sample['class_position'] = 7
        tracker.ingest(self.sample, self.race, 'one', 3.)
        self.assertEqual(tracker.view()['finish_position'], 7)
        self.assertTrue(tracker.view()['provisional'])

    def test_cockpit_exit_does_not_replace_last_observed_live_rank(self):
        tracker = RaceResultTracker()
        self.finish(tracker)
        self.sample.update(on_track=False, class_position=18)
        tracker.ingest(self.sample, self.race, 'one', 2.)
        self.assertEqual(tracker.view()['finish_position'], 8)

    def test_lmu_native_finish_confirms_finish_but_not_official_rank(self):
        tracker = RaceResultTracker()
        self.race['game'] = 'lmu'
        self.sample['finish_status'] = 1
        tracker.ingest(self.sample, self.race, 'one', 0.)
        self.assertEqual(tracker.view()['status'], 'FINISHED')
        self.assertTrue(tracker.view()['provisional'])

    def sdk_race(self, official=1, rows=None):
        class SDK(dict):
            game = 'iracing'
        sdk = SDK(DriverInfo={'DriverCarIdx': 0, 'Drivers': [
            {'CarIdx': 0, 'CarClassID': 1}, {'CarIdx': 1, 'CarClassID': 1}]},
            SessionInfo={'Sessions': [{'SessionNum': 0, 'SessionType': 'Race',
                'ResultsOfficial': official, 'ResultsPositions': rows if rows is not None else [
                    {'CarIdx': 0, 'ClassPosition': 5, 'Position': 6},
                    {'CarIdx': 1, 'ClassPosition': 4, 'Position': 5}]}]})
        return read_race(sdk, set(), 0)

    def test_official_sdk_table_converts_zero_based_class_rank_and_survives_later_live_values(self):
        tracker = RaceResultTracker()
        self.finish(tracker)
        race = self.sdk_race()
        tracker.ingest(self.sample, race, 'one', 2.)
        self.assertEqual(tracker.view()['finish_position'], 6)
        self.assertFalse(tracker.view()['provisional'])
        self.sample.update(class_position=20)
        tracker.ingest(self.sample, self.race, 'one', 3.)
        self.assertEqual(tracker.view()['finish_position'], 6)
        self.assertFalse(tracker.view()['provisional'])

    def test_unofficial_or_other_session_table_never_finalizes_rank(self):
        for official, number in ((0, 0), ('0', 0), (1, 1)):
            with self.subTest(official=official, session=number):
                tracker = RaceResultTracker()
                self.sample.update(session_num=0, lap_completed=10, on_track=True)
                self.finish(tracker)
                race = self.sdk_race(official)
                self.sample['session_num'] = number
                tracker.ingest(self.sample, race, 'one', 2.)
                self.assertTrue(tracker.view()['provisional'])

    def test_official_table_can_arrive_after_cockpit_exit(self):
        tracker = RaceResultTracker()
        self.finish(tracker)
        self.sample.update(on_track=False, class_position=20)
        tracker.ingest(self.sample, self.sdk_race(), 'one', 2.)
        self.assertEqual(tracker.view()['finish_position'], 6)
        self.assertFalse(tracker.view()['provisional'])

    def test_invalid_official_numbers_never_fall_back_to_first_place(self):
        for rank in (None, True, -1, 1.5, float('nan'), 2000, '0'):
            with self.subTest(rank=rank):
                race = self.sdk_race(rows=[{'CarIdx': 0, 'ClassPosition': rank, 'Position': 3}])
                self.assertIsNone(race['result_classification'])

    def test_pending_finish_cannot_start_after_dsq_or_session_change(self):
        radio = RaceRadio()
        radio.run_id, radio.metadata = 'one', self.race
        previous = dict(self.sample)
        self.sample['lap_completed'] += 1
        radio._results(self.sample, previous, 1., self.race)
        event = next(e for e in radio.pending if e['key'] == 'race_result')
        radio.latest, radio.last_sample = dict(self.sample), 5.
        self.assertTrue(radio.valid(event, 5.))
        radio.latest['session_flags'] |= 0x20000
        self.assertFalse(radio.valid(event, 5.))
        radio.latest = dict(self.sample)
        radio.metadata = {**self.race, 'session_type': 'Practice'}
        self.assertFalse(radio.valid(event, 5.))

    def test_ambiguous_official_rows_remain_provisional(self):
        for rows in ([{'CarIdx': 0, 'ClassPosition': 0, 'Position': 1}] * 2,
                     [{'CarIdx': 0, 'ClassPosition': 0, 'Position': 1},
                      {'CarIdx': 1, 'ClassPosition': 0, 'Position': 2}]):
            tracker = RaceResultTracker()
            self.sample['lap_completed'] = 10
            self.finish(tracker)
            tracker.ingest(self.sample, self.sdk_race(rows=rows), 'one', 2.)
            self.assertTrue(tracker.view()['provisional'])

    def test_pending_result_updates_rank_but_uses_prepared_neutral_audio(self):
        radio = RaceRadio()
        radio.run_id = 'one'
        radio.metadata = self.race
        previous = dict(self.sample)
        self.sample['lap_completed'] = 11
        radio._results(self.sample, previous, 1., self.race)
        event = next(e for e in radio.pending if e['key'] == 'race_result')
        identifier, speech = event['id'], event['spoken_text']
        self.assertIn('잠정', event['text'])
        self.assertIn(speech, STATIC_CALLS['finish'])
        self.assertTrue(event['prepared_only'])
        self.sample.update(class_position=7, session_state=6)
        radio._results(self.sample, previous, 2., self.race)
        self.assertEqual(radio.result['class_position'], 7)
        self.assertEqual(len([e for e in radio.pending if e['key'] == 'race_result']), 1)
        current = next(e for e in radio.pending if e['key'] == 'race_result')
        self.assertEqual((current['id'], current['spoken_text']), (identifier, speech))
        self.assertIn('7위', current['text'])

    def test_dsq_and_new_run_keep_existing_finish_guards(self):
        tracker = RaceResultTracker()
        self.finish(tracker)
        self.sample['session_flags'] |= 0x20000
        tracker.ingest(self.sample, self.race, 'one', 2.)
        self.assertEqual(tracker.view()['status'], 'DSQ')
        self.sample.update(session_flags=0, lap_completed=0, session_state=3)
        tracker.ingest(self.sample, self.race, 'two', 3.)
        self.assertIsNone(tracker.view())

    def test_history_retains_authority_and_never_speaks_a_provisional_rank_as_final(self):
        from live_formation import RaceHistory, history_facts
        identity = {'game': 'iracing', 'car_id': '1', 'track_id': '2'}
        history = RaceHistory()
        result = {'id': 'same-finish', 'game': 'iracing', 'class_position': 7, 'at': 100, 'best_lap_s': 99.,
                  'provisional': True, 'status': 'FINISHED', 'position_source': 'LIVE_CLASS_POSITION'}
        history.add(identity, result)
        self.assertIsNone(history_facts(history.last(identity), None, 20)['hist_pos'])
        result.update(class_position=6, provisional=False, position_source='IRACING_FINAL_CLASSIFICATION')
        history.add(identity, result)
        saved = history.last(identity)
        self.assertEqual(saved['position_source'], 'IRACING_FINAL_CLASSIFICATION')
        self.assertEqual(history_facts(saved, None, 20)['hist_pos'], 6)
        self.assertEqual(history.count(identity), 1)

    def test_live_ingest_updates_the_same_saved_finish_from_cooldown_to_official(self):
        from live_telemetry import LiveTelemetry
        from live_formation import RaceHistory
        live = LiveTelemetry(clock=lambda: 100.)
        live.race_history = RaceHistory()
        live.history_lookup = live.notes_lookup = None
        identity = {'game': 'iracing', 'car_id': '1', 'track_id': '2'}
        sample = {**self.sample, 'session_time': 0., 'on_pit_road': False}
        live.ingest(identity, sample, self.race)
        sample.update(session_time=1., lap_completed=11)
        live.ingest(identity, sample, self.race)
        sample.update(session_time=2., session_state=6, class_position=7)
        live.ingest(identity, sample, self.race)
        saved = live.race_history.last(identity)
        self.assertEqual(saved['class_position'], 7)
        self.assertTrue(saved['provisional'])
        self.assertEqual(saved['position_source'], 'LIVE_CLASS_POSITION')
        sample.update(session_time=3., class_position=20)
        live.ingest(identity, sample, self.sdk_race())
        saved = live.race_history.last(identity)
        self.assertEqual(saved['class_position'], 6)
        self.assertFalse(saved['provisional'])
        self.assertEqual(live.race_history.count(identity), 1)
