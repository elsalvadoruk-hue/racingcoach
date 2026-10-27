"""Launcher lifecycle checks without starting the real API or accessing personal data."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import launch_app


class LauncherTests(unittest.TestCase):
    def test_existing_app_is_reused_without_launching_or_stopping_processes(self):
        with patch.object(launch_app, "api_ready", return_value=True), \
             patch.object(launch_app, "require_current_api"), \
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
             patch.object(launch_app, "require_current_api"), \
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


class ApiCompatibilityTests(unittest.TestCase):
    def test_old_running_backend_is_not_silently_reused_or_stopped(self):
        with patch.object(launch_app, 'api_ready', return_value=True), \
             patch.object(launch_app, 'ui_ready', return_value=True), \
             patch.object(launch_app, 'read_http', return_value=b'{"paths":{"/api/lmu/workspace/{route}":{"post":{}}}}'), \
             patch.object(launch_app.subprocess, 'Popen') as spawn, \
             patch.object(launch_app, 'stop_process') as stop:
            with self.assertRaisesRegex(RuntimeError, '이전 분석 서버'):
                launch_app.run(1, False)
            spawn.assert_not_called()
            stop.assert_not_called()

    def test_current_import_api_is_accepted_but_missing_or_malformed_contract_is_rejected(self):
        with patch.object(launch_app, 'read_http', return_value=b'{"paths":{"/api/lmu/telemetry/import":{"post":{}},"/api/session-logs/auto":{"put":{}},"/api/driver-history/refresh":{"get":{},"post":{}}}}'):
            launch_app.require_current_api()
        for response in (None, b'not json', b'{"paths":null}', b'{"paths":{"/api/lmu/telemetry/import":{"get":{}}}}', b'{"paths":{"/api/lmu/telemetry/import":{"post":{}}}}', b'{"paths":{"/api/lmu/telemetry/import":{"post":{}},"/api/session-logs/auto":{"put":{}}}}'):
            with self.subTest(response=response), patch.object(launch_app, 'read_http', return_value=response):
                with self.assertRaises(RuntimeError):
                    launch_app.require_current_api()


class ScreenSelectionTests(unittest.TestCase):
    def layout(self, temp, built_after_source, dev_tools=True):
        root = Path(temp)
        (root / "src").mkdir(); (root / "public").mkdir(); (root / "build").mkdir()
        if dev_tools:
            (root / "node_modules" / "react-scripts").mkdir(parents=True)
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

    def test_a_shared_copy_serves_its_build_even_with_node_installed(self):
        # Unzipping a patch with a tool that drops file times can make src/ look newer than build/.
        with tempfile.TemporaryDirectory() as temp:
            command = self.frontend(self.layout(temp, False, dev_tools=False), "node")
            self.assertTrue(command[1].endswith("serve_ui.py"))


FAKE_PYTHON = """import sys
sys.version_info = (3, {minor}, 0, 'final', 0)
if sys.argv[1:2] == ['-c']:
    exec(sys.argv[2])
"""

# A stand-in for the Windows py launcher: "py -3.12 ..." runs as 3.12 when that version is listed,
# "py -3 ..." runs as the newest listed one, anything else fails like a missing version.
FAKE_LAUNCHER = """import sys
installed = {minors!r}
wanted = sys.argv[1] if len(sys.argv) > 1 else ''
if wanted == '-3' and installed:
    minor = max(installed)
elif wanted.startswith('-3.') and wanted[3:].isdigit() and int(wanted[3:]) in installed:
    minor = int(wanted[3:])
else:
    sys.exit(103)
sys.version_info = (3, minor, 0, 'final', 0)
if sys.argv[2:3] == ['-c']:
    exec(sys.argv[3])
"""


@unittest.skipUnless(sys.platform == "win32", "the bat files run only on Windows")
class FindPythonTests(unittest.TestCase):
    """find_python.bat picks the Python that 설치.bat installs into and 실행.bat starts."""

    def probe(self, *minors, launcher=()):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "한글 폴더 (1)"
            (root / "scripts").mkdir(parents=True)
            shutil.copy2(Path(__file__).with_name("find_python.bat"), root / "scripts" / "find_python.bat")
            (root / "probe.bat").write_bytes(
                b'@echo off\r\nsetlocal DisableDelayedExpansion\r\npushd "%~dp0"\r\ncall "scripts\\find_python.bat"\r\n'
                b'echo [%COACH_PYTHON%][%COACH_PY_VERSION%][%COACH_PY_NEW%]\r\npopd\r\n')
            path = []
            for index, minor in enumerate(minors):
                fake = Path(folder) / f"python{index}"
                fake.mkdir()
                (fake / "fake.py").write_text(FAKE_PYTHON.format(minor=minor), encoding="utf-8")
                (fake / "python.cmd").write_text(f'@"{sys.executable}" "%~dp0fake.py" %*\r\n', encoding="ascii")
                path.append(str(fake))
            if launcher:
                fake = Path(folder) / "launcher"
                fake.mkdir()
                (fake / "fakepy.py").write_text(FAKE_LAUNCHER.format(minors=list(launcher)), encoding="utf-8")
                (fake / "py.cmd").write_text(f'@"{sys.executable}" "%~dp0fakepy.py" %*\r\n', encoding="ascii")
                path.append(str(fake))
            # Only the fakes and cmd's own folder: no real py launcher, no real Python on the path.
            path.append(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"))
            env = dict(os.environ, PATH=os.pathsep.join(path))
            result = subprocess.run(["cmd", "/c", str(root / "probe.bat")], capture_output=True, env=env,
                                    stdin=subprocess.DEVNULL, timeout=60)
            return result.stdout.decode("utf-8", "replace").strip()

    def test_a_tested_version_is_used(self):
        self.assertEqual(self.probe(12), "[python][3.12.0][]")

    def test_an_older_supported_version_is_still_used(self):
        self.assertEqual(self.probe(11), "[python][3.11.0][]")

    def test_a_newer_python_is_tried_when_it_is_the_only_one(self):
        self.assertEqual(self.probe(15), "[python][3.15.0][1]")
        self.assertEqual(self.probe(launcher=(15,)), "[py -3][3.15.0][1]")

    def test_a_tested_version_wins_over_a_newer_default(self):
        # The reported case: "python" is 3.15, and 3.12 was added beside it.
        self.assertEqual(self.probe(15, launcher=(15, 12)), "[py -3.12][3.12.0][]")
        self.assertEqual(self.probe(15, 11), "[python][3.15.0][1]")

    def test_too_old_or_nothing_found(self):
        self.assertEqual(self.probe(9), "[][][]")
        self.assertEqual(self.probe(), "[][][]")

# Answers the version probe like FAKE_PYTHON; "-m pip ..." writes what it was given and succeeds or fails.
FAKE_PIP_PYTHON = """import os, sys
sys.version_info = (3, {minor}, 0, 'final', 0)
if sys.argv[1:2] == ['-c']:
    exec(sys.argv[2])
elif sys.argv[1:3] == ['-m', 'pip']:
    with open(os.path.join(os.path.dirname(__file__), 'pip-call.txt'), 'w') as log:
        log.write(' '.join(sys.argv[3:]) + '\\n' + os.environ.get('PYYAML_FORCE_LIBYAML', '<unset>'))
    sys.exit({pip_exit})
elif len(sys.argv) > 1 and sys.argv[1].replace(chr(92), '/') == 'scripts/install_ui.py':
    print('MOCK_SCREEN_SETUP')
    sys.exit({ui_exit})
"""


@unittest.skipUnless(sys.platform == "win32", "the bat files run only on Windows")
class InstallerTests(unittest.TestCase):
    """설치.bat installs with the picked Python and explains a failure on a new one."""

    def install(self, minor, pip_exit, ui_exit=0):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "한글 폴더 (1)"
            (root / "scripts").mkdir(parents=True)
            project = Path(__file__).resolve().parents[1]
            shutil.copy2(project / "설치.bat", root / "설치.bat")
            shutil.copy2(project / "scripts" / "find_python.bat", root / "scripts" / "find_python.bat")
            fake = Path(folder) / "python"
            fake.mkdir()
            (fake / "fake.py").write_text(FAKE_PIP_PYTHON.format(minor=minor, pip_exit=pip_exit, ui_exit=ui_exit), encoding="utf-8")
            (fake / "python.cmd").write_text(f'@"{sys.executable}" "%~dp0fake.py" %*\r\n', encoding="ascii")
            path = os.pathsep.join([str(fake), os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")])
            env = dict(os.environ, PATH=path)
            env.pop("PYYAML_FORCE_LIBYAML", None)
            result = subprocess.run(["cmd", "/c", str(root / "설치.bat")], capture_output=True, env=env,
                                    input=b"\r\n", timeout=60)
            call = (fake / "pip-call.txt").read_text() if (fake / "pip-call.txt").exists() else ""
            return result.returncode, result.stdout.decode("utf-8", "replace"), call

    def test_pyyaml_is_installed_without_compiling_its_optional_part(self):
        code, output, call = self.install(12, 0)
        self.assertEqual(code, 0, output)
        self.assertIn("MOCK_SCREEN_SETUP", output)
        arguments, pyyaml_setting = call.splitlines()
        self.assertIn("--prefer-binary", arguments)
        self.assertIn("requirements_core.txt", arguments)
        self.assertEqual(pyyaml_setting, "0")
        self.assertNotIn("newer than the tested", output)

    def test_screen_failure_is_not_reported_as_success(self):
        code, output, _ = self.install(12, 0, ui_exit=1)
        self.assertEqual(code, 1)
        self.assertIn("Screen setup failed", output)
        self.assertNotIn("Done. Start the app", output)

    def test_a_failure_on_a_new_python_points_to_3_12(self):
        code, output, _ = self.install(15, 1)
        self.assertEqual(code, 1)
        self.assertIn("newer than the tested", output)
        self.assertIn("py install 3.12", output)

    def test_a_failure_on_a_tested_python_points_to_the_messages(self):
        code, output, _ = self.install(12, 1)
        self.assertEqual(code, 1)
        self.assertIn("Check the internet connection", output)
        self.assertNotIn("py install 3.12", output)


class UpdateLaunchTests(unittest.TestCase):
    """A downloaded update is applied before the servers start, and a restart request starts the launcher again."""

    def test_backend_asking_for_restart_stops_the_servers_and_returns_the_restart_code(self):
        backend, frontend = Mock(), Mock()
        backend.poll.side_effect = [None, launch_app.app_update.RESTART_EXIT]
        frontend.poll.return_value = None
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(launch_app, "LOG_DIR", Path(temp)), \
             patch.object(launch_app, "api_ready", side_effect=[False, True]), \
             patch.object(launch_app, "ui_ready", side_effect=[False, True]), \
             patch.object(launch_app, "port_busy", return_value=False), \
             patch.object(launch_app, "apply_update") as apply, \
             patch.object(launch_app, "commands", return_value={"backend": ["python", "api.py"], "frontend": ["node", "app.js"]}), \
             patch.object(launch_app.subprocess, "Popen", side_effect=[backend, frontend]) as spawn, \
             patch.object(launch_app.time, "sleep"), \
             patch.object(launch_app, "stop_process") as stop:
            self.assertEqual(launch_app.run(1, False), launch_app.app_update.RESTART_EXIT)
            apply.assert_called_once_with()
            self.assertEqual(spawn.call_args_list[0].kwargs["env"][launch_app.app_update.RESTART_ENV], "1")
            self.assertNotIn(launch_app.app_update.RESTART_ENV, spawn.call_args_list[1].kwargs["env"])
            self.assertEqual(stop.call_count, 2)

    def test_an_update_is_not_applied_under_a_running_app(self):
        with patch.object(launch_app, "api_ready", return_value=True), \
             patch.object(launch_app, "require_current_api"), \
             patch.object(launch_app, "ui_ready", return_value=True), \
             patch.object(launch_app, "apply_update") as apply, \
             patch.object(launch_app.webbrowser, "open", return_value=True):
            self.assertEqual(launch_app.run(1, False), 0)
            apply.assert_not_called()

    def test_restart_runs_the_launcher_again_without_a_new_browser_tab(self):
        child = Mock()
        child.wait.side_effect = [KeyboardInterrupt, 0]
        with patch.object(launch_app, "run", return_value=launch_app.app_update.RESTART_EXIT), \
             patch.object(launch_app.sys, "argv", ["launch_app.py", "--dev"]), \
             patch.object(launch_app.subprocess, "Popen", return_value=child) as spawn:
            self.assertEqual(launch_app.main(), 0)
            command = spawn.call_args.args[0]
            self.assertTrue(command[1].endswith("launch_app.py"))
            self.assertEqual(command[2:], ["--dev", "--no-browser"])

    def test_applied_update_installs_changed_packages_and_builds_a_source_only_screen(self):
        result = {"version": "1.5.0", "files": 3, "has_build": False, "requirements_changed": True}
        with patch.object(launch_app.app_update, "read_pending", return_value={"version": "1.5.0"}), \
             patch.object(launch_app.app_update, "apply_staged", return_value=result), \
             patch.object(launch_app, "install_packages") as packages, \
             patch.object(launch_app, "build_is_current", return_value=False), \
             patch("install_ui.install", return_value=0) as build:
            launch_app.apply_update()
            packages.assert_called_once_with()
            build.assert_called_once_with()

    def test_prebuilt_update_without_package_changes_only_copies(self):
        result = {"version": "1.5.0", "files": 3, "has_build": True, "requirements_changed": False}
        with patch.object(launch_app.app_update, "read_pending", return_value={"version": "1.5.0"}), \
             patch.object(launch_app.app_update, "apply_staged", return_value=result), \
             patch.object(launch_app, "install_packages") as packages, \
             patch("install_ui.install") as build:
            launch_app.apply_update()
            packages.assert_not_called()
            build.assert_not_called()

    def test_a_failed_copy_still_starts_the_current_version(self):
        with patch.object(launch_app.app_update, "read_pending", return_value={"version": "1.5.0"}), \
             patch.object(launch_app.app_update, "apply_staged", side_effect=PermissionError("locked")), \
             patch.object(launch_app, "install_packages") as packages:
            launch_app.apply_update()
            packages.assert_not_called()


if __name__ == "__main__":
    unittest.main()
