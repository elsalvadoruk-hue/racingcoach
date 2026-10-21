"""Temporary per-game/car preferences; no personal stores or native windows."""
import tempfile
import io
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from overlay_control import OverlayController, routes
from overlay_preferences import DEFAULTS, PreferencesStore, car_key, settings_path
from overlay_visibility import VisibilityStore


class PerCarTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.legacy = self.root / 'overlay-settings.json'
        self.controller = OverlayController(self.legacy)

    def view(self, game='iracing', name='Porsche 963'):
        return {'connected': True, 'identity': {'game': game, 'car_name': name},
                'snapshot': {'on_track': False, 'is_replay': False, 'session_state': 4}}

    def test_keys_are_bounded_safe_and_reserved_names_cannot_collide(self):
        self.assertEqual(car_key({'car_path': 'Cars/PORSCHE 963!!', 'car_name': 'ignored'}), 'cars-porsche-963')
        self.assertEqual(car_key({'car_name': ' BMW   M4 GT3 '}), 'bmw-m4-gt3')
        self.assertEqual(len(car_key({'car_name': 'A' * 200})), 80)
        self.assertFalse(car_key({'car_name': 'a' * 79 + ' b'}).endswith('-'))
        for name in ('', '한글', '!!!'):
            self.assertIsNone(car_key({'car_name': name}))
        self.assertEqual(car_key({'car_name': '_default'}), 'car-default')
        self.assertEqual(car_key({'car_name': 'visibility'}), 'car-visibility')
        with self.assertRaises(ValueError):
            settings_path(self.root, 'lmu', '../outside')

    def test_read_order_and_save_leave_legacy_and_other_game_intact(self):
        self.controller.sync(self.view())
        store = self.controller.store()
        self.assertEqual(store.load(), DEFAULTS)
        PreferencesStore(self.legacy).save({**DEFAULTS, 'width': 111})
        legacy_bytes = self.legacy.read_bytes()
        self.assertEqual(store.load()['width'], 111)
        default = settings_path(self.root, 'iracing')
        PreferencesStore(default).save({**DEFAULTS, 'width': 122})
        self.assertEqual(store.load()['width'], 122)
        store.save({**DEFAULTS, 'width': 133})
        self.assertEqual(store.load()['width'], 133)
        # The native child constructs its own store with only --settings.
        self.assertEqual(PreferencesStore(store.path).load()['width'], 133)
        self.controller.sync(self.view('lmu'))
        self.assertEqual(self.controller.store().load()['width'], 111)
        self.controller.store().save({**DEFAULTS, 'width': 144})
        self.assertNotEqual(self.controller.store().path, store.path)
        self.assertEqual(store.load()['width'], 133)
        self.assertEqual(self.legacy.read_bytes(), legacy_bytes)
        store.path.unlink()
        self.assertEqual(PreferencesStore(store.path).load()['width'], 122)

    def test_car_transition_closes_only_owned_window_and_preview_keeps_its_target(self):
        self.controller.sync(self.view())
        process = self.controller.process = Mock()
        process.poll.return_value = None
        with patch.object(self.controller, 'stop', wraps=self.controller.stop) as stop, \
                patch('overlay_control.subprocess.Popen') as spawn:
            self.controller.sync(self.view(name='BMW'))
            stop.assert_called_once()
            spawn.assert_not_called()
        self.controller.process = Mock()
        self.controller.process.poll.return_value = None
        self.controller.preview = True
        target = self.controller.settings_target
        with patch.object(self.controller, 'stop') as stop:
            self.controller.sync(self.view('lmu'))
            stop.assert_not_called()
        self.assertEqual(self.controller.settings_target, target)
        self.assertEqual(self.controller.latest['identity']['game'], 'lmu')
        self.controller.process = None

    def test_visibility_is_per_game_and_legacy_is_preserved(self):
        legacy = self.root / 'overlay-visibility.json'
        VisibilityStore(legacy).save(False)
        original = legacy.read_bytes()
        self.controller.select_game('lmu')
        self.assertFalse(self.controller.enabled())
        self.controller.set_visibility(True)
        self.assertTrue(self.controller.enabled())
        self.controller.select_game('iracing')
        self.assertFalse(self.controller.enabled())
        self.controller.set_visibility(False)
        self.controller.select_game('lmu')
        self.assertTrue(self.controller.enabled())
        self.assertEqual(legacy.read_bytes(), original)

    def test_game_query_sets_idle_default_and_delete_affects_only_current_car(self):
        app = FastAPI()
        app.include_router(routes(self.controller))
        with TestClient(app) as client:
            target = client.get('/api/overlay?game=lmu').json()['settings_target']
            self.assertEqual(target, {'game': 'lmu', 'car_key': None, 'car_name': None})
            self.assertEqual(self.controller.store().path, settings_path(self.root, 'lmu'))
            self.assertEqual(client.get('/api/overlay?game=unknown').status_code, 422)
            self.assertEqual(client.delete('/api/overlay/settings/car?game=lmu').status_code, 409)
            self.controller.sync(self.view('lmu'))
            path = self.controller.store().path
            PreferencesStore(path).save({**DEFAULTS, 'width': 144})
            default = settings_path(self.root, 'lmu')
            PreferencesStore(default).save({**DEFAULTS, 'width': 122})
            other = settings_path(self.root, 'iracing', 'porsche-963')
            PreferencesStore(other).save({**DEFAULTS, 'width': 155})
            other_bytes = other.read_bytes()
            value = client.delete('/api/overlay/settings/car?game=lmu').json()
            self.assertEqual(value['settings']['width'], 122)
            self.assertFalse(path.exists())
            self.assertTrue(default.exists())
            self.assertEqual(other.read_bytes(), other_bytes)

    def test_native_child_receives_the_selected_car_path(self):
        self.controller.sync(self.view('lmu'))
        self.controller.log_directory = self.root / 'logs'
        child = Mock()
        child.poll.return_value = None
        child.stdout = io.StringIO(json.dumps({'state': {'settings': dict(DEFAULTS)}}) + '\n')
        child.stdin = io.StringIO()
        with patch('overlay_control.subprocess.Popen', return_value=child) as spawn:
            self.controller._start_child()
        command = spawn.call_args.args[0]
        self.assertEqual(command[command.index('--settings') + 1], str(settings_path(self.root, 'lmu', 'porsche-963')))
        self.controller.stop()


if __name__ == '__main__':
    unittest.main()
