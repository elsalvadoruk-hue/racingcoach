from dataclasses import replace
import unittest
from tire_reader.snapshot import TireSnapshot, WheelState, WHEELS
from tire_reader.advisor import TireAdvisor, PHRASES


def snapshot(t=0., state='high', **patch):
    return TireSnapshot(game='iracing',car='mclaren720sgt3',track='track-layout',session_key='session',
        t=t,observed_at=t,lap=3,active=True,
        wheels={w:WheelState(pressure_kpa=170.,temp_c=90.,temp_kind='carcass',
            pressure_state=state,temp_state='ok') for w in WHEELS},
        steering=0.,throttle=1.,brake=0.,speed_mps=30.,on_pit_road=False,**patch)


class AdvisorTests(unittest.TestCase):
    def setUp(self):
        self.now=0.;self.advisor=TireAdvisor(clock=lambda:self.now)

    def observe(self, s):
        self.now=s.observed_at;self.advisor.observe(s.to_dict(),enabled=True)

    def test_three_fresh_consistent_states_required_and_wrong_wheel_never_named(self):
        for t in (0.,1.):self.observe(snapshot(t));self.assertIsNone(self.advisor.current(t))
        s=snapshot(2.)
        self.observe(replace(s,wheels={**s.wheels,'FL':replace(s.wheels['FL'],pressure_state='ok')}))
        event=self.advisor.current(2.)
        self.assertEqual(event['key'],'pressure_high_FR')
        self.assertEqual(event['text'],'오른쪽 앞 타이어 압력이 높습니다.')
        self.assertTrue(event['quiet'])

    def test_old_missing_disagreeing_or_recovered_samples_cancel_warning(self):
        for t in (0.,1.,2.):self.observe(snapshot(t))
        self.assertIsNotNone(self.advisor.current(2.))
        self.assertIsNone(self.advisor.current(8.))
        self.observe(snapshot(9.));self.assertIsNone(self.advisor.current(9.))
        for t in (10.,11.):self.observe(snapshot(t))
        self.assertIsNotNone(self.advisor.current(11.))
        self.observe(snapshot(12.,state='ok'))
        self.assertNotEqual((self.advisor.current(12.) or {}).get('key'),'pressure_high_FL')

    def test_unknown_values_no_recommendation_and_no_pressure_from_temperature(self):
        for t in range(10):
            s=snapshot(float(t),state=None)
            self.observe(replace(s,wheels={w:replace(v,pressure_kpa=None) for w,v in s.wheels.items()}))
        self.assertIsNone(self.advisor.current(9.))
        self.assertFalse(any('추천' in text for text in PHRASES.values()))

    def test_corner_pit_and_unknown_controls_defer_ack_and_context_change_discards(self):
        for t in (0.,1.,2.):self.observe(snapshot(t))
        event=self.advisor.current(2.)
        for patch in ({'steering':.2},{'brake':.5},{'throttle':None},{'on_pit_road':True},{'speed_mps':0.}):
            self.observe(replace(snapshot(3.),**patch))
            self.assertFalse(self.advisor.current(3.)['quiet'])
            self.assertFalse(self.advisor.ack(event['id'],3.))
        self.observe(replace(snapshot(4.),car='bmwm4gt3'))
        self.assertIsNone(self.advisor.current(4.))

    def test_repeat_waits_for_both_three_laps_and_three_minutes(self):
        for t in (0.,1.,2.):self.observe(snapshot(t))
        event=self.advisor.current(2.);self.assertTrue(self.advisor.ack(event['id'],2.))
        # Other wheels cannot hide a repeat of the tested wheel.
        for t in (3.,4.,5.):
            s=snapshot(t);s=replace(s,wheels={w:replace(v,pressure_state='high' if w=='FL' else None,temp_state=None) for w,v in s.wheels.items()})
            self.observe(s)
        self.assertIsNone(self.advisor.current(5.))
        for t in (183.,184.,185.):self.observe(replace(s,observed_at=t,t=t,lap=5))
        self.assertIsNone(self.advisor.current(185.))
        self.observe(replace(s,observed_at=186.,t=186.,lap=6))
        self.assertEqual(self.advisor.current(186.)['key'],'pressure_high_FL')

    def test_disabled_replay_rewind_and_session_changes_remove_pending(self):
        for patch in ({'active':False},{'session_key':'new'},{'t':0.}):
            self.advisor.reset()
            for t in (1.,2.,3.):self.observe(snapshot(t))
            self.observe(replace(snapshot(4.),**patch))
            self.assertIsNone(self.advisor.current(4.))
        self.advisor.observe(snapshot().to_dict(),enabled=False)
        self.assertIsNone(self.advisor.current(4.))

    def test_new_unknown_color_does_not_reuse_previous_confirmed_color(self):
        for t in (0.,1.,2.):self.observe(snapshot(t))
        self.assertIsNotNone(self.advisor.current(2.))
        self.observe(snapshot(3.,state=None))
        self.assertIsNone(self.advisor.current(3.))


class WarmupTests(unittest.TestCase):
    """Cold tires read low at the start and after a pit stop; that is not a warning."""
    def setUp(self):
        self.now=0.;self.advisor=TireAdvisor(clock=lambda:self.now)

    def drive(self, start, lap, state, **patch):
        for t in (start, start+1., start+2.):
            self.now=t;self.advisor.observe(replace(snapshot(t,state=state),lap=lap,**patch).to_dict(),enabled=True)
        return (self.advisor.current(self.now) or {}).get('key')

    def test_low_pressure_waits_through_the_out_lap_but_not_forever(self):
        self.assertIsNone(self.drive(0.,0,'low'))
        self.assertIsNone(self.drive(10.,1,'low'))
        self.assertEqual(self.drive(20.,2,'low'),'pressure_low_FL')   # still low after two laps: really low

    def test_a_wheel_that_warmed_up_and_then_drops_is_warned_at_once(self):
        self.drive(0.,0,'ok')                                          # warmed up (says the tires are fine)
        self.advisor.pending=None
        self.assertEqual(self.drive(10.,0,'low'),'pressure_low_FL')

    def test_leaving_the_pit_lane_starts_the_warm_up_again(self):
        self.drive(0.,5,'ok')
        self.drive(10.,6,'ok',on_pit_road=True)
        self.assertIsNone(self.drive(20.,6,'low'))
        self.assertIsNone(self.drive(30.,7,'low'))
        self.assertEqual(self.drive(40.,8,'low'),'pressure_low_FL')

    def test_high_pressure_and_overheating_are_not_held_back(self):
        self.assertEqual(self.drive(0.,0,'high'),'pressure_high_FL')
