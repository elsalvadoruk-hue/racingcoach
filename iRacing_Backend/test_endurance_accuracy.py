"""Endurance regressions: synthetic values, no game, personal data or speech service."""
from collections import deque
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from live_telemetry import LiveTelemetry
from overlay_presentation import CoachingPresentation
from overlay_studio import defaults
from race_engineer import RaceEngineer
from race_radio import RaceRadio


def sample(**values):
    return dict(dict(session_time=100., session_state=4, session_flags=0, lap_number=4,
                     lap_completed=3, distance_pct=.5, fuel_l=18., fuel_l_per_lap=13.4,
                     class_position=2, on_track=True, is_replay=False, on_pit_road=False,
                     incidents=0, speed_mps=50., brake=0., steering_rad=0., speech_hold_s=-1.), **values)


def view(**values):
    return dict(dict(active=True, game='iracing', fuel_laps=1.34, fuel_to_add_l=40.,
                     fuel_finish_shortfall_l=36., fuel_next_stop_l=40., fuel_next_stop_longer_l=None,
                     fuel_next_stop_full=False, remaining_laps=4., pace_samples=0, pace_s=500.,
                     fuel_save_feasible=False, finish_total_laps=28, class_position=None), **values)


def race(**values):
    cars = [dict(idx=i, class_id=10, class_position=i+1, position=i+1, lap_completed=3,
                 distance_pct=p, track_surface=3, on_pit_road=False) for i, p in enumerate((.8, .5, .3))]
    return dict(dict(game='iracing', session_type='Race', state=4, sample_time=100.,
                     player_idx=1, cars=cars, total_laps=10), **values)


class EnduranceFuelTests(unittest.TestCase):
    def test_urgent_box_includes_numeric_addition_in_same_delivery(self):
        wall = RaceEngineer()
        report = wall.select(wall.candidates(view(), sample(), {}), sample(), 'all', cached_audio=True)
        self.assertEqual(report['key'], 'fuel_critical')
        self.assertIn('40리터', report['spoken_text'])
        self.assertEqual(len(report['speech_parts']), 2)

    def test_numeric_plan_survives_below_old_1_3_lap_cutoff(self):
        wall = RaceEngineer()
        wall.last_report = 80
        reports = wall.candidates(view(fuel_laps=1.2), sample(), {})
        self.assertTrue(any('40리터' in r['spoken_text'] for r in reports))

    def test_full_tank_has_explicit_maximum_addition_setting(self):
        reports = RaceEngineer().candidates(view(fuel_next_stop_full=True, tank_capacity_l=98.), sample(), {})
        self.assertTrue(any('98리터' in r['spoken_text'] for r in reports))
        self.assertTrue(all('가득' in r['spoken_text'] for r in reports if '98리터' in r['spoken_text']))

    def test_combined_fuel_call_keeps_independent_alert_switches(self):
        from voice_alerts import reports_enabled
        reports=RaceEngineer().candidates(view(),sample(),{})
        box=next(r for r in reports_enabled(reports,{'fuel_plan':False}) if r['key']=='fuel_critical')
        self.assertNotIn('리터',box['spoken_text'])
        wall=RaceEngineer()
        wall.delivered(box,100.)
        self.assertNotIn('pit_fuel_plan',wall.reported)
        amount=next(r for r in reports_enabled(reports,{'fuel_critical':False}) if r['key']=='fuel_critical')
        self.assertIn('40리터',amount['spoken_text'])
        self.assertNotIn('피트합니다',amount['spoken_text'])

    def test_final_lap_reserve_does_not_request_refuel(self):
        reports = RaceEngineer().candidates(view(fuel_laps=.7, remaining_laps=.52,
                    fuel_finish_shortfall_l=0., fuel_to_add_l=1.6), sample(session_state=5), {})
        self.assertNotIn('fuel_reserve', [r['key'] for r in reports])

    def test_same_finish_forecast_is_not_reannounced_after_a_dip(self):
        wall, told = RaceEngineer(), []
        for at, total in [(0,28),(1,29),(22,29),(45,28),(66,28),(90,29),(111,29),(140,30),(161,30)]:
            reports = wall.candidates(view(finish_total_laps=total), sample(session_time=at), {})
            report = next((r for r in reports if r['key']=='finish_laps'), None)
            if report:
                told.append(total)
                wall.delivered(report, at)
        self.assertEqual(told, [29,30])

    def test_refuel_keeps_verified_use_at_both_sample_rates(self):
        for interval in (1/30, .3):
            probe = SimpleNamespace(lap_fuel=None, fuel_history=deque([13.4,13.5], maxlen=5),
                    fuel_feedback=deque(maxlen=3), pit_wall=SimpleNamespace(view=lambda *a,**k:{}), race_options={})
            for step in range(31):
                LiveTelemetry._track_fuel(probe, sample(session_time=100+step*interval,
                    on_pit_road=True, fuel_l=4+.8*step*interval))
            self.assertEqual(list(probe.fuel_history), [13.4,13.5])

    def test_incident_does_not_erase_measured_consumption_but_taints_feedback(self):
        probe = SimpleNamespace(lap_fuel=None, fuel_history=deque(maxlen=5), fuel_feedback=deque(maxlen=3),
                    pit_wall=SimpleNamespace(view=lambda *a,**k:{}), race_options={})
        for step in range(101):
            LiveTelemetry._track_fuel(probe, sample(session_time=100+step, lap_number=4+step//100,
                distance_pct=(step%100)/100, fuel_l=90-13.4*step/100, incidents=int(step>=50)))
        self.assertAlmostEqual(probe.fuel_history[0], 13.4)
        self.assertFalse(probe.fuel_feedback)

    def test_consumption_is_invalidated_by_wetness_transition_and_age(self):
        for patch in ({'track_wetness':3}, {'session_time':3701.}):
            probe=SimpleNamespace(lap_fuel=None,fuel_history=deque([13.4],maxlen=5),fuel_feedback=deque(maxlen=3),
                pit_wall=SimpleNamespace(view=lambda *a,**k:{}),race_options={},_fuel_measured_at=100.,_fuel_condition=False)
            LiveTelemetry._track_fuel(probe,sample(track_wetness=patch.get('track_wetness',1),
                session_time=patch.get('session_time',101.)))
            self.assertFalse(probe.fuel_history)

    def test_fuel_increase_even_at_high_sample_rate_invalidates_the_current_lap(self):
        probe=SimpleNamespace(lap_fuel=None,fuel_history=deque([13.4],maxlen=5),fuel_feedback=deque(maxlen=3),
                pit_wall=SimpleNamespace(view=lambda *a,**k:{}),race_options={})
        LiveTelemetry._track_fuel(probe,sample(distance_pct=.01))
        LiveTelemetry._track_fuel(probe,sample(session_time=100.1,distance_pct=.011,fuel_l=18.03))
        self.assertFalse(probe.lap_fuel['measured'])
        self.assertEqual(list(probe.fuel_history),[13.4])

    def test_final_lap_fuel_alert_uses_distance_to_finish(self):
        radio = RaceRadio()
        radio.ingest(sample(fuel_l=8, session_state=5, fuel_finish_remaining_laps=.32), 'r', 100,
                     {'session_type':'Race'})
        radio.ingest(sample(session_time=100.1, fuel_l=6.7, session_state=5,
                            fuel_finish_remaining_laps=.31), 'r', 100.1)
        self.assertFalse(any(e['key']=='fuel_low' for e in radio.pending))
        radio.ingest(sample(session_time=100.2, fuel_l=1., session_state=5,
                            fuel_finish_remaining_laps=.3), 'r', 100.2)
        self.assertTrue(any(e['key']=='fuel_low' for e in radio.pending))


class EndurancePositionTests(unittest.TestCase):
    def wall_view(self, data, **patch):
        wall = RaceEngineer()
        wall.ingest(data, 'r', 100.)
        return wall.view(sample(**patch))

    def test_native_rank_needs_agreement_with_progress(self):
        data = race()
        data['cars'][2]['distance_pct'] = .6
        result = self.wall_view(data)
        self.assertIsNone(result['class_position'])
        self.assertEqual(result['observed_class_position'], 2)
        self.assertIsNone(result['ahead'])
        self.assertIsNone(result['behind'])

    def test_agreeing_fresh_rank_remains_available(self):
        self.assertEqual(self.wall_view(race())['class_position'], 2)

    def test_stale_or_newer_disagreeing_scalar_suppresses_rank(self):
        self.assertIsNone(self.wall_view(race(), session_time=103.)['class_position'])
        self.assertIsNone(self.wall_view(race(), class_position=1)['class_position'])

    def test_unseen_car_on_same_lap_is_uncertain_but_old_retiree_is_behind(self):
        data = race()
        data['cars'][2].update(distance_pct=-1, track_surface=-1)
        self.assertIsNone(self.wall_view(data)['class_position'])
        data['cars'][2]['lap_completed'] = 1
        self.assertEqual(self.wall_view(data)['class_position'], 2)

    def test_lmu_keeps_native_scoring_policy(self):
        data = race(game='lmu')
        data['cars'][2]['distance_pct'] = .6
        self.assertEqual(self.wall_view(data)['class_position'], 2)

    def test_disconnected_car_with_reset_lap_uses_retained_scoring_order(self):
        data=race()
        data['cars'][2].update(lap_completed=-1,distance_pct=-1,track_surface=-1)
        self.assertEqual(self.wall_view(data)['class_position'],2)
        data['cars'][2]['class_position']=None
        self.assertIsNone(self.wall_view(data)['class_position'])

    def test_lap_call_does_not_attach_a_rank_that_can_change_in_queue(self):
        radio = RaceRadio()
        radio.ingest(sample(lap_last_s=500., lap_best_s=490.), 'r',100, {'session_type':'Race'})
        radio.lap_position=3
        radio.ingest(sample(session_time=100.1,lap_completed=4,lap_last_s=501.,lap_best_s=490.),'r',100.1)
        event=next(e for e in radio.pending if e['key']=='lap_time')
        self.assertNotIn('위', event['speech_text'])

    def test_rank_recheck_invalidates_queued_straight_brief(self):
        radio = RaceRadio()
        radio.ingest(sample(workload_in_s=15., position_confirmed=2),'r',100,{'session_type':'Race'})
        event = radio.emit('straight_brief',100,'현재 클래스 2위입니다.',condition=('straight',5))
        radio.track_delivery(event,position=2)
        radio.ingest(sample(session_time=100.1,workload_in_s=15.,position_confirmed=None),'r',100.1)
        self.assertFalse(radio.valid(event,100.1))

    def test_verified_position_returns_after_a_scoring_disagreement_without_direction(self):
        radio=RaceRadio()
        radio.ingest(sample(position_confirmed=None),'r',100,{'session_type':'Race','game':'iracing'})
        for i in range(1,62):
            radio.ingest(sample(session_time=100+i/10,position_confirmed=2),'r',100+i/10)
        event=next(e for e in radio.pending if e['key']=='position')
        self.assertNotIn('올라',event['speech_text'])
        self.assertNotIn('내려',event['speech_text'])
        radio.delivery(event['id'],'completed')
        radio.ack(event['id'],106.1)
        for i in range(62,125):
            radio.ingest(sample(session_time=100+i/10,position_confirmed=None if i<64 else 2),'r',100+i/10)
        self.assertFalse(any(e['key']=='position' for e in radio.pending))


class EndurancePresentationTests(unittest.TestCase):
    def test_formation_to_race_within_half_second_resets_before_reading_previous(self):
        presentation = CoachingPresentation()
        config = defaults()
        v = {'connected':True,'run_id':'r','snapshot':sample()}
        presentation.build(v, {'formation':{'active':True}},config,'formation')
        v['snapshot']['session_time'] += .1
        result = presentation.build(v,{},config,'race')
        self.assertIn('state',result)


class EnduranceAudioTests(unittest.TestCase):
    def test_prepared_fuel_plan_does_not_switch_from_an_incomplete_selected_pack(self):
        from voice_coach import VoiceCoach
        coach=VoiceCoach.__new__(VoiceCoach)
        coach.fixed_audio=MagicMock()
        coach.fixed_audio.cached.return_value=True
        coach.custom_packs=MagicMock()
        coach.custom_packs.has_selection.return_value=True
        coach.custom_packs.has.return_value=False
        plan=next(r for r in RaceEngineer().candidates(view(),sample(),{}) if r['key']=='pit_fuel_plan')
        self.assertFalse(coach.prepared_report_ready(plan))

    def test_missing_prepared_plan_does_not_block_another_ready_report(self):
        import asyncio
        import base64
        from test_race_engineer import PitWallIntegrationTests
        from voice_coach import ReportRequest
        fixture=PitWallIntegrationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        plans=RaceEngineer().candidates(view(),sample(),{})
        plan=next(r for r in plans if r['key']=='pit_fuel_plan')
        other=dict(key='warning_detail',engineer='race',text='차량 경고가 계속됩니다.',
                   speech_text='차량 경고가 계속됩니다.',spoken_text='차량 경고가 계속됩니다.',
                   prepared_only=True,priority=3,token='warning',cooldown=180)
        fixture.live.pit_wall.candidates=lambda *args:[plan,other]
        fixture.coach.fixed_audio.cached=MagicMock(side_effect=lambda role,text:'리터' not in text)
        fixture.coach.fixed_audio.read_prepared=MagicMock(return_value=base64.b64encode(b'ID3'+b'x'*500).decode())
        result=asyncio.run(fixture.coach.report(ReportRequest()))['report']
        self.assertIsNotNone(result)
        self.assertEqual(result['spoken_text'],other['spoken_text'])
        fixture.speech.synthesize.assert_not_called()

    def test_missing_fuel_number_does_not_silence_the_cached_box_call(self):
        import asyncio
        import base64
        from test_race_engineer import PitWallIntegrationTests
        from voice_coach import ReportRequest
        fixture=PitWallIntegrationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.live.race_view=lambda **options:view()
        cache=fixture.coach.fixed_audio
        cache.cached=MagicMock(side_effect=lambda role,text:'리터' not in text)
        cache.read_prepared=MagicMock(return_value=base64.b64encode(b'ID3'+b'x'*500).decode())
        result=asyncio.run(fixture.coach.report(ReportRequest()))['report']
        self.assertIsNotNone(result)
        self.assertTrue(result['audio_base64'])
        self.assertNotIn('리터',result['spoken_text'])
        self.assertIn('40리터',result['note']['reply'])
        self.assertTrue(fixture.coach.acknowledge_report(result['note']['id'])['accepted'])
        self.assertNotIn('pit_fuel_plan',fixture.live.pit_wall.reported)
        fixture.speech.synthesize.assert_not_called()

    def test_long_lap_uses_existing_update_clip_without_inventing_a_numeric_time(self):
        from voice_coach import VoiceCoach
        from radio_audio import texts
        coach = VoiceCoach.__new__(VoiceCoach)
        coach.fixed_audio = MagicMock()
        coach.fixed_audio.cached.side_effect = lambda role, text: text == texts('lap_time')[0]
        original = dict(key='lap_time',engineer='spotter',text='직전 랩 11분 25초입니다.',
                        speech_text='이번 랩 11분 25초 0입니다.',speech_parts=['이번 랩 11분 25초 0입니다.'],immediate=True)
        fallback = coach.prepared_lap_fallback(original)
        self.assertEqual(fallback['spoken_text'],texts('lap_time')[0])
        self.assertEqual(fallback['text'],original['text'])
        self.assertTrue(fallback['numeric_audio_missing'])
        self.assertNotIn('speech_parts',fallback)
        coach.fixed_audio.phrase.assert_not_called()

    def test_lap_fallback_does_not_switch_voice_or_generate_when_cache_is_missing(self):
        from voice_coach import VoiceCoach
        coach=VoiceCoach.__new__(VoiceCoach)
        coach.fixed_audio=MagicMock()
        coach.fixed_audio.cached.return_value=False
        self.assertIsNone(coach.prepared_lap_fallback({'key':'lap_time','engineer':'spotter'}))
        self.assertIsNone(coach.prepared_lap_fallback({'key':'position','engineer':'spotter'}))
        coach.fixed_audio.phrase.assert_not_called()

    def test_actual_render_route_uses_only_the_prepared_fallback(self):
        import base64
        from fastapi.testclient import TestClient
        from test_race_radio import RadioRouteTests
        from radio_audio import texts
        fixture=RadioRouteTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        cache=fixture.coach.fixed_audio
        cache.cached=MagicMock(side_effect=lambda role,text:text==texts('lap_time')[0])
        cache.read_prepared=MagicMock(return_value=base64.b64encode(b'ID3'+b'x'*500).decode())
        event=fixture.live.radio.emit('lap_time',fixture.now[0],'직전 랩 11분 25초입니다.',immediate=True,
            speech_text='이번 랩 11분 25초 0입니다.',speech_parts=['이번 랩 11분 25초 0입니다.'])
        with TestClient(fixture.app) as client:
            response=client.post('/api/live-coach/spotter/radio/'+event['id']+'/render').json()
        self.assertTrue(response['audio_base64'])
        self.assertEqual(response['event']['spoken_text'],texts('lap_time')[0])
        self.assertEqual(response['event']['text'],'직전 랩 11분 25초입니다.')
        self.assertTrue(response['event']['numeric_audio_missing'])
        cache.read_prepared.assert_called_once_with('spotter',texts('lap_time')[0])
        fixture.speech.synthesize.assert_not_called()

    def test_lap_fallback_respects_a_selected_pack_without_the_update_clip(self):
        from voice_coach import VoiceCoach
        coach=VoiceCoach.__new__(VoiceCoach)
        coach.fixed_audio=MagicMock()
        coach.fixed_audio.cached.return_value=True
        coach.custom_packs=MagicMock()
        coach.custom_packs.has_selection.return_value=True
        coach.custom_packs.has.return_value=False
        self.assertIsNone(coach.prepared_lap_fallback({'key':'lap_time','engineer':'spotter'}))


if __name__ == '__main__':
    unittest.main()
