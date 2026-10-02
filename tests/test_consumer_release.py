from copy import deepcopy
import gzip
import json
import tempfile
from pathlib import Path
import unittest

from resource_pipeline.consumer_release import build_consumer_release, validate_manifest, manifest_body, ROLES, verify_consumer_release
from resource_pipeline.security import PipelineError, canonical_json, sha256

BINDING = {"serverSha256": "a" * 64, "clientTreeSha": "b" * 40, "consumerCommit": "c" * 40}


def fixture():
    # Original synthetic records. Never copied from a game/client/private repo.
    return {
        "materials.base": canonical_json({"tiles": [[0, "Synthetic tile", "#123456", "Tile"]], "walls": [[1, "Synthetic wall", "#654321", "Wall"]], "paints": []}),
        "materials.rules": canonical_json({"materials": [], "variants": [], "shapes": [], "frameImportant": [False]}),
        "pixel.catalog": canonical_json({"tileIds": [0], "wallIds": [1], "paintIds": [], "algorithm": "synthetic-only"}),
        "pixel.rgb": b"SYNTHETIC RGB INDEX\x00\x01",
    }


class ConsumerReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def build(self, name="out", **kwargs):
        return build_consumer_release(self.root / name, game_version="0.0.1", source_binding=BINDING, objects=fixture(), **kwargs)

    def resign(self, manifest):
        manifest["releaseId"] = sha256(canonical_json(manifest_body(manifest)))

    def test_deterministic_complete_atomic_release(self):
        a, b = self.build("a"), self.build("b")
        self.assertEqual(a, b)
        self.assertEqual(set(a["objects"]), set(ROLES))
        self.assertEqual((self.root / "a/manifest.json").read_bytes(), canonical_json(a))
        for role, ref in a["objects"].items():
            raw = (self.root / "a" / ref["path"]).read_bytes()
            self.assertEqual(raw, fixture()[role])
            self.assertEqual(sha256(raw), ref["sha256"])
            self.assertEqual(ref["gameVersion"], a["gameVersion"])
            if role != "materials.base":
                self.assertEqual(ref["baseSha256"], a["objects"]["materials.base"]["rawSha256"])

    def test_gzip_retains_raw_hashes(self):
        a = self.build(compress=True)
        for role, ref in a["objects"].items():
            stored = (self.root / "out" / ref["path"]).read_bytes()
            self.assertEqual(gzip.decompress(stored), fixture()[role])
            self.assertEqual(sha256(stored), ref["sha256"])
            self.assertEqual(sha256(gzip.decompress(stored)), ref["rawSha256"])

    def test_invalid_manifests_fail_even_if_rehashed(self):
        initial = self.build()
        mutations = [
            lambda m: m.update(extra=True),
            lambda m: m.update(schemaVersion=True),
            lambda m: m["objects"].pop("pixel.rgb"),
            lambda m: m["sourceBinding"].update(consumerCommit="main"),
            lambda m: m["objects"]["pixel.rgb"].update(baseSha256="d" * 64),
            lambda m: m["objects"]["pixel.rgb"].update(gameVersion="0.0.2"),
            lambda m: m["objects"]["pixel.rgb"].update(path="../bad"),
            lambda m: m["objects"]["pixel.rgb"].update(bytes=True),
            lambda m: m["objects"]["pixel.rgb"].update(rawBytes=0),
            lambda m: m["objects"]["pixel.rgb"].update(rawBytes=33 * 1024 * 1024),
            lambda m: m["objects"]["pixel.rgb"].update(format="json"),
            lambda m: m["objects"]["pixel.rgb"].update(encoding="br"),
            lambda m: m["objects"]["materials.base"].update(baseSha256="a" * 64),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                changed = deepcopy(initial)
                mutation(changed)
                self.resign(changed)
                with self.assertRaises(PipelineError):
                    validate_manifest(changed)

    def test_manifest_hash_detects_changes(self):
        manifest = self.build()
        manifest["gameVersion"] = "0.0.2"
        with self.assertRaises(PipelineError):
            validate_manifest(manifest)

    def test_invalid_inputs_do_not_write_output(self):
        for raw in (b"{bad", b'{"a":NaN}', b'{"a":1,"a":2}', b"\xff"):
            objects = fixture()
            objects["materials.base"] = raw
            with self.assertRaises(PipelineError):
                build_consumer_release(self.root / "bad", game_version="0.0.1", source_binding=BINDING, objects=objects)
            self.assertFalse((self.root / "bad").exists())

    def test_existing_output_never_overwritten(self):
        self.build()
        with self.assertRaises(PipelineError):
            self.build()

    def test_reverify_both_encodings_and_tamper(self):
        for compress in (False, True):
            name = "compressed" if compress else "identity"
            manifest = self.build(name, compress=compress)
            pin = sha256(canonical_json(manifest))
            self.assertEqual(verify_consumer_release(self.root / name, manifest_sha256=pin), fixture())
            ref = manifest["objects"]["pixel.rgb"]
            path = self.root / name / ref["path"]
            path.write_bytes(b"x" * ref["bytes"])
            with self.assertRaises(PipelineError):
                verify_consumer_release(self.root / name, manifest_sha256=pin)

    def test_pin_mismatch_and_symlink_rejected(self):
        manifest = self.build()
        with self.assertRaises(PipelineError):
            verify_consumer_release(self.root / "out", manifest_sha256="0" * 64)
        ref = manifest["objects"]["pixel.rgb"]
        path = self.root / "out" / ref["path"]
        raw = path.read_bytes()
        path.unlink()
        alternate = self.root / "elsewhere.bin"
        alternate.write_bytes(raw)
        path.symlink_to(alternate)
        with self.assertRaises(PipelineError):
            verify_consumer_release(self.root / "out", manifest_sha256=sha256(canonical_json(manifest)))

    def test_gzip_truncation_and_trailing_members_rejected(self):
        for case in ("truncated", "trailing-empty", "trailing-data", "over-expansion"):
            manifest = self.build(case, compress=True)
            ref = manifest["objects"]["pixel.rgb"]
            path = self.root / case / ref["path"]
            stored = path.read_bytes()
            if case == "truncated":
                stored = stored[:-1]
            elif case == "trailing-empty":
                stored += gzip.compress(b"")
            elif case == "trailing-data":
                stored += b"trailer"
            else:
                stored = gzip.compress(fixture()["pixel.rgb"] + b"x")
            ref["sha256"] = sha256(stored)
            ref["bytes"] = len(stored)
            ref["path"] = f'objects/{ref["sha256"][:2]}/{ref["sha256"]}.bin.gz'
            target = self.root / case / ref["path"]
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(stored)
            self.resign(manifest)
            (self.root / case / "manifest.json").write_bytes(canonical_json(manifest))
            with self.subTest(case=case), self.assertRaises(PipelineError):
                verify_consumer_release(self.root / case, manifest_sha256=sha256(canonical_json(manifest)))

    def test_duplicate_manifest_keys_rejected(self):
        manifest = self.build()
        raw = canonical_json(manifest)
        raw = b'{"schemaVersion":1,' + raw[1:]
        (self.root / "out/manifest.json").write_bytes(raw)
        with self.assertRaises(PipelineError):
            verify_consumer_release(self.root / "out", manifest_sha256=sha256(raw))

    def test_parent_root_links_rejected(self):
        manifest = self.build()
        link = self.root / "linked"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(PipelineError):
            verify_consumer_release(link / "out", manifest_sha256=sha256(canonical_json(manifest)))
        with self.assertRaises(PipelineError):
            build_consumer_release(link / "new", game_version="0.0.1", source_binding=BINDING, objects=fixture())
        self.assertFalse((self.root / "new").exists())

    def test_aggregate_budget_rejected(self):
        manifest = self.build(compress=True)
        for ref in manifest["objects"].values():
            ref["rawBytes"] = 17 * 1024 * 1024
        self.resign(manifest)
        with self.assertRaisesRegex(PipelineError, "aggregate"):
            validate_manifest(manifest)

    def test_other_fixed_groups_roundtrip_without_weakening_materials(self):
        examples = {
            "items": {"items.catalog": b"[[],[],[],[],[],[],[]]", "items.rules": b"{}", "items.categories": b"{}"},
            "player": {"player.presentation": b"{}", "player.walk": b"synthetic-walk", "player.atlas": b"synthetic-atlas"},
            "worldgen": {"worldgen.choices": b"{}"},
            "markers": {"markers.catalog": b"{}", "markers.images": b"synthetic-markers"},
        }
        for group, objects in examples.items():
            with self.subTest(group=group):
                output = self.root / group
                manifest = build_consumer_release(output, game_version="0.0.1", source_binding=BINDING, objects=objects, group=group, compress=True)
                pin = sha256(canonical_json(manifest))
                self.assertEqual(verify_consumer_release(output, manifest_sha256=pin, group=group), objects)
                with self.assertRaises(PipelineError):
                    validate_manifest(manifest)
                with self.assertRaises(PipelineError):
                    build_consumer_release(self.root / (group + "-missing"), game_version="0.0.1", source_binding=BINDING, objects={}, group=group)
        world = json.loads((self.root / "worldgen/manifest.json").read_bytes())
        self.assertNotIn("baseSha256", world["objects"]["worldgen.choices"])

    def test_unknown_groups_and_role_mixing_rejected(self):
        with self.assertRaises(PipelineError):
            build_consumer_release(self.root / "bad", game_version="0.0.1", source_binding=BINDING, objects=fixture(), group="upload-defined")
        with self.assertRaises(PipelineError):
            build_consumer_release(self.root / "bad", game_version="0.0.1", source_binding=BINDING, objects=fixture(), group="items")

    def test_cannot_enter_trusted_publication_gate(self):
        from resource_pipeline.catalog import trusted_synthetic_policy
        self.build()
        with self.assertRaises(PipelineError):
            trusted_synthetic_policy("1.4.5.8", "consumer-release", "export-bundle")

if __name__ == "__main__":
    unittest.main()
