"""Selection preference uses synthetic stores, telemetry and API responses only."""
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch

import app_settings
import backend_api as api
import garage61_api_collector as worker
import garage61_crawler as browser

from fastapi.testclient import TestClient


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(app_settings, 'data_dir', return_value=self.root))
        self.enterContext(patch.object(api.garage61_collector, 'active_job', return_value=None))
        self.enterContext(patch.object(api.garage61_collector, 'api_token_configured', return_value=True))
        self.client = self.enterContext(TestClient(api.app))

    def test_default_persistence_and_unrelated_settings_survive_both_settings_forms(self):
        url = '/api/garage61/collector/preferences'
        self.assertEqual(self.client.get(url).json()['selection_mode'], 'MATCHED')
        app_settings.save({'gcp_location': 'europe-west1'})
        self.assertEqual(self.client.put(url, json={'selection_mode': 'FASTEST'}).status_code, 200)
        self.assertEqual(self.client.get(url).json()['selection_mode'], 'FASTEST')
        app_settings.save({'speech_region': 'eu'})
        self.assertEqual(app_settings.load()['garage61_selection_mode'], 'FASTEST')
        self.assertEqual(app_settings.load()['gcp_location'], 'europe-west1')
        self.assertEqual(self.client.put(url, json={'selection_mode': 'MATCHED'}).status_code, 200)
        self.assertEqual(app_settings.load()['garage61_selection_mode'], 'MATCHED')

    def test_invalid_preference_and_busy_job_do_not_write(self):
        url = '/api/garage61/collector/preferences'
        for value in ('fast', '', None):
            self.assertEqual(self.client.put(url, json={'selection_mode': value}).status_code, 422)
        with patch.object(api.garage61_collector, 'active_job', return_value={'busy': True}):
            self.assertEqual(self.client.put(url, json={'selection_mode': 'FASTEST'}).status_code, 409)
        self.assertFalse((self.root / 'settings.json').exists())

    def test_unknown_saved_preference_defaults_safely(self):
        (self.root / 'settings.json').write_text(json.dumps({'garage61_selection_mode': 'invalid'}))
        self.assertEqual(app_settings.load()['garage61_selection_mode'], 'MATCHED')

    def test_fastest_query_drops_personal_conditions_but_keeps_identity_and_normal_laps(self):
        request = {'session_setup_type': 2, 'current_best_lap_s': 100,
                   'conditions': {'track_temp_c': 35, 'fuel_l': 60, 'weather_declared_wet': True}}
        matched = worker.query_passes(request, 12, 34)
        self.assertEqual(matched[0][0], 'STRICT')
        self.assertIn('minFuel', matched[0][1])
        self.assertIn('sessionSetupTypes', matched[0][1])
        label, fastest = worker.query_passes({**request, 'selection_mode': 'FASTEST'}, 12, 34)[0]
        self.assertEqual(label, 'FASTEST')
        self.assertEqual(fastest, {'tracks': 12, 'cars': 34, 'group': 'driver', 'limit': 1000,
                                  'age': -4, 'lapTypes': 1, 'round': 'metric'})

    def test_fastest_ranking_ignores_conditions_and_keeps_telemetry_quality_gates(self):
        laps = [{'id': 'MATCH', 'lapTime': 99, 'trackTemp': 35, 'fuelLevel': 60,
                 'clean': True, 'canViewTelemetry': True},
                {'id': 'FAST', 'lapTime': 90, 'trackTemp': 10, 'fuelLevel': 5,
                 'clean': True, 'canViewTelemetry': True}]
        request = {'current_best_lap_s': 105, 'conditions': {'track_temp_c': 35, 'fuel_l': 60}}
        self.assertEqual(worker.rank_candidates(laps, request)[0]['lap_id'], 'MATCH')
        invalid = [{**laps[1], 'id': 'BAD', 'lapTime': 80, key: value}
                   for key, value in [('clean', False), ('canViewTelemetry', False), ('pitlane', True), ('missing', True)]]
        result = worker.rank_candidates(laps + invalid, {**request, 'selection_mode': 'FASTEST'})
        self.assertEqual([row['lap_id'] for row in result], ['FAST', 'MATCH'])

    def test_browser_fallback_uses_fastest_rank_too(self):
        rows = [{'lap_id': 'MATCH', 'lap_time_s': 99, 'track_temp_c': 35},
                {'lap_id': 'FAST', 'lap_time_s': 90, 'track_temp_c': 10}]
        request = {'conditions': {'track_temp_c': 35}, 'current_best_lap_s': 105}
        self.assertEqual(browser.rank_rows(rows, request, 8)[0]['lap_id'], 'MATCH')
        self.assertEqual(browser.rank_rows(rows, {**request, 'selection_mode': 'FASTEST'}, 8)[0]['lap_id'], 'FAST')

    def test_modes_have_separate_cache_keys_and_fastest_does_not_depend_on_weather(self):
        telemetry = SimpleNamespace(metadata={'car_name': 'Test Car', 'track_name': 'Test Track'}, laps=[])
        with patch.object(api, 'telemetry_reference_conditions', return_value={'track_temp_c': 30}):
            matched = api.garage61_collection_context(telemetry, 'drive.ibt', 'MATCHED')
            fastest = api.garage61_collection_context(telemetry, 'drive.ibt', 'FASTEST')
        with patch.object(api, 'telemetry_reference_conditions', return_value={'track_temp_c': 50}):
            self.assertNotEqual(matched['context_key'], api.garage61_collection_context(telemetry, 'drive.ibt', 'MATCHED')['context_key'])
            self.assertEqual(fastest['context_key'], api.garage61_collection_context(telemetry, 'drive.ibt', 'FASTEST')['context_key'])
        self.assertNotEqual(matched['context_key'], fastest['context_key'])

    def test_library_ranking_recommendation_and_health_use_selected_mode(self):
        telemetry = SimpleNamespace(metadata={'car_name': 'Test Car', 'track_name': 'Test Track'}, laps=[])
        rows = {'match.csv': {'lap_time_s': 99, 'driver_name': 'Match'},
                'fast.csv': {'lap_time_s': 90, 'driver_name': 'Fast'}}
        for name in rows:
            (self.root / name).write_text('synthetic csv')
        self.enterContext(patch.object(api, '_external_reference_paths', return_value=[self.root / k for k in rows]))
        self.enterContext(patch.object(api, '_reference_catalog', return_value={}))
        self.enterContext(patch.object(api, 'garage61_reference_metadata', side_effect=lambda path, _: {'name': path.name, **rows[path.name]}))
        self.enterContext(patch.object(api, 'external_reference_validation', return_value={'usable': True}))
        self.enterContext(patch.object(api, 'external_reference_compatibility', return_value={'compatible': True}))
        self.enterContext(patch.object(api, 'reference_condition_similarity', side_effect=lambda meta, _: {
            'score': .95 if meta['driver_name'] == 'Match' else .1, 'setup_compatible': meta['driver_name'] == 'Match'}))
        self.enterContext(patch.object(api, 'telemetry_reference_conditions', return_value={}))
        self.enterContext(patch.object(api, '_garage61_context_scan', return_value={}))
        for mode, first, count in [('MATCHED', 'match.csv', 1), ('FASTEST', 'fast.csv', 2)]:
            app_settings.save({'garage61_selection_mode': mode})
            payload = api.list_external_reference_metadata(telemetry)
            self.assertEqual(payload['files'][0]['name'], first)
            self.assertEqual(payload['recommended_primary'], first)
            health = api.garage61_reference_health(telemetry, 'drive.ibt', payload)
            self.assertEqual(health['usable_reference_count'], count)
            self.assertEqual(health['context']['selection_mode'], mode)
            self.assertFalse(health['cache_fresh'])
            # A fastest retrieval does not upgrade setup compatibility evidence.
            fast = next(row for row in payload['files'] if row['name'] == 'fast.csv')
            self.assertFalse(fast['condition_similarity']['setup_compatible'])
