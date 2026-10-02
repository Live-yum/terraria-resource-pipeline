"""Synthetic, data-only tests for reusing locally computed ZIP inventories."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from resource_pipeline.adapters import AdapterLimits, tree_inventory
from resource_pipeline.pipeline import Pipeline
from resource_pipeline.preflight import RawInputPreflight
from resource_pipeline.real_producer import RawEvidenceProducer
from resource_pipeline.security import PipelineError, canonical_json, sha256


class SemanticInventoryReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = self.root / 'client'
        self.client.mkdir()
        (self.client / 'z.txt').write_bytes(b'last synthetic input')
        (self.client / 'a.txt').write_bytes(b'first synthetic input')
        self.sources = {'client': self.client}
        self.expected = {'client': tree_inventory(self.client)}
        self.output = self.root / 'evidence'

    def produce(self, expected=None, **options):
        return RawEvidenceProducer().produce(self.sources, {}, self.output,
                    expected_inventories=self.expected if expected is None else expected, **options)

    def test_reused_inventory_skips_only_first_walk_and_normalizes_order(self):
        expected = {'client': list(reversed(deepcopy(self.expected['client'])))}
        with patch('resource_pipeline.real_producer.tree_inventory', wraps=tree_inventory) as walk:
            manifest = self.produce(expected)
        self.assertEqual(walk.call_count, 1)
        self.assertEqual(walk.call_args.args[0], self.client)
        self.assertEqual(manifest['inputBinding']['client']['treeSha256'], sha256(canonical_json(self.expected['client'])))
        self.assertFalse(manifest['publishable'])
        self.assertFalse(manifest['extractionComplete'])
        self.assertTrue((self.output / 'version-adapter-manifest.json').is_file())
        self.assertEqual(expected['client'][0]['path'], 'z.txt')

    def test_direct_producer_default_retains_initial_and_final_walk(self):
        with patch('resource_pipeline.real_producer.tree_inventory', wraps=tree_inventory) as walk:
            RawEvidenceProducer().produce(self.sources, {}, self.output)
        self.assertEqual(walk.call_count, 2)

    def test_all_roles_are_validated_before_any_source_file_read(self):
        server = self.root / 'server'
        server.mkdir()
        (server / 'TerrariaServer.exe').write_bytes(b'inert synthetic bytes')
        sources = {'server': server, 'client': self.client}
        inspect = Mock()
        producer = RawEvidenceProducer(inspect)
        for name in ('../private.txt', '/absolute', 'a\\b', 'C:drive', 'dir/../file', 'con.txt'):
            expected = {'server': tree_inventory(server), 'client': [{'path': name, 'bytes': 1, 'sha256': 'a' * 64}]}
            with self.subTest(name=name), patch('resource_pipeline.real_producer.file_digest') as read, \
                 patch('resource_pipeline.real_producer.tree_inventory') as walk, self.assertRaises(PipelineError):
                producer.produce(sources, {}, self.output, expected_inventories=expected)
            read.assert_not_called()
            walk.assert_not_called()
            inspect.assert_not_called()
            self.assertFalse(self.output.exists())

    def test_strict_row_schema_types_hashes_and_role_binding(self):
        valid = self.expected['client'][0]
        invalid = [[], {'client': ()}, {'server': []}, {'client': [None]},
                   {'client': [{**valid, 'extra': 'untrusted'}]},
                   {'client': [{**valid, 'bytes': True}]}, {'client': [{**valid, 'bytes': -1}]},
                   {'client': [{**valid, 'bytes': 1.0}]}, {'client': [{**valid, 'bytes': '1'}]},
                   {'client': [{**valid, 'path': 3}]}, {'client': [{**valid, 'sha256': 'A' * 64}]},
                   {'client': [{**valid, 'sha256': 'a' * 63}]}]
        for expected in invalid:
            with self.subTest(expected=expected), patch('resource_pipeline.real_producer.tree_inventory') as walk, self.assertRaises(PipelineError):
                RawEvidenceProducer().produce(self.sources, {}, self.output, expected_inventories=expected)
            walk.assert_not_called()
            self.assertFalse(self.output.exists())

    def test_path_duplicates_aliases_and_file_directory_conflicts(self):
        for names in (('a', 'a'), ('A', 'a'), ('dir', 'dir/file'), ('Dir/a', 'dir/b')):
            expected = {'client': [{'path': name, 'bytes': 1, 'sha256': 'a' * 64} for name in names]}
            with self.subTest(names=names), self.assertRaises(PipelineError):
                self.produce(expected)
            self.assertFalse(self.output.exists())

    def test_file_byte_and_derived_path_budgets_are_checked_before_read(self):
        valid = deepcopy(self.expected)
        limits = (AdapterLimits(file_bytes=1), AdapterLimits(total_bytes=1), AdapterLimits(files=1))
        for limit in limits:
            with self.subTest(limit=limit), patch('resource_pipeline.real_producer.AdapterLimits', return_value=limit), \
                 patch('resource_pipeline.real_producer.tree_inventory') as walk, self.assertRaises(PipelineError):
                self.produce(valid)
            walk.assert_not_called()
        nested = {'client': [{'path': 'a/b/file', 'bytes': 0, 'sha256': 'a' * 64}]}
        with patch('resource_pipeline.real_producer.AdapterLimits', return_value=AdapterLimits(files=2)), self.assertRaisesRegex(PipelineError, 'path-count'):
            self.produce(nested)

    def test_missing_or_symlinked_root_is_rejected_without_source_read(self):
        absent = self.root / 'absent'
        alias = self.root / 'alias'
        alias.symlink_to(self.client, target_is_directory=True)
        for root in (absent, alias, self.client / 'a.txt'):
            with self.subTest(root=root), patch('resource_pipeline.real_producer.tree_inventory') as walk, self.assertRaises(PipelineError):
                RawEvidenceProducer().produce({'client': root}, {}, self.output, expected_inventories=self.expected)
            walk.assert_not_called()
            self.assertFalse(self.output.exists())

    def test_final_full_inventory_rejects_mutated_missing_added_and_symlink_files(self):
        for mode in ('mutated', 'missing', 'added', 'symlink'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / 'source'
                source.mkdir()
                path = source / 'input.txt'
                path.write_bytes(b'original')
                expected = {'client': tree_inventory(source)}
                if mode == 'mutated':
                    path.write_bytes(b'changed!')
                elif mode == 'missing':
                    path.unlink()
                elif mode == 'added':
                    (source / 'extra').write_bytes(b'added')
                else:
                    outside = root / 'outside'
                    outside.write_bytes(b'original')
                    path.unlink()
                    path.symlink_to(outside)
                output = root / 'output'
                with self.assertRaises(PipelineError):
                    RawEvidenceProducer().produce({'client': source}, {}, output, expected_inventories=expected)
                self.assertFalse((output / 'version-adapter-manifest.json').exists())

    def test_well_formed_but_false_hash_or_size_cannot_create_manifest(self):
        for field, value in (('sha256', '0' * 64), ('bytes', 0)):
            expected = deepcopy(self.expected)
            expected['client'][0][field] = value
            output = self.root / ('output-' + field)
            with self.subTest(field=field), self.assertRaisesRegex(PipelineError, 'Original source changed'):
                RawEvidenceProducer().produce(self.sources, {}, output, expected_inventories=expected)
            self.assertFalse((output / 'version-adapter-manifest.json').exists())

    def test_executable_symlink_parent_cannot_bypass_removed_first_walk(self):
        server = self.root / 'server'
        server.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'TerrariaServer.exe').write_bytes(b'not to be read')
        (server / 'nested').symlink_to(outside, target_is_directory=True)
        expected = {'server': [{'path': 'nested/TerrariaServer.exe', 'bytes': 14, 'sha256': 'a' * 64}]}
        inspect = Mock()
        with patch('resource_pipeline.real_producer.file_digest') as read, self.assertRaisesRegex(PipelineError, 'traverse links'):
            RawEvidenceProducer(inspect).produce({'server': server}, {}, self.output, expected_inventories=expected)
        read.assert_not_called()
        inspect.assert_not_called()
        self.assertFalse((self.output / 'version-adapter-manifest.json').exists())

    def test_cancel_during_validation_or_final_inventory_never_writes_manifest(self):
        def cancel(*args, **kwargs):
            raise PipelineError('RAW_JOB_CANCELED')
        with patch('resource_pipeline.real_producer.tree_inventory') as walk, self.assertRaisesRegex(PipelineError, 'CANCELED'):
            self.produce(checkpoint=cancel)
        walk.assert_not_called()
        self.assertFalse(self.output.exists())
        with patch('resource_pipeline.real_producer.tree_inventory', side_effect=cancel), self.assertRaisesRegex(PipelineError, 'CANCELED'):
            self.produce()
        self.assertFalse((self.output / 'version-adapter-manifest.json').exists())

    @staticmethod
    def upload(entries):
        archive = BytesIO()
        with zipfile.ZipFile(archive, 'w') as package:
            for name, data in entries.items():
                package.writestr(name, data)
        return BytesIO(archive.getvalue()), 'synthetic.zip'

    def test_preflight_passes_only_fresh_local_inventory_to_exact_builtin(self):
        pipeline = Pipeline(self.root / 'service')
        producer = RawEvidenceProducer()
        service = RawInputPreflight(pipeline, semantic_producer=producer)
        job = service.submit(client_file=self.upload({'actual.txt': b'actual bytes', 'uploaded-claims.json': b'{"expected_inventories":"ignored"}'}))
        job['sources']['client']['inventory'] = {'files': [{'path': '../forged', 'bytes': 1, 'sha256': '0' * 64}]}
        pipeline.save(job)
        with patch.object(producer, 'produce', wraps=producer.produce) as invoked, \
             patch('resource_pipeline.real_producer.tree_inventory', wraps=tree_inventory) as walk:
            result = service.process(job['id'])
        rows = invoked.call_args.kwargs['expected_inventories']['client']
        self.assertEqual({row['path'] for row in rows}, {'actual.txt', 'uploaded-claims.json'})
        self.assertEqual(walk.call_count, 1)
        self.assertEqual(result['producerEvidence']['status'], 'PARTIAL')
        self.assertFalse(result['extractionComplete'])
        self.assertIsNone(pipeline.current())

    def test_injected_producers_and_subclasses_keep_original_call_signature(self):
        class Injected:
            def produce(inner, sources, textures, output, *, checkpoint):
                checkpoint()
                return {'status': 'PARTIAL', 'publishable': False}
        class Subclass(RawEvidenceProducer):
            def produce(inner, sources, textures, output, *, checkpoint):
                checkpoint()
                return {'status': 'PARTIAL', 'publishable': False}
        for index, producer in enumerate((Injected(), Subclass())):
            pipeline = Pipeline(self.root / f'service-{index}')
            service = RawInputPreflight(pipeline, semantic_producer=producer)
            job = service.submit(client_file=self.upload({'actual.txt': b'actual'}))
            with patch.object(producer, 'produce', wraps=producer.produce) as invoked:
                result = service.process(job['id'])
            self.assertEqual(set(invoked.call_args.kwargs), {'checkpoint'})
            self.assertEqual(result['producerEvidence']['status'], 'PARTIAL')

    def test_preflight_final_verification_failure_stays_private_and_unpublishable(self):
        pipeline = Pipeline(self.root / 'service')
        producer = RawEvidenceProducer()
        service = RawInputPreflight(pipeline, semantic_producer=producer)
        job = service.submit(client_file=self.upload({'actual.txt': b'actual'}))
        original = producer.produce
        def mutate_then_produce(sources, textures, output, **options):
            (sources['client'] / 'actual.txt').write_bytes(b'changed')
            return original(sources, textures, output, **options)
        with patch.object(producer, 'produce', side_effect=mutate_then_produce):
            result = service.process(job['id'])
        self.assertNotIn('producerEvidence', result)
        self.assertIn('SEMANTIC_PRODUCER_REJECTED', {row['code'] for row in result['blockers']})
        self.assertFalse(result['extractionComplete'])
        self.assertIsNone(pipeline.current())
        self.assertFalse((pipeline.root / 'jobs' / job['id'] / 'adapter-evidence/version-adapter-manifest.json').exists())

    def test_validated_inventory_is_a_deep_row_copy(self):
        expected = deepcopy(self.expected)
        original_binding = sha256(canonical_json(expected['client']))
        changed = False
        def mutate_caller_metadata_after_validation():
            nonlocal changed
            if self.output.exists() and not changed:
                expected['client'][0]['path'] = '../caller-mutation'
                expected['client'][0]['sha256'] = '0' * 64
                changed = True
        result = self.produce(expected, checkpoint=mutate_caller_metadata_after_validation)
        self.assertTrue(changed)
        self.assertEqual(result['inputBinding']['client']['treeSha256'], original_binding)
        self.assertFalse(result['publishable'])
