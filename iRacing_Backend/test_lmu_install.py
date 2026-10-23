import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import lmu_install


class SteamLibraryTests(unittest.TestCase):
    def setUp(self):
        # Never let this PC's real Steam registry leak into a test.
        self.steam_roots = []
        self.registered_game = None
        for name, value in (('registry_steam_roots', lambda: list(self.steam_roots)),
                            ('registry_game_folder', lambda: self.registered_game)):
            patcher = patch.object(lmu_install, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def _library_file(steam, *libraries):
        (steam / 'steamapps').mkdir(parents=True, exist_ok=True)
        entries = ''.join(' "%d"\n {\n  "path"  "%s"\n }\n' % (i, str(path).replace('\\', '\\\\'))
                          for i, path in enumerate(libraries))
        (steam / 'steamapps' / 'libraryfolders.vdf').write_text(
            '"libraryfolders"\n{\n%s}\n' % entries, encoding='utf-8')

    def test_games_in_another_steam_library_are_found(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            steam, library = root / 'Steam', root / 'Games' / 'SteamLibrary'
            game = library / lmu_install.GAME_FOLDER
            game.mkdir(parents=True)
            self._library_file(steam, library)
            with patch.dict(os.environ, {'ProgramFiles(x86)': str(root), 'ProgramFiles': str(root / 'none')}):
                self.assertIn(library, lmu_install.steam_libraries())
                self.assertEqual(lmu_install.game_folders(), [game])
                self.assertEqual(lmu_install.default_game_folder(), game)

    def test_without_steam_the_default_folder_is_reported(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(os.environ, {'ProgramFiles(x86)': temp, 'ProgramFiles': temp}):
            self.assertEqual(lmu_install.game_folders(), [])
            self.assertTrue(str(lmu_install.default_game_folder()).endswith('Le Mans Ultimate'))

    def test_steam_installed_outside_program_files_is_found_from_the_registry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            steam, library = root / 'D' / 'Steam', root / 'E' / 'SteamLibrary'
            game = library / lmu_install.GAME_FOLDER
            game.mkdir(parents=True)
            self._library_file(steam, steam, library)
            empty = root / 'ProgramFiles'
            with patch.dict(os.environ, {'ProgramFiles(x86)': str(empty), 'ProgramFiles': str(empty)}):
                self.assertEqual(lmu_install.game_folders(), [])
                # Steam writes its folder with forward slashes in lower case.
                self.steam_roots = [Path(str(steam).replace('\\', '/').lower())]
                self.assertEqual(lmu_install.game_folders(), [game])
                self.assertEqual(lmu_install.default_game_folder(), game)
                # Empty Program Files guess, D:\Steam and E:\SteamLibrary, each once.
                self.assertEqual(len(lmu_install.steam_libraries()), 3)

    def test_the_game_in_the_steam_folder_itself_is_found(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            steam = root / 'Steam'
            game = steam / lmu_install.GAME_FOLDER
            game.mkdir(parents=True)
            self.steam_roots = [steam]
            empty = root / 'ProgramFiles'
            with patch.dict(os.environ, {'ProgramFiles(x86)': str(empty), 'ProgramFiles': str(empty)}):
                self.assertEqual(lmu_install.game_folders(), [game])

    def test_the_folder_steam_registered_for_lmu_is_used_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = root / 'Elsewhere' / 'Le Mans Ultimate'
            game.mkdir(parents=True)
            empty = root / 'ProgramFiles'
            with patch.dict(os.environ, {'ProgramFiles(x86)': str(empty), 'ProgramFiles': str(empty)}):
                self.registered_game = game
                self.assertEqual(lmu_install.game_folders(), [game])
                self.registered_game = root / 'missing'
                self.assertEqual(lmu_install.game_folders(), [])
                steam = root / 'Steam'
                same = steam / lmu_install.GAME_FOLDER
                same.mkdir(parents=True)
                self.steam_roots = [steam]
                self.registered_game = Path(str(same).upper())
                self.assertEqual(lmu_install.game_folders(), [same])



class RegistryLookupTests(unittest.TestCase):
    def test_registry_values_that_are_missing_are_skipped(self):
        with patch.object(lmu_install, '_registry_value', return_value=None):
            self.assertEqual(lmu_install.registry_steam_roots(), [])
            self.assertIsNone(lmu_install.registry_game_folder())

    def test_each_steam_key_is_read(self):
        with patch.object(lmu_install, '_registry_value', side_effect=lambda hive, key, name: Path(key)):
            self.assertEqual(len(lmu_install.registry_steam_roots()), len(lmu_install._STEAM_KEYS))


@unittest.skipUnless(os.name == 'nt', 'Windows registry')
class RegistryReadTests(unittest.TestCase):
    def test_a_key_that_does_not_exist_reads_as_nothing(self):
        self.assertIsNone(lmu_install._registry_value(
            'HKEY_CURRENT_USER', r'Software\iracing-coach-test\does-not-exist', 'SteamPath'))

    def test_reading_the_real_registry_never_raises(self):
        roots = lmu_install.registry_steam_roots()
        self.assertTrue(all(isinstance(path, Path) for path in roots))
        folder = lmu_install.registry_game_folder()
        self.assertTrue(folder is None or isinstance(folder, Path))


if __name__ == '__main__':
    unittest.main()
