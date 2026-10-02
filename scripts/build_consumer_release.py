"""Pack four reviewed, private material inputs without publishing them.

Run with PYTHONPATH=src python scripts/build_consumer_release.py --help.
The output is private data: never commit/upload it to public CI.
"""
import argparse
from pathlib import Path
from resource_pipeline.consumer_release import build_consumer_release, MAX_RAW_BYTES
from resource_pipeline.security import PipelineError, canonical_json, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-version", required=True)
    parser.add_argument("--server-sha256", required=True)
    parser.add_argument("--client-tree-sha", required=True)
    parser.add_argument("--consumer-commit", required=True)
    parser.add_argument("--materials-base", type=Path, required=True)
    parser.add_argument("--materials-rules", type=Path, required=True)
    parser.add_argument("--pixel-catalog", type=Path, required=True)
    parser.add_argument("--pixel-rgb", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gzip", action="store_true")
    args = parser.parse_args()
    objects = {}
    for role, path in (("materials.base", args.materials_base), ("materials.rules", args.materials_rules),
                       ("pixel.catalog", args.pixel_catalog), ("pixel.rgb", args.pixel_rgb)):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_RAW_BYTES:
            raise PipelineError("Input is not a bounded regular file")
        objects[role] = path.read_bytes()
    manifest = build_consumer_release(args.output, game_version=args.game_version,
        source_binding={"serverSha256": args.server_sha256, "clientTreeSha": args.client_tree_sha,
                        "consumerCommit": args.consumer_commit}, objects=objects, compress=args.gzip)
    # Stats only: no names, private paths, or image payload in logs.
    print(canonical_json({"releaseId": manifest["releaseId"], "manifestSha256": sha256(canonical_json(manifest)),
                          "objects": len(manifest["objects"]), "publicationApproved": False,
                          "rawBytes": sum(r["rawBytes"] for r in manifest["objects"].values())}).decode())


if __name__ == "__main__":
    main()
