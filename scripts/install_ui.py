"""Build the screen after installing a source download from GitHub."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

from launch_app import build_is_current

ROOT = Path(__file__).resolve().parents[1]
SETUP_GUIDANCE = (
    "For a first installation, download racingcoach-setup-*.zip from:\n"
    "https://github.com/elsalvadoruk-hue/racingcoach/releases/latest\n"
    "It includes Python and the screen; Node.js is not required.\n"
    "Extract the entire ZIP into a new folder and run its installer."
)


def install(check_only: bool = False) -> int:
    if not (ROOT / 'node_modules' / 'react-scripts').is_dir() and (ROOT / 'build' / 'index.html').is_file():
        print("Using the included screen. Node.js is not required.", flush=True)
        return 0
    if build_is_current():
        print("The screen build is already up to date.", flush=True)
        return 0
    # Python 3.12.0 can select npm's extensionless Unix script on Windows.
    # Request the Windows command explicitly instead of trying to execute that file.
    npm = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
    if not shutil.which("node") or not npm:
        print("The screen is missing or needs rebuilding.\n" + SETUP_GUIDANCE + "\n\n"
              "For a source installation, Node.js LTS and npm are required.\n"
              "Install it from https://nodejs.org/ and reopen the installer.\n"
              "Your data and downloaded voice files stay in place.", flush=True)
        return 1
    if check_only:
        print("The screen needs building from source.\n" + SETUP_GUIDANCE + "\n"
              "Node.js and npm were found. Continuing the source installation.", flush=True)
        return 0
    for args in ([npm, "ci"], [npm, "run", "build"]):
        print("Preparing the screen: npm " + " ".join(args[1:]), flush=True)
        try:
            result = subprocess.run(args, cwd=ROOT, check=False)
        except OSError as exc:
            print(f"Unable to start npm: {exc}\n" + SETUP_GUIDANCE + "\n"
                  "For a source installation, check Node.js LTS and npm.cmd on Windows.", flush=True)
            return 1
        if result.returncode:
            print("Screen setup failed. Check the messages above and rerun the installer.", flush=True)
            return 1
    if not build_is_current():
        print("The screen build is missing or out of date. Rerun the installer.", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='check installation files and tools without installing')
    raise SystemExit(install(check_only=parser.parse_args().check))
