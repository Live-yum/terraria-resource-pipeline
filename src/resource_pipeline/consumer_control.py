"""Server-owned synthetic release-set review and local bare-Git publication.

NOT a real asset publication bypass. The only enabled producer is the original
synthetic fixture below; request JSON cannot supply pins, source bindings,
coverage, approved flags, destinations, or paths. Real adapters remain disabled
until their independent semantic coverage/rights/deployment review is complete.
No credentials, key generation, hosting or authentication grants are created.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import uuid

from .consumer_release import GROUPS, build_consumer_release, verify_consumer_release
from .security import PipelineError, atomic_write, canonical_json, read_json, sha256

REQUIRED_GROUPS = ("materials", "items", "player", "markers")
CHANNEL_PATH = "channels/consumer-stable.json"
SYNTHETIC_BINDING = {"serverSha256": sha256(b"original synthetic server fixture"),
                     "clientTreeSha": "b" * 40, "consumerCommit": "c" * 40}


def synthetic_objects(group: str, revision: int) -> dict[str, bytes]:
    """Original transport test data; does not claim Terraria semantic coverage."""
    formats, _ = GROUPS[group]
    return {role: canonical_json({"synthetic": True, "role": role, "revision": revision})
            if fmt == "json" else f"ORIGINAL-SYNTHETIC:{role}:{revision}".encode()
            for role, fmt in formats.items()}


class ConsumerReleaseControl:
    def __init__(self, pipeline, *, publisher_id: str | None = None, synthetic_demo: bool = False):
        self.pipeline = pipeline
        self.publisher_id = publisher_id
        self.synthetic_demo = synthetic_demo
        if publisher_id is not None and not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,79}", publisher_id):
            raise PipelineError("Invalid server-owned publisher identity")

    def _enabled(self):
        if not self.synthetic_demo or not self.publisher_id:
            raise PipelineError("Consumer release publication is unconfigured; synthetic demo requires explicit server configuration")

    def current(self):
        try:
            return json.loads(self.pipeline.published_file(CHANNEL_PATH))
        except FileNotFoundError:
            return None

    def _review_path(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9-]{36}", identity):
            raise PipelineError("Invalid consumer review identity")
        return self.pipeline.root / "consumer-reviews" / identity / "review.json"

    def preview_demo(self, revision: int) -> dict:
        self._enabled()
        if type(revision) is not int or revision not in (1, 2):
            raise PipelineError("Only original synthetic demo revisions 1 and 2 are available")
        with self.pipeline.lock:
            identity = str(uuid.uuid4())
            root = self._review_path(identity).parent
            group_hashes = {}
            for group in REQUIRED_GROUPS:
                manifest = build_consumer_release(root / group, game_version=f"0.0.{revision}", source_binding=SYNTHETIC_BINDING,
                                                  objects=synthetic_objects(group, revision), compress=True, group=group)
                group_hashes[group] = sha256(canonical_json(manifest))
            current = self.current()
            review = {"id": identity, "state": "READY_FOR_REVIEW", "kind": "original-synthetic-consumer-set",
                      "syntheticRevision": revision, "publisherId": self.publisher_id,
                      "basePointerSha256": sha256(canonical_json(current)) if current else None,
                      "groupManifestHashes": group_hashes, "gameVersion": f"0.0.{revision}",
                      "sourceBinding": SYNTHETIC_BINDING, "requiredGroups": list(REQUIRED_GROUPS),
                      "worldgen": [], "warning": "Synthetic transport proof only; not approved Terraria runtime data"}
            review["reviewDigest"] = sha256(canonical_json(review))
            atomic_write(self._review_path(identity), canonical_json(review))
            return review

    def _clean(self):
        pipeline = self.pipeline
        if pipeline.git(["status", "--porcelain"], pipeline.checkout) or pipeline.git(["rev-parse", "HEAD"], pipeline.checkout) != pipeline.git(["--git-dir", str(pipeline.remote), "rev-parse", "main"]):
            raise PipelineError("Local publication state needs manual reconciliation")

    def _commit(self, paths, message):
        self.pipeline.git(["add", *paths], self.pipeline.checkout)
        if self.pipeline.git(["status", "--porcelain"], self.pipeline.checkout):
            self.pipeline.git(["commit", "-m", message], self.pipeline.checkout)
        return self.pipeline.git(["rev-parse", "HEAD"], self.pipeline.checkout)

    def _publish_pointer(self, document, revision, operation, current):
        pointer = {"schemaVersion": 1, "publisherId": self.publisher_id,
                   "approvalSequence": current["approvalSequence"] + 1 if current else 1,
                   "operation": operation, "previousSetId": current["releaseSetId"] if current else None,
                   "releaseSetId": document["releaseSetId"], "releaseSetSha256": sha256(canonical_json(document)), "revision": revision}
        atomic_write(self.pipeline.checkout / CHANNEL_PATH, canonical_json(pointer))
        # Store approved history in the publisher-owned tree. Uploaded bytes do
        # not enter this namespace. Rollback can choose only an existing entry.
        atomic_write(self.pipeline.checkout / f"consumer-approvals/{pointer['approvalSequence']}.json", canonical_json(pointer))
        commit = self._commit(["channels", "consumer-approvals"], f"Approve synthetic consumer set {pointer['approvalSequence']}")
        self.pipeline.git(["push", "origin", "main"], self.pipeline.checkout)
        if self.pipeline.git(["--git-dir", str(self.pipeline.remote), "rev-parse", "main"]) != commit:
            raise PipelineError("Local consumer release publication was not verified")
        return pointer, commit

    def publish(self, identity: str, review_digest: str, confirmed: bool) -> dict:
        self._enabled()
        with self.pipeline.lock:
            path = self._review_path(identity)
            review = read_json(path, 64 * 1024)
            if review.get("state") != "READY_FOR_REVIEW" or confirmed is not True or review_digest != review.get("reviewDigest"):
                raise PipelineError("Explicit approval of the exact server-issued preview is required")
            unsigned = {key: value for key, value in review.items() if key != "reviewDigest"}
            if sha256(canonical_json(unsigned)) != review_digest or review["publisherId"] != self.publisher_id:
                raise PipelineError("Server review record changed")
            current = self.current()
            if (sha256(canonical_json(current)) if current else None) != review["basePointerSha256"]:
                raise PipelineError("The channel changed; generate and review a fresh complete preview")
            self._clean()
            # Recompute exact server fixture and every staged byte before Git can
            # see it. A self-reported synthetic flag/coverage is never sufficient.
            manifests = {}
            for group in REQUIRED_GROUPS:
                root = path.parent / group
                objects = verify_consumer_release(root, manifest_sha256=review["groupManifestHashes"][group], group=group)
                if objects != synthetic_objects(group, review["syntheticRevision"]):
                    raise PipelineError("Consumer inputs are not the server-owned synthetic fixture")
                manifest = read_json(root / "manifest.json", 256 * 1024)
                if manifest["sourceBinding"] != SYNTHETIC_BINDING or manifest["gameVersion"] != review["gameVersion"]:
                    raise PipelineError("Mixed consumer source binding")
                expected = {"manifest.json", *(ref["path"] for ref in manifest["objects"].values())}
                actual = {str(file.relative_to(root)) for file in root.rglob("*") if file.is_file()}
                if actual != expected or any(file.is_symlink() for file in root.rglob("*")):
                    raise PipelineError("Unexpected staged consumer files")
                manifests[group] = manifest
            review["state"] = "PUBLISHING"
            atomic_write(path, canonical_json(review))
            try:
                for group, manifest in manifests.items():
                    root = path.parent / group
                    for ref in manifest["objects"].values():
                        target = self.pipeline.checkout / ref["path"]
                        raw = (root / ref["path"]).read_bytes()
                        if target.exists() and target.read_bytes() != raw:
                            raise PipelineError("Immutable consumer object collision")
                        if not target.exists():
                            atomic_write(target, raw)
                    atomic_write(self.pipeline.checkout / f"consumer/{group}/{manifest['releaseId']}.json", canonical_json(manifest))
                group_revision = self._commit(["consumer", "objects"], "Stage verified synthetic consumer objects")
                groups = {group: {"revision": group_revision, "manifestPath": f"consumer/{group}/{manifest['releaseId']}.json",
                                  "manifestSha256": review["groupManifestHashes"][group], "releaseId": manifest["releaseId"], "gameVersion": manifest["gameVersion"]}
                          for group, manifest in manifests.items()}
                document = {"schemaVersion": 1, "gameVersion": review["gameVersion"], "sourceBinding": SYNTHETIC_BINDING, "groups": groups, "worldgen": []}
                document["releaseSetId"] = sha256(canonical_json(document))
                atomic_write(self.pipeline.checkout / f"release-sets/{document['releaseSetId']}.json", canonical_json(document))
                set_revision = self._commit(["release-sets"], "Stage immutable synthetic complete resource set")
                pointer, commit = self._publish_pointer(document, set_revision, "advance", current)
                review.update(state="PUBLISHED", pointer=pointer, commit=commit)
            except Exception:
                review["state"] = "INTERRUPTED"
                atomic_write(path, canonical_json(review))
                raise
            atomic_write(path, canonical_json(review))
            return review

    def preview_rollback(self, approval_sequence: int) -> dict:
        self._enabled()
        if type(approval_sequence) is not int or approval_sequence < 1:
            raise PipelineError("Rollback must select an exact prior approved sequence")
        with self.pipeline.lock:
            current = self.current()
            if not current or approval_sequence >= current["approvalSequence"]:
                raise PipelineError("Rollback target must be a previous approved complete set")
            target = json.loads(self.pipeline.published_file(f"consumer-approvals/{approval_sequence}.json"))
            identity = str(uuid.uuid4())
            review = {"id": identity, "state": "READY_FOR_REVIEW", "kind": "consumer-set-rollback", "target": target,
                      "basePointerSha256": sha256(canonical_json(current))}
            review["reviewDigest"] = sha256(canonical_json(review))
            atomic_write(self._review_path(identity), canonical_json(review))
            return review

    def rollback(self, identity: str, review_digest: str, confirmed: bool) -> dict:
        self._enabled()
        with self.pipeline.lock:
            path = self._review_path(identity)
            review = read_json(path, 64 * 1024)
            unsigned = {key: value for key, value in review.items() if key != "reviewDigest"}
            if confirmed is not True or review.get("state") != "READY_FOR_REVIEW" or review.get("kind") != "consumer-set-rollback" or review_digest != review.get("reviewDigest") or sha256(canonical_json(unsigned)) != review_digest:
                raise PipelineError("Explicit approval of the exact rollback preview is required")
            current = self.current()
            if sha256(canonical_json(current)) != review["basePointerSha256"]:
                raise PipelineError("Rollback preview is stale")
            target = review["target"]
            if json.loads(self.pipeline.published_file(f"consumer-approvals/{target['approvalSequence']}.json")) != target:
                raise PipelineError("Rollback target is not publisher-approved history")
            document = json.loads(self.pipeline.published_file(f"release-sets/{target['releaseSetId']}.json"))
            if sha256(canonical_json(document)) != target["releaseSetSha256"]:
                raise PipelineError("Rollback manifest integrity failure")
            self._clean()
            pointer, commit = self._publish_pointer(document, target["revision"], "rollback", current)
            review.update(state="PUBLISHED", pointer=pointer, commit=commit)
            atomic_write(path, canonical_json(review))
            return review
