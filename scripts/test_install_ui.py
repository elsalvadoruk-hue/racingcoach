"""Source installation checks never install packages or access personal data."""
import unittest
from pathlib import Path
import tempfile
from unittest.mock import Mock, call, patch

import install_ui


class InstallScreenTests(unittest.TestCase):
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
