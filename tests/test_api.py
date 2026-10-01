from pathlib import Path
import tempfile
import unittest
from fastapi.testclient import TestClient
from resource_pipeline.api import create_app


class ApiTests(unittest.TestCase):
    def test_browser_contract_and_published_only_cdn(self):
        with tempfile.TemporaryDirectory() as root, TestClient(create_app(Path(root), synchronous=True)) as client:
            self.assertEqual(200, client.get("/").status_code)
            fixture = client.get("/api/demo/1.zip").content
            response = client.post("/api/jobs", files={"file": ("demo.zip", fixture, "application/zip")})
            self.assertEqual(202, response.status_code)
            job = response.json()
            self.assertEqual("READY_FOR_REVIEW", job["state"])
            path = job["manifest"]["packs"]["items"]["path"]
            self.assertEqual(404, client.get("/cdn/" + path).status_code)
            bad = client.post(f"/api/jobs/{job['id']}/publish", json={"reviewDigest": job["reviewDigest"], "confirmed": True}, headers={"Origin": "https://evil.invalid"})
            self.assertEqual(403, bad.status_code)
            approved = client.post(f"/api/jobs/{job['id']}/publish", json={"reviewDigest": job["reviewDigest"], "confirmed": True})
            self.assertEqual(200, approved.status_code)
            self.assertEqual("PUBLISHED", approved.json()["state"])
            self.assertEqual(200, client.get("/cdn/" + path).status_code)
            self.assertEqual(404, client.get("/cdn/jobs.sqlite").status_code)
            self.assertEqual(400, client.get("/api/current", headers={"Host": "attacker.invalid"}).status_code)

    def test_bad_zip_and_raw_game_do_not_execute_or_publish(self):
        with tempfile.TemporaryDirectory() as root, TestClient(create_app(Path(root), synchronous=True)) as client:
            response = client.post("/api/jobs", files={"file": ("game.zip", b"MZ-not-a-zip")})
            self.assertEqual("BLOCKED", response.json()["state"])
            self.assertIsNone(client.get("/api/current").json())


if __name__ == "__main__":
    unittest.main()
