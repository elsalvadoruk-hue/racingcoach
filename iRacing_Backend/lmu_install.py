"""Where LMU is installed: every Steam library, not only the default drive."""
from __future__ import annotations

import os
from pathlib import Path
import re

GAME_FOLDER = Path('steamapps/common/Le Mans Ultimate')


def steam_libraries():
    roots = [Path(root) / 'Steam' for root in (os.getenv('ProgramFiles(x86)'), os.getenv('ProgramFiles')) if root]
    libraries = list(roots)
    for root in roots:
        try:
            text = (root / 'steamapps' / 'libraryfolders.vdf').read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        # VDF escapes backslashes: "D:\\SteamLibrary".
        libraries += [Path(path.replace(2 * chr(92), chr(92)))
                      for path in re.findall(r'"path"\s+"([^"]+)"', text)]
    unique = {}
    for path in libraries:
        unique.setdefault(str(path).casefold(), path)
    return list(unique.values())


def game_folders():
    """Installed LMU folders, default Steam folder first."""
    return [library / GAME_FOLDER for library in steam_libraries() if (library / GAME_FOLDER).is_dir()]


def default_game_folder():
    found = game_folders()
    return found[0] if found else Path(r'C:\Program Files (x86)\Steam') / GAME_FOLDER
