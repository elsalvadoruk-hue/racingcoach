"""Build the screen after installing a source download from GitHub."""
from pathlib import Path
import shutil
import subprocess

from launch_app import build_is_current

ROOT = Path(__file__).resolve().parents[1]


def install() -> int:
    if not (ROOT / 'node_modules' / 'react-scripts').is_dir() and (ROOT / 'build' / 'index.html').is_file():
        print("Using the included screen. Node.js is not required.", flush=True)
        return 0
    if build_is_current():
        print("The screen build is already up to date.", flush=True)
        return 0
    npm = shutil.which("npm")
    if not shutil.which("node") or not npm:
        print("Node.js LTS is required to build the screen from GitHub source.\n"
              "Install it from https://nodejs.org/ and reopen the installer.\n"
              "Your data and downloaded voice files stay in place.", flush=True)
        return 1
    for args in ([npm, "ci"], [npm, "run", "build"]):
        print("Preparing the screen: npm " + " ".join(args[1:]), flush=True)
        try:
            result = subprocess.run(args, cwd=ROOT, check=False)
        except OSError:
            print("Unable to start npm. Reinstall Node.js LTS and try again.", flush=True)
            return 1
        if result.returncode:
            print("Screen setup failed. Check the messages above and rerun the installer.", flush=True)
            return 1
    if not build_is_current():
        print("The screen build is missing or out of date. Rerun the installer.", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(install())
