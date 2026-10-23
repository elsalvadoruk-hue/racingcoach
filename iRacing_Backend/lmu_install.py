"""Where LMU is installed: every Steam library, not only the default drive."""
from __future__ import annotations

import os
from pathlib import Path
import re

GAME_FOLDER = Path('steamapps/common/Le Mans Ultimate')
STEAM_APP_ID = 2399420

# Where Steam records its own folder, wherever the user installed it.
_STEAM_KEYS = (
    ('HKEY_CURRENT_USER', r'Software\Valve\Steam', 'SteamPath'),
    ('HKEY_LOCAL_MACHINE', r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath'),
    ('HKEY_LOCAL_MACHINE', r'SOFTWARE\Valve\Steam', 'InstallPath'),
)
_GAME_KEY = ('HKEY_LOCAL_MACHINE',
             r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App %d' % STEAM_APP_ID,
             'InstallLocation')


def _registry_value(hive, key, name):
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(getattr(winreg, hive), key) as handle:
            value, kind = winreg.QueryValueEx(handle, name)
    except OSError:
        return None
    if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or not str(value).strip():
        return None
    return Path(os.path.expandvars(str(value).strip()))


def registry_steam_roots():
    """Steam folders recorded in the registry (e.g. D:\\Steam)."""
    return [path for path in (_registry_value(*key) for key in _STEAM_KEYS) if path]


def registry_game_folder():
    """LMU's own install folder as Steam registered it, if any."""
    return _registry_value(*_GAME_KEY)


def steam_libraries():
    defaults = [Path(root) / 'Steam' for root in (os.getenv('ProgramFiles(x86)'), os.getenv('ProgramFiles')) if root]
    roots = defaults + registry_steam_roots()
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
        unique.setdefault(os.path.normcase(os.path.normpath(str(path))), path)
    return list(unique.values())


def game_folders():
    """Installed LMU folders, default Steam folder first."""
    found = [library / GAME_FOLDER for library in steam_libraries() if (library / GAME_FOLDER).is_dir()]
    registered = registry_game_folder()
    if registered is not None and registered.is_dir():
        known = {os.path.normcase(os.path.normpath(str(path))) for path in found}
        if os.path.normcase(os.path.normpath(str(registered))) not in known:
            found.append(registered)
    return found


def default_game_folder():
    found = game_folders()
    return found[0] if found else Path(r'C:\Program Files (x86)\Steam') / GAME_FOLDER
