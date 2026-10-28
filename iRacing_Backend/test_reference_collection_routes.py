"""Garage61 HTTP collection policy, with synthetic stores and no worker/network."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from reference_routes import ReferenceRoutesServices, register_routes


class CollectionRouteTests(unittest.TestCase):
    def setUp(self):
        self.health = {"enabled": True, "enough_data": False, "cache_fresh": False,
                       "last_context_scan_age_hours": None,
                       "context": {"context_key": "car-track", "filename": "drive.ibt"}}
        self.manager = Mock()
        self.manager.start.return_value = {"state": "QUEUED", "busy": True, "context_key": "car-track"}
        self.manager.active_job.return_value = None
        self.manager.cancel.return_value = {"state": "CANCELLED", "busy": False}
        self.repository = Mock()
        self.repository.load.return_value = SimpleNamespace(metadata={})
        self.resolve = Mock(return_value=Path("drive.ibt"))
        self.enabled = True
        self.status = Mock(side_effect=lambda *args: {"health": self.health, "job": self.manager.active_job()})
        services = ReferenceRoutesServices(
            get_garage61_action_timeout_ms=lambda: 60000,
            get_garage61_auto_collect_enabled=lambda: self.enabled,
            get_garage61_collector_cooldown_min=lambda: 15,
            get_garage61_collector_max_wall_seconds=lambda: 600,
            get_garage61_navigation_timeout_ms=lambda: 180000,
            get_garage61_retry_count=lambda: 3,
            get_garage61_settle_ms=lambda: 2200,
            get_garage61_target_references=lambda: 8,
            get_parsed_telemetry=lambda: object,
            garage61_api_connection_status=lambda: {},
            get_garage61_collector=lambda: self.manager,
            garage61_collector_status_payload=self.status,
            garage61_reference_health=lambda *args: self.health,
            list_external_reference_metadata=lambda *args, **kwargs: {"files": []},
            get_repository=lambda: self.repository,
            resolve_telemetry_file=self.resolve,
        )
        app = FastAPI()
        register_routes(app, services)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def start(self, **extra):
        return self.client.post("/api/garage61/collector/start", json={"filename": "drive.ibt", **extra})

    def test_fresh_sufficient_cache_skips_without_starting_a_worker(self):
        self.health.update(enough_data=True, cache_fresh=True, last_context_scan_age_hours=0)
        reply = self.start()
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.json()["job"]["state"], "SKIPPED_CACHE_SUFFICIENT")
        self.assertFalse(reply.json()["job"]["busy"])
        self.manager.start.assert_not_called()

    def test_no_token_skips_normally_before_cache_cooldown_or_disabled_policy(self):
        self.health.update(enough_data=True, cache_fresh=True, last_context_scan_age_hours=0,
                           collection_policy={'api_token_configured': False})
        for enabled in (False, True):
            self.enabled = enabled
            for force in (False, True):
                with self.subTest(enabled=enabled, force=force):
                    response = self.start(force=force)
                    self.assertEqual(response.status_code, 200)
                    job = response.json()['job']
                    self.assertEqual(job['state'], 'SKIPPED_NO_TOKEN')
                    self.assertNotIn('error_code', job)
                    self.assertFalse(job['busy'])
        self.manager.start.assert_not_called()

    def test_cooldown_is_explicit_even_when_no_compatible_files_were_found(self):
        self.health["last_context_scan_age_hours"] = 14.9 / 60
        for _ in range(2):
            reply = self.start()
            self.assertEqual(reply.status_code, 200)
            job = reply.json()["job"]
            self.assertEqual(job["state"], "SKIPPED_COOLDOWN")
            self.assertIn("최근 15분 이내", job["message"])
            self.assertFalse(job["busy"])
        self.manager.start.assert_not_called()

    def test_expired_cooldown_and_stale_cache_start_the_selected_context(self):
        self.health.update(last_context_scan_age_hours=15 / 60, enough_data=True, cache_fresh=False)
        reply = self.start()
        self.assertEqual(reply.status_code, 200)
        request = self.manager.start.call_args.args[0]
        self.assertEqual(request["context_key"], "car-track")
        self.assertEqual(request["filename"], "drive.ibt")
        self.assertFalse(request["force"])
        self.assertEqual(request["collector_mode"], "AUTO")

    def test_disabled_collection_and_missing_files_do_not_start_workers(self):
        self.enabled = False
        self.assertEqual(self.start().status_code, 503)
        self.enabled = True
        self.resolve.return_value = None
        self.assertEqual(self.start().status_code, 404)
        self.assertEqual(self.client.get("/api/garage61/collector/status?filename=missing.ibt").status_code, 404)
        self.manager.start.assert_not_called()

    def test_status_reports_the_existing_job_without_starting_or_cancelling_it(self):
        self.manager.active_job.return_value = {"job_id": "same-job", "state": "DOWNLOADING", "busy": True}
        reply = self.client.get("/api/garage61/collector/status?filename=drive.ibt")
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.json()["job"]["job_id"], "same-job")
        self.repository.load.assert_called_once_with(Path("drive.ibt"))
        self.manager.start.assert_not_called()
        self.manager.cancel.assert_not_called()

    def test_cooldown_uses_exact_seconds_instead_of_rounded_display_hours(self):
        self.health.update(last_context_scan_age_hours=.2, last_context_scan_age_seconds=899.5)
        reply = self.start().json()['job']
        self.assertEqual(reply['state'], 'SKIPPED_COOLDOWN')
        self.assertEqual(reply['retry_after_s'], 1)
        self.health['last_context_scan_age_seconds'] = 900.
        self.start()
        self.manager.start.assert_called_once()

    def test_another_context_has_no_cooldown_and_clicking_does_not_extend_the_timer(self):
        self.health.update(last_context_scan_age_hours=0, last_context_scan_age_seconds=60.)
        first = self.start().json()['job']
        second = self.start().json()['job']
        self.assertEqual(first['retry_after_s'], second['retry_after_s'])
        self.assertEqual(first['context_key'], 'car-track')
        self.manager.start.assert_not_called()
        self.health.update(last_context_scan_age_hours=None, last_context_scan_age_seconds=None,
                           context={'context_key': 'other-car-track', 'filename': 'drive.ibt'})
        self.start()
        self.assertEqual(self.manager.start.call_args.args[0]['context_key'], 'other-car-track')


if __name__ == "__main__":
    unittest.main()
