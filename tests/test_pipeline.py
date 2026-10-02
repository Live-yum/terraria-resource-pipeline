import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from resource_pipeline.fixtures import demo_archive
from resource_pipeline.pipeline import Pipeline
from resource_pipeline.security import ArchiveLimits, PipelineError, extract_zip


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pipeline = Pipeline(self.root / "service")

    def tearDown(self):
        self.temp.cleanup()

    def stage(self, revision=1, incomplete=False):
        job = self.pipeline.submit(demo_archive(revision, incomplete), "demo.zip")
        return self.pipeline.process(job["id"])

    def test_review_publish_real_git_and_version_diff(self):
        one = self.stage()
        self.assertEqual("READY_FOR_REVIEW", one["state"])
        self.assertEqual(1, one["manifest"]["counts"]["uniqueImages"])
        self.assertEqual(2, one["manifest"]["counts"]["imageReferences"])
        self.assertIsNone(self.pipeline.current())
        with self.assertRaises(PipelineError):
            self.pipeline.publish(one["id"], one["reviewDigest"], False)
        published = self.pipeline.publish(one["id"], one["reviewDigest"], True)
        self.assertEqual("PUBLISHED", published["state"])
        self.assertEqual(published["commit"], self.pipeline.git(["--git-dir", str(self.pipeline.remote), "rev-parse", "main"]))
        two = self.stage(2)
        self.assertEqual(["2"], two["diff"]["items"]["added"])
        self.assertEqual(["1"], two["diff"]["items"]["changed"])
        self.assertEqual(1, two["diff"]["tiles"]["unchanged"])
        self.pipeline.publish(two["id"], two["reviewDigest"], True)
        self.assertEqual(one["release"], self.pipeline.current()["previous"])

    def test_identical_reviewed_uploads_are_noop_and_still_verify_evidence(self):
        initial = self.stage()
        published = self.pipeline.publish(initial["id"], initial["reviewDigest"], True)
        pointer = self.pipeline.current()
        for _ in range(3):
            repeated = self.stage()
            with self.assertRaises(PipelineError):
                self.pipeline.publish(repeated["id"], repeated["reviewDigest"], False)
            result = self.pipeline.publish(repeated["id"], repeated["reviewDigest"], True)
            self.assertTrue(result["publicationNoOp"])
            self.assertEqual(published["commit"], result["commit"])
            self.assertEqual(pointer, self.pipeline.current())
        tampered = self.stage()
        artifact = next((self.pipeline.root / "jobs" / tampered["id"] / "staged" / "objects").rglob("*.gz"))
        artifact.write_bytes(b"tampered")
        with self.assertRaises(PipelineError):
            self.pipeline.publish(tampered["id"], tampered["reviewDigest"], True)
        self.assertEqual(pointer, self.pipeline.current())

    def test_incomplete_blocks_even_when_uploaded_required_list_lies(self):
        job = self.stage(incomplete=True)
        self.assertEqual("BLOCKED", job["state"])
        self.assertIn("walls", job["coverage"]["missingFamilies"])
        with self.assertRaises(PipelineError):
            self.pipeline.publish(job["id"], job["reviewDigest"], True)

    def test_stale_review_and_tamper_are_rejected(self):
        first = self.stage()
        second = self.stage(2)
        self.pipeline.publish(first["id"], first["reviewDigest"], True)
        with self.assertRaisesRegex(PipelineError, "基线"):
            self.pipeline.publish(second["id"], second["reviewDigest"], True)
        third = self.stage(2)
        target = self.pipeline.root / "jobs" / third["id"] / "staged/manifest.json"
        target.write_text("{}")
        with self.assertRaisesRegex(PipelineError, "修改"):
            self.pipeline.publish(third["id"], third["reviewDigest"], True)

    def test_restart_preserves_jobs_without_republishing(self):
        job = self.stage()
        restarted = Pipeline(self.pipeline.root)
        self.assertEqual(job, restarted.get(job["id"]))
        self.assertIsNone(restarted.current())

    def test_extraction_rejects_traversal_duplicate_and_symlink(self):
        for case, name in enumerate(("../escape", "C:/evil", "a\\evil", "CON.txt")):
            archive = self.root / f"bad{case}.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr(name, b"x")
            with self.assertRaises(PipelineError):
                extract_zip(archive, self.root / f"out{case}")
        archive = self.root / "link.zip"
        with zipfile.ZipFile(archive, "w") as z:
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            z.writestr(info, "../outside")
        with self.assertRaises(PipelineError):
            extract_zip(archive, self.root / "linkout")


if __name__ == "__main__":
    unittest.main()
