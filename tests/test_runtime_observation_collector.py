"""Original collector source-contract tests; these do not compile or run C#."""
import json
from pathlib import Path
import re
import unittest

from resource_pipeline.runtime_item_adapter import OBSERVED_FIELDS
from resource_pipeline.item_assembler import GROUPS, POOLS

ROOT = Path(__file__).resolve().parents[1] / 'tools' / 'RuntimeObservationCollector'


class RuntimeObservationCollectorSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = json.loads((ROOT / 'fixed-profile.json').read_text())
        cls.program = (ROOT / 'Program.cs').read_text()
        cls.collector = (ROOT / 'FixedCollector.cs').read_text()

    def test_fixed_named_fields_cover_consumer_without_derived_defaults(self):
        fields = self.profile['fields']
        self.assertEqual({f['name'] for f in fields if f['key'].startswith('item.')}, set(OBSERVED_FIELDS))
        self.assertEqual(len(OBSERVED_FIELDS), 47)
        self.assertEqual({f['name'] for f in fields if f['key'].startswith('groups.')}, set(GROUPS))
        self.assertEqual({f['name'] for f in fields if f['key'].startswith('pools.')}, set(POOLS))
        self.assertEqual(len([f for f in fields if f['key'].startswith('priorities.')]), 20)
        self.assertTrue(all(f['signatureHex'] == '061d08' for f in fields if f['key'].startswith('priorities.')))

    def test_manifest_and_compiled_descriptors_agree(self):
        generated = (ROOT / 'FixedProfile.cs').read_text()
        for pin in self.profile['methods']:
            self.assertIn(f'new MethodPin("{pin["key"]}", "{pin["owner"]}", "{pin["name"]}", 0x{pin["token"]:08x}, "{pin["signatureSha256"]}", "{pin["ilSha256"]}")', generated)
        for pin in self.profile['fields']:
            self.assertIn(f'new FieldPin("{pin["key"]}", "{pin["owner"]}", "{pin["name"]}", 0x{pin["token"]:08x}, "{pin["signatureHex"]}")', generated)
        for pins in (self.profile['methods'], self.profile['fields']):
            self.assertEqual(len(pins), len({p['key'] for p in pins}))
            self.assertEqual(len(pins), len({p['token'] for p in pins}))

    def test_only_fixed_root_keys_and_no_generic_invocation(self):
        allowed = {p['key'] for p in self.profile['methods']}
        used = set(re.findall(r'(?:Call|Stage|Cctor)\("([A-Za-z]+)"', self.collector))
        self.assertTrue(used <= allowed)
        self.assertNotIn('BoringSetup', {p['name'] for p in self.profile['methods']})
        self.assertNotIn('Initialize_AlmostEverything', {p['name'] for p in self.profile['methods']})
        self.assertNotIn('Terraria.Netplay', {p['owner'] for p in self.profile['methods']})
        self.assertNotIn('Assembly.Load', self.program)
        self.assertNotIn('GetMethod(', self.collector)
        self.assertIn('Object.ReferenceEquals(a,item.Key)', self.collector)
        self.assertIn('finally { pendingMemory.Remove(name); }', self.collector)
        self.assertIn('CheckAssembly(assembly);', self.collector)
        self.assertNotIn('permittedMemory', self.collector)
        self.assertNotIn('BinaryFormatter', self.collector)

    def test_self_test_branch_precedes_all_runtime_gates(self):
        branch = self.program.index('if (args.Length==1 && args[0]=="--self-test")')
        self.assertLess(branch, self.program.index('WindowsJob.Install()'))
        self.assertLess(branch, self.program.index('string input=LocalPath'))
        self.assertLess(branch, self.program.index('new FixedCollector'))
        build = (ROOT / 'Build.ps1').read_text()
        self.assertIn('/platform:x86', build)
        self.assertIn('--self-test', build)
        self.assertNotIn('--observe-pinned-client', build)
        self.assertNotIn('Terraria.exe', build)

    def test_partial_only_and_explicit_missing_join_semantics(self):
        self.assertFalse(self.profile['initializationVerified'])
        self.assertFalse(self.profile['complete'])
        self.assertFalse(self.profile['publishable'])
        for label in ('isolationVerified', 'initializationVerified', 'sourceSemanticsVerified', 'complete', 'publishable'):
            self.assertIn(f'"{label}",false', self.collector)
        for label in ('persistentIdPresent', 'resolvedType', 'researchPresent', 'priority-override-provenance'):
            self.assertIn(label, self.collector)

    def test_context_and_float_precision_are_explicit(self):
        for text in ('"zh-Hans"', '"infectedSeed"', '"UNEXPECTED_ITEM_VARIANT"',
                     '"UNEXPECTED_DIFFICULTY_OVERRIDE"', '"Microsoft.VisualC"', '"10.0.0.0"'):
            self.assertIn(text, self.collector)
        self.assertIn('((double)n).ToString("R", CultureInfo.InvariantCulture)', (ROOT / "PrimitiveJson.cs").read_text())
        self.assertIn("1.2000000476837158", (ROOT / "SelfTest.cs").read_text())
        self.assertNotIn("source_closure_audit", (ROOT / "audit_fixed_profile.py").read_text())

    def test_resource_bounds_and_primitive_only_serialization(self):
        serializer = (ROOT / 'PrimitiveJson.cs').read_text()
        self.assertIn('64 * 1024 * 1024', serializer)
        self.assertIn('UNAPPROVED_JSON_TYPE', serializer)
        self.assertIn('NONFINITE_NUMBER', serializer)
        self.assertIn('type == typeof(SortedDictionary<string, object>)', serializer)
        self.assertNotIn('GetProperties', serializer)
        job = (ROOT / 'WindowsJob.cs').read_text()
        self.assertIn('ActiveProcessLimit = 1', job)
        self.assertIn('120000', job)
        self.assertIn('process.Threads.Count > 64', job)
        self.assertIn('OUTPUT_MUST_NOT_EXIST', self.program)
        self.assertIn('IsLocalDriveAbsolute(path)', self.program)
        self.assertIn('observedNames.SetEquals(names)', self.program)
        self.assertIn('OUTPUT_CANNOT_BE_INSIDE_INPUT', self.program)
        self.assertLess(self.program.index('file.Flush(true)'), self.program.index('File.Move(pending,path)'))
        self.assertIn('INPUT_HASH_MISMATCH', self.program)
        self.assertIn('NATIVE_BINDING_HASH_MISMATCH', self.collector)


if __name__ == '__main__':
    unittest.main()
