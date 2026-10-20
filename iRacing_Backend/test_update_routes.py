"""The update window's endpoints with stand-ins for the updater and the voice sync."""
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from update_routes import UpdateRoutesServices, register_routes


class FakeUpdater:
    def __init__(self, staged=None, error=None):
        self.staged, self.error, self.calls = staged, error, []

    def status(self):
        return {"current": "1.4.1", "staged": self.staged}

    def check(self):
        self.calls.append("check")

    def start_download(self):
        self.calls.append("download")
        if self.error:
            raise self.error


class FakeVoice:
    def __init__(self):
        self.calls = []

    def status(self):
        return {"missing": 3}

    def __getattr__(self, name):
        return lambda: self.calls.append(name)


def client(updater, restart=lambda: True):
    app = FastAPI()
    voice = FakeVoice()
    register_routes(app, UpdateRoutesServices(app_updater=updater, voice_sync=voice, request_restart=restart))
    return TestClient(app), voice


class UpdateRouteTests(unittest.TestCase):
    def test_status_and_actions_reach_the_updater_and_voice(self):
        updater = FakeUpdater()
        api, voice = client(updater)
        self.assertEqual(api.get("/api/update").json(), {"app": {"current": "1.4.1", "staged": None}, "voice": {"missing": 3}})
        api.post("/api/update/check")
        api.post("/api/update/app/download")
        for action in ("download", "stop", "cleanup"):
            self.assertEqual(api.post(f"/api/update/voice/{action}").status_code, 200)
        self.assertEqual(updater.calls, ["check", "download"])
        self.assertEqual(voice.calls, ["rescan", "start_download", "stop", "start_cleanup"])

    def test_download_refusals_are_explained(self):
        api, _ = client(FakeUpdater(error=PermissionError("git pull로 업데이트하세요.")))
        response = api.post("/api/update/app/download")
        self.assertEqual((response.status_code, response.json()["detail"]), (409, "git pull로 업데이트하세요."))

    def test_restart_needs_a_staged_update_and_a_launcher(self):
        api, _ = client(FakeUpdater())
        self.assertEqual(api.post("/api/update/app/restart").status_code, 409)
        api, _ = client(FakeUpdater(staged="1.5.0"), restart=lambda: False)
        response = api.post("/api/update/app/restart")
        self.assertEqual(response.status_code, 409)
        self.assertIn("실행.bat", response.json()["detail"])
        restarts = []
        api, _ = client(FakeUpdater(staged="1.5.0"), restart=lambda: restarts.append(1) or True)
        self.assertEqual(api.post("/api/update/app/restart").json(), {"restarting": True})
        self.assertEqual(restarts, [1])


if __name__ == "__main__":
    unittest.main()
