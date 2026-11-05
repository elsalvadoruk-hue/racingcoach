"""App-owned additional native overlay. Controlled only through private pipes."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'iRacing_Backend'), str(ROOT)]

if __name__ == '__main__':
    from tire_reader.managed_overlay import main
    main()
