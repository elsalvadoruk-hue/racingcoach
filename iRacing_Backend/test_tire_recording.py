"""History stays useful: unsupported cars log only changes; LMU records with the window and voice off."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

from tire_overlay_control import TireOverlayController
from test_tire_overlay_control import Child
from test_tire_advisor import snapshot


def wait_for(condition, timeout=5.):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(.02)
    return False


class RecordingTests(unittest.TestCase):
    def controller(self, game, child):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / f'overlay/{game}/tires.json'
        self.spawn = Mock(return_value=child)
        controller = TireOverlayController(self.path, popen=self.spawn, game=game)
        self.addCleanup(controller.close)
        return controller

    def test_unsupported_car_is_logged_only_when_the_status_changes(self):
        child = Child()
        controller = self.controller('iracing', child)
        controller.update(enabled=True)
        controller.history = Mock()
        data = replace(snapshot(0.), car='toyotagr86', active=False).to_dict()
        for status in ('unsupported_car',) * 5 + ('cockpit_waiting', 'unsupported_car', 'unsupported_car'):
            child.stdout.queue.put(dict(type='state', status=status, tire_snapshot=data))
        child.stdout.queue.put(dict(type='state', status='reading', tire_snapshot=snapshot(1.).to_dict()))
        self.assertTrue(wait_for(lambda: controller.current.get('status') == 'reading'))
        calls = controller.history.submit.call_args_list
        self.assertEqual(len(calls), 4)  # first unsupported, cockpit_waiting, unsupported again, reading
        self.assertEqual([c.args[0] is None for c in calls], [True, False, True, False])

    def test_lmu_records_by_default_with_window_and_voice_off(self):
        controller = self.controller('lmu', Child())
        controller.start()
        self.assertTrue(self.spawn.called)
        self.assertIn('--hidden', self.spawn.call_args.args[0])
        view = controller.status()
        self.assertEqual((view['running'], view['enabled'], view['voice_enabled'], view['lmu_record']), (True, False, False, True))
        view = controller.update(lmu_record=False)
        self.assertFalse(view['running'])
        self.assertFalse(json.loads(self.path.read_text())['lmu_record'])

    def test_iracing_does_not_run_without_window_or_voice(self):
        controller = self.controller('iracing', Child())
        controller.start()
        self.spawn.assert_not_called()
        with self.assertRaises(ValueError):
            controller.update(lmu_record=True)


if __name__ == '__main__':
    unittest.main()
