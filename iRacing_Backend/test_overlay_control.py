"""Overlay preferences and route validation with temporary files and no real processes."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from overlay_control import OverlayController, routes
from overlay_preferences import DEFAULTS, PreferencesStore, fit_position
from overlay_visibility import VisibilityStore, driving


class PreferenceTests(unittest.TestCase):
    def test_saved_appearance_is_restored_and_unknown_fields_are_omitted(self):
        with tempfile.TemporaryDirectory() as folder:
            store = PreferencesStore(Path(folder)/"overlay.json")
            self.assertEqual(store.load(), DEFAULTS)
            values = {**DEFAULTS, "width": 121, "height": 122, "x": -700, "y": 55,
                      "fixed": True, "content_opacity": 70, "background_opacity": 0, "unrelated": "omit"}
            store.save(values)
            self.assertEqual(store.load(), {key: values[key] for key in DEFAULTS})
            self.assertNotIn("unrelated", json.loads(store.path.read_text()))

    def test_invalid_file_and_types_use_safe_defaults_and_bounded_values(self):
        with tempfile.TemporaryDirectory() as folder:
            store = PreferencesStore(Path(folder)/"overlay.json")
            store.path.write_text("broken")
            self.assertEqual(store.load(), DEFAULTS)
            self.assertIsNotNone(store.load_error)
            store.path.write_text(json.dumps({"version": 1, "width": True, "height": 800,
                                              "fixed": "false", "content_opacity": -50}))
            loaded = store.load()
            self.assertEqual((loaded["width"], loaded["height"], loaded["content_opacity"], loaded["fixed"]),
                             (100, 200, 0, False))

    def test_failed_atomic_save_preserves_the_previous_file(self):
        with tempfile.TemporaryDirectory() as folder:
            store = PreferencesStore(Path(folder)/"overlay.json")
            store.save({**DEFAULTS, "width": 121})
            with patch("overlay_preferences.os.replace", side_effect=PermissionError("busy")):
                with self.assertRaises(PermissionError):
                    store.save({**DEFAULTS, "width": 199})
            self.assertEqual(store.load()["width"], 121)
            self.assertEqual(list(Path(folder).glob("*.tmp")), [])

    def test_missing_monitor_recovers_the_entire_hud_and_preserves_negative_monitor_positions(self):
        areas = [(-1920, 0, 0, 1080), (0, 0, 1920, 1040)]
        self.assertEqual(fit_position(-1500, 50, 600, 230, areas), (-1500, 50))
        self.assertEqual(fit_position(6000, -500, 600, 230, areas), (1320, 0))
        self.assertEqual(fit_position(1800, 1000, 600, 230, areas), (1320, 810))


class OverlayRouteTests(unittest.TestCase):
    def setUp(self):
        self.controller = Mock()
        self.controller.command.return_value = {"running": True}
        app = FastAPI()
        app.include_router(routes(self.controller))
        self.client = TestClient(app)

    def test_settings_reject_invalid_ranges_booleans_and_unrecognized_fields(self):
        for body in ({"width": 201}, {"height": 74}, {"content_opacity": 101},
                     {"background_opacity": -1}, {"x": True}, {"width": "120"}, {"program": "other.exe"}):
            self.assertEqual(self.client.put("/api/overlay/settings", json=body).status_code, 422)
        self.controller.command.assert_not_called()

    def test_fixed_conflict_is_reported_and_no_persistence_is_claimed(self):
        self.controller.command.side_effect = RuntimeError("먼저 고정을 해제하세요.")
        response = self.client.put("/api/overlay/settings", json={"width": 121})
        self.assertEqual(response.status_code, 409)
        self.assertIn("고정을 해제", response.json()["detail"])

    def test_scene_validation_and_explicit_save_command(self):
        self.assertEqual(self.client.post("/api/overlay/demo", json={"goal": "unknown"}).status_code, 422)
        self.assertEqual(self.client.post("/api/overlay/fixed", json={"fixed": True}).status_code, 200)
        self.controller.command.assert_called_once_with("fixed", {"fixed": True})

    def test_auto_display_requires_a_real_boolean_and_does_not_change_appearance(self):
        for value in ('true', 1, None):
            self.assertEqual(self.client.put('/api/overlay/visibility', json={'enabled': value}).status_code, 422)
        self.controller.set_visibility.return_value = {'auto_show': False}
        self.assertEqual(self.client.put('/api/overlay/visibility', json={'enabled': False}).json(), {'auto_show': False})
        self.controller.set_visibility.assert_called_once_with(False)
        self.controller.command.assert_not_called()

    def test_idle_status_does_not_spawn_and_shutdown_only_owns_its_child(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = OverlayController(Path(folder)/"overlay.json")
            with patch("overlay_control.subprocess.Popen") as spawn:
                self.assertFalse(controller.status()["running"])
                self.assertFalse(controller.stop()["running"])
                spawn.assert_not_called()


def active(**changes):
    return {'connected': True, 'run_id': 'session-a', 'snapshot': {
        'on_track': True, 'is_replay': False, 'session_state': 4, 'speed_mps': 0, **changes}, 'points': []}


class VisibilityTests(unittest.TestCase):
    def test_monitor_counts_processing_time_inside_frame_interval(self):
        controller = OverlayController()
        now, waits, frames = [0.], [], []

        def sync(view):
            frames.append(now[0])
            now[0] += .012

        def wait(delay):
            waits.append(delay)
            now[0] += delay
            if len(waits) == 3:
                controller.monitor_stop.set()

        controller.sync = sync
        with patch('overlay_control.monotonic', side_effect=lambda: now[0]), \
                patch.object(controller.monitor_stop, 'wait', side_effect=wait):
            controller.start_monitor(lambda: {})
            controller.monitor.join(timeout=1)
        self.assertFalse(controller.monitor.is_alive())
        self.assertEqual(len(frames), 3)
        self.assertAlmostEqual(frames[1]-frames[0], 1/30)
        self.assertAlmostEqual(waits[0], 1/30-.012)

    def test_live_status_uses_child_appearance_without_reading_disk_each_frame(self):
        controller = OverlayController()
        controller.process = Mock()
        controller.process.poll.return_value = None
        controller.current = {'settings': dict(DEFAULTS), 'visible': True}
        controller.auto_show = True
        with patch.object(PreferencesStore, 'load', side_effect=AssertionError('frame disk read')):
            self.assertEqual(controller.status()['settings'], DEFAULTS)

    def test_stationary_cockpit_shows_but_absent_replay_and_finished_states_do_not(self):
        self.assertTrue(driving(active()))
        for changes in ({'on_track': False}, {'on_track': None}, {'is_replay': True}, {'is_replay': None},
                        {'session_state': 0}, {'session_state': 6}, {'session_state': None}, {'session_state': True}):
            self.assertFalse(driving(active(**changes)), changes)
        self.assertFalse(driving({**active(), 'connected': False}))
        self.assertFalse(driving({}))

    def test_automatic_preference_is_independent_and_failed_save_keeps_previous_value(self):
        with tempfile.TemporaryDirectory() as folder:
            store = VisibilityStore(Path(folder)/'visibility.json')
            self.assertTrue(store.load())
            store.save(False)
            with patch('overlay_visibility.os.replace', side_effect=PermissionError('busy')):
                with self.assertRaises(PermissionError):
                    store.save(True)
            self.assertFalse(store.load())
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])
            store.path.write_text('[]')
            self.assertFalse(store.load())

    def test_monitor_input_is_fresh_bounded_and_does_not_rebuild_voice_context(self):
        from live_telemetry import LiveTelemetry
        now = [100.]
        live = LiveTelemetry(clock=lambda: now[0])
        live.state = 'CONNECTED'
        for tick in range(301):
            live.samples.append({'wall_time': 90+tick/30, 'session_time': tick/30,
                                 **active()['snapshot'], 'brake': .2, 'throttle': None})
        with patch.object(live, 'context', side_effect=AssertionError('heavy context')):
            view = live.overlay_state()
            self.assertTrue(driving(view))
            self.assertLessEqual(len(view['points']), 301)
            self.assertIsNone(view['points'][-1]['throttle'])
            now[0] = 101.
            self.assertFalse(live.overlay_state()['connected'])
            now[0] = 100.
            live.state = 'WAITING'
            self.assertFalse(live.overlay_state()['connected'])

    def fake_child(self, controller):
        def start():
            controller.process = Mock()
            controller.process.poll.return_value = None
            controller.current = controller.current or {'settings': dict(DEFAULTS), 'visible': False}

        def command(action, values):
            self.assertEqual(action, 'display')
            controller.current.update(mode=values['mode'], visible=values['visible'])
            return controller.status()

        controller._start_child = Mock(side_effect=start)
        controller.command = Mock(side_effect=command)

    def test_driving_transitions_hide_both_layers_without_browser_polling(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = OverlayController(Path(folder)/'overlay.json')
            self.fake_child(controller)
            controller.sync({'connected': False})
            controller._start_child.assert_not_called()
            controller.sync(active())
            self.assertEqual(controller.current['mode'], 'live')
            self.assertTrue(controller.current['visible'])
            for state in (active(on_track=False), active(is_replay=True), active(session_state=6), {'connected': False}):
                controller.sync(state)
                self.assertFalse(controller.current['visible'])
                controller.sync(active())
                self.assertTrue(controller.current['visible'])

    def test_explicit_preview_overrides_driving_and_closing_returns_to_automatic(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = OverlayController(Path(folder)/'overlay.json')
            self.fake_child(controller)
            controller.start()
            controller.sync({'connected': False})
            self.assertTrue(controller.preview)
            self.assertEqual(controller.current['mode'], 'preview')
            controller.sync(active())
            controller.end_preview()
            self.assertFalse(controller.preview)
            self.assertEqual(controller.current['mode'], 'live')
            self.assertTrue(controller.current['visible'])

    def test_disabled_auto_display_is_restored_without_persisting_appearance(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'overlay.json'
            controller = OverlayController(path)
            self.fake_child(controller)
            controller.sync(active())
            controller.set_visibility(False)
            self.assertFalse(controller.current['visible'])
            self.assertFalse(path.exists())
            restored = OverlayController(path)
            self.assertFalse(restored.enabled())
            self.fake_child(restored)
            restored.sync(active())
            restored._start_child.assert_not_called()

    def test_exited_child_does_not_leave_a_preview_override_or_receive_hide_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = OverlayController(Path(folder)/'overlay.json')
            self.fake_child(controller)
            controller.start()
            controller.process.poll.return_value = 0
            controller.command.reset_mock()
            controller.sync({'connected': False})
            controller.command.assert_not_called()
            self.assertFalse(controller.status()['preview'])


if __name__ == "__main__":
    unittest.main()
