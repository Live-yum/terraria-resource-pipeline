import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from resource_pipeline.fixtures import demo_archive
from resource_pipeline.pipeline import Pipeline
from resource_pipeline.security import ArchiveLimits, PipelineError, extract_zip, read_json


class ArchiveSecurityTests(unittest.TestCase):
    def check_rejected(self, entries, **limits):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            source = path / "a.zip"
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, content in entries:
                    archive.writestr(name, content)
            with self.assertRaises(PipelineError):
                extract_zip(source, path / "out", ArchiveLimits(**limits))

    def test_case_collision(self):
        self.check_rejected([("A.json", "1"), ("a.json", "2")])

    def test_parent_file_conflict(self):
        self.check_rejected([("a", "1"), ("a/b.json", "2")])

    def test_windows_unsafe_names(self):
        for name in ("a/aux.png", "name. ", "a:name", "/absolute", "a//b", "a/./b", "a/../b"):
            with self.subTest(name=name):
                self.check_rejected([(name, "x")])

    def test_archive_budgets(self):
        self.check_rejected([("large", b"x" * 1000)], file_bytes=999)
        self.check_rejected([("a", b"x" * 500), ("b", b"y" * 500)], expanded_bytes=999)
        self.check_rejected([("a", "1"), ("b", "2")], files=1)
        self.check_rejected([("high-ratio", b"x" * 10000)], ratio=2)

    def test_duplicate_and_nonfinite_json_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "input.json"
            for text in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
                path.write_text(text)
                with self.assertRaises(PipelineError):
                    read_json(path)


class PublicationSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pipeline = Pipeline(Path(self.temp.name))
    def tearDown(self):
        self.temp.cleanup()
    def stage(self, data=None):
        job = self.pipeline.submit(data or demo_archive(), "fixture.zip")
        return self.pipeline.process(job["id"])

    def test_missing_or_extra_object_never_published(self):
        job = self.stage()
        root = self.pipeline.root / "jobs" / job["id"] / "staged"
        object_file = root / job["manifest"]["packs"]["items"]["path"]
        object_file.unlink()
        with self.assertRaisesRegex(PipelineError, "缺失"):
            self.pipeline.publish(job["id"], job["reviewDigest"], True)
        self.assertIsNone(self.pipeline.current())

    def test_failed_git_push_leaves_public_pointer_unchanged(self):
        job = self.stage()
        original = self.pipeline.git
        def failing(args, cwd=None):
            if args and args[0] == "push":
                raise PipelineError("synthetic push failure")
            return original(args, cwd)
        with patch.object(self.pipeline, "git", side_effect=failing), self.assertRaises(PipelineError):
            self.pipeline.publish(job["id"], job["reviewDigest"], True)
        self.assertIsNone(self.pipeline.current())
        self.assertEqual("INTERRUPTED", self.pipeline.get(job["id"])["state"])

    def test_repeat_confirm_does_not_make_a_second_commit(self):
        job = self.stage()
        done = self.pipeline.publish(job["id"], job["reviewDigest"], True)
        with self.assertRaises(PipelineError):
            self.pipeline.publish(job["id"], job["reviewDigest"], True)
        self.assertEqual(done["commit"], self.pipeline.git(["--git-dir", str(self.pipeline.remote), "rev-parse", "main"]))

    def test_mismatched_version_and_missing_images_block(self):
        for mismatch in (True, False):
            source = io.BytesIO(demo_archive())
            output = io.BytesIO()
            with zipfile.ZipFile(source) as original, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
                for item in original.infolist():
                    data = original.read(item)
                    if item.filename == "resource-bundle.json":
                        document = json.loads(data)
                        if mismatch:
                            document["sources"][0]["game_version"] = "9.9.9"
                        else:
                            document["families"]["items"][0]["images"] = ["images/missing.png"]
                        data = json.dumps(document).encode()
                    target.writestr(item.filename, data)
            job = self.stage(output.getvalue())
            self.assertEqual("BLOCKED", job["state"])
            self.assertIsNone(self.pipeline.current())


if __name__ == "__main__":
    unittest.main()
