import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from lmu_setup import MAX_SETUP_BYTES, compare_setups, parse_setup, setup_report
from lmu_telemetry import LmuTelemetryFormatError, LmuTelemetryReader
from test_lmu_telemetry import duckdb, make_lmu_file


def setting(index=2, display="5.2 cm", **overrides):
    return {"available": True, "value": index, "stringValue": display,
            "minValue": 0, "maxValue": 10, **overrides}


def report(entries):
    return parse_setup(json.dumps(entries)) | {
        "context": {"car_name": "Test GT3", "car_class": "GT3", "track_name": "Test Track",
                    "track_layout": "GP"}, "scope": "RECORDING_METADATA"}


class SetupParserTests(unittest.TestCase):
    def test_native_vm_and_named_wheel_entries_preserve_display_not_physical_values(self):
        data = {"VM_BRAKE_BALANCE": setting(3, "51.2:48.8"),
                "WM_PRESSURE-W_FL": setting(0, "136 kPa"),
                "WM_RIDEHEIGHT-W_RR": setting(8, "5.8 cm"),
                "VM_BRAKE_PRESSURE": setting(68, "108 kgf (90%)", maxValue=81),
                "VM_FUEL_LEVEL": setting(88, "0.89", maxValue=120),
                "gearGraph": {"unit": "kph", "topSpeed": [999]}, "symmetric": True}
        result = parse_setup(json.dumps(data))
        self.assertEqual(result["status"], "AVAILABLE")
        rows = {x["key"]: x for x in result["items"]}
        self.assertEqual(rows["WM_PRESSURE-W_FL"]["value"], 0)
        self.assertEqual(rows["WM_PRESSURE-W_FL"]["stringValue"], "136 kPa")
        self.assertEqual(rows["VM_FUEL_LEVEL"]["stringValue"], "0.89")
        self.assertEqual(rows["VM_BRAKE_BALANCE"]["value"], 3)
        self.assertEqual(result["counts"], {"AVAILABLE": 5, "UNAVAILABLE": 0})
        self.assertNotIn("gearGraph", rows)

    def test_na_is_unavailable_even_when_exporter_available_is_true(self):
        for available in (True, False):
            result = parse_setup(json.dumps({"VM_ENGINE_BOOST": setting(0, "N/A", available=available)}))
            self.assertEqual(result["counts"]["UNAVAILABLE"], 1)
            self.assertEqual(result["items"][0]["status"], "UNAVAILABLE")
            self.assertIsNone(result["items"][0]["stringValue"])

    def test_only_known_setting_keys_and_current_supported_fields_are_returned(self):
        result = parse_setup(json.dumps({
            "VM_REAR_WING": setting(2, "2.0 deg", caption="PRIVATE_CAPTION",
                                    lastSavedStringValue="PRIVATE_OLD", notes="PRIVATE_NOTES"),
            "VM_PRIVATE_NOTES": setting(1, "PRIVATE_ARBITRARY"),
            "SteamID": "PRIVATE_STEAM_ID", "SetupPath": "PRIVATE_PATH"}))
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(len(result["items"]), 1)
        serialized = json.dumps(result)
        for secret in ("PRIVATE_CAPTION", "PRIVATE_OLD", "PRIVATE_NOTES", "PRIVATE_ARBITRARY",
                       "PRIVATE_STEAM_ID", "PRIVATE_PATH"):
            self.assertNotIn(secret, serialized)

    def test_invalid_display_values_are_redacted_without_losing_other_fields(self):
        for text in ("<img src=x>", "C:\\private\\secret.svm", "/home/private", "user@private.test",
                     "https://private.test", "private\nnotes", "x"*121):
            with self.subTest(text=text):
                result = parse_setup(json.dumps({"VM_REAR_WING": setting(display=text)}))
                self.assertEqual(result["status"], "PARTIAL")
                self.assertIsNone(result["items"][0]["stringValue"])
                self.assertEqual(result["items"][0]["status"], "UNAVAILABLE")
        self.assertEqual(parse_setup(json.dumps({"WM_SPRING-W_FL": setting(display="120 N/mm")}))
                         ["items"][0]["stringValue"], "120 N/mm")

    def test_invalid_json_size_duplicate_keys_and_schema_are_bounded(self):
        for raw, reason in ((None, "SETUP_MISSING"), ("", "SETUP_MISSING"),
                            ("PRIVATE_SETUP", "SETUP_INVALID_JSON"),
                            ('{"VM_REAR_WING":{},"VM_REAR_WING":{}}', "SETUP_INVALID_JSON"),
                            ('{"VM_REAR_WING":{"value":NaN}}', "SETUP_INVALID_JSON"),
                            ("[]", "SETUP_UNSUPPORTED_SCHEMA"),
                            (json.dumps({str(n): None for n in range(513)}), "SETUP_UNSUPPORTED_SCHEMA"),
                            (" " * (MAX_SETUP_BYTES+1), "SETUP_TOO_LARGE_OR_UNSUPPORTED")):
            with self.subTest(reason=reason):
                result = parse_setup(raw)
                self.assertEqual(result["status"], "UNAVAILABLE")
                self.assertEqual(result["reason"], reason)
                self.assertEqual(result["items"], [])

    def test_nonfinite_bool_missing_out_of_range_and_malformed_setting_are_unknown(self):
        for change in ({"value": True}, {"value": float("inf")}, {"value": 10**400},
                       {"value": -1}, {"value": 11}, {"minValue": None},
                       {"maxValue": -1}, {"available": "true"}):
            with self.subTest(change=change):
                result = parse_setup(json.dumps({"VM_REAR_WING": setting(**change)}))
                self.assertIn(result["status"], ("PARTIAL", "UNAVAILABLE"))
                if result["items"]:
                    self.assertEqual(result["items"][0]["status"], "UNAVAILABLE")
                json.dumps(result, allow_nan=False)
        malformed = parse_setup('{"VM_REAR_WING":false}')
        self.assertEqual(malformed["items"][0]["reason"], "INVALID_SETTING")


class SetupComparisonTests(unittest.TestCase):
    def compare(self, first, second):
        return compare_setups(report(first), report(second))

    def test_same_display_and_different_compound_index_is_unknown_not_changed(self):
        first = {f"WM_COMPOUND-W_{wheel}": setting(0, "Medium") for wheel in ("FL", "FR", "RL", "RR")}
        second = {key: setting(1, "Medium") for key in first}
        result = self.compare(first, second)
        self.assertEqual(result["counts"], {"CHANGED": 0, "UNCHANGED": 0, "UNKNOWN": 4})
        self.assertTrue(all(row["reason"] == "VALUE_CHANGED_WITH_SAME_DISPLAY" for row in result["rows"]))
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["effect"], "NOT_EVALUATED")

    def test_display_and_index_changes_are_reported_in_both_directions(self):
        first = {"VM_REAR_WING": setting(2, "1.4 deg"), "VM_BRAKE_BALANCE": setting(3, "51.2:48.8")}
        second = {"VM_REAR_WING": setting(3, "1.6 deg"), "VM_BRAKE_BALANCE": setting(3, "51.2:48.8")}
        for a, b in ((first, second), (second, first)):
            result = self.compare(a, b)
            self.assertEqual(result["counts"], {"CHANGED": 1, "UNCHANGED": 1, "UNKNOWN": 0})
            self.assertEqual(result["status"], "AVAILABLE")
            self.assertEqual(result["compatibility"]["vehicle_model"], "UNVERIFIED")
            self.assertEqual(result["compatibility"]["conditions"], "NOT_COMPARED")

    def test_missing_unavailable_range_and_display_only_changes_remain_unknown(self):
        first = {"VM_REAR_WING": setting(), "VM_FRONT_WING": setting(),
                 "VM_BRAKE_BALANCE": setting(), "VM_ENGINE_BOOST": setting(display="N/A")}
        second = {"VM_REAR_WING": setting(display="5.3 cm"),
                  "VM_FRONT_WING": setting(maxValue=11), "VM_ENGINE_BOOST": setting(display="N/A")}
        result = self.compare(first, second)
        self.assertEqual(result["counts"], {"CHANGED": 0, "UNCHANGED": 0, "UNKNOWN": 4})
        self.assertEqual({row["reason"] for row in result["rows"]},
                         {"DISPLAY_TEXT_CHANGED", "SETTING_RANGE_CHANGED", "ITEM_MISSING_OR_UNAVAILABLE"})

    def test_metadata_mismatch_rejects_comparison_and_missing_context_stays_unverified(self):
        a = report({"VM_REAR_WING": setting()})
        b = report({"VM_REAR_WING": setting()})
        b["context"]["car_name"] = "Different Car"
        result = compare_setups(a, b)
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertEqual(result["reason"], "METADATA_MISMATCH")
        self.assertEqual(result["rows"], [])
        b["context"]["car_name"] = None
        result = compare_setups(a, b)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["compatibility"]["status"], "UNKNOWN")
        self.assertIn("car_name", result["compatibility"]["missing_fields"])

    def test_missing_whole_setup_never_establishes_equality(self):
        result = compare_setups(report({}), report({}))
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertEqual(result["reason"], "SETUP_UNAVAILABLE")
        self.assertEqual(result["counts"]["UNCHANGED"], 0)


@unittest.skipUnless(duckdb, "LMU 전용 의존성이 없는 환경입니다.")
class SetupRecordingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "setup.duckdb"
        make_lmu_file(self.path)

    def sql(self, query, parameters=None):
        with duckdb.connect(str(self.path)) as con:
            con.execute(query, parameters or [])

    def test_recording_scope_privacy_context_and_read_only_preservation(self):
        self.sql("UPDATE metadata SET value=? WHERE key='CarSetup'", [json.dumps({
            "WM_CAMBER-W_FL": setting(2, "-2.20 deg", lastSavedStringValue="PRIVATE_OLD"),
            "VM_PRIVATE": setting(display="PRIVATE_CONTENT")})])
        self.sql("INSERT INTO metadata VALUES ('CarClass','GT3')")
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        result = setup_report(LmuTelemetryReader(self.path))
        self.assertEqual(result["context"]["car_class"], "GT3")
        self.assertEqual(result["scope"], "RECORDING_METADATA")
        self.assertEqual(result["units"], "EXPORTER_DISPLAY_UNVERIFIED")
        self.assertEqual(result["reference_status"], "VALIDITY_UNVERIFIED")
        serialized = json.dumps(result)
        for secret in ("PRIVATE_OLD", "PRIVATE_CONTENT", "PRIVATE_TEST_ID", "DriverName", str(self.path)):
            self.assertNotIn(secret, serialized)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        self.assertFalse(Path(str(self.path)+".wal").exists())

    def test_duplicate_metadata_and_oversized_setup_are_not_silently_accepted(self):
        self.sql("UPDATE metadata SET value=? WHERE key='CarSetup'", [" "*(MAX_SETUP_BYTES+1)])
        result = setup_report(LmuTelemetryReader(self.path))
        self.assertEqual(result["reason"], "SETUP_TOO_LARGE_OR_UNSUPPORTED")
        self.sql("INSERT INTO metadata VALUES ('CarSetup','{}')")
        with self.assertRaises(LmuTelemetryFormatError):
            setup_report(LmuTelemetryReader(self.path))

    def test_duplicate_context_is_missing_instead_of_selecting_arbitrary_value(self):
        self.sql("INSERT INTO metadata VALUES ('CarName','Other Car')")
        result = setup_report(LmuTelemetryReader(self.path))
        self.assertIsNone(result["context"]["car_name"])


if __name__ == "__main__":
    unittest.main()
