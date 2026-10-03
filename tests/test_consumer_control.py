from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from fastapi.testclient import TestClient
from resource_pipeline.api import create_app
from resource_pipeline.pipeline import Pipeline
from resource_pipeline.consumer_control import ConsumerReleaseControl, REQUIRED_GROUPS
from resource_pipeline.security import PipelineError, canonical_json, sha256


class ConsumerControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pipeline = Pipeline(self.root)
        self.control = ConsumerReleaseControl(self.pipeline, publisher_id="synthetic-local-demo", synthetic_demo=True)

    def tearDown(self):
        self.temp.cleanup()

    def approve(self, revision):
        review = self.control.preview_demo(revision)
        return self.control.publish(review["id"], review["reviewDigest"], True)

    def test_default_disabled_and_no_upload_approval_path(self):
        with self.assertRaises(PipelineError):
            ConsumerReleaseControl(self.pipeline).preview_demo(1)
        with self.assertRaises(PipelineError):
            self.control.preview_demo(True)
        with self.assertRaises(PipelineError):
            self.control.preview_demo(3)
        self.assertIsNone(self.control.current())

    def test_complete_review_publishes_exact_group_pins_to_local_git(self):
        result = self.approve(1)
        pointer = self.control.current()
        self.assertEqual(result["pointer"], pointer)
        self.assertEqual(pointer["approvalSequence"], 1)
        self.assertEqual(pointer["operation"], "advance")
        raw = self.pipeline.published_file(f"release-sets/{pointer['releaseSetId']}.json")
        self.assertEqual(sha256(raw), pointer["releaseSetSha256"])
        document = json.loads(raw)
        self.assertEqual(set(document["groups"]), set(REQUIRED_GROUPS))
        self.assertEqual(document["worldgen"], [])
        for group, pin in document["groups"].items():
            manifest = self.pipeline.git(["--git-dir", str(self.pipeline.remote), "show", f"{pin['revision']}:{pin['manifestPath']}"])
            self.assertEqual(sha256(manifest.encode()), pin["manifestSha256"])
            self.assertEqual(json.loads(manifest)["sourceBinding"], document["sourceBinding"])

    def test_missing_confirmation_bad_digest_stale_review_rejected(self):
        review = self.control.preview_demo(1)
        for digest, confirm in ((review["reviewDigest"], False), ("0" * 64, True)):
            with self.assertRaises(PipelineError):
                self.control.publish(review["id"], digest, confirm)
        stale = self.control.preview_demo(2)
        self.control.publish(review["id"], review["reviewDigest"], True)
        before = self.control.current()
        with self.assertRaises(PipelineError):
            self.control.publish(stale["id"], stale["reviewDigest"], True)
        self.assertEqual(self.control.current(), before)

    def test_post_review_tamper_cannot_publish_even_one_group(self):
        review = self.control.preview_demo(1)
        root = self.root / "consumer-reviews" / review["id"]
        manifest = json.loads((root / "markers/manifest.json").read_bytes())
        ref = next(iter(manifest["objects"].values()))
        (root / "markers" / ref["path"]).write_bytes(b"tampered")
        with self.assertRaises(PipelineError):
            self.control.publish(review["id"], review["reviewDigest"], True)
        self.assertIsNone(self.control.current())
        self.assertEqual(self.pipeline.git(["status", "--porcelain"], self.pipeline.checkout), "")

    def test_explicit_rollback_increases_approval_sequence(self):
        first = self.approve(1)
        second = self.approve(2)
        review = self.control.preview_rollback(1)
        with self.assertRaises(PipelineError):
            self.control.rollback(review["id"], review["reviewDigest"], False)
        rolled = self.control.rollback(review["id"], review["reviewDigest"], True)
        self.assertEqual(rolled["pointer"]["approvalSequence"], 3)
        self.assertEqual(rolled["pointer"]["releaseSetId"], first["pointer"]["releaseSetId"])
        self.assertEqual(rolled["pointer"]["previousSetId"], second["pointer"]["releaseSetId"])
        self.assertEqual(rolled["pointer"]["operation"], "rollback")

    def test_api_extra_approval_fields_and_default_disabled(self):
        with TestClient(create_app(self.root / "api-disabled")) as client:
            self.assertEqual(client.post("/api/consumer-demo/1/preview").status_code, 409)
        with TestClient(create_app(self.root / "api", consumer_demo=True)) as client:
            review = client.post("/api/consumer-demo/1/preview").json()
            endpoint = f"/api/consumer-reviews/{review['id']}/publish"
            self.assertEqual(client.post(endpoint, json={"confirmed": True, "reviewDigest": review["reviewDigest"], "approved": True}).status_code, 422)
            result = client.post(endpoint, json={"confirmed": True, "reviewDigest": review["reviewDigest"]})
            self.assertEqual(result.status_code, 200)
            pointer = result.json()["pointer"]
            self.assertEqual(client.get("/cdn/channels/consumer-stable.json").json(), pointer)
            self.assertEqual(client.get(f"/cdn/release-sets/{pointer['releaseSetId']}.json").status_code, 200)
            self.assertEqual(client.post(endpoint, json={"confirmed": True, "reviewDigest": review["reviewDigest"]}).status_code, 409)
