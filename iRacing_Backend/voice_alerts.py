"""Shared UI/event catalog. Preferences change delivery, never telemetry collection."""
from functools import lru_cache
from fnmatch import fnmatchcase
import json
from pathlib import Path


CATALOG = json.loads((Path(__file__).resolve().parents[1] / 'src/lib/voiceAlerts.json').read_text(encoding='utf-8'))
IDS = {scope: frozenset(item['id'] for group in groups for item in group['items']) for scope, groups in CATALOG.items()}
PATTERNS = tuple((pattern, f"{scope}.{item['id']}") for scope, groups in CATALOG.items()
                 for group in groups for item in group['items'] for pattern in item['keys'])


@lru_cache(maxsize=512)
def category(key):
    return next((name for pattern, name in PATTERNS if fnmatchcase(key, pattern)), None)


def clean(scope, values):
    return {key: value for key, value in (values or {}).items() if key in IDS[scope] and type(value) is bool}


def allowed(name, spotter=None, pitwall=None):
    if not name:  # Old clients/events and explicitly requested drill voices retain their behavior.
        return True
    scope, key = name.split('.', 1)
    selected = (spotter if scope == 'spotter' else pitwall) or {}
    if scope == 'spotter' and key in ('repeat', 'clear') and selected.get('overlap') is False:
        return False
    return selected.get(key) is not False


def event_allowed(event, spotter=None, pitwall=None):
    names = event.get('alert_categories') or [category(event.get('key', ''))]
    return all(allowed(name, spotter, pitwall) for name in names)


def reports_enabled(reports, selections):
    result = []
    for report in reports:
        names = report.get('part_categories')
        if names and report.get('speech_parts'):
            keep = [i for i, name in enumerate(names) if allowed(name, pitwall=selections)]
            if not keep:
                continue
            if len(keep) != len(names):
                parts = [report['speech_parts'][i] for i in keep]
                text = ' '.join(parts)
                report = {**report, 'speech_parts': parts, 'speech_text': text, 'spoken_text': text,
                          'text': text, 'speech_specs': [report['speech_specs'][i] for i in keep]}
            report = {**report, 'alert_categories': list(dict.fromkeys(names[i] for i in keep))}
        elif not allowed(category(report['key']), pitwall=selections):
            continue
        result.append(report)
    return result
