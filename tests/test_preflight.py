from io import BytesIO
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient
from resource_pipeline.api import create_app
from resource_pipeline.pipeline import Pipeline
from resource_pipeline.preflight import RawInputPreflight, TrustedSource
from resource_pipeline.security import ArchiveLimits, PipelineError, sha256


def archive(entries=None):
    result = BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for name, data in (entries or {"original.txt": b"original harmless fixture"}).items():
            out.writestr(name, data)
    return result.getvalue()


def source(data, name="fixture.zip"):
    return BytesIO(data), name


class RawPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pipeline = Pipeline(Path(self.temp.name))
        self.preflight = RawInputPreflight(self.pipeline)
        self.server = archive({"TerrariaServer.exe": b"original synthetic nonexecutable input"})
        self.client = archive({"Content/Images/original.txt": b"original synthetic client fixture"})

    def tearDown(self):
        self.temp.cleanup()

    def run_job(self, service=None, server=True, client=True, version=None):
        service = service or self.preflight
        job = service.submit(source(self.server) if server else None, source(self.client) if client else None, version)
        return service.process(job["id"])

    def pins(self, server_version="1.4.5.0", client_version="1.4.5.0"):
        return (TrustedSource("server", sha256(self.server), server_version),
                TrustedSource("client", sha256(self.client), client_version))

    def test_dual_sources_persist_actual_hash_inventory_without_execution_or_publication(self):
        job = self.run_job(version="1.4.5.0")
        self.assertEqual("BLOCKED", job["state"])
        self.assertFalse(job["executedInput"])
        self.assertFalse(job["extractionComplete"])
        for role, data in (("server", self.server), ("client", self.client)):
            item = job["sources"][role]
            self.assertEqual(sha256(data), item["archiveSha256"])
            self.assertEqual(len(data), item["archiveBytes"])
            self.assertEqual("unverified", item["versionEvidence"]["status"])
            self.assertEqual("verified", item["inventoryStatus"])
            with zipfile.ZipFile(BytesIO(data)) as fixture:
                row = item["inventory"]["files"][0]
                self.assertEqual(sha256(fixture.read(row["path"])), row["sha256"])
        self.assertEqual(job, Pipeline(self.pipeline.root).get(job["id"]))
        self.assertIsNone(self.pipeline.current())
        self.assertEqual(2, sum(row["code"] == "VERSION_UNVERIFIED" for row in job["blockers"]))
        self.assertIn("NO_TRUSTED_ADAPTER", {row["code"] for row in job["blockers"]})
        with self.assertRaises(PipelineError):
            self.pipeline.publish(job["id"], "", True)

    def test_missing_each_source_is_explicit_and_persisted(self):
        for server, client, code in ((False, True, "MISSING_SERVER_INPUT"),
                                     (True, False, "MISSING_CLIENT_INPUT"),
                                     (False, False, "MISSING_SERVER_INPUT")):
            job = self.run_job(server=server, client=client)
            self.assertIn(code, {row["code"] for row in job["blockers"]})
            self.assertEqual("BLOCKED", self.pipeline.get(job["id"])["state"])

    def test_pinned_matching_versions_still_require_real_adapter(self):
        service = RawInputPreflight(self.pipeline, trusted_sources=self.pins())
        job = self.run_job(service, version="1.4.5.0")
        self.assertEqual(["NO_TRUSTED_ADAPTER"], [row["code"] for row in job["blockers"]])
        self.assertEqual("operator-pinned-archive-sha256", job["sources"]["server"]["versionEvidence"]["method"])
        self.assertEqual("BLOCKED", job["state"])

    def test_pinned_source_and_declared_version_mismatch_are_blockers(self):
        service = RawInputPreflight(self.pipeline, trusted_sources=self.pins(client_version="1.4.4.9"))
        job = self.run_job(service, version="1.4.5.0")
        self.assertTrue({"SOURCE_VERSION_MISMATCH", "DECLARED_VERSION_MISMATCH"} <= {row["code"] for row in job["blockers"]})

    def test_upload_metadata_and_filename_never_verify_versions(self):
        data = archive({"1.4.5.0/metadata.json": json.dumps({"game_version": "1.4.5.0", "trusted": True, "complete": True}).encode()})
        job = self.preflight.submit(source(data, "TerrariaServer-1.4.5.0.zip"), source(data, "Content-1.4.5.0.zip"), "1.4.5.0")
        done = self.preflight.process(job["id"])
        self.assertTrue(all(row["versionEvidence"]["status"] == "unverified" for row in done["sources"].values()))
        self.assertFalse(done["extractionComplete"])

    def test_combined_upload_limit_and_failure_clean_only_new_job(self):
        good = self.preflight.submit(source(self.server))
        service = RawInputPreflight(self.pipeline, ArchiveLimits(archive_bytes=len(self.server) + len(self.client) - 1))
        with self.assertRaisesRegex(PipelineError, "合计"):
            service.submit(source(self.server), source(self.client))
        class Broken:
            def read(self, size):
                raise OSError("synthetic stream failure")
        with self.assertRaises(OSError):
            self.preflight.submit(source(self.server), (Broken(), "broken.zip"))
        self.assertEqual([good["id"]], [row["id"] for row in self.pipeline.list()])
        self.assertEqual([good["id"]], [path.name for path in (self.pipeline.root / "jobs").iterdir()])

    def test_combined_file_and_expansion_budgets(self):
        for limits in (ArchiveLimits(files=1), ArchiveLimits(expanded_bytes=50)):
            service = RawInputPreflight(self.pipeline, limits)
            job = self.run_job(service)
            self.assertEqual("verified", job["sources"]["server"]["inventoryStatus"])
            self.assertEqual("rejected", job["sources"]["client"]["inventoryStatus"])
            self.assertIn("INVALID_ARCHIVE", {row["code"] for row in job["blockers"]})
            self.assertFalse((self.pipeline.root / "jobs" / job["id"] / "client-files").exists())

    def test_directory_entries_count_toward_combined_budget(self):
        service = RawInputPreflight(self.pipeline, ArchiveLimits(files=3))
        first = archive({"folder/": b"", "folder/file": b"one"})
        second = archive({"folder/": b"", "folder/file": b"two"})
        job = service.process(service.submit(source(first), source(second))["id"])
        self.assertEqual(2, job["archiveEntries"])
        self.assertEqual("rejected", job["sources"]["client"]["inventoryStatus"])

    def test_traversal_and_symlink_input_cannot_escape(self):
        for bad in (archive({"../outside": b"no"}), b"not a zip"):
            job = self.preflight.process(self.preflight.submit(source(bad))["id"])
            self.assertEqual("rejected", job["sources"]["server"]["inventoryStatus"])
            self.assertFalse((self.pipeline.root / "jobs" / job["id"] / "server-files").exists())
        data = BytesIO()
        with zipfile.ZipFile(data, "w") as out:
            item = zipfile.ZipInfo("linked")
            item.create_system = 3
            item.external_attr = 0o120777 << 16
            out.writestr(item, "../outside")
        job = self.preflight.process(self.preflight.submit(source(data.getvalue()))["id"])
        self.assertEqual("rejected", job["sources"]["server"]["inventoryStatus"])

    def test_stream_reads_are_bounded_and_private(self):
        class Bounded(BytesIO):
            def read(self, size=-1):
                if not 0 < size <= 1024 * 1024:
                    raise AssertionError("unbounded input read")
                return super().read(size)
        job = self.preflight.submit((Bounded(self.server), "a/server.zip"), source(self.client))
        self.assertEqual("server.zip", job["sources"]["server"]["filename"])
        if os.name == "posix":
            folder = self.pipeline.root / "jobs" / job["id"]
            self.assertEqual(0o700, stat.S_IMODE(folder.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE((folder / "server.zip").stat().st_mode))

    def test_repeated_process_refuses_and_repeated_submit_has_distinct_private_jobs(self):
        first = self.run_job()
        with self.assertRaisesRegex(PipelineError, "重复"):
            self.preflight.process(first["id"])
        second = self.run_job()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["sources"], second["sources"])

    def test_interrupt_cleans_partial_extraction_and_persists_interrupted(self):
        job = self.preflight.submit(source(self.server))
        def interrupt(source_path, destination, limits, *, checkpoint=None):
            destination.mkdir()
            (destination / "partial").write_bytes(b"partial")
            raise KeyboardInterrupt("synthetic interrupt")
        with patch("resource_pipeline.preflight.extract_zip", side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.preflight.process(job["id"])
        self.assertEqual("INTERRUPTED", self.pipeline.get(job["id"])["state"])
        self.assertFalse((self.pipeline.root / "jobs" / job["id"] / "server-files").exists())
        restarted = Pipeline(self.pipeline.root)
        self.assertEqual("INTERRUPTED", restarted.get(job["id"])["state"])
        self.assertIsNone(restarted.current())

    def test_crash_state_is_marked_interrupted_without_automatic_execution(self):
        job = self.preflight.submit(source(self.server))
        job["state"] = "EXTRACTING"
        self.pipeline.save(job)
        restarted = Pipeline(self.pipeline.root)
        self.assertEqual("INTERRUPTED", restarted.get(job["id"])["state"])
        self.assertFalse((restarted.root / "jobs" / job["id"] / "server-files").exists())

    def test_input_drift_is_rejected_before_inventory(self):
        job = self.preflight.submit(source(self.server))
        (self.pipeline.root / "jobs" / job["id"] / "server.zip").write_bytes(self.client)
        done = self.preflight.process(job["id"])
        self.assertEqual("rejected", done["sources"]["server"]["inventoryStatus"])

    def test_invalid_hint_and_save_failure_leave_no_new_jobs(self):
        with self.assertRaises(PipelineError):
            self.preflight.submit(source(self.server), declared_version="../../private")
        with patch.object(self.pipeline, "save", side_effect=OSError("synthetic database failure")), self.assertRaises(OSError):
            self.preflight.submit(source(self.server))
        self.assertEqual([], self.pipeline.list())
        self.assertEqual([], list((self.pipeline.root / "jobs").iterdir()))


class RawPreflightApiTests(unittest.TestCase):
    def test_distinct_raw_route_summary_and_full_inventory(self):
        with tempfile.TemporaryDirectory() as root, TestClient(create_app(Path(root), synchronous=True)) as client:
            data = archive()
            response = client.post("/api/raw-jobs", files={"server_file": ("server.zip", data), "client_file": ("Content.zip", data)}, data={"declared_version": "1.4.5.0"})
            self.assertEqual(202, response.status_code)
            job = response.json()
            self.assertEqual("BLOCKED", job["state"])
            self.assertFalse(job["executedInput"])
            summary = client.get("/api/jobs").json()[0]
            self.assertEqual(1, summary["sources"]["server"]["inventory"]["fileCount"])
            self.assertNotIn("files", summary["sources"]["server"]["inventory"])
            full = client.get(f"/api/jobs/{job['id']}").json()
            self.assertEqual(1, len(full["sources"]["server"]["inventory"]["files"]))
            self.assertIsNone(client.get("/api/current").json())
            self.assertEqual(409, client.post(f"/api/jobs/{job['id']}/publish", json={"reviewDigest": "", "confirmed": True}).status_code)
            self.assertEqual(404, client.get(f"/cdn/jobs/{job['id']}/server.zip").status_code)
            missing = client.post("/api/raw-jobs").json()
            self.assertTrue({"MISSING_SERVER_INPUT", "MISSING_CLIENT_INPUT"} <= {row["code"] for row in missing["blockers"]})
            self.assertEqual(403, client.post("/api/raw-jobs", headers={"Origin": "https://evil.invalid"}).status_code)


if __name__ == "__main__":
    unittest.main()
