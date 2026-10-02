from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from resource_pipeline.adapters import (AdapterLimits, AdapterRegistry, AdapterSpec, BubblewrapSandbox,
    CommandPlan, SourceInputs, TrustedAdapterRunner, tree_digest, validate_normalized_package)
from resource_pipeline.adapters_synthetic import (SYNTHETIC_SERVER, synthetic_profile,
                                                  write_synthetic_package)
from resource_pipeline.contracts import (CLAIM_KEYS, FieldSchema, InputBinding, validate_contract)
from resource_pipeline.security import PipelineError, atomic_write, canonical_json, sha256


class FakeToolTestBackend:
    """Test-only injection: runs our tiny fixture producer, NOT an OS sandbox.

    Production has no such backend. These tests prove orchestration and failure
    paths without claiming network/filesystem isolation was established.
    """
    def plan(self, spec, job):
        return CommandPlan((sys.executable, "-I", "-S", str(job / "tool/fake.py"),
                            "--request", str(job / "inputs/request.json"), "--output", str(job / "output")),
                           job / "work", {"PATH": "/usr/bin:/bin"}, "TEST-ONLY-NO-OS-ISOLATION")


FAKE_TOOL = '''import argparse,json,pathlib,shutil
p=argparse.ArgumentParser();p.add_argument('--request');p.add_argument('--output');a=p.parse_args()
r=json.loads(pathlib.Path(a.request).read_text());out=pathlib.Path(a.output)
shutil.copytree(pathlib.Path(__file__).parent/'fixture',out,dirs_exist_ok=True,copy_function=shutil.copyfile)
c=json.loads((out/'coverage.json').read_text());c['binding']=r['binding'];(out/'coverage.json').write_text(json.dumps(c))
b=json.loads((out/'resource-bundle.json').read_text());b['adapter']=r['binding']['adapter_id']
b['sources']=[{'role':k,'game_version':r['binding']['game_version'],'sha256':r['binding'][k+'_sha256']} for k in ('server','client','metadata') if r['binding'][k+'_sha256'] is not None]
(out/'resource-bundle.json').write_text(json.dumps(b))
'''


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.binding, self.profile = write_synthetic_package(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def coverage(self):
        return json.loads((self.root / "coverage.json").read_text())

    def save_coverage(self, document):
        atomic_write(self.root / "coverage.json", canonical_json(document))

    def edit_rows(self, key, edit):
        document = self.coverage()
        claim = next(item for item in document["claims"] if item["key"] == key)
        target = self.root / claim["evidence"]["path"]
        rows = json.loads(target.read_text())
        edit(rows)
        content = canonical_json(rows)
        target.write_bytes(content)
        claim["evidence"]["sha256"] = sha256(content)
        self.save_coverage(document)

    def test_all_110_subclaims_verified_with_three_policy_exclusions(self):
        result = validate_normalized_package(self.root, self.binding, self.profile)
        self.assertEqual(110, len(CLAIM_KEYS))
        self.assertEqual(110, result["submanifestCount"])
        self.assertTrue(result["complete"])
        self.assertEqual(107, result["verifiedRequired"])
        self.assertEqual(3, sum(item["status"] == "not-applicable" for item in result["subclaims"]))

    def test_nonempty_family_does_not_mask_missing_subclaim(self):
        document = self.coverage()
        document["claims"] = [item for item in document["claims"] if item["key"] != "items.tooltip.templates"]
        self.save_coverage(document)
        # A required FK to a missing table is a hard failure, not a fallback.
        with self.assertRaisesRegex(PipelineError, "foreign key"):
            validate_contract(self.root, self.binding, self.profile)

    def test_missing_row_reports_exact_id_and_blocks_complete(self):
        self.edit_rows("walls.traits", lambda rows: rows.clear())
        result = validate_contract(self.root, self.binding, self.profile)
        self.assertFalse(result["complete"])
        row = next(item for item in result["subclaims"] if item["key"] == "walls.traits")
        self.assertEqual([1], row["missingIds"])

    def test_upload_cannot_self_exempt_required_coverage(self):
        document = self.coverage()
        document["claims"][0] = {"key": "items.identity", "status": "not-applicable", "reason": "user says optional"}
        self.save_coverage(document)
        with self.assertRaisesRegex(PipelineError, "trusted profile"):
            validate_contract(self.root, self.binding, self.profile)

    def test_upload_cannot_forge_optional_exemption_reason(self):
        document = self.coverage()
        next(item for item in document["claims"] if item["key"] == "pixel.optional-gallery")["reason"] = "different policy"
        self.save_coverage(document)
        with self.assertRaises(PipelineError):
            validate_contract(self.root, self.binding, self.profile)

    def test_field_schema_is_checked_not_just_count(self):
        self.edit_rows("items.attributes.defaults", lambda rows: rows[0]["defaults"].update(syntheticPower="wrong"))
        with self.assertRaisesRegex(PipelineError, "type"):
            validate_contract(self.root, self.binding, self.profile)

    def test_unknown_field_and_duplicate_id_fail(self):
        self.edit_rows("walls.traits", lambda rows: rows.append(dict(rows[0])))
        with self.assertRaisesRegex(PipelineError, "Duplicate"):
            validate_contract(self.root, self.binding, self.profile)

    def test_signed_and_string_ids_are_distinct(self):
        self.edit_rows("walls.traits", lambda rows: rows[0].update(id="1"))
        with self.assertRaisesRegex(PipelineError, "outside"):
            validate_contract(self.root, self.binding, self.profile)

    def test_foreign_key_must_resolve_to_real_evidence_row(self):
        self.edit_rows("items.tooltip.resolved-baseline", lambda rows: rows[0].update(template_id=999))
        with self.assertRaisesRegex(PipelineError, "foreign key"):
            validate_contract(self.root, self.binding, self.profile)

    def test_missing_image_fails_closed(self):
        (self.root / "images/synthetic.png").unlink()
        with self.assertRaisesRegex(PipelineError, "Missing"):
            validate_normalized_package(self.root, self.binding, self.profile)

    def test_image_bytes_and_dimensions_are_verified(self):
        self.edit_rows("items.images.originals", lambda rows: rows[0]["image"].update(width=123))
        with self.assertRaisesRegex(PipelineError, "dimensions"):
            validate_contract(self.root, self.binding, self.profile)

    def test_evidence_hash_is_verified(self):
        (self.root / "evidence/walls.traits.json").write_text("[]")
        with self.assertRaisesRegex(PipelineError, "hash"):
            validate_contract(self.root, self.binding, self.profile)

    def test_thumbnail_origin_cannot_certify_original(self):
        document = self.coverage()
        next(item for item in document["claims"] if item["key"] == "items.images.originals")["origin_kind"] = "ui-atlas-crop"
        self.save_coverage(document)
        with self.assertRaisesRegex(PipelineError, "provenance"):
            validate_contract(self.root, self.binding, self.profile)

    def test_different_metadata_or_tool_binding_is_rejected(self):
        for field in ("metadata_sha256", "adapter_tool_sha256", "client_sha256"):
            with self.subTest(field=field), self.assertRaisesRegex(PipelineError, "binding mismatch"):
                validate_contract(self.root, self.binding.model_copy(update={field: "f" * 64}), self.profile)

    def test_symlink_parent_is_rejected(self):
        (self.root / "evidence").rename(self.root / "real-evidence")
        (self.root / "evidence").symlink_to("real-evidence", target_is_directory=True)
        with self.assertRaisesRegex(PipelineError, "links"):
            validate_contract(self.root, self.binding, self.profile)

    def test_static_native_ids_cannot_claim_real_complete(self):
        with self.assertRaisesRegex(PipelineError, "Synthetic"):
            replace(self.profile, source_kind="export-bundle")
        with self.assertRaisesRegex(PipelineError, "110"):
            replace(self.profile, claims=self.profile.claims[:1])


class AdapterRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = self.root / "server.txt"
        self.server.write_bytes(SYNTHETIC_SERVER)
        self.tool = self.root / "installed-tool"
        self.tool.mkdir()
        (self.tool / "fake.py").write_text(FAKE_TOOL)
        write_synthetic_package(self.tool / "fixture")
        self.spec = AdapterSpec("test-installed-producer", "1", synthetic_profile(), self.tool, tree_digest(self.tool),
                                ("/usr/bin/python3", "-B", "/tool/fake.py"), frozenset({sha256(SYNTHETIC_SERVER)}),
                                requires_client=False, requires_metadata=False)
        self.registry = AdapterRegistry()
        self.registry.register(self.spec)
        self.runner = TrustedAdapterRunner(self.registry, FakeToolTestBackend())
        self.sources = SourceInputs("0.0.1", self.server)

    def tearDown(self):
        self.temp.cleanup()

    def test_default_registry_rejects_raw_game(self):
        with self.assertRaisesRegex(PipelineError, "raw game execution is disabled"):
            AdapterRegistry().get("uploaded-command.exe")

    def test_fake_trusted_tool_end_to_end_and_originals_unchanged(self):
        before = tree_digest(self.tool)
        result = self.runner.run(self.spec.adapter_id, self.sources, self.root / "job")
        self.assertTrue(result.coverage["complete"])
        self.assertEqual(110, result.coverage["submanifestCount"])
        self.assertEqual("TEST-ONLY-NO-OS-ISOLATION", result.isolation)
        self.assertEqual(before, tree_digest(self.tool))
        self.assertEqual(SYNTHETIC_SERVER, self.server.read_bytes())
        self.assertEqual(result.output_sha256, tree_digest(result.package_root))
        self.assertTrue((self.root / "job/result.json").is_file())

    def test_unknown_version_and_mismatched_actual_server_bytes_fail(self):
        with self.assertRaisesRegex(PipelineError, "version"):
            self.runner.run(self.spec.adapter_id, replace(self.sources, game_version="9.9.9"), self.root / "bad")
        self.server.write_bytes(b"different bytes")
        with self.assertRaisesRegex(PipelineError, "Server input fingerprint"):
            self.runner.run(self.spec.adapter_id, self.sources, self.root / "bad")

    def test_tool_drift_fails_before_execution(self):
        (self.tool / "fake.py").write_text("raise Exception('must never run')")
        with self.assertRaisesRegex(PipelineError, "Installed adapter fingerprint"):
            self.runner.run(self.spec.adapter_id, self.sources, self.root / "bad")

    def test_fresh_output_is_required(self):
        job = self.root / "job"
        job.mkdir()
        with self.assertRaisesRegex(PipelineError, "must be new"):
            self.runner.run(self.spec.adapter_id, self.sources, job)

    def test_missing_os_sandbox_never_falls_back_to_host(self):
        runner = TrustedAdapterRunner(self.registry, BubblewrapSandbox(self.root / "missing-bwrap"))
        with self.assertRaisesRegex(PipelineError, "sandbox is unavailable"):
            runner.run(self.spec.adapter_id, self.sources, self.root / "job")
        self.assertFalse((self.root / "job/output/resource-bundle.json").exists())

    def test_command_plan_has_readonly_roots_no_network_and_no_host_secret_env(self):
        job = self.root / "job"
        plan = BubblewrapSandbox(Path(sys.executable)).plan(self.spec, job)
        self.assertIn("--unshare-all", plan.argv)
        self.assertIn("--clearenv", plan.argv)
        self.assertIn("--ro-bind", plan.argv)
        self.assertNotIn("--share-net", plan.argv)
        self.assertNotIn("HOME", plan.env)
        self.assertNotIn("/root", plan.argv)
        self.assertIn("--request", plan.argv)
        self.assertNotIn("--tmpfs", plan.argv)
        self.assertIn(str(job / "work/tmp"), plan.argv)
        self.assertIn("--remount-ro", plan.argv)

    def test_consumer_catalog_cannot_differ_from_proven_domain(self):
        output = self.root / "normalized"
        binding, profile = write_synthetic_package(output)
        bundle = json.loads((output / "resource-bundle.json").read_text())
        bundle["families"]["items"][0]["id"] = 999
        (output / "resource-bundle.json").write_bytes(canonical_json(bundle))
        with self.assertRaisesRegex(PipelineError, "catalog IDs"):
            validate_normalized_package(output, binding, profile)

    def new_runner(self, code, limits=AdapterLimits()):
        (self.tool / "fake.py").write_text(code)
        registry = AdapterRegistry()
        spec = replace(self.spec, tool_sha256=tree_digest(self.tool), limits=limits)
        registry.register(spec)
        return TrustedAdapterRunner(registry, FakeToolTestBackend())

    def test_timeout_is_bounded_and_no_output_approved(self):
        runner = self.new_runner("import time; time.sleep(20)", replace(AdapterLimits(), timeout_seconds=0.15))
        with self.assertRaisesRegex(PipelineError, "timed out"):
            runner.run(self.spec.adapter_id, self.sources, self.root / "job")
        self.assertFalse((self.root / "job/result.json").exists())
        self.assertEqual("BLOCKED", json.loads((self.root / "job/status.json").read_text())["state"])

    def test_tool_error_is_redacted(self):
        runner = self.new_runner("import sys; print('SECRET_PRIVATE_PATH_TOKEN',file=sys.stderr);sys.exit(12)")
        with self.assertRaises(PipelineError) as error:
            runner.run(self.spec.adapter_id, self.sources, self.root / "job")
        self.assertNotIn("SECRET", str(error.exception))
        self.assertNotIn(str(self.root), str(error.exception))

    def test_output_capacity_is_bounded(self):
        code = "import pathlib; pathlib.Path('../output/huge').write_bytes(b'x' * 300000)"
        # The whole pinned tool fixture is larger than a very small test quota,
        # so keep the per-file budget high and set the writable-tree total here.
        runner = self.new_runner(code, replace(AdapterLimits(), total_bytes=150000))
        with self.assertRaisesRegex(PipelineError, "capacity"):
            runner.run(self.spec.adapter_id, self.sources, self.root / "job")

    def test_metadata_must_bind_actual_server_and_client(self):
        client = self.root / "client"
        client.mkdir()
        (client / "original.txt").write_text("synthetic client input")
        metadata = self.root / "metadata"
        metadata.mkdir()
        (metadata / "source-binding.json").write_bytes(canonical_json({"game_version": "0.0.1", "server_sha256": "f" * 64,
                                                                      "client_sha256": tree_digest(client)}))
        registry = AdapterRegistry()
        registry.register(replace(self.spec, requires_client=True, requires_metadata=True,
                                  approved_client_sha256=frozenset({tree_digest(client)}),
                                  approved_metadata_sha256=frozenset({tree_digest(metadata)})))
        runner = TrustedAdapterRunner(registry, FakeToolTestBackend())
        with self.assertRaisesRegex(PipelineError, "different server/client"):
            runner.run(self.spec.adapter_id, SourceInputs("0.0.1", self.server, client, metadata), self.root / "job")


if __name__ == "__main__":
    unittest.main()
