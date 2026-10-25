"""Observed content, isolated preference persistence and personal fallback regressions."""
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from overlay_studio import StudioStore, CoachingDashboard, defaults, normalize, session_id, select_modules, MODULES
from overlay_control import OverlayController, routes
from overlay_personal import PersonalOverlayLoader
from overlay_reference import OverlayReference, live_request
from test_overlay_reference import live, Lap, wait_for


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = StudioStore(Path(self.folder.name)/'overlay/iracing/car.json')

    def test_car_game_fallback_and_independent_modes_survive_restart(self):
        game = defaults(); game['modes']['race'] = 'inputs'
        self.store.save(game, 'game')
        self.assertEqual(self.store.load(), game)
        car = deepcopy(game); car['modes']['practice'] = 'inputs'; car['graph']['steering'] = True
        car['auto'] = False; car['manual_session'] = 'formation'
        self.store.save(car)
        # Pass the appearance path, not the content path.
        restarted = StudioStore(Path(self.folder.name)/'overlay/iracing/car.json')
        self.assertEqual(restarted.load(), car)
        self.assertEqual(restarted.load('game'), game)

    def test_corrupt_fields_are_bounded_and_lmu_always_personal(self):
        config = normalize({'graph': {'window': 999, 'gear': 'false'}, 'slots': {'race': ['bad']}, 'modes': []})
        self.assertEqual(config, defaults())
        self.assertEqual(normalize(None, 'lmu')['source'], 'personal')
        self.assertEqual(normalize({'source': 'garage61'}, 'lmu')['source'], 'personal')

    def test_failed_atomic_save_keeps_previous_and_no_temporary_files(self):
        original = self.store.save(defaults())
        changed = deepcopy(original); changed['quiet'] = False
        with patch('overlay_studio.os.replace', side_effect=PermissionError):
            with self.assertRaises(PermissionError): self.store.save(changed)
        self.assertEqual(self.store.load(), original)
        self.assertEqual(list(self.store.path.parent.glob('*.tmp')), [])

    def client(self):
        self.controller = OverlayController(Path(self.folder.name)/'legacy.json')
        app = FastAPI(); app.include_router(routes(self.controller))
        return TestClient(app)

    def test_routes_persist_offline_without_starting_a_window(self):
        client = self.client()
        body = {'config': defaults(), 'scope': 'game', 'target': {'game': 'iracing', 'car_key': None}}
        self.assertEqual(client.put('/api/overlay/studio', json=body).status_code, 200)
        self.assertIsNone(self.controller.process)
        self.assertEqual(client.get('/api/overlay/studio').json()['studio'], defaults())
        self.assertEqual(client.put('/api/overlay/studio/session', json={'session': 'formation'}).json()['preview_session'], 'formation')

    def test_stale_target_and_malformed_config_cannot_overwrite_settings(self):
        client = self.client()
        body = {'config': defaults(), 'scope': 'car', 'target': {'game': 'iracing', 'car_key': 'old-car'}}
        self.assertEqual(client.put('/api/overlay/studio', json=body).status_code, 409)
        body.update(scope='game', target={'game': 'iracing', 'car_key': None})
        body['config']['graph']['window'] = True
        self.assertEqual(client.put('/api/overlay/studio', json=body).status_code, 422)
        self.assertFalse(self.controller.studio_store().path.exists())

    def test_older_api_payload_is_readable_and_malformed_multiple_choices_do_not_overwrite(self):
        client = self.client()
        old = defaults(); old.pop('selected')
        body = {'config': old, 'scope': 'game', 'target': {'game': 'iracing', 'car_key': None}}
        self.assertEqual(client.put('/api/overlay/studio', json=body).status_code, 200)
        path = self.controller.studio_store().fallback; before = path.read_bytes()
        for selected in (['score', 'score'], ['unknown'], [False], 'score'):
            body['config'] = defaults(); body['config']['selected']['practice'] = selected
            self.assertEqual(client.put('/api/overlay/studio', json=body).status_code, 422)
            self.assertEqual(path.read_bytes(), before)

    def test_manual_session_restores_and_first_preview_selection_is_not_overwritten(self):
        path = Path(self.folder.name)/'legacy.json'
        original = OverlayController(path)
        config = defaults(); config.update(auto=False, manual_session='formation')
        original.save_studio(config, 'game', {'game': 'iracing', 'car_key': None})
        restarted = OverlayController(path)
        self.assertEqual(restarted.status()['preview_session'], 'formation')
        self.assertFalse(restarted.content()['auto'])
        fresh = OverlayController(path)
        self.assertEqual(fresh.select_preview_session('qualify')['preview_session'], 'qualify')
        self.assertEqual(fresh.content()['manual_session'], 'formation')


class MultiSelectionTests(unittest.TestCase):
    def config(self):
        config = defaults()
        config['selected']['practice'] = ['goal', 'success', 'score', 'personal', 'summary', 'loss', 'stint', 'consistency']
        return config

    def observations(self, **values):
        return {'items': {key: {'available': True} for key in MODULES}, **values}

    def test_multiple_choices_persist_and_empty_selection_does_not_restore_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'car.json'
            config = self.config(); config['selected']['race'] = []
            StudioStore(path).save(config)
            loaded = StudioStore(path).load()
            self.assertEqual(loaded['selected'], config['selected'])
            self.assertEqual(select_modules(loaded, 'race', self.observations())['waiting'], 'empty')

    def test_old_four_item_layout_migrates_without_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'car.json'; store = StudioStore(path)
            old = defaults(); old.pop('selected'); old['slots']['practice'] = ['score', None, 'personal', 'loss']
            import json
            store.path.write_text(json.dumps({'version': 1, 'config': old}))
            before = store.path.read_bytes()
            self.assertEqual(store.load()['selected']['practice'], ['score', 'personal', 'loss'])
            self.assertEqual(store.path.read_bytes(), before)

    def test_approach_pass_and_lap_show_different_checked_information(self):
        config = self.config()
        before = select_modules(config, 'practice', self.observations())
        after = select_modules(config, 'practice', self.observations(result=True))
        lap = select_modules(config, 'practice', self.observations(result=True, lap_result=True))
        self.assertEqual(before['ids'], ['goal', 'stint', 'success'])
        self.assertEqual(after['ids'], ['score', 'personal', 'success'])
        self.assertEqual(lap['ids'], ['summary', 'loss', 'stint'])
        for layout in (before, after, lap):
            self.assertLessEqual(len(layout['ids']), 3)
            self.assertTrue(set(layout['ids']).issubset(config['selected']['practice']))

    def test_data_ready_choices_take_priority_over_unmeasured_and_unchecked_items(self):
        dashboard = self.observations(result=True)
        dashboard['items']['score']['available'] = False
        self.assertEqual(select_modules(self.config(), 'practice', dashboard)['ids'], ['personal', 'success', 'consistency'])
        self.assertNotIn('change', select_modules(self.config(), 'practice', dashboard)['ids'])

    def test_result_only_selection_waits_for_its_phase_and_honors_lap_only_setting(self):
        config = self.config(); config['selected']['practice'] = ['score', 'summary']
        self.assertEqual(select_modules(config, 'practice', self.observations())['waiting'], 'phase')
        config['timing'] = 'lap'
        self.assertEqual(select_modules(config, 'practice', self.observations(result=True))['ids'], [])
        self.assertEqual(select_modules(config, 'practice', self.observations(lap_result=True))['ids'], ['summary'])

    def test_formation_and_race_use_suitable_checked_items(self):
        config = self.config()
        config['selected']['formation'] = ['summary', 'reference', 'goal', 'score']
        config['selected']['race'] = ['goal', 'success', 'stint', 'consistency', 'reference']
        self.assertEqual(select_modules(config, 'formation', self.observations(result=True, lap_result=True))['ids'], ['reference', 'goal'])
        self.assertEqual(select_modules(config, 'race', self.observations())['ids'], ['stint', 'consistency', 'goal'])

    def test_unmeasured_never_produces_a_numeric_result_and_new_diagnostics_can_be_selected(self):
        config = self.config(); config['selected']['practice'] = ['score', 'personal']
        layout = select_modules(config, 'practice', {'result': True, 'items': {}})
        self.assertEqual(layout['waiting'], 'data'); self.assertEqual(layout['ids'], ['score'])
        config['selected']['practice'] = ['impact', 'pattern']
        self.assertEqual(select_modules(config, 'practice', self.observations(result=True))['ids'], ['pattern', 'impact'])
        self.assertIsNone(select_modules(config, 'practice', self.observations(result=True))['waiting'])


class DashboardTests(unittest.TestCase):
    def view(self):
        return {'connected': True, 'run_id': 'run', 'session': {'kind': 'PRACTICE'},
                'snapshot': {'session_time': 100., 'lap_number': 3},
                'corner_pass': {'id': 'C4', 'clean': True, 'exit_at': 99., 'lap': 3, 'call': 'C4'},
                'feedback': {'corner_id': 'C4', 'delta_s': -.12, 'brake_delta_m': 8.},
                'reference_status': {'state': 'READY', 'source': 'PERSONAL', 'lap_id': 'own1', 'lap_time_s': 100.}}

    def test_only_clean_fresh_matching_pass_counts_once(self):
        dashboard = CoachingDashboard(); view = self.view()
        self.assertEqual(dashboard.build(view)['items']['score']['metric'], '-0.120')
        dashboard.build(view)
        self.assertEqual(len(dashboard.recent), 1)
        for patch_value in ({'corner_pass': {**view['corner_pass'], 'clean': False}},
                            {'feedback': {'corner_id': 'C5', 'delta_s': -.1}},
                            {'corner_pass': {**view['corner_pass'], 'exit_at': 20.}}):
            self.assertEqual(CoachingDashboard().build({**view, **patch_value})['items']['score']['metric'], '—')

    def test_result_expires_and_run_reference_and_gap_reset_history(self):
        view = self.view(); dashboard = CoachingDashboard()
        self.assertTrue(dashboard.build(view)['result'])
        view['snapshot']['session_time'] = 107.
        self.assertFalse(dashboard.build(view)['result'])
        view['corner_pass'] = None; view['feedback'] = None
        for key, value in [('run_id', 'new'), ('reference_status', {'lap_id': 'other'})]:
            dashboard.build({**view, key: value})
            self.assertEqual(len(dashboard.recent), 0)

    def test_formation_session_and_lap_result_expiry(self):
        view = self.view(); view['session']['kind'] = 'RACE'; view['snapshot']['session_state'] = 3
        self.assertEqual(session_id(view), 'formation')
        view['snapshot']['session_state'] = 4
        self.assertEqual(session_id(view), 'race')
        view['session_events'] = {'lap': {'at': 95}}
        self.assertTrue(CoachingDashboard().build(view)['lap_result'])
        view['session_events']['lap']['at'] = 90
        self.assertFalse(CoachingDashboard().build(view)['lap_result'])


class ReferenceFallbackTests(unittest.TestCase):
    def service(self, folder, personal):
        service = OverlayReference(folder, Mock(), personal_loader=personal)
        self.addCleanup(service.close)
        return service

    def test_lmu_never_calls_garage_even_with_pinned_external_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.service(folder, Mock(return_value=None))
            service._prepare_garage = Mock(side_effect=AssertionError('LMU must not access Garage61'))
            context = live_request(live()); context.update(game='lmu', pinned_reference={'source': 'GARAGE61'})
            self.assertIsNone(service._prepare(None, context, 5000)[0])
            service._prepare_garage.assert_not_called()

    def test_explicit_personal_source_overrides_an_existing_task_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            personal = Mock(return_value=(Lap(), {'state': 'READY', 'source': 'PERSONAL'}))
            service = self.service(folder, personal)
            service._prepare_garage = Mock(side_effect=AssertionError('Explicit personal source must skip task/Garage61'))
            view = live(); view['reference_source'] = 'personal'
            view['practice'] = {'reference': {'source': 'GARAGE61', 'files': ['synthetic.csv']}}
            context = live_request(view)
            self.assertEqual(service._prepare(None, context, 5000)[1]['source'], 'PERSONAL')
            service._prepare_garage.assert_not_called()
            self.assertIsNone(context['pinned_reference'])

    def test_auto_falls_back_after_missing_or_failed_garage_and_explicit_source_does_not(self):
        with tempfile.TemporaryDirectory() as folder:
            personal = Mock(return_value=(Lap(), {'state': 'READY', 'source': 'PERSONAL'}))
            service = self.service(folder, personal)
            context = live_request(live())
            for result in [(None, {'state': 'TOKEN_REQUIRED'}), OSError('offline')]:
                service._prepare_garage = Mock(side_effect=result if isinstance(result, Exception) else None, return_value=result)
                self.assertEqual(service._prepare(None, context, 5000)[1]['source'], 'PERSONAL')
            personal.reset_mock(); context['source'] = 'garage61'
            service._prepare_garage = Mock(return_value=(None, {'state': 'NO_MATCH'}))
            self.assertIsNone(service._prepare(None, context, 5000)[0]); personal.assert_not_called()

    def test_personal_reference_is_dropped_when_conditions_become_incompatible(self):
        with tempfile.TemporaryDirectory() as folder:
            personal = Mock(return_value=None)
            service = self.service(folder, personal)
            view = live(); view['reference_source'] = 'personal'
            service.observe(view, True); wait_for(lambda: service.status()['state'] == 'NO_MATCH')
            reference = SimpleNamespace(meta={'weather_declared_wet': False, 'track_temp_c': 30., 'fuel_l': 30.})
            with service.condition:
                service.reference = reference; service.state = {'state': 'READY', 'source': 'PERSONAL'}
            service.observe(live(track_temp_c=60.), True)
            self.assertIsNot(service.reference, reference)


class PersonalSelectionTests(unittest.TestCase):
    def test_lmu_same_generated_track_id_does_not_admit_a_different_explicit_layout(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'own.duckdb'; path.write_bytes(b'synthetic')
            meta = {'car_platform_id': '10', 'track_platform_id': '20', 'track_config': 'short'}
            analyzer = Mock()
            loader = PersonalOverlayLoader(lambda game:[path], lambda p:SimpleNamespace(metadata=meta), lambda p:meta, analyzer)
            context = live_request(live()); context['game'] = 'lmu'; context['identity']['track_config'] = 'full'
            self.assertIsNone(loader(context, 5000))
            analyzer.assert_not_called()

    def test_fastest_complete_valid_matching_compatible_lap_is_chosen_without_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'own.ibt'; path.write_bytes(b'synthetic')
            meta = {'car_platform_id': '10', 'track_platform_id': '20'}
            laps = [SimpleNamespace(lap_number=i, lap_time_s=t, valid=valid, complete=complete)
                    for i, t, valid, complete in [(1, 99, True, True), (2, 80, False, True), (3, 75, True, False), (4, 95, True, True), (5, 60, True, True)]]
            telemetry = SimpleNamespace(metadata=meta, laps=laps)
            analyzer = Mock(); analyzer.return_value._lap_grid.return_value = {
                'distance_pct': np.linspace(0, 1, 1001), 'time_s': np.linspace(0, 95, 1001),
                'Brake': np.zeros(1001), 'Throttle': np.ones(1001)}
            loader = PersonalOverlayLoader(lambda game:[path], lambda p:telemetry, lambda p:meta, analyzer)
            conditions = lambda t, lap: {'weather_declared_wet': lap.lap_number == 5, 'track_temp_c': 30., 'fuel_l': 30.}
            before = path.stat()
            with patch('overlay_personal._conditions', side_effect=conditions):
                _, status = loader(live_request(live()), 5000)
            self.assertEqual(status['lap'], 4)
            self.assertEqual(status['source'], 'PERSONAL')
            after = path.stat(); self.assertEqual((before.st_size, before.st_mtime_ns), (after.st_size, after.st_mtime_ns))
            self.assertEqual(len(list(Path(folder).iterdir())), 1)

    def test_cancellation_before_scan_loads_nothing(self):
        load = Mock()
        loader = PersonalOverlayLoader(lambda game:[Path('unused.ibt')], load, Mock(), Mock())
        self.assertIsNone(loader(live_request(live()), 5000, lambda:False)); load.assert_not_called()


if __name__ == '__main__': unittest.main()
