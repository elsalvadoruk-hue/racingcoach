"""Launcher lifecycle checks without starting the real API or accessing personal data."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import launch_app


class LauncherTests(unittest.TestCase):
    def test_existing_app_is_reused_without_launching_or_stopping_processes(self):
        with patch.object(launch_app, "api_ready", return_value=True), \
             patch.object(launch_app, "ui_ready", return_value=True), \
             patch.object(launch_app.subprocess, "Popen") as spawn, \
             patch.object(launch_app, "stop_process") as stop, \
             patch.object(launch_app.webbrowser, "open", return_value=True) as browser:
            self.assertEqual(launch_app.run(1, True), 0)
            spawn.assert_not_called()
            stop.assert_not_called()
            browser.assert_called_once_with(launch_app.UI_URL)

    def test_unrelated_occupied_port_is_reported_without_starting_services(self):
        with patch.object(launch_app, "api_ready", return_value=False), \
             patch.object(launch_app, "ui_ready", return_value=False), \
             patch.object(launch_app, "port_busy", return_value=True), \
             patch.object(launch_app.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError, "24158"):
                launch_app.run(1, False)
            spawn.assert_not_called()

    def test_interrupt_stops_only_the_new_service_and_closes_its_log(self):
        process = Mock()
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(launch_app, "LOG_DIR", Path(temp)), \
             patch.object(launch_app, "api_ready", return_value=True), \
             patch.object(launch_app, "ui_ready", side_effect=[False, True]), \
             patch.object(launch_app, "port_busy", return_value=False), \
             patch.object(launch_app, "commands", return_value={"frontend": ["node", "app.js"]}) as commands, \
             patch.object(launch_app.subprocess, "Popen", return_value=process) as spawn, \
             patch.object(launch_app.time, "sleep", side_effect=KeyboardInterrupt), \
             patch.object(launch_app, "stop_process") as stop:
            self.assertEqual(launch_app.run(1, False), 0)
            commands.assert_called_once_with(False, True)
            stop.assert_called_once_with(process)
            self.assertTrue(spawn.call_args.kwargs["stdout"].closed)
            env = spawn.call_args.kwargs["env"]
            self.assertEqual((env["PORT"], env["BROWSER"]), ("24157", "none"))

    def test_startup_failure_closes_started_processes_before_reporting_error(self):
        process = Mock()
        process.poll.return_value = 1
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(launch_app, "LOG_DIR", Path(temp)), \
             patch.object(launch_app, "api_ready", return_value=False), \
             patch.object(launch_app, "ui_ready", return_value=False), \
             patch.object(launch_app, "port_busy", return_value=False), \
             patch.object(launch_app, "commands", return_value={"backend": ["python", "api.py"]}), \
             patch.object(launch_app.subprocess, "Popen", return_value=process), \
             patch.object(launch_app, "stop_process") as stop:
            with self.assertRaisesRegex(RuntimeError, "backend.log"):
                launch_app.run(1, False)
            stop.assert_called_once_with(process)


class ScreenSelectionTests(unittest.TestCase):
    def layout(self, temp, built_after_source):
        root = Path(temp)
        (root / "src").mkdir(); (root / "public").mkdir(); (root / "build").mkdir()
        source, built = root / "src" / "App.js", root / "build" / "index.html"
        source.write_text("x"); built.write_text("x")
        import os
        os.utime(source, (1000, 1000)); os.utime(built, (2000, 2000) if built_after_source else (500, 500))
        return root

    def frontend(self, root, node, dev=False):
        with patch.object(launch_app, "ROOT", root), patch.object(launch_app, "BUILD_DIR", root / "build"),              patch.object(launch_app, "USE_DEV_SERVER", dev), patch.object(launch_app.shutil, "which", return_value=node),              patch.object(Path, "is_file", autospec=True, side_effect=lambda path: path.name == "start.js" or path.exists() and not path.is_dir()):
            return launch_app.commands(False, True)["frontend"]

    def test_current_build_is_served_without_node(self):
        with tempfile.TemporaryDirectory() as temp:
            command = self.frontend(self.layout(temp, True), None)
            self.assertTrue(command[1].endswith("serve_ui.py"))

    def test_stale_build_or_dev_flag_uses_the_dev_server(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(self.frontend(self.layout(temp, False), "node")[0], "node")
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(self.frontend(self.layout(temp, True), "node", dev=True)[0], "node")

    def test_stale_build_is_still_used_when_node_is_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertTrue(self.frontend(self.layout(temp, False), None)[1].endswith("serve_ui.py"))


if __name__ == "__main__":
    unittest.main()
