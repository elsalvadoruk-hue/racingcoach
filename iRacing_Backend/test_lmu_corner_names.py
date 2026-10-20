"""Manual LMU naming regression tests; all storage is temporary and AI-free."""
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from lmu_corner_names import LmuCornerNamesStore, corner_names_payload, official_map
from telemetry_core import ChannelTrace


METADATA = {"track_name": "Autodromo Nazionale Monza", "track_layout": "Autodromo Nazionale Monza"}
DEFINITIONS = [
    {"id": "C1", "entry_pct": .1, "apex_pct": .15, "exit_pct": .2},
    {"id": "C2", "entry_pct": .4, "apex_pct": .45, "exit_pct": .5},
]
ENTRIES = [
    {"id": "C1", "number": "1-2", "name": "첫 시케인", "spoken": "첫 시케인 진입"},
    {"id": "C2", "number": "4-5", "name": "두 번째 시케인", "spoken": ""},
]


def lap(metadata=None, channels=None):
    return SimpleNamespace(metadata=dict(METADATA if metadata is None else metadata), channels=channels or {})


def gps_lap():
    times = np.arange(301.)
    def trace(unit, values):
        return ChannelTrace("test", unit, 1., times, np.asarray(values), np.ones(len(times), dtype=bool))
    return lap(channels={"distance_m": trace("m", times * 20),
                         "latitude_deg": trace("deg", 60 + np.sin(times/100)*.01),
                         "longitude_deg": trace("deg", np.cos(times/100)*.01)})


class LmuCornerNamesStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "nested" / "lmu-names.json"
        self.store = LmuCornerNamesStore(self.path)

    def test_construction_reads_and_payload_do_not_create_store(self):
        self.assertIsNone(self.store.track_names(METADATA))
        self.assertEqual(self.store.names_for(DEFINITIONS, METADATA), {})
        payload = corner_names_payload(lap(), DEFINITIONS, self.store)
        self.assertFalse(payload["confirmed"])
        self.assertEqual(payload["status"], "NO_GPS")
        self.assertFalse(self.path.parent.exists())

    def test_roundtrip_keeps_unicode_names_and_server_positions_only(self):
        entries = [dict(entry, apex_pct=.99, filename="untrusted") for entry in ENTRIES]
        self.store.confirm(METADATA, DEFINITIONS, entries)
        fresh = LmuCornerNamesStore(self.path)
        names = fresh.names_for(DEFINITIONS, METADATA)
        self.assertEqual(names["C1"]["number"], "1-2")
        self.assertEqual(names["C1"]["name"], "첫 시케인")
        self.assertEqual(names["C1"]["apex_pct"], .15)
        stored = self.path.read_text(encoding="utf-8")
        self.assertNotIn("filename", stored)
        self.assertNotIn('"id"', stored)
        self.assertNotIn("0.99", stored)
        names["C1"]["name"] = "consumer mutation"
        self.assertEqual(fresh.names_for(DEFINITIONS, METADATA)["C1"]["name"], "첫 시케인")

    def test_reference_renumbering_and_small_boundary_shifts_preserve_names(self):
        self.store.confirm(METADATA, DEFINITIONS, ENTRIES)
        changed = [{"id": "C8", "entry_pct": .102, "apex_pct": .155, "exit_pct": .205},
                   {"id": "C9", "entry_pct": .405, "apex_pct": .46, "exit_pct": .51}]
        names = self.store.names_for(changed, dict(METADATA, car_name="different vehicle", filename="other"))
        self.assertEqual(names["C8"]["number"], "1-2")
        self.assertEqual(names["C9"]["number"], "4-5")
        self.assertNotIn("C1", names)

    def test_layout_keys_are_normalized_but_do_not_match_other_layouts(self):
        self.store.confirm(METADATA, DEFINITIONS, ENTRIES)
        equivalent = {"track_name": " AUTODROMO  NAZIONALE MONZA ", "track_layout": "autodromo nazionale monza"}
        self.assertEqual(len(self.store.names_for(DEFINITIONS, equivalent)), 2)
        for metadata in (dict(METADATA, track_layout="Short"), dict(METADATA, track_name="Another Monza")):
            self.assertEqual(self.store.names_for(DEFINITIONS, metadata), {})

    def test_blank_confirmation_explicitly_clears_saved_names(self):
        self.store.confirm(METADATA, DEFINITIONS, ENTRIES)
        self.store.confirm(METADATA, DEFINITIONS, [{"id": "C1", "number": "  "}, {"id": "C2"}])
        self.assertEqual(self.store.names_for(DEFINITIONS, METADATA), {})
        self.assertTrue(self.store.track_names(METADATA)["confirmed"])
        self.assertEqual(self.store.track_names(METADATA)["points"], [])

    def test_merged_or_unrelated_windows_do_not_silently_steal_names(self):
        self.store.confirm(METADATA, DEFINITIONS, ENTRIES)
        merged = [{"id": "C1", "entry_pct": .1, "apex_pct": .3, "exit_pct": .5}]
        self.assertEqual(self.store.names_for(merged, METADATA), {})
        payload = corner_names_payload(lap(), merged, self.store, include_suggestions=False)
        self.assertEqual(payload["unmapped_names"], 2)
        self.assertIsNone(payload["corners"][0]["call"])
        unrelated = [{"id": "C1", "entry_pct": .7, "apex_pct": .75, "exit_pct": .8}]
        self.assertEqual(self.store.names_for(unrelated, METADATA), {})

    def test_overlapping_windows_use_nearest_apex_but_exact_ambiguity_is_unnamed(self):
        self.store.confirm(METADATA, DEFINITIONS, ENTRIES)
        overlapping = [{"id": "C7", "entry_pct": .1, "apex_pct": .13, "exit_pct": .2},
                       {"id": "C8", "entry_pct": .1, "apex_pct": .18, "exit_pct": .2}]
        self.assertEqual(self.store.names_for(overlapping, METADATA)["C7"]["number"], "1-2")
        overlapping[1]["apex_pct"] = .17
        self.assertEqual(self.store.names_for(overlapping, METADATA), {})

    def test_invalid_inputs_reject_entire_edit_and_preserve_previous_file(self):
        self.store.confirm(METADATA, DEFINITIONS, ENTRIES)
        before = self.path.read_bytes()
        invalid_entries = [ENTRIES[:1], ENTRIES + [ENTRIES[0]],
                           [dict(ENTRIES[0], id="C9"), ENTRIES[1]], [ENTRIES[0], ENTRIES[0]],
                           [dict(ENTRIES[0], number="x"*13), ENTRIES[1]],
                           [dict(ENTRIES[0], name="x"*61), ENTRIES[1]],
                           [dict(ENTRIES[0], spoken="bad\ntext"), ENTRIES[1]],
                           [dict(ENTRIES[0], name=23), ENTRIES[1]]]
        for entries in invalid_entries:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                self.store.confirm(METADATA, DEFINITIONS, entries)
            self.assertEqual(self.path.read_bytes(), before)
        for definitions in ([dict(DEFINITIONS[0], apex_pct=float("nan")), DEFINITIONS[1]],
                            [dict(DEFINITIONS[0], entry_pct=True), DEFINITIONS[1]],
                            [dict(DEFINITIONS[0], apex_pct=.9), DEFINITIONS[1]],
                            [DEFINITIONS[0], dict(DEFINITIONS[1], id="C1")]):
            with self.subTest(definitions=definitions), self.assertRaises(ValueError):
                self.store.confirm(METADATA, definitions, ENTRIES)
            self.assertEqual(self.path.read_bytes(), before)

    def test_missing_identity_cannot_load_or_write(self):
        for metadata in ({}, {"track_name": "Monza"}, dict(METADATA, track_layout=" ")):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                self.store.confirm(metadata, DEFINITIONS, ENTRIES)
        self.assertFalse(self.path.exists())

    def test_distinct_instances_reload_before_writing_other_tracks(self):
        second = LmuCornerNamesStore(self.path)
        self.assertIsNone(second.track_names(METADATA))
        self.store.confirm(METADATA, DEFINITIONS, ENTRIES)
        metadata = dict(METADATA, track_layout="Another layout")
        second.confirm(metadata, DEFINITIONS, ENTRIES)
        self.assertIsNotNone(self.store.track_names(METADATA))
        self.assertIsNotNone(self.store.track_names(metadata))

    def test_corrupt_wrong_version_foreign_or_invalid_store_is_not_overwritten(self):
        self.path.parent.mkdir()
        examples = [b"not JSON", b'{"schema_version":2,"provider":"LMU","tracks":{}}',
                    b'{"schema_version":1,"provider":"IRACING","tracks":{}}',
                    b'{"schema_version":1,"schema_version":1,"provider":"LMU","tracks":{}}']
        self.store.confirm(METADATA, DEFINITIONS, ENTRIES)
        invalid = json.loads(self.path.read_text(encoding="utf-8"))
        next(iter(invalid["tracks"].values()))["points"][0]["apex_pct"] = float("inf")
        examples.append(json.dumps(invalid).encode())
        for content in examples:
            self.path.write_bytes(content)
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.store.confirm(METADATA, DEFINITIONS, ENTRIES)
            self.assertEqual(self.path.read_bytes(), content)

    def test_atomic_write_failure_preserves_old_names_and_removes_temporary_file(self):
        self.store.confirm(METADATA, DEFINITIONS, ENTRIES)
        before = self.path.read_bytes()
        with patch("lmu_corner_names.os.replace", side_effect=OSError("simulated replace failure")):
            with self.assertRaises(OSError):
                self.store.confirm(METADATA, DEFINITIONS, [{"id": "C1"}, {"id": "C2"}])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_default_path_can_be_isolated_with_lmu_only_environment(self):
        with patch.dict(os.environ, {"LMU_CORNER_NAMES_PATH": str(self.path)}):
            store = LmuCornerNamesStore()
            self.assertEqual(store.path, self.path)
            self.assertIsNone(store.track_names(METADATA))
            self.assertFalse(self.path.exists())

    def test_payload_keeps_confirmed_names_separate_from_suggestions(self):
        suggestion = {"status": "SUGGESTED", "kind": "numbered", "fit_median_m": 12.9,
                      "corners": {"C1": {"number": "1-2", "name": "Reference", "labels": ["1", "2"]}}}
        with patch("lmu_corner_names.corner_names.suggest", return_value=suggestion):
            payload = corner_names_payload(gps_lap(), DEFINITIONS, self.store)
            self.assertFalse(payload["confirmed"])
            self.assertIsNone(payload["corners"][0]["number"])
            self.assertIsNone(payload["corners"][0]["call"])
            self.assertEqual(payload["corners"][0]["suggested_number"], "1-2")
            self.assertFalse(self.path.exists())
            self.store.confirm(METADATA, DEFINITIONS, ENTRIES)
            payload = corner_names_payload(gps_lap(), DEFINITIONS, self.store)
        self.assertTrue(payload["confirmed"])
        self.assertEqual(payload["status"], "CONFIRMED")
        self.assertEqual(payload["corners"][0]["call"], "첫 시케인 진입")
        self.assertEqual(payload["corners"][1]["call"], "4번 코너")
        self.assertIsNone(payload["track_id"])
        self.assertFalse(payload["live_speech_supported"])
        self.assertEqual(payload["geometry_status"], "REFERENCE_GEOMETRY_UNVERIFIED")
        json.dumps(payload, ensure_ascii=False, allow_nan=False)

    def test_missing_coordinate_samples_are_preserved_for_coverage_rejection(self):
        source = gps_lap()
        lat = source.channels["latitude_deg"]
        mask = lat.valid_mask.copy()
        mask[::3] = False
        source.channels["latitude_deg"] = replace(lat, valid_mask=mask)
        with patch("lmu_corner_names.corner_names.suggest", return_value={"status": "NO_GPS", "corners": {}}) as suggest:
            payload = corner_names_payload(source, DEFINITIONS, self.store)
        self.assertEqual(payload["suggestion_status"], "NO_GPS")
        self.assertEqual(len(suggest.call_args.args[2]), 301)
        self.assertTrue(np.isnan(suggest.call_args.args[2][0]))

    def test_reference_maps_require_explicit_layout_match_and_existing_assets(self):
        diagram = official_map(METADATA)
        self.assertEqual(diagram["track_id"], 239)
        self.assertEqual(diagram["source"], "IRACING_REFERENCE_MAP")
        self.assertFalse(diagram["gps_registered"])
        self.assertTrue(diagram["user_confirmation_required"])
        root = Path(__file__).resolve().parent.parent / "public"
        for asset in diagram["assets"].values():
            self.assertTrue((root / asset.lstrip("/")).is_file())
        for metadata in (dict(METADATA, track_layout="Junior"), dict(METADATA, track_name="Monza")):
            self.assertIsNone(official_map(metadata))
            with patch("lmu_corner_names.corner_names.suggest") as suggest:
                payload = corner_names_payload(lap(metadata), DEFINITIONS, self.store)
            suggest.assert_not_called()
            self.assertEqual(payload["suggestion_status"], "NO_OFFICIAL_MAP")


if __name__ == "__main__":
    unittest.main()
