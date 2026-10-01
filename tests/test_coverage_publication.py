import gzip
from io import BytesIO
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from resource_pipeline.catalog import manifest_references, verified_object
from resource_pipeline.fixtures import demo_archive
from resource_pipeline.pipeline import Pipeline
from resource_pipeline.security import ArchiveLimits, PipelineError, canonical_json, sha256


def edited_archive(edit):
    with zipfile.ZipFile(BytesIO(demo_archive())) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    edit(files)
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


class CoveragePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pipeline = Pipeline(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def stage(self, content=None):
        job = self.pipeline.submit(content or demo_archive(), "synthetic.zip")
        return self.pipeline.process(job["id"])

    def test_full_proof_survives_review_git_and_readback(self):
        job = self.stage()
        self.assertEqual("READY_FOR_REVIEW", job["state"])
        self.assertEqual(110, job["coverage"]["submanifestCount"])
        self.assertEqual(107, job["coverage"]["verifiedRequired"])
        self.assertEqual(107, job["coverage"]["requiredCount"])
        manifest = job["manifest"]
        proof = manifest["coverageProof"]
        self.assertEqual("synthetic-fixture-v1", proof["profileId"])
        self.assertEqual("synthetic", proof["binding"]["source_kind"])
        self.assertIn("evidence/items.attributes.defaults.json", proof["artifacts"])
        self.assertIn("evidence/items.tooltip.resolved-baseline.json", proof["artifacts"])
        self.assertIn("images/synthetic.png", proof["artifacts"])
        self.pipeline.publish(job["id"], job["reviewDigest"], True)
        for reference in manifest_references(manifest):
            data = self.pipeline.published_file(reference["path"])
            self.assertEqual(reference["sha256"], sha256(data))
            if reference.get("encoding") == "gzip":
                self.assertEqual(reference["rawSha256"], sha256(gzip.decompress(data)))
        self.assertEqual(manifest, json.loads(self.pipeline.published_file(f"releases/{job['release']}/manifest.json")))

    def test_nonempty_families_cannot_replace_a_missing_subclaim(self):
        def edit(files):
            coverage = json.loads(files["coverage.json"])
            coverage["claims"] = [claim for claim in coverage["claims"] if claim["key"] != "walls.traits"]
            files["coverage.json"] = canonical_json(coverage)
        job = self.stage(edited_archive(edit))
        self.assertEqual("BLOCKED", job["state"])
        self.assertEqual([], job["coverage"]["missingFamilies"])
        claim = next(row for row in job["coverage"]["subclaims"] if row["key"] == "walls.traits")
        self.assertEqual("missing", claim["status"])
        self.assertEqual([1], claim["missingIds"])
        self.assertFalse(job["coverage"]["complete"])

    def test_uploaded_complete_boolean_and_self_selected_profile_are_rejected(self):
        for change in ({"complete": True}, {"profile_id": "user-chosen-loose-profile"}):
            def edit(files):
                coverage = json.loads(files["coverage.json"])
                coverage.update(change)
                files["coverage.json"] = canonical_json(coverage)
            with self.subTest(change=change):
                self.assertEqual("BLOCKED", self.stage(edited_archive(edit))["state"])

    def test_missing_proof_and_evidence_hash_mismatch_block(self):
        for edit in (lambda files: files.pop("coverage.json"),
                     lambda files: files.update({"evidence/items.attributes.defaults.json": b"[]"})):
            self.assertEqual("BLOCKED", self.stage(edited_archive(edit))["state"])

    def test_uploaded_binding_does_not_become_expected_binding(self):
        def edit(files):
            coverage = json.loads(files["coverage.json"])
            coverage["binding"]["server_sha256"] = "a" * 64
            envelope = json.loads(files["resource-bundle.json"])
            envelope["sources"][0]["sha256"] = "a" * 64
            files["coverage.json"] = canonical_json(coverage)
            files["resource-bundle.json"] = canonical_json(envelope)
            files["synthetic-input.txt"] = b"different actual input"
        self.assertEqual("BLOCKED", self.stage(edited_archive(edit))["state"])

    def test_unregistered_real_export_and_unknown_synthetic_version_block(self):
        for kind, version in (("export-bundle", "0.0.1"), ("synthetic", "9.9.9")):
            def edit(files):
                envelope = json.loads(files["resource-bundle.json"])
                envelope.update(source_kind=kind, game_version=version)
                for source in envelope["sources"]:
                    source["game_version"] = version
                files["resource-bundle.json"] = canonical_json(envelope)
            job = self.stage(edited_archive(edit))
            self.assertEqual("BLOCKED", job["state"])
            self.assertIn("trusted coverage profile", job["error"])

    def test_proof_table_missing_or_tampered_after_review_never_publishes(self):
        for tamper in (False, True):
            job = self.stage()
            reference = job["manifest"]["coverageProof"]["artifacts"]["evidence/items.attributes.defaults.json"]
            file = self.root / "jobs" / job["id"] / "staged" / reference["path"]
            if tamper:
                original = file.read_bytes()
                file.write_bytes(bytes([original[0] ^ 1]) + original[1:])
            else:
                file.unlink()
            with self.assertRaisesRegex(PipelineError, "校验"):
                self.pipeline.publish(job["id"], job["reviewDigest"], True)
            self.assertIsNone(self.pipeline.current())

    def test_proof_image_and_profile_object_missing_never_publish(self):
        for kind in ("image", "profile"):
            job = self.stage()
            proof = job["manifest"]["coverageProof"]
            reference = proof["profile"] if kind == "profile" else proof["artifacts"]["images/synthetic.png"]
            (self.root / "jobs" / job["id"] / "staged" / reference["path"]).unlink()
            with self.assertRaises(PipelineError):
                self.pipeline.publish(job["id"], job["reviewDigest"], True)
            self.assertIsNone(self.pipeline.current())

    def test_decompressed_evidence_hash_mismatch_is_checked(self):
        job = self.stage()
        reference = dict(job["manifest"]["coverageProof"]["document"], rawSha256="a" * 64)
        root = self.root / "jobs" / job["id"] / "staged"
        with self.assertRaisesRegex(PipelineError, "解压哈希"):
            verified_object(root, reference)

    def test_record_diff_baseline_is_review_bound(self):
        job = self.stage()
        root = self.root / "jobs" / job["id"] / "staged"
        (root / "record-hashes.json").write_text("{}")
        with self.assertRaisesRegex(PipelineError, "差异基线"):
            self.pipeline.publish(job["id"], job["reviewDigest"], True)
        self.assertIsNone(self.pipeline.current())

    def test_publication_revalidates_policy_even_if_job_status_is_wrong(self):
        def edit(files):
            coverage = json.loads(files["coverage.json"])
            coverage["claims"] = [claim for claim in coverage["claims"] if claim["key"] != "walls.traits"]
            files["coverage.json"] = canonical_json(coverage)
        job = self.stage(edited_archive(edit))
        self.assertEqual("BLOCKED", job["state"])
        job["state"] = "READY_FOR_REVIEW"  # Simulate an unrelated queue/state regression.
        self.pipeline.save(job)
        with self.assertRaisesRegex(PipelineError, "覆盖证明不完整"):
            self.pipeline.publish(job["id"], job["reviewDigest"], True)
        self.assertIsNone(self.pipeline.current())


class StreamingSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pipeline = Pipeline(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_streaming_submission_uses_bounded_reads_and_actual_hash(self):
        class BoundedStream(BytesIO):
            sizes = []
            def read(self, size=-1):
                self.sizes.append(size)
                if size < 0 or size > 1024 * 1024:
                    raise AssertionError("Unbounded upload read")
                return super().read(size)
        data = b"x" * (2 * 1024 * 1024 + 7)
        stream = BoundedStream(data)
        job = self.pipeline.submit_file(stream, "path/fixture.zip")
        self.assertGreater(len(stream.sizes), 2)
        self.assertEqual(sha256(data), job["inputSha256"])
        self.assertEqual(len(data), job["inputBytes"])
        self.assertEqual("fixture.zip", job["filename"])
        self.assertFalse(stream.closed)
        if os.name == "posix":
            directory = self.pipeline.root / "jobs" / job["id"]
            self.assertEqual(0o700, stat.S_IMODE(directory.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE((directory / "input.zip").stat().st_mode))

    def test_oversized_stream_leaves_no_queued_job_or_partial_directory(self):
        with patch("resource_pipeline.pipeline.ArchiveLimits", return_value=ArchiveLimits(archive_bytes=100)):
            with self.assertRaisesRegex(PipelineError, "体积限制"):
                self.pipeline.submit_file(BytesIO(b"x" * 101), "large.zip")
        self.assertEqual([], self.pipeline.list())
        self.assertEqual([], list((self.pipeline.root / "jobs").iterdir()))

    def test_broken_stream_cleans_only_its_own_partial_directory(self):
        good = self.pipeline.submit_file(BytesIO(b"keep me"), "good.zip")
        class BrokenStream:
            count = 0
            def read(self, size):
                self.count += 1
                if self.count == 1:
                    return b"partial"
                raise OSError("synthetic read failure")
        with self.assertRaises(OSError):
            self.pipeline.submit_file(BrokenStream(), "broken.zip")
        self.assertEqual([good["id"]], [job["id"] for job in self.pipeline.list()])
        self.assertEqual([good["id"]], [path.name for path in (self.pipeline.root / "jobs").iterdir()])

    def test_invalid_stream_type_and_interruption_leave_no_partial_job(self):
        class InvalidStream:
            def __init__(self, result):
                self.result = result
            def read(self, size):
                return self.result
        for result in ("", "text", None):
            with self.subTest(result=result), self.assertRaisesRegex(PipelineError, "二进制"):
                self.pipeline.submit_file(InvalidStream(result), "invalid.zip")
        class InterruptedStream:
            def read(self, size):
                raise KeyboardInterrupt("synthetic cancellation")
        with self.assertRaises(KeyboardInterrupt):
            self.pipeline.submit_file(InterruptedStream(), "interrupted.zip")
        self.assertEqual([], self.pipeline.list())
        self.assertEqual([], list((self.pipeline.root / "jobs").iterdir()))

    def test_failed_job_save_cleans_completed_upload_bytes(self):
        with patch.object(self.pipeline, "save", side_effect=OSError("synthetic database failure")):
            with self.assertRaises(OSError):
                self.pipeline.submit_file(BytesIO(b"complete bytes"), "failed-save.zip")
        self.assertEqual([], self.pipeline.list())
        self.assertEqual([], list((self.pipeline.root / "jobs").iterdir()))


if __name__ == "__main__":
    unittest.main()
