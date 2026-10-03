"""Raw upload/unpack telemetry integration with original tiny synthetic ZIPs.

The store and failing decoder are explicit test doubles; ZIP validation and
RawInputPreflight are the actual production code. No game binaries are used.
"""
import copy
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile

from resource_pipeline.preflight import RawInputPreflight
from resource_pipeline.security import PipelineError


class MemoryJobStore:
    def __init__(self, root):
        self.root = root
        self.lock = threading.RLock()
        self.jobs = {}

    def save(self, job):
        self.jobs[job["id"]] = copy.deepcopy(job)

    def get(self, identity):
        return copy.deepcopy(self.jobs[identity])


def archive(name="original-synthetic.txt"):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as target:
        target.writestr(name, b"original synthetic test data; not a game asset")
    result.seek(0)
    return result


class RawStageMetricsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = MemoryJobStore(Path(self.temporary.name))
        self.preflight = RawInputPreflight(self.store)

    def submit(self, client=None, server=None):
        return self.preflight.submit(
            server_file=(server or archive(), "private-server.zip"),
            client_file=(client or archive(), "private-content.zip"),
            declared_version="1.4.5.0")

    def test_valid_synthetic_archives_record_both_hash_and_unpack_stages(self):
        uploaded = self.submit()
        result = self.preflight.process(uploaded["id"])
        self.assertEqual(result["state"], "BLOCKED")
        self.assertFalse(result["extractionComplete"])
        self.assertFalse(result["executedInput"])
        metrics = result["metrics"]
        self.assertEqual([s["stage"] for s in metrics["stages"]],
                         ["server_verify", "server_unpack", "client_verify", "client_unpack"])
        self.assertTrue(all(s["status"] == "SUCCEEDED" for s in metrics["stages"]))
        self.assertEqual(self.store.get(result["id"])["metrics"], metrics)
        self.assertNotIn("private-server", json.dumps(metrics))
        self.assertNotIn("private-content", json.dumps(metrics))
        self.assertEqual(result["sources"]["server"]["archiveSha256"],
                         uploaded["sources"]["server"]["archiveSha256"])

    def test_invalid_archive_retains_failed_stage_and_no_publish_permission(self):
        job = self.submit(client=io.BytesIO(b"not a ZIP"))
        result = self.preflight.process(job["id"])
        rows = result["metrics"]["stages"]
        self.assertEqual(rows[-1]["stage"], "client_unpack")
        self.assertEqual(rows[-1]["status"], "FAILED")
        self.assertEqual(result["sources"]["client"]["inventoryStatus"], "rejected")
        self.assertFalse(result["extractionComplete"])

    def test_tampered_source_fails_before_unpack_and_records_verification_failure(self):
        job = self.submit()
        path = self.store.root / "jobs" / job["id"] / "server.zip"
        path.write_bytes(b"changed")
        result = self.preflight.process(job["id"])
        rows = result["metrics"]["stages"]
        self.assertEqual(rows[0]["stage"], "server_verify")
        self.assertEqual(rows[0]["status"], "FAILED")
        self.assertNotIn("server_unpack", [r["stage"] for r in rows])

    def test_early_cancellation_retains_empty_but_honest_metrics(self):
        job = self.submit()
        self.preflight.request_cancel(job["id"])
        result = self.preflight.process(job["id"])
        self.assertEqual(result["state"], "CANCELED")
        self.assertEqual(result["metrics"]["stages"], [])
        self.assertFalse(result["extractionComplete"])

    def test_unexpected_decoder_failure_retains_terminal_metrics(self):
        class BrokenDecoder:
            def extract(self, *args):
                raise RuntimeError("/private/decoder/detail")
        self.preflight.texture_extractor = BrokenDecoder()
        job = self.submit(client=archive("Content/Images/Item_1.xnb"))
        result = self.preflight.process(job["id"])
        self.assertEqual(result["error"], "RAW_PROCESSING_FAILED")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertEqual(result["metrics"]["stages"][-1]["stage"], "client_textures")
        self.assertEqual(result["metrics"]["stages"][-1]["status"], "FAILED")
        self.assertNotIn("/private/decoder", json.dumps(result))

    def test_decoder_interrupt_is_reraised_and_metrics_survive(self):
        class InterruptedDecoder:
            def extract(self, *args):
                raise KeyboardInterrupt()
        self.preflight.texture_extractor = InterruptedDecoder()
        job = self.submit(client=archive("Content/Images/Item_1.xnb"))
        with self.assertRaises(KeyboardInterrupt):
            self.preflight.process(job["id"])
        result = self.store.get(job["id"])
        self.assertEqual(result["state"], "INTERRUPTED")
        self.assertEqual(result["metrics"]["stages"][-1]["status"], "INTERRUPTED")

    def test_repeated_process_cannot_replace_existing_metrics(self):
        job = self.submit()
        result = self.preflight.process(job["id"])
        with self.assertRaises(PipelineError):
            self.preflight.process(job["id"])
        self.assertEqual(self.store.get(job["id"])["metrics"], result["metrics"])

    def test_semantic_stage_does_not_claim_partial_evidence_is_complete(self):
        class PartialProducer:
            def produce(self, *args, **kwargs):
                return {"status": "PARTIAL", "extractionComplete": False, "publishable": False}
        self.preflight.semantic_producer = PartialProducer()
        job = self.submit()
        result = self.preflight.process(job["id"])
        self.assertEqual(result["metrics"]["stages"][-1]["stage"], "semantic_producer")
        self.assertFalse(result["extractionComplete"])
        self.assertEqual(result["state"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
