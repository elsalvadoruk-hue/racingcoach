import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import lmu_install


class SteamLibraryTests(unittest.TestCase):
    def test_games_in_another_steam_library_are_found(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            steam, library = root / 'Steam', root / 'Games' / 'SteamLibrary'
            (steam / 'steamapps').mkdir(parents=True)
            game = library / lmu_install.GAME_FOLDER
            game.mkdir(parents=True)
            escaped = str(library).replace('\\', '\\\\')
            (steam / 'steamapps' / 'libraryfolders.vdf').write_text(
                '"libraryfolders"\n{\n "0"\n {\n  "path"  "%s"\n }\n}\n' % escaped, encoding='utf-8')
            with patch.dict(os.environ, {'ProgramFiles(x86)': str(root), 'ProgramFiles': str(root / 'none')}):
                self.assertIn(library, lmu_install.steam_libraries())
                self.assertEqual(lmu_install.game_folders(), [game])
                self.assertEqual(lmu_install.default_game_folder(), game)

    def test_without_steam_the_default_folder_is_reported(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(os.environ, {'ProgramFiles(x86)': temp, 'ProgramFiles': temp}):
            self.assertEqual(lmu_install.game_folders(), [])
            self.assertTrue(str(lmu_install.default_game_folder()).endswith('Le Mans Ultimate'))


if __name__ == '__main__':
    unittest.main()
