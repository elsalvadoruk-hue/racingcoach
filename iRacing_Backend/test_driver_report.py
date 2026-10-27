"""Report authority and deduplication checks using synthetic temporary stores."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from driver_history import DriverHistoryStore
from driver_history_routes import register_routes
from driver_report import build_driver_report
from test_driver_history import identity, source


NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
CONDITIONS = {'fuel_level_l': 50, 'track_temp_c': 30, 'air_temp_c': 20,
              'track_wetness': 0, 'tire_compound': 1, 'setup_hash': 'f' * 64}


def facts(name='a', drive='drive', *, start=0., end=10., distance=500., lap_times=(100., 100.1, 99.9),
          date='2026-10-26T10:00:00Z', game='iracing', conditions=None, **changes):
    payload = source(name, drive=drive, game=game, intervals=[[start, end]], recorded_at=date, **changes)
    row = payload['observations'][0]
    row['labels'] = {'car': '예시 GT3', 'track': '예시 코스', 'layout': 'GP'}
    row['metrics'] = {'schema_version': 1, 'driving': {'status': 'OBSERVED', 'time_s': end - start,
        'distance_m': distance, 'intervals': [[start, end]], 'reason_codes': []},
        'laps': [{'lap_number': index + 1, 'start_s': index * 110., 'end_s': (index + 1) * 110.,
            'lap_time_s': value, 'measurable': True, 'conditions': deepcopy(CONDITIONS if conditions is None else conditions),
            'official_validity': 'UNKNOWN'} for index, value in enumerate(lap_times)]}
    return payload


class DriverReportTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.path = Path(self.work.name) / 'history.sqlite3'
        self.store = DriverHistoryStore(self.path)

    def report(self, game='iracing', days=30):
        return build_driver_report(self.store.report_snapshot(game), game, days, now=NOW)

    def test_empty_report_is_read_only_and_never_replaces_unknown_totals_with_zero(self):
        report = self.report()
        self.assertFalse(self.path.exists())
        self.assertEqual(report['state'], 'EMPTY')
        self.assertTrue(all(item['value'] is None for item in report['career'].values()))
        self.assertEqual(report['pace']['points'], [])
        self.assertEqual(report['style']['items'], [])

    def test_measured_facts_are_kept_separate_from_final_results_and_official_validity(self):
        self.store.record_source(facts())
        report = self.report()
        self.assertEqual(report['career']['distance_m']['value'], 500)
        self.assertEqual(report['career']['driving_time_s']['value'], 10)
        self.assertEqual(report['pace']['median_lap_s'], 100)
        self.assertAlmostEqual(report['pace']['mad_pct'], .1)
        self.assertEqual(report['pace']['trend'], 'UNAVAILABLE')
        for key in ('wins', 'podiums', 'average_position'):
            self.assertIsNone(report['career'][key]['value'])
        self.assertEqual(report['recent_drives'][0]['car'], '예시 GT3')

    def test_same_source_and_native_laps_do_not_increase_after_reanalysis_or_copy(self):
        self.store.record_source(facts())
        before = self.report()
        self.store.record_source(facts())
        self.assertEqual(before, self.report())
        self.store.record_source(facts('copy'))
        report = self.report()
        self.assertEqual(report['career']['driving_time_s']['value'], 10)
        self.assertEqual(report['career']['distance_m']['value'], 500)
        self.assertEqual(report['coverage']['native_session_count'], 1)
        self.assertEqual(report['pace']['lap_count'], 3)

    def test_overlap_time_is_union_but_distance_is_a_conservative_lower_bound(self):
        self.store.record_source(facts('a', start=0, end=10, distance=500))
        self.store.record_source(facts('b', start=5, end=20, distance=750))
        report = self.report()
        self.assertEqual(report['career']['driving_time_s']['value'], 20)
        self.assertEqual(report['career']['distance_m']['value'], 750)
        self.assertEqual(report['career']['distance_m']['status'], 'PARTIAL')

    def test_disjoint_sources_sum_known_ranges_without_filling_the_gap(self):
        self.store.record_source(facts('a', start=0, end=10, distance=500))
        self.store.record_source(facts('b', start=20, end=30, distance=400))
        report = self.report()
        self.assertEqual(report['career']['driving_time_s']['value'], 20)
        self.assertEqual(report['career']['distance_m']['value'], 900)

    def test_different_time_bases_cannot_double_driving_time(self):
        self.store.record_source(facts('a'))
        other = facts('b', start=1800000000, end=1800000010)
        other['observations'][0]['coverage']['time_basis'] = 'UTC'
        self.store.record_source(other)
        report = self.report()
        self.assertEqual(report['career']['driving_time_s']['value'], 10)
        self.assertEqual(report['career']['driving_time_s']['status'], 'PARTIAL')

    def test_source_only_observations_never_become_independent_drives_or_growth(self):
        for index in range(4):
            self.store.record_source(facts(str(index), str(index), identity_status='SOURCE_ONLY', date=f'2026-10-{20 + index}T10:00:00Z'))
        report = self.report()
        self.assertEqual(report['coverage']['native_session_count'], 0)
        self.assertEqual(report['coverage']['source_only_count'], 4)
        self.assertIsNone(report['career']['distance_m']['value'])
        self.assertEqual(report['pace']['trend'], 'UNAVAILABLE')

    def test_game_filter_and_career_are_independent_of_recent_period(self):
        self.store.record_source(facts('old', 'old', date='2026-08-01T10:00:00Z'))
        self.store.record_source(facts('lmu', 'lmu', game='lmu', distance=900))
        report = self.report()
        self.assertEqual(report['career']['distance_m']['value'], 500)
        self.assertEqual(report['coverage']['period_session_count'], 0)
        self.assertEqual(report['career'], self.report(days=90)['career'])
        self.assertEqual(self.report('lmu')['career']['distance_m']['value'], 900)

    def test_undated_observation_is_not_placed_on_a_growth_timeline(self):
        self.store.record_source(facts(date=None))
        report = self.report()
        self.assertEqual(report['coverage']['undated_count'], 1)
        self.assertEqual(report['pace']['points'], [])
        self.assertEqual(report['coverage']['period_session_count'], 0)

    def test_conflicting_lap_times_do_not_silently_choose_the_faster_lap(self):
        self.store.record_source(facts('a', lap_times=(100.,)))
        self.store.record_source(facts('b', lap_times=(90.,)))
        self.assertIsNone(self.report()['pace']['median_lap_s'])

    def populate_trend(self, *, conditions=CONDITIONS, identity_status='NATIVE', session_type='PRACTICE'):
        for index, times in enumerate(((100., 100.5, 99.5), (100., 100.5, 99.5), (98., 98.2, 97.8), (98., 98.2, 97.8))):
            self.store.record_source(facts(str(index), str(index), date=f'2026-10-{20 + index}T10:00:00Z',
                lap_times=times, conditions=conditions, identity_status=identity_status, session_type=session_type))

    def test_comparable_native_sessions_show_only_scoped_observed_change(self):
        self.populate_trend()
        report = self.report()
        self.assertEqual(report['pace']['trend'], 'IMPROVING')
        self.assertEqual(report['pace']['session_count'], 4)
        self.assertEqual(report['pace']['pace_change_pct'], -2.)
        self.assertEqual(len(report['pace']['points']), 4)
        self.assertEqual(report['style']['items'], [])
        self.assertIn('이 조합', report['overview']['body'])

    def test_missing_conditions_never_match_each_other_as_a_comparison_cohort(self):
        self.populate_trend(conditions={})
        self.assertEqual(self.report()['pace']['trend'], 'UNAVAILABLE')
        self.assertEqual(len(self.report()['pace']['points']), 1)

    def test_duplicate_lap_conflicting_or_missing_conditions_withhold_trend_but_keep_measurements(self):
        for variant in ('conflicting', 'missing'):
            with self.subTest(variant=variant):
                self.store = DriverHistoryStore(self.path.with_name(variant + '.sqlite3'))
                self.populate_trend()
                for index, times in enumerate(((100., 100.5, 99.5), (100., 100.5, 99.5),
                                                (98., 98.2, 97.8), (98., 98.2, 97.8))):
                    duplicate = facts('copy' + str(index), str(index), lap_times=times,
                                      date=f'2026-10-{20 + index}T10:00:00Z')
                    for lap in duplicate['observations'][0]['metrics']['laps']:
                        lap['start_s'] += .01
                        lap['end_s'] += .01
                        if variant == 'conflicting':
                            lap['conditions']['fuel_level_l'] = 80. + index
                        else:
                            lap['conditions'] = {}
                    self.store.record_source(duplicate)
                report = self.report()
                self.assertEqual(report['pace']['trend'], 'UNAVAILABLE')
                self.assertEqual(report['pace']['median_lap_s'], 98.)
                self.assertEqual(report['pace']['lap_count'], 3)
                self.assertEqual(len(report['pace']['points']), 1)

    def test_duplicate_lap_agreed_conditions_preserve_the_same_scoped_trend(self):
        self.populate_trend()
        before = self.report()['pace']
        for index, times in enumerate(((100., 100.5, 99.5), (100., 100.5, 99.5),
                                        (98., 98.2, 97.8), (98., 98.2, 97.8))):
            self.store.record_source(facts('copy' + str(index), str(index), lap_times=times,
                date=f'2026-10-{20 + index}T10:00:00Z'))
        self.assertEqual(self.report()['pace'], before)

    def test_unknown_actual_session_type_blocks_period_comparison(self):
        self.populate_trend(session_type='UNKNOWN')
        self.assertEqual(self.report()['pace']['trend'], 'UNAVAILABLE')

    def test_metrics_whitelist_never_persists_arbitrary_paths_or_raw_material(self):
        payload = facts()
        payload['observations'][0]['metrics']['path'] = 'PRIVATE_RAW_PATH'
        payload['observations'][0]['labels']['driver_name'] = 'PRIVATE_DRIVER'
        self.store.record_source(payload)
        encoded = json.dumps(self.store.report_snapshot('iracing'))
        self.assertNotIn('PRIVATE_RAW_PATH', encoded)
        self.assertNotIn('PRIVATE_DRIVER', encoded)

    def test_metrics_validation_rejects_invalid_authority_and_inconsistent_ranges(self):
        for mutation in ('time', 'official', 'path'):
            with self.subTest(mutation=mutation):
                payload = facts(mutation)
                row = payload['observations'][0]
                if mutation == 'time':
                    row['metrics']['driving']['time_s'] = 1000
                elif mutation == 'official':
                    row['metrics']['laps'][0]['official_validity'] = 'VALID'
                else:
                    row['labels']['car'] = 'C:\\private\\recording.ibt'
                with self.assertRaises(ValueError):
                    self.store.record_source(payload)
        self.assertFalse(self.path.exists())

    def test_http_empty_error_and_filter_contracts_do_not_create_or_expose_storage(self):
        app = FastAPI()
        register_routes(app, store_factory=lambda: self.store)
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/driver-report?game=lmu&period_days=90').json()['state'], 'EMPTY')
            self.assertFalse(self.path.exists())
            for query in ('game=other', 'period_days=7', 'period_days=30.5'):
                self.assertEqual(client.get('/api/driver-report?' + query).status_code, 422)
            self.assertEqual(client.post('/api/driver-report').status_code, 405)
            self.path.write_bytes(b'not a sqlite database')
            response = client.get('/api/driver-report')
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(str(self.path), response.text)


if __name__ == '__main__':
    unittest.main()
