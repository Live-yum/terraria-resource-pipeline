"""Synthetic client evidence only; no proprietary fixtures or execution."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from resource_pipeline.adapters import tree_inventory
from resource_pipeline.client_metadata import extract_client_metadata, compare_metadata
from resource_pipeline.real_producer import RawEvidenceProducer
from resource_pipeline.security import PipelineError
from resource_pipeline.server_semantics import SemanticLimits, _Metadata
from test_server_semantics import synthetic_pe


class ClientMetadataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.client = self.root / 'client'
        self.client.mkdir()
        self.binary = self.client / 'Content' / 'Terraria.exe'
        self.binary.parent.mkdir()
        self.data = synthetic_pe()
        self.binary.write_bytes(self.data)
        self.output = self.root / 'evidence'

    def produce(self, server=False, **options):
        sources = {'client': self.client}
        if server:
            sources['server'] = self.root / 'server'
            sources['server'].mkdir()
            (sources['server'] / 'TerrariaServer.exe').write_bytes(self.data)
        return RawEvidenceProducer().produce(sources, {}, self.output, **options)

    def test_client_only_has_hash_bound_constants_and_raw_locales(self):
        result = self.produce()
        row = result['clientMetadata'][0]
        self.assertEqual('client', row['sourceRole'])
        self.assertEqual(hashlib.sha256(self.data).hexdigest(), row['sha256'])
        evidence = json.loads((self.output / row['path']).read_text())
        self.assertEqual(len(self.data), evidence['input']['bytes'])
        self.assertEqual(1, evidence['ids']['ItemID']['Example'])
        self.assertEqual(3, evidence['localeDiagnostics']['keyCounts']['zh-Hans'])
        self.assertEqual('DECLARED_IN_ASSEMBLY', evidence['gameVersionEvidence']['status'])
        self.assertFalse(evidence['gameVersionEvidence']['trusted'])
        self.assertNotIn('itemDefaultStages', evidence)
        self.assertNotIn('localeMappingEvidence', evidence)
        self.assertEqual([], result['serverMetadata'])
        self.assertEqual([], result['researchEvidence'])
        self.assertEqual([], result['localeMappingEvidence'])
        self.assertFalse(result['publishable'])
        self.assertFalse(result['extractionComplete'])

    def test_matching_server_client_declarations_never_become_trusted(self):
        result = self.produce(server=True)
        comparison = result['serverClientComparisons'][0]
        self.assertTrue(comparison['declaredVersionsMatch'])
        self.assertFalse(comparison['trusted'])
        self.assertFalse(comparison['installationProvenanceVerified'])
        self.assertTrue(comparison['idDomains']['ItemID']['match'])
        self.assertTrue(all(row['match'] for row in comparison['localeResourceHashes'].values()))
        self.assertFalse(result['publishable'])
        self.assertFalse(result['extractionComplete'])

    def test_version_mismatch_retains_partial_evidence_and_explicit_blocker(self):
        data = bytearray(self.data)
        _, offset = _Metadata(self.data, SemanticLimits()).row(32, 1)
        struct.pack_into('<H', data, offset + 4, 9)
        self.binary.write_bytes(data)
        result = self.produce(server=True)
        self.assertFalse(result['serverClientComparisons'][0]['declaredVersionsMatch'])
        self.assertIn('SERVER_CLIENT_DECLARED_VERSION_MISMATCH', result['blockers'])
        self.assertFalse(result['publishable'])
        self.assertFalse(result['extractionComplete'])

    def test_lfs_pointer_rejected_as_metadata(self):
        self.binary.write_bytes(b'version https://git-lfs.github.com/spec/v1\noid sha256:' + b'0' * 64 + b'\nsize 2048\n')
        with self.assertRaisesRegex(PipelineError, 'LFS pointer'):
            extract_client_metadata(self.binary)
        result = self.produce()
        self.assertEqual([], result['clientMetadata'])
        self.assertEqual('STATIC_METADATA_REJECTED', result['rejectedClientMetadata'][0]['code'])
        self.assertFalse(result['publishable'])

    def test_inventory_hash_and_size_tamper_fail_closed(self):
        for field, value in [('sha256', '0' * 64), ('bytes', len(self.data) + 1)]:
            with self.subTest(field=field):
                expected = {'client': tree_inventory(self.client)}
                expected['client'][0][field] = value
                target = self.root / field
                with self.assertRaises(PipelineError):
                    RawEvidenceProducer().produce({'client': self.client}, {}, target,
                                                   expected_inventories=expected)
                self.assertFalse((target / 'version-adapter-manifest.json').exists())

    def test_parser_cannot_dispatch_server_models_or_execute_input(self):
        with patch('subprocess.Popen', side_effect=AssertionError('must not execute')), \
             patch('os.system', side_effect=AssertionError('must not execute')), \
             patch('resource_pipeline.server_semantics.extract_server_semantics', side_effect=AssertionError('no server model')), \
             patch('resource_pipeline.server_semantics._item_evidence', side_effect=AssertionError('no item model')), \
             patch('resource_pipeline.server_semantics.embedded_baselines', side_effect=AssertionError('no locale model')):
            result = self.produce()
        self.assertEqual(1, len(result['clientMetadata']))
        self.assertFalse(result['executedInput'])

    def test_malformed_assembly_name_is_controlled_rejection(self):
        data = bytearray(self.data)
        index = data.index(b'SyntheticServer')
        data[index] = 0xff
        self.binary.write_bytes(data)
        with self.assertRaisesRegex(PipelineError, 'Malformed or unsupported'):
            extract_client_metadata(self.binary)
        result = self.produce()
        self.assertEqual([], result['clientMetadata'])
        self.assertEqual(1, len(result['rejectedClientMetadata']))

    def test_client_output_and_input_limits_are_enforced(self):
        for limits in (replace(SemanticLimits(), input_bytes=100), replace(SemanticLimits(), output_bytes=100)):
            with self.assertRaises(PipelineError):
                extract_client_metadata(self.binary, limits)

    def test_client_rehash_detects_change_during_inspection(self):
        original = extract_client_metadata
        def changed(path, **options):
            evidence = original(path, **options)
            path.write_bytes(path.read_bytes() + b'changed')
            return evidence
        with patch('resource_pipeline.real_producer.extract_client_metadata', side_effect=changed):
            with self.assertRaises(PipelineError):
                self.produce()
        self.assertFalse((self.output / 'version-adapter-manifest.json').exists())

    def test_version_literal_amplification_rejected_before_repeated_decode(self):
        from resource_pipeline.server_semantics import _constant
        self.binary.write_bytes(synthetic_pe(version_fields=[('versionNumber', 14, 'x' * 100)] * 200))
        with patch('resource_pipeline.server_semantics._constant', wraps=_constant) as decode:
            with self.assertRaisesRegex(PipelineError, 'Duplicate known version field'):
                extract_client_metadata(self.binary)
        self.assertEqual(1, decode.call_count)
        self.binary.write_bytes(synthetic_pe(version_fields=[('versionNumber', 14, 'x' * 4097)]))
        with self.assertRaisesRegex(PipelineError, 'Constant string exceeds'):
            extract_client_metadata(self.binary)

    def test_comparison_checks_cancellation_within_domains(self):
        evidence = extract_client_metadata(self.binary)
        calls = []
        def cancel():
            calls.append(None)
            if len(calls) == 3:
                raise PipelineError('cancelled')
        with self.assertRaisesRegex(PipelineError, 'cancelled'):
            compare_metadata(evidence, evidence, cancel)
        self.assertEqual(3, len(calls))

    def test_different_ids_and_locale_payloads_are_informational(self):
        from copy import deepcopy
        original = extract_client_metadata(self.binary)
        changed = deepcopy(original)
        changed['ids']['ItemID']['Example'] = 1000
        next(row for row in changed['resources'] if 'language' in row)['sha256'] = '0' * 64
        compared = compare_metadata(original, changed)
        self.assertFalse(compared['idDomains']['ItemID']['match'])
        self.assertTrue(any(not row['match'] for row in compared['localeResourceHashes'].values()))
        self.assertTrue(compared['declaredVersionsMatch'])
        self.assertFalse(compared['trusted'])

    def test_multiple_client_executables_are_bounded(self):
        for index in range(4):
            directory = self.client / str(index)
            directory.mkdir()
            (directory / 'Terraria.exe').write_bytes(self.data)
        with self.assertRaisesRegex(PipelineError, 'Ambiguous client'):
            self.produce()
