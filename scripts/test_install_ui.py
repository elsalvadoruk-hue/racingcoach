"""Source installation checks never install packages or access personal data."""
import unittest
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
from unittest.mock import Mock, call, patch

import install_ui


class InstallScreenTests(unittest.TestCase):
    def test_windows_uses_cmd_even_when_python_finds_extensionless_npm(self):
        npm = r'C:\Program Files\nodejs\npm'
        npm_cmd = npm + '.cmd'
        commands = {'node': r'C:\Program Files\nodejs\node.exe', 'npm': npm, 'npm.cmd': npm_cmd}

        def launch(args, **kwargs):
            if args[0] == npm:
                raise OSError(8, 'not a valid Win32 application', None, 193)
            return Mock(returncode=0)

        with patch('sys.platform', 'win32'), \
                patch.object(install_ui, 'build_is_current', side_effect=[False, True]), \
                patch.object(install_ui.shutil, 'which', side_effect=commands.get), \
                patch.object(install_ui.subprocess, 'run', side_effect=launch) as run:
            self.assertEqual(install_ui.install(), 0)
            self.assertEqual(run.call_args_list, [call([npm_cmd, 'ci'], cwd=install_ui.ROOT, check=False),
                                                 call([npm_cmd, 'run', 'build'], cwd=install_ui.ROOT, check=False)])

    def test_windows_extensionless_npm_without_cmd_is_not_started(self):
        commands = {'node': 'node.exe', 'npm': 'npm'}
        output = io.StringIO()
        with patch('sys.platform', 'win32'), redirect_stdout(output), \
                patch.object(install_ui, 'build_is_current', return_value=False), \
                patch.object(install_ui.shutil, 'which', side_effect=commands.get), \
                patch.object(install_ui.subprocess, 'run') as run:
            self.assertEqual(install_ui.install(), 1)
            run.assert_not_called()
        self.assertIn('racingcoach-setup-*.zip', output.getvalue())

    def test_unix_source_installation_still_uses_npm(self):
        commands = {'node': '/usr/bin/node', 'npm': '/usr/bin/npm'}
        with patch('sys.platform', 'linux'), \
                patch.object(install_ui, 'build_is_current', side_effect=[False, True]), \
                patch.object(install_ui.shutil, 'which', side_effect=commands.get), \
                patch.object(install_ui.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            self.assertEqual(install_ui.install(), 0)
            self.assertEqual(run.call_args_list[0].args[0], ['/usr/bin/npm', 'ci'])

    def test_check_only_accepts_source_tools_without_installing_anything(self):
        with patch.object(install_ui, 'build_is_current', return_value=False), \
                patch.object(install_ui.shutil, 'which', return_value='tool'), \
                patch.object(install_ui.subprocess, 'run') as run:
            self.assertEqual(install_ui.install(check_only=True), 0)
            run.assert_not_called()

    def test_check_only_accepts_packaged_screen_without_node(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'build').mkdir()
            (root / 'build' / 'index.html').write_text('<html>included</html>')
            with patch.object(install_ui, 'ROOT', root), \
                    patch.object(install_ui, 'build_is_current', return_value=False), \
                    patch.object(install_ui.shutil, 'which') as which, \
                    patch.object(install_ui.subprocess, 'run') as run:
                self.assertEqual(install_ui.install(check_only=True), 0)
                which.assert_not_called()
                run.assert_not_called()

    def test_npm_launch_failure_shows_windows_error_and_first_install_download(self):
        output = io.StringIO()
        with redirect_stdout(output), \
                patch.object(install_ui, 'build_is_current', return_value=False), \
                patch.object(install_ui.shutil, 'which', return_value='npm.cmd'), \
                patch.object(install_ui.subprocess, 'run', side_effect=OSError(8, 'not a valid Win32 application', None, 193)):
            self.assertEqual(install_ui.install(), 1)
        self.assertIn('193', output.getvalue())
        self.assertIn('racingcoach-setup-*.zip', output.getvalue())
        self.assertIn('https://github.com/elsalvadoruk-hue/racingcoach/releases/latest', output.getvalue())

    def test_included_build_without_dev_tools_needs_no_node_even_with_newer_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'build').mkdir()
            (root / 'build/index.html').write_text('<html>included</html>')
            with patch.object(install_ui, 'ROOT', root), patch.object(install_ui, 'build_is_current', return_value=False), \
                    patch.object(install_ui.shutil, 'which') as which, patch.object(install_ui.subprocess, 'run') as run:
                self.assertEqual(install_ui.install(), 0)
                which.assert_not_called()
                run.assert_not_called()

    def test_existing_current_build_needs_no_node(self):
        with patch.object(install_ui, 'build_is_current', return_value=True), \
                patch.object(install_ui.shutil, 'which') as which, \
                patch.object(install_ui.subprocess, 'run') as run:
            self.assertEqual(install_ui.install(), 0)
            which.assert_not_called()
            run.assert_not_called()

    def test_missing_node_does_not_start_package_installation(self):
        with patch.object(install_ui, 'build_is_current', return_value=False), \
                patch.object(install_ui.shutil, 'which', return_value=None), \
                patch.object(install_ui.subprocess, 'run') as run:
            self.assertEqual(install_ui.install(), 1)
            run.assert_not_called()

    def test_source_download_installs_then_builds_and_checks_the_result(self):
        npm = r'C:\Program Files\nodejs\npm.cmd'
        with patch.object(install_ui, 'build_is_current', side_effect=[False, True]), \
                patch.object(install_ui.shutil, 'which', return_value=npm), \
                patch.object(install_ui.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            self.assertEqual(install_ui.install(), 0)
            self.assertEqual(run.call_args_list, [call([npm, 'ci'], cwd=install_ui.ROOT, check=False),
                                                 call([npm, 'run', 'build'], cwd=install_ui.ROOT, check=False)])

    def test_failed_package_install_does_not_continue_to_build(self):
        with patch.object(install_ui, 'build_is_current', return_value=False), \
                patch.object(install_ui.shutil, 'which', return_value='npm.cmd'), \
                patch.object(install_ui.subprocess, 'run', return_value=Mock(returncode=1)) as run:
            self.assertEqual(install_ui.install(), 1)
            self.assertEqual(run.call_count, 1)

    def test_successful_exit_without_a_current_build_is_rejected(self):
        with patch.object(install_ui, 'build_is_current', return_value=False), \
                patch.object(install_ui.shutil, 'which', return_value='npm.cmd'), \
                patch.object(install_ui.subprocess, 'run', return_value=Mock(returncode=0)):
            self.assertEqual(install_ui.install(), 1)


if __name__ == '__main__':
    unittest.main()
