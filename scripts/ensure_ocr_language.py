"""Installer step: offer English Windows OCR for the tire overlay. Never fails the install."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'iRacing_Backend'), str(ROOT)]

if __name__ == '__main__':
    try:
        from tire_reader.ocr_language import ensure_interactive
        ensure_interactive()
    except Exception as error:
        print(f'Windows OCR check skipped ({type(error).__name__}).')
    raise SystemExit(0)
