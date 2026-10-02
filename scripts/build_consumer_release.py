"""Pack one fixed, reviewed private resource group without publishing it.

Run with PYTHONPATH=src python scripts/build_consumer_release.py --help.
The output is private data: never commit/upload it to public CI.
"""
import argparse
from pathlib import Path
from resource_pipeline.consumer_release import build_consumer_release, MAX_RAW_BYTES, GROUPS
from resource_pipeline.security import PipelineError, canonical_json, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-version", required=True)
    parser.add_argument("--server-sha256", required=True)
    parser.add_argument("--client-tree-sha", required=True)
    parser.add_argument("--consumer-commit", required=True)
    parser.add_argument("--group", choices=sorted(GROUPS), default="materials")
    parser.add_argument("--object", action="append", default=[], metavar="ROLE=PATH")
    parser.add_argument("--materials-base", type=Path)
    parser.add_argument("--materials-rules", type=Path)
    parser.add_argument("--pixel-catalog", type=Path)
    parser.add_argument("--pixel-rgb", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gzip", action="store_true")
    args = parser.parse_args()
    legacy = (("materials.base", args.materials_base), ("materials.rules", args.materials_rules),
              ("pixel.catalog", args.pixel_catalog), ("pixel.rgb", args.pixel_rgb))
    paths = {}
    if args.object:
        if any(path is not None for _, path in legacy):
            parser.error("Do not mix --object and legacy material flags")
        for value in args.object:
            role, separator, path = value.partition("=")
            if not separator or not path or role in paths:
                parser.error("Each --object must be one unique ROLE=PATH")
            paths[role] = Path(path)
    else:
        if args.group != "materials" or any(path is None for _, path in legacy):
            parser.error("Supply every role with --object ROLE=PATH")
        paths = dict(legacy)
    if set(paths) != set(GROUPS[args.group][0]):
        parser.error("Inputs must exactly match the selected fixed group")
    objects = {}
    for role, path in paths.items():
        if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file() or path.stat().st_size > MAX_RAW_BYTES:
            raise PipelineError("Input is not a bounded regular file")
        objects[role] = path.read_bytes()
    manifest = build_consumer_release(args.output, game_version=args.game_version,
        source_binding={"serverSha256": args.server_sha256, "clientTreeSha": args.client_tree_sha,
                        "consumerCommit": args.consumer_commit}, objects=objects, compress=args.gzip, group=args.group)
    # Stats only: no names, private paths, or image payload in logs.
    print(canonical_json({"releaseId": manifest["releaseId"], "manifestSha256": sha256(canonical_json(manifest)),
                          "group": args.group, "objects": len(manifest["objects"]), "publicationApproved": False,
                          "rawBytes": sum(r["rawBytes"] for r in manifest["objects"].values())}).decode())


if __name__ == "__main__":
    main()
