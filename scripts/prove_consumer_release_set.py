#!/usr/bin/env python3
"""Create ONLY original synthetic proof in a fresh local bare-Git simulator.

No network, credentials or real asset publishing. The companion client test can
read this output to verify the actual Python/Git -> JS transport boundary.
"""
import argparse
import json
from pathlib import Path
from resource_pipeline.consumer_control import ConsumerReleaseControl, SYNTHETIC_BINDING
from resource_pipeline.pipeline import Pipeline
from resource_pipeline.security import atomic_write, canonical_json

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
if args.output.exists():
    parser.error("--output must be a new directory")
pipeline = Pipeline(args.output)
control = ConsumerReleaseControl(pipeline, publisher_id="synthetic-local-demo", synthetic_demo=True)
pointers = []
for revision in (1, 2):
    review = control.preview_demo(revision)
    published = control.publish(review["id"], review["reviewDigest"], True)
    pointers.append(published["pointer"])
review = control.preview_rollback(1)
pointers.append(control.rollback(review["id"], review["reviewDigest"], True)["pointer"])
proof = {"schemaVersion": 1, "syntheticOnly": True, "remote": str(pipeline.remote), "pointers": pointers,
         "trust": {"repository": "Live-yan/terraviewer-images", "publisherId": "synthetic-local-demo",
                   "consumerCommit": SYNTHETIC_BINDING["consumerCommit"], "minimumSequence": 1}}
atomic_write(args.output / "proof.json", canonical_json(proof))
print(json.dumps({"proof": str(args.output / "proof.json"), "approvalSequences": [p["approvalSequence"] for p in pointers], "syntheticOnly": True}))
