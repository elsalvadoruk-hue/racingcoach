"""IBT parsing with no dependency on routes, cloud clients or persistence."""
from __future__ import annotations
import json
import logging
import math
import re
import struct
from pathlib import Path
from typing import Any, Iterable, Optional
import numpy as np
import yaml
from analysis_numeric import file_signature, parse_number, regex_value, safe_int
from telemetry_types import VariableHeader, LapSegment, ParsedTelemetry
from telemetry_channels import IRSDK_DTYPES, ANALYSIS_CHANNELS
from analysis_support import session_context, suggested_session_mode
_YAML_LOADER = getattr(yaml, 'CSafeLoader', yaml.SafeLoader)
logger = logging.getLogger('iRacingSetupTuner')


class IBTFormatError(ValueError):
    pass


IBT_FILE_WAIT_MESSAGE = 'IBT 파일 읽기 대기 · iRacing이 기록 중이거나 다른 프로그램이 파일을 사용 중일 수 있습니다. 텔레메트리 기록을 종료하면 자동으로 다시 확인합니다. 계속 대기하면 파일의 읽기 권한을 확인하세요.'


class TelemetryFileBusy(PermissionError):
    def __init__(self, path):
        self.path = Path(path)
        super().__init__(IBT_FILE_WAIT_MESSAGE)


_BARE_QUESTION_MARK_VALUE = re.compile(
    r"(?m)^([ \t]*(?:-[ \t]+)?[A-Za-z0-9_][A-Za-z0-9_ ./'()\-]*:[ \t]*)(\?(?:[ \t].*)?)$"
)


def sanitize_iracing_session_yaml(text: str) -> tuple[str, int]:
    """Quote leading ``?`` placeholder scalar values emitted by iRacing.

    A line such as ``Initials: ?`` is not valid YAML because ``?`` is a
    mapping-key indicator. iRacing uses it as a placeholder value, so quoting
    only that scalar preserves the session data while allowing PyYAML to parse
    the full document, including CarSetup.
    """

    def quote_value(match: re.Match[str]) -> str:
        value = match.group(2)
        comment = re.search(r"[ \t]+#.*$", value)
        suffix = comment.group(0) if comment else ""
        if comment:
            value = value[:comment.start()]
        return match.group(1) + json.dumps(value.rstrip(), ensure_ascii=False) + suffix

    return _BARE_QUESTION_MARK_VALUE.subn(quote_value, text)


class IBTReader:
    """Reads every telemetry sample and only downsamples visualization payloads."""

    HEADER_SIZE = 112
    VAR_HEADER_SIZE = 144

    def __init__(self, path: Path):
        self.path = path
        self.file_size = path.stat().st_size

    def parse(self) -> ParsedTelemetry:
        with self.path.open("rb") as handle:
            header = handle.read(self.HEADER_SIZE)
            if len(header) < self.HEADER_SIZE:
                raise IBTFormatError("IBT header is truncated")

            (
                version,
                status,
                tick_rate,
                session_update,
                session_len,
                session_offset,
                num_vars,
                var_header_offset,
                num_buffers,
                buffer_len,
            ) = struct.unpack("<10i", header[:40])

            if version <= 0 or tick_rate <= 0 or buffer_len <= 0 or num_vars <= 0:
                raise IBTFormatError("IBT header contains invalid dimensions")

            data_offset = self._detect_data_offset(
                header=header,
                session_offset=session_offset,
                session_len=session_len,
                buffer_len=buffer_len,
            )

            handle.seek(session_offset)
            session_bytes = handle.read(session_len)
            session_text = session_bytes.decode("latin-1", errors="replace")
            parse_text, repaired_scalar_count = sanitize_iracing_session_yaml(session_text)
            try:
                session_info = yaml.load(parse_text, Loader=_YAML_LOADER) or {}
                if repaired_scalar_count:
                    logger.debug(
                        "Repaired %s bare '?' YAML value(s) in %s",
                        repaired_scalar_count,
                        self.path.name,
                    )
            except yaml.YAMLError as exc:
                logger.warning(
                    "Session YAML parse failed after safe scalar repair; using regex metadata: %s",
                    exc,
                )
                session_info = {}

            variables = self._read_variable_headers(handle, num_vars, var_header_offset)

        record_count = (self.file_size - data_offset) // buffer_len
        if record_count <= 0:
            raise IBTFormatError("IBT contains no telemetry records")

        channels = self._read_channels(
            variables=variables,
            data_offset=data_offset,
            buffer_len=buffer_len,
            record_count=record_count,
            requested=ANALYSIS_CHANNELS,
        )

        metadata = self._extract_metadata(session_info, session_text)
        setup = session_info.get("CarSetup", {}) if isinstance(session_info, dict) else {}
        laps = self._build_lap_segments(channels, tick_rate)
        signature = file_signature(self.path)

        return ParsedTelemetry(
            path=self.path,
            signature=signature,
            tick_rate=tick_rate,
            buffer_length=buffer_len,
            record_count=record_count,
            data_offset=data_offset,
            session_text=session_text,
            session_info=session_info,
            variables=variables,
            channels=channels,
            metadata=metadata,
            setup=setup if isinstance(setup, dict) else {},
            laps=laps,
        )

    def _detect_data_offset(
        self,
        header: bytes,
        session_offset: int,
        session_len: int,
        buffer_len: int,
    ) -> int:
        session_end = session_offset + session_len
        candidates = list(struct.unpack("<18i", header[40:112]))
        valid: list[tuple[int, int]] = []
        for candidate in candidates:
            if candidate < session_end or candidate >= self.file_size:
                continue
            remainder = (self.file_size - candidate) % buffer_len
            valid.append((0 if remainder == 0 else remainder, candidate))

        if not valid:
            # Most disk telemetry files align the data immediately after session info.
            aligned = session_end
            if aligned < self.file_size:
                return aligned
            raise IBTFormatError("Could not locate telemetry record buffer")

        valid.sort(key=lambda item: (item[0], abs(item[1] - session_end)))
        return valid[0][1]

    def _read_variable_headers(
        self,
        handle: Any,
        num_vars: int,
        offset: int,
    ) -> dict[str, VariableHeader]:
        variables: dict[str, VariableHeader] = {}
        handle.seek(offset)
        for _ in range(num_vars):
            raw = handle.read(self.VAR_HEADER_SIZE)
            if len(raw) < self.VAR_HEADER_SIZE:
                break
            var_type, var_offset, count, count_as_time = struct.unpack("<4i", raw[:16])
            name = raw[16:48].decode("latin-1", errors="ignore").rstrip("\x00").strip()
            description = raw[48:112].decode("latin-1", errors="ignore").rstrip("\x00").strip()
            unit = raw[112:144].decode("latin-1", errors="ignore").rstrip("\x00").strip()
            if name:
                variables[name] = VariableHeader(
                    var_type=var_type,
                    offset=var_offset,
                    count=count,
                    count_as_time=bool(count_as_time),
                    name=name,
                    description=description,
                    unit=unit,
                )
        return variables

    def _read_channels(
        self,
        variables: dict[str, VariableHeader],
        data_offset: int,
        buffer_len: int,
        record_count: int,
        requested: Iterable[str],
    ) -> dict[str, np.ndarray[Any, Any]]:
        mmap = np.memmap(self.path, mode="r", dtype=np.uint8)
        channels: dict[str, np.ndarray[Any, Any]] = {}
        for name in requested:
            header = variables.get(name)
            if not header or header.var_type not in IRSDK_DTYPES or header.count < 1:
                continue
            dtype = IRSDK_DTYPES[header.var_type]
            try:
                view = np.ndarray(
                    shape=(record_count,),
                    dtype=dtype,
                    buffer=mmap,
                    offset=data_offset + header.offset,
                    strides=(buffer_len,),
                )
                channels[name] = np.asarray(view).copy()
            except (ValueError, BufferError) as exc:
                logger.warning("Could not read channel %s: %s", name, exc)
        return channels

    def _extract_metadata(self, session_info: dict[str, Any], session_text: str) -> dict[str, Any]:
        weekend = session_info.get("WeekendInfo", {}) if isinstance(session_info, dict) else {}
        weekend_options = session_info.get("WeekendOptions", {}) if isinstance(session_info, dict) else {}
        driver_info = session_info.get("DriverInfo", {}) if isinstance(session_info, dict) else {}
        drivers = driver_info.get("Drivers", []) if isinstance(driver_info, dict) else []
        driver_idx = driver_info.get("DriverCarIdx", 0) if isinstance(driver_info, dict) else 0
        driver = next(
            (item for item in drivers if isinstance(item, dict) and item.get("CarIdx") == driver_idx),
            drivers[0] if drivers else {},
        )

        split_info = session_info.get("SplitTimeInfo", {}) if isinstance(session_info, dict) else {}
        sector_starts = []
        for sector in split_info.get("Sectors", []) if isinstance(split_info, dict) else []:
            if isinstance(sector, dict) and isinstance(sector.get("SectorStartPct"), (int, float)):
                sector_starts.append(float(sector["SectorStartPct"]))

        track_length_km = parse_number(weekend.get("TrackLength"))
        track_length_m = track_length_km * 1000.0 if track_length_km else None

        fixed_setup_raw = weekend_options.get("IsFixedSetup") if isinstance(weekend_options, dict) else None
        fixed_setup_value: Optional[bool]
        if fixed_setup_raw is None or str(fixed_setup_raw).strip() == "":
            fixed_setup_value = None
        else:
            fixed_setup_value = bool(safe_int(fixed_setup_raw) or 0)

        metadata = {
            "track_name": weekend.get("TrackDisplayName") or regex_value(session_text, r"TrackDisplayName:\s*([^\n]+)") or "Unknown Track",
            "track_config": weekend.get("TrackConfigName") or "",
            "track_path": weekend.get("TrackName") or "",
            "track_platform_id": str(weekend.get("TrackID") or "").strip() or None,
            "track_length_m": track_length_m,
            "track_turns": safe_int(weekend.get("TrackNumTurns")),
            "track_version": weekend.get("TrackVersion") or "",
            "car_name": driver.get("CarScreenName") or regex_value(session_text, r"CarScreenName:\s*([^\n]+)") or "Unknown Car",
            "car_path": driver.get("CarPath") or regex_value(session_text, r"CarPath:\s*([^\n]+)") or "",
            "car_platform_id": str(driver.get("CarID") or "").strip() or None,
            "weight_penalty_kg": parse_number(driver.get("CarClassWeightPenalty")),
            "power_adjust_pct": parse_number(driver.get("CarClassPowerAdjust")),
            "fuel_max_l": parse_number(driver_info.get("DriverCarFuelMaxLtr")),
            "setup_name": driver_info.get("DriverSetupName") or "Embedded setup",
            "setup_modified": bool(driver_info.get("DriverSetupIsModified", 0)),
            "setup_passed_tech": bool(driver_info.get("DriverSetupPassedTech", 0)),
            "estimated_lap_time_s": parse_number(driver_info.get("DriverCarEstLapTime")),
            "sector_starts": sorted(set(sector_starts)),
            "event_type": weekend.get("EventType") or "",
            "is_fixed_setup": fixed_setup_value,
        }
        # The session this file was driven in decides the analysis mode it starts with.
        metadata.update(session_context(session_info))
        metadata["suggested_session_mode"] = suggested_session_mode(
            metadata["session_type"], metadata["session_duration_s"], metadata["session_laps_limit"], metadata["estimated_lap_time_s"])
        return metadata

    def _build_lap_segments(
        self,
        channels: dict[str, np.ndarray[Any, Any]],
        tick_rate: int,
    ) -> list[LapSegment]:
        required = {"Lap", "LapDistPct", "SessionTime"}
        if not required.issubset(channels):
            return []

        lap_values = channels["Lap"].astype(np.int64, copy=False)
        starts = np.r_[0, np.flatnonzero(lap_values[1:] != lap_values[:-1]) + 1]
        ends = np.r_[starts[1:] - 1, len(lap_values) - 1]
        segments: list[LapSegment] = []

        for position, (start, end) in enumerate(zip(starts, ends)):
            lap_number = int(lap_values[start])
            if lap_number <= 0:
                continue

            lap_dist = channels["LapDistPct"][start : end + 1].astype(float, copy=False)
            start_pct = float(lap_dist[0]) if len(lap_dist) else 0.0
            end_pct = float(lap_dist[-1]) if len(lap_dist) else 0.0
            complete = start_pct <= 0.02 and end_pct >= 0.98

            pit_samples = int(np.count_nonzero(channels.get("OnPitRoad", np.zeros(len(lap_values), dtype=bool))[start : end + 1]))
            surface = channels.get("PlayerTrackSurface")
            if surface is not None:
                lap_surface = surface[start : end + 1]
                on_track_ratio = float(np.mean(lap_surface == 3))
                # Approaching the pits (2) or in the stall (1): a pit lap, not an off.
                pit_samples += int(np.count_nonzero((lap_surface == 1) | (lap_surface == 2)))
            else:
                on_track_ratio = 1.0
            # iRacing's own verdict on going off: an incident of 1x or more (the second byte of
            # PlayerIncidents is the penalty: 1 = 0x, 2 = 1x ...). Kerbs and run-off without one
            # count, as in the sim (2026-10-21, the driver's rule: a lap is off only with incidents).
            incidents = channels.get("PlayerIncidents")
            penalised = (bool(np.any((np.asarray(incidents[start : end + 1], dtype=np.int64) >> 8 & 0xFF) >= 2))
                         if incidents is not None else None)

            official_time = self._completed_lap_time(
                channels=channels,
                current_end=int(end),
                next_start=int(starts[position + 1]) if position + 1 < len(starts) else None,
                tick_rate=tick_rate,
            )

            fuel_used = None
            fuel = channels.get("FuelLevel")
            if fuel is not None and end > start:
                value = float(fuel[start] - fuel[end])
                if math.isfinite(value) and value >= -0.1:
                    fuel_used = max(0.0, value)

            invalid_reason = None
            if not complete:
                status = "OUT_LAP" if pit_samples else "PARTIAL"
            elif pit_samples:
                status = "INVALID"
                invalid_reason = "pit road samples present"
            elif penalised:
                status = "INVALID"
                invalid_reason = "incident during the lap"
            elif penalised is None and on_track_ratio < 0.995:
                # Recordings without the incident log keep the surface rule.
                status = "INVALID"
                invalid_reason = "off-track samples present"
            elif official_time is None or official_time <= 0:
                status = "INVALID"
                invalid_reason = "iRacing did not publish a valid completed lap time"
            else:
                status = "VALID"

            segments.append(
                LapSegment(
                    lap_number=lap_number,
                    start_idx=int(start),
                    end_idx=int(end),
                    complete=complete,
                    valid=status == "VALID",
                    status=status,
                    lap_time_s=official_time,
                    start_pct=start_pct,
                    end_pct=end_pct,
                    on_track_ratio=on_track_ratio,
                    pit_samples=pit_samples,
                    fuel_used_l=fuel_used,
                    invalid_reason=invalid_reason,
                )
            )
        return segments

    def _completed_lap_time(
        self,
        channels: dict[str, np.ndarray[Any, Any]],
        current_end: int,
        next_start: Optional[int],
        tick_rate: int,
    ) -> Optional[float]:
        values = channels.get("LapLastLapTime")
        if values is None or next_start is None:
            return None

        before = float(values[current_end])
        completed = channels.get("LapCompleted")
        completed_before = float(completed[current_end]) if completed is not None else None
        search_end = min(len(values), next_start + max(tick_rate * 2, 30))
        window = values[next_start:search_end].astype(float, copy=False)
        for offset, candidate in enumerate(window):
            if not math.isfinite(float(candidate)) or candidate <= 0:
                continue
            if before <= 0 or abs(float(candidate) - before) > 0.001:
                return float(candidate)
            # Equal consecutive lap times are valid when the completion counter
            # advances. Require current-lap elapsed time to agree so an invalid
            # lap cannot reuse a stale published time.
            if completed is not None and math.isfinite(completed_before):
                after = float(completed[next_start + offset])
                elapsed = channels.get("LapCurrentLapTime")
                measured = float(elapsed[current_end]) if elapsed is not None else math.nan
                tolerance = max(0.02, 2.0 / max(1, tick_rate))
                if after > completed_before and math.isfinite(measured) and abs(measured - float(candidate)) <= tolerance:
                    return float(candidate)
        return None
