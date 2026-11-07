"""Independent passes, changing fuel and confounded controls: synthetic only."""
import copy
from pathlib import Path
import tempfile
import unittest
import time

from driving_learning import DrivingHistory, learn, applicable


def passes(feature='release_m', kind='corner'):
    rows = []
    for lap in range(1, 17):
        low = lap % 2 == 1
        rows.append(dict(id=str(lap), scope='car-track', window='C1', kind=kind,
                         members=['C1'], run='run', lap=lap, ordinal=lap, created=time.time(), source='LIVE', traffic='SOLO',
                         start_pct=.1, end_pct=.3, conditions=dict(fuel=80.-lap*3.9, temp=35., compound=0., wet=0.),
                         controls=dict(bb=52., abs=3., tc=5.), setup='fixed-mechanical',
                         metrics=dict(time_s=10. if low else 10.4, entry_speed=40., end_speed=45.,
                                      release_m=20., corrections=0., relifts=0., time_error_s=.02)))
        rows[-1]['metrics'][feature] = 10. if low else 20.
    return rows


class DrivingLearningTests(unittest.TestCase):
    def test_fuel_burn_keeps_local_matched_pairs_and_independent_confirmation(self):
        result = learn(passes())
        item = result['technique'][0]
        self.assertEqual(item['state'], 'CONFIRMED')
        self.assertEqual(item['action'], 'release_earlier')
        self.assertGreaterEqual(item['pairs'], 4)
        self.assertGreaterEqual(item['validation_pairs'], 2)
        self.assertAlmostEqual(item['gain_s'], .4)
        self.assertEqual(len(item['evidence_ids']), len(set(item['evidence_ids'])))

    def test_copying_one_pass_never_creates_support(self):
        rows = passes()[:2]
        self.assertFalse(learn(rows * 30)['technique'])

    def test_future_results_must_confirm_the_discovery(self):
        rows = passes()
        for row in rows[8:]:
            row['metrics']['time_s'] = 10.5 if row['lap'] % 2 else 10.
        self.assertFalse(learn(rows)['technique'])

    def test_unknown_conditions_and_different_controls_do_not_match(self):
        for key in ('fuel', 'temp', 'compound', 'wet'):
            rows = passes()
            for row in rows:
                row['conditions'][key] = None
            self.assertFalse(learn(rows)['technique'], key)
        rows = passes()
        for row in rows:
            if row['lap'] % 2:
                row['controls']['tc'] = 7.
        self.assertFalse(learn(rows)['technique'])

    def test_faster_boundary_with_slower_exit_is_not_promoted(self):
        rows = passes()
        for row in rows:
            if row['lap'] % 2:
                row['metrics']['end_speed'] = 40.
        self.assertFalse(learn(rows)['technique'])

    def test_settings_are_reported_by_observed_value_without_map_order_assumptions(self):
        rows = passes()
        for row in rows:
            row['controls']['abs'] = 7. if row['lap'] % 2 else 3.
            row['metrics']['release_m'] = 20.
        item = learn(rows)['settings'][0]
        self.assertEqual(item['control'], 'abs')
        self.assertEqual(item['preferred_value'], 7.)
        self.assertFalse(item['causal'])

    def test_two_control_changes_do_not_become_a_single_setting_result(self):
        rows = passes()
        for row in rows:
            if row['lap'] % 2:
                row['controls'].update(bb=53., abs=4.)
        self.assertFalse(learn(rows)['settings'])

    def test_connected_corner_result_uses_full_window(self):
        rows = passes(feature='entry_speed', kind='chain')
        for row in rows:
            row['metrics']['entry_speed'] += 25.
            row['metrics']['corner_time_s'] = 4.08 if row['lap'] % 2 else 4.
        item = learn(rows)['chains'][0]
        self.assertEqual(item['action'], 'chain_entry_slower')
        self.assertAlmostEqual(item['gain_s'], .4)

    def test_impact_without_a_kerb_signal_is_named_as_an_impact(self):
        rows = passes(feature='impact_score')
        for row in rows:
            row['impact_verified'] = False
        item = learn(rows)['impacts'][0]
        self.assertFalse(item['kerb_verified'])
        self.assertIn('충격', item['text'])

    def test_current_condition_extrapolation_and_same_lap_are_rejected(self):
        rows = passes()
        model = learn(rows)['technique'][0]
        current = copy.deepcopy(rows[-1])
        current['lap'] = 20
        current['conditions']['fuel'] = rows[-1]['conditions']['fuel']
        self.assertTrue(applicable(model, current))
        current['conditions']['temp'] = 60.
        self.assertFalse(applicable(model, current))
        current = copy.deepcopy(rows[-1])
        self.assertFalse(applicable(model, current))

    def test_history_is_bounded_deduplicated_and_atomic_in_isolated_storage(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'driving.json'
            history = DrivingHistory(path, limit=12)
            history.update(passes())
            history.update(passes())
            loaded = DrivingHistory(path, limit=12).rows()
            self.assertEqual(len(loaded), 12)
            self.assertEqual(len({r['id'] for r in loaded}), 12)
            self.assertFalse(list(Path(folder).glob('*.tmp')))

    def test_setting_stability_gain_is_separate_from_time_gain(self):
        rows=passes()
        for row in rows:
            low=row['lap']%2==1
            row['controls']['abs']=7. if low else 3.
            row['metrics'].update(time_s=10.,release_m=20.,corrections=0 if low else 3)
        item=learn(rows)['settings'][0]
        self.assertEqual(item['state'],'STABILITY_CONFIRMED')
        self.assertEqual(item['preferred_value'],7.)
        self.assertNotIn('gain_s',item)

    def test_setting_faster_with_worse_stability_has_no_preferred_value(self):
        rows=passes()
        for row in rows:
            low=row['lap']%2==1
            row['controls']['abs']=7. if low else 3.
            row['metrics'].update(release_m=20.,corrections=3 if low else 0)
        item=learn(rows)['settings'][0]
        self.assertEqual(item['state'],'INCONCLUSIVE')
        self.assertIsNone(item['preferred_value'])


if __name__ == '__main__':
    unittest.main()
