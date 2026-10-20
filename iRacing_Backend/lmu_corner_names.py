"""Explicit LMU corner names, anchored to a track/layout rather than a lap ID.

Reads never create files. Only ``confirm`` writes, and neither this module nor
its reference-map suggestions access iRacing's personal stores or live SDK.
Definitions must come from the same selected reference geometry used by the UI.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile
import threading

import numpy as np

import corner_names


DEFAULT_PATH = Path(__file__).resolve().parent.parent / "logs" / "lmu-corner-names.json"
SCHEMA_VERSION = 1
MAX_CORNERS = 128
MAX_STORE_BYTES = 2 * 1024 * 1024
_MARGIN = .004
_FIELDS = {"number": 12, "name": 60, "spoken": 60}
_LOCK = threading.RLock()


def _text(value, limit, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("코너 이름과 트랙 정보는 제어 문자가 없는 문자열이어야 합니다.")
    value = value.strip()
    if len(value) > limit or (required and not value):
        raise ValueError("코너 이름 또는 트랙 정보의 길이가 올바르지 않습니다.")
    return value or None


def _identity(metadata):
    if not isinstance(metadata, dict):
        raise ValueError("LMU 트랙과 레이아웃 정보가 필요합니다.")
    track = _text(metadata.get("track_name"), 240, required=True)
    layout = _text(metadata.get("track_layout"), 240, required=True)
    key = json.dumps([" ".join(value.split()).casefold() for value in (track, layout)],
                     ensure_ascii=False, separators=(",", ":"))
    return key, track, layout


def _position(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("코너 위치는 0~1 범위의 유한한 수여야 합니다.")
    return float(value)


def _definitions(definitions):
    if not isinstance(definitions, (list, tuple)) or len(definitions) > MAX_CORNERS:
        raise ValueError("LMU 자동 구간 목록이 올바르지 않습니다.")
    result, identifiers = [], set()
    for definition in definitions:
        if not isinstance(definition, dict):
            raise ValueError("LMU 자동 구간 정보가 올바르지 않습니다.")
        identifier = _text(definition.get("id"), 64, required=True)
        if identifier in identifiers:
            raise ValueError("LMU 자동 구간 ID가 중복됩니다.")
        identifiers.add(identifier)
        entry, apex, exit_ = (_position(definition.get(key)) for key in ("entry_pct", "apex_pct", "exit_pct"))
        if not entry <= apex <= exit_ or entry == exit_:
            raise ValueError("LMU 자동 구간의 진입·정점·탈출 순서가 올바르지 않습니다.")
        result.append({"id": identifier, "entry_pct": entry, "apex_pct": apex, "exit_pct": exit_})
    return result


def _named_fields(entry):
    return {key: _text(entry.get(key), limit) for key, limit in _FIELDS.items()}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("LMU 코너 이름 저장소에 중복 필드가 있습니다.")
        result[key] = value
    return result


class LmuCornerNamesStore:
    def __init__(self, path=None):
        self.path = Path(path if path is not None else os.getenv("LMU_CORNER_NAMES_PATH", str(DEFAULT_PATH)))

    def _read(self):
        try:
            with self.path.open("rb") as source:
                raw = source.read(MAX_STORE_BYTES + 1)
        except FileNotFoundError:
            return {"schema_version": SCHEMA_VERSION, "provider": "LMU", "tracks": {}}
        if len(raw) > MAX_STORE_BYTES:
            raise ValueError("LMU 코너 이름 저장소가 크기 제한을 초과했습니다.")
        try:
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
            if (not isinstance(data, dict) or type(data.get("schema_version")) is not int or
                    data["schema_version"] != SCHEMA_VERSION or data.get("provider") != "LMU" or
                    not isinstance(data.get("tracks"), dict)):
                raise ValueError("지원하지 않는 LMU 코너 이름 저장소입니다.")
            for key, track in data["tracks"].items():
                if (not isinstance(track, dict) or _identity(track)[0] != key or
                        track.get("confirmed") is not True or not isinstance(track.get("points"), list) or
                        len(track["points"]) > MAX_CORNERS):
                    raise ValueError("LMU 코너 이름 저장소의 트랙 정보가 올바르지 않습니다.")
                anchors = set()
                for point in track["points"]:
                    if not isinstance(point, dict):
                        raise ValueError("LMU 코너 이름 저장소의 위치 정보가 올바르지 않습니다.")
                    apex = _position(point.get("apex_pct"))
                    if apex in anchors or not any(_named_fields(point).values()):
                        raise ValueError("LMU 코너 이름 저장소의 이름 또는 위치가 올바르지 않습니다.")
                    anchors.add(apex)
            return data
        except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
            raise ValueError("LMU 코너 이름 저장소를 읽을 수 없습니다. 기존 파일은 변경하지 않았습니다.") from exc

    def track_names(self, metadata):
        """Return a detached confirmed record, or None; never create storage."""
        key, _, _ = _identity(metadata)
        with _LOCK:
            return self._read()["tracks"].get(key)

    def names_for(self, definitions, metadata):
        """Map saved positions into current windows; ambiguous merges stay unnamed.

        C1/C2 identifiers are deliberately not stored. A changed reference can
        renumber windows without moving a driver's confirmed turn name.
        """
        definitions = _definitions(definitions)
        record = self.track_names(metadata)
        return _map_names(definitions, record)

    def confirm(self, metadata, definitions, entries):
        """Replace this layout's names with a complete, explicitly confirmed edit.

        Positions come exclusively from server definitions. Client positions,
        vehicle names, filenames, and corner IDs are never persisted as anchors.
        """
        key, track, layout = _identity(metadata)
        definitions = _definitions(definitions)
        if not definitions or not isinstance(entries, list) or len(entries) != len(definitions):
            raise ValueError("현재 자동 구간 전체의 코너 이름을 보내세요.")
        known, checked = {definition["id"]: definition for definition in definitions}, {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("코너 이름 입력이 올바르지 않습니다.")
            identifier = _text(entry.get("id"), 64, required=True)
            if identifier not in known or identifier in checked:
                raise ValueError("코너 이름의 구간 ID가 현재 분석과 다르거나 중복됩니다.")
            checked[identifier] = _named_fields(entry)
        points = [{"apex_pct": definition["apex_pct"], **checked[definition["id"]]}
                  for definition in definitions if any(checked[definition["id"]].values())]
        if len({point["apex_pct"] for point in points}) != len(points):
            raise ValueError("이름을 저장할 자동 구간의 정점 위치가 중복됩니다.")
        record = {"track_name": track, "track_layout": layout, "confirmed": True, "points": points}
        with _LOCK:
            data = self._read()  # Reload before writes; never overwrite malformed/foreign stores.
            data["tracks"][key] = record
            encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > MAX_STORE_BYTES:
                raise ValueError("LMU 코너 이름 저장소가 크기 제한을 초과했습니다.")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=self.path.name+".", suffix=".tmp", delete=False) as target:
                    temporary = Path(target.name)
                    target.write(encoded)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return record


def _map_names(definitions, record):
    candidates = {}
    for point in (record or {}).get("points", []):
        inside = [definition for definition in definitions
                  if definition["entry_pct"] - _MARGIN <= point["apex_pct"] <= definition["exit_pct"] + _MARGIN]
        if not inside:
            continue
        ordered = sorted(inside, key=lambda definition: abs(point["apex_pct"] - definition["apex_pct"]))
        if (len(ordered) > 1 and math.isclose(abs(point["apex_pct"] - ordered[0]["apex_pct"]),
                                             abs(point["apex_pct"] - ordered[1]["apex_pct"]), abs_tol=1e-9)):
            continue
        candidates.setdefault(ordered[0]["id"], []).append(point)
    return {identifier: dict(points[0]) for identifier, points in candidates.items() if len(points) == 1}


def official_map(metadata):
    """An allowlisted reference diagram, never an LMU/iRacing platform identity."""
    key, _, _ = _identity(metadata)
    monza = "autodromo nazionale monza"
    if key != json.dumps([monza, monza], separators=(",", ":")):
        return None
    return {"track_id": 239, "source": "IRACING_REFERENCE_MAP", "name": "Autodromo Nazionale Monza",
            "layout": "Grand Prix", "gps_registered": False, "user_confirmation_required": True,
            "source_url": "https://members-assets.iracing.com/public/track-maps/tracks_monza/239-monza-full",
            "assets": {name: f"/track-maps/official/239/{name}.svg"
                       for name in ("active", "pitroad", "start-finish", "turns")}}


def _suggest(lap, definitions, reference_map):
    if reference_map is None:
        return {"status": "NO_OFFICIAL_MAP", "corners": {}}
    if not definitions:
        return {"status": "NO_CORNERS", "corners": {}}
    lat, lon, distance = (lap.channels.get(key) for key in ("latitude_deg", "longitude_deg", "distance_m"))
    if (lat is None or lon is None or distance is None or lat.unit != "deg" or lon.unit != "deg" or
            distance.unit != "m" or not len(distance.values) or not np.all(distance.valid_mask) or
            distance.values[-1] <= 0 or np.any(np.diff(distance.values) <= 0)):
        return {"status": "NO_GPS", "corners": {}}
    # at() respects invalid samples and gaps. Do not filter them before suggest,
    # which rejects sparse coverage rather than joining unsupported geometry.
    positions = distance.at(lat.time_s) / distance.values[-1]
    return corner_names.suggest(definitions, positions, lat.at(lat.time_s), lon.at(lat.time_s), reference_map["track_id"])


def corner_names_payload(lap, definitions, store=None, *, include_suggestions=True):
    """Return the shared corner-name page contract without persisting a GET.

    ``lap`` is the selected normalized reference AnalysisLap. ``definitions``
    use shared C1..Cn IDs and entry_pct/apex_pct/exit_pct in native distance / 
    track length, exactly as displayed by the current workspace comparison.
    """
    definitions = _definitions(definitions)
    store = store if store is not None else LmuCornerNamesStore()
    _, track, layout = _identity(lap.metadata)
    saved = store.track_names(lap.metadata)
    mapped = _map_names(definitions, saved)
    reference_map = official_map(lap.metadata)
    suggestion = (_suggest(lap, definitions, reference_map) if include_suggestions else
                  {"status": "NOT_REQUESTED", "corners": {}})
    rows = []
    for order, definition in enumerate(definitions, 1):
        kept, proposed = mapped.get(definition["id"], {}), suggestion["corners"].get(definition["id"], {})
        fields = {field: kept.get(field) for field in _FIELDS}
        rows.append({**definition, "order": order, **fields, "call": corner_names.spoken_call(**fields),
                     "suggested_number": proposed.get("number"), "suggested_name": proposed.get("name"),
                     "labels": proposed.get("labels", [])})
    return {"provider": "LMU", "track_id": None, "track_name": track, "track_config": layout,
            "track_layout": layout, "official_map": reference_map, "confirmed": saved is not None,
            "status": "CONFIRMED" if saved is not None else suggestion["status"],
            "suggestion_status": suggestion["status"], "kind": suggestion.get("kind"),
            "fit_median_m": suggestion.get("fit_median_m"), "corners": rows,
            "mapped_names": len(mapped), "unmapped_names": len((saved or {}).get("points", [])) - len(mapped),
            "geometry_status": "REFERENCE_GEOMETRY_UNVERIFIED", "live_speech_supported": False}
