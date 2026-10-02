"""Synthetic/mocked orchestration only: no game data or converter executes."""
from io import BytesIO
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image

from resource_pipeline.adapters import AdapterLimits, CommandPlan, TrustedAdapterRunner, tree_digest, tree_inventory
from resource_pipeline.security import PipelineError, canonical_json, sha256
from resource_pipeline.texture_batches import TextureBatchPolicy, plan_texture_batches
from resource_pipeline.textures import TextureExtractor, TextureTool


def header(expanded=128):
    return b'XNBw\x05\x80' + struct.pack('<ii', 15, expanded) + b'\x00'


class FakeSandbox:
    def plan(self, spec, job):
        return CommandPlan(('never-executed', '--', *spec.command), job, {}, 'synthetic-only')


class TextureBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.tool = self.root / 'tool'
        self.tool.mkdir()
        (self.tool / 'TextureExtractor.dll').write_bytes(b'non-executable synthetic placeholder')
        self.runtime = self.root / 'dotnet'
        self.runtime.write_bytes(b'non-executable synthetic placeholder')
        self.extractor = TextureExtractor(TextureTool(self.tool, tree_digest(self.tool), self.runtime), FakeSandbox(),
                                          policy=TextureBatchPolicy(files_per_child=2))
        self.job = self.root / 'job'
        buf = BytesIO()
        Image.new('RGBA', (2, 2), (17, 34, 51, 255)).save(buf, format='PNG')
        self.png = buf.getvalue()
        self.calls = []

    def sources(self, count, expanded=128):
        (self.source / 'Content/Images').mkdir(parents=True, exist_ok=True)
        for index in range(count):
            (self.source / f'Content/Images/Image_{index:05}.xnb').write_bytes(header(expanded))

    def decode(self, plan, limits, job, **kwargs):
        # This fake only writes a known PNG; it does not read/decompress XNB payload.
        self.calls.append((job, limits, kwargs))
        rows = []
        for item in tree_inventory(job / 'inputs/package'):
            if not item['path'].endswith('.xnb'):
                continue
            name = str(Path(item['path']).with_suffix('.png'))
            target = job / 'output' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.png)
            rows.append({'input': item['path'], 'output': name, 'sourceSha256': item['sha256'],
                         'sha256': sha256(self.png), 'width': 2, 'height': 2, 'surfaceFormat': 0,
                         'mipLevels': 2, 'exportedMip': 0})
        report = {'schemaVersion': 1, 'imageCount': len(rows), 'images': rows, 'skipped': [],
                  'executedInput': False, 'extractionComplete': False, 'publishable': False}
        (job / 'output/texture-report.json').write_bytes(canonical_json(report))

    def test_one_tree_is_automatically_batched_preserving_paths_and_limits(self):
        self.sources(5)
        before = tree_inventory(self.source)
        with patch.object(TrustedAdapterRunner, '_execute', side_effect=self.decode):
            result = self.extractor.extract(self.source, self.job)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(result['imageCount'], 5)
        self.assertEqual(result['batching']['totalPixels'], 20)
        self.assertEqual(result['batching']['batchCount'], 3)
        self.assertEqual(result['batching']['maxConcurrentChildren'], 1)
        self.assertFalse(result['batching']['partialReuse'])
        self.assertFalse(result['publishable'])
        self.assertFalse(result['extractionComplete'])
        self.assertEqual(tree_inventory(self.source), before)
        self.assertFalse((self.job / 'work/active-child').exists())
        self.assertEqual(result['outputSha256'], tree_digest(self.job / 'output'))
        self.assertEqual(len(list((self.job / 'output/Content/Images').glob('*.png'))), 5)
        for _, limits, kwargs in self.calls:
            self.assertEqual(limits.memory_bytes, 2 * 1024**3)
            self.assertLess(limits.total_bytes, 2 * 1024**3)
            self.assertLessEqual(limits.timeout_seconds, 120)
            self.assertEqual(kwargs['watchdog_job'], self.job)
        self.assertTrue(all(len(row['rgbaSha256']) == 64 for row in result['images']))
        with patch.object(TrustedAdapterRunner, '_execute') as decoder, self.assertRaises(PipelineError):
            self.extractor.extract(self.source, self.job)
        decoder.assert_not_called()

    def test_small_tree_keeps_original_job_path(self):
        self.sources(2)
        with patch.object(TrustedAdapterRunner, '_execute', side_effect=self.decode):
            result = self.extractor.extract(self.source, self.job)
        self.assertEqual(self.calls[0][0], self.job)
        self.assertEqual(result['batching']['batchCount'], 1)

    def test_declared_expansion_plans_full_content_scale_without_decoding(self):
        self.sources(15_123, expanded=145_367)
        plan = plan_texture_batches(self.source, tree_inventory(self.source))
        self.assertEqual(sum(map(len, plan.batches)), 15_123)
        self.assertEqual(len(plan.batches), 60)
        self.assertGreater(plan.pixel_risk, TextureBatchPolicy().total_pixels)
        self.assertLess(plan.declared_expanded_bytes, TextureBatchPolicy().expanded_bytes)
        self.assertTrue(all(len(batch) <= 256 for batch in plan.batches))

    def test_header_risk_splits_even_below_file_threshold(self):
        self.sources(17, expanded=32_000_000)
        plan = plan_texture_batches(self.source, tree_inventory(self.source))
        self.assertEqual([len(batch) for batch in plan.batches], [16, 1])

    def test_header_invalid_expansion_and_plan_bombs_fail_before_decoder(self):
        self.sources(3)
        cases = [TextureBatchPolicy(expanded_bytes=128), TextureBatchPolicy(files_per_child=1, max_children=2)]
        for policy in cases:
            with self.subTest(policy=policy), self.assertRaises(PipelineError):
                plan_texture_batches(self.source, tree_inventory(self.source), policy)
        next(self.source.rglob('*.xnb')).write_bytes(header(-1))
        with patch.object(TrustedAdapterRunner, '_execute') as decoder, self.assertRaisesRegex(PipelineError, 'HEADER'):
            self.extractor.extract(self.source, self.job)
        decoder.assert_not_called()
        self.assertFalse(self.job.exists())

    def test_failure_cleans_prior_merged_batches_and_retry_starts_fresh(self):
        self.sources(5)
        attempts = 0
        def fail_second(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise PipelineError('Synthetic child failed')
            self.decode(*args, **kwargs)
        with patch.object(TrustedAdapterRunner, '_execute', side_effect=fail_second), self.assertRaises(PipelineError):
            self.extractor.extract(self.source, self.job)
        self.assertFalse(self.job.exists())
        self.assertEqual(len(list(self.source.rglob('*.xnb'))), 5)
        with patch.object(TrustedAdapterRunner, '_execute', side_effect=self.decode):
            self.assertEqual(self.extractor.extract(self.source, self.job)['imageCount'], 5)

    def test_total_actual_pixels_are_bounded_across_children(self):
        self.sources(3)
        self.extractor.policy = TextureBatchPolicy(files_per_child=1, total_pixels=8)
        with patch.object(TrustedAdapterRunner, '_execute', side_effect=self.decode), self.assertRaisesRegex(PipelineError, 'total decoded-pixel'):
            self.extractor.extract(self.source, self.job)
        self.assertFalse(self.job.exists())

    def test_cancel_between_children_cleans_all_partial_output(self):
        self.sources(5)
        cancelled = False
        def decode_and_cancel(*args, **kwargs):
            nonlocal cancelled
            self.decode(*args, **kwargs)
            cancelled = True
        with patch.object(TrustedAdapterRunner, '_execute', side_effect=decode_and_cancel), self.assertRaisesRegex(PipelineError, 'cancelled'):
            self.extractor.extract(self.source, self.job, cancel_check=lambda: cancelled)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.job.exists())

    def test_expired_deadline_and_precancel_do_not_start_or_create_job(self):
        self.sources(1)
        for options in ({'deadline': time.monotonic() - 1}, {'cancel_check': lambda: True}):
            with self.subTest(options=options), patch.object(TrustedAdapterRunner, '_execute') as decoder, self.assertRaises(PipelineError):
                self.extractor.extract(self.source, self.job, **options)
            decoder.assert_not_called()
            self.assertFalse(self.job.exists())

    def test_png_receipt_mips_and_source_mutation_rejected(self):
        self.sources(3)
        mutations = [lambda report: report['images'][0].update(sha256='0' * 64),
                     lambda report: report['images'][0].update(width=1),
                     lambda report: report['images'][0].update(sourceSha256='0' * 64),
                     lambda report: report['images'][0].update(mipLevels=True),
                     lambda report: report['images'][0].update(mipLevels=3),
                     lambda report: report['images'][0].update(exportedMip=1)]
        import json
        for mutate in mutations:
            def bad(plan, limits, job, **kwargs):
                self.decode(plan, limits, job, **kwargs)
                path = job / 'output/texture-report.json'
                report = json.loads(path.read_text())
                mutate(report)
                path.write_bytes(canonical_json(report))
            with self.subTest(mutate=mutate), patch.object(TrustedAdapterRunner, '_execute', side_effect=bad), self.assertRaises(PipelineError):
                self.extractor.extract(self.source, self.job)
            self.assertFalse(self.job.exists())

    def test_original_source_mutation_and_unexpected_artifact_are_rejected(self):
        self.sources(3)
        def mutate_source(*args, **kwargs):
            self.decode(*args, **kwargs)
            next(self.source.rglob('*.xnb')).write_bytes(header(129))
        with patch.object(TrustedAdapterRunner, '_execute', side_effect=mutate_source), self.assertRaisesRegex(PipelineError, 'Original texture inputs changed'):
            self.extractor.extract(self.source, self.job)
        self.assertFalse(self.job.exists())
        def extra(plan, limits, job, **kwargs):
            self.decode(plan, limits, job, **kwargs)
            (job / 'output/unexpected.txt').write_text('synthetic')
        with patch.object(TrustedAdapterRunner, '_execute', side_effect=extra), self.assertRaisesRegex(PipelineError, 'Unexpected texture output'):
            self.extractor.extract(self.source, self.job)
        self.assertFalse(self.job.exists())

    def test_legacy_mips_remain_unknown(self):
        self.sources(1)
        import json
        def legacy(plan, limits, job, **kwargs):
            self.decode(plan, limits, job, **kwargs)
            path = job / 'output/texture-report.json'
            report = json.loads(path.read_text())
            report['images'][0].pop('mipLevels')
            report['images'][0].pop('exportedMip')
            path.write_bytes(canonical_json(report))
        with patch.object(TrustedAdapterRunner, '_execute', side_effect=legacy):
            result = self.extractor.extract(self.source, self.job)
        self.assertNotIn('mipLevels', result['images'][0])
        self.assertNotIn('exportedMip', result['images'][0])

    def test_concurrency_slot_rejects_nested_job(self):
        self.sources(1)
        def nested(*args, **kwargs):
            with self.assertRaisesRegex(PipelineError, 'busy'):
                self.extractor.extract(self.source, self.root / 'second-job')
            self.decode(*args, **kwargs)
        with patch.object(TrustedAdapterRunner, '_execute', side_effect=nested):
            self.extractor.extract(self.source, self.job)
        self.assertFalse((self.root / 'second-job').exists())

    def test_runner_cancel_kills_process_group(self):
        from unittest.mock import Mock
        process = Mock(pid=123456, returncode=None)
        process.poll.return_value = None
        counter = iter([False, True])
        plan = CommandPlan(('/synthetic-command',), self.root, {}, 'synthetic-only')
        with patch('resource_pipeline.adapters.subprocess.Popen', return_value=process), \
             patch('resource_pipeline.adapters.os.killpg') as kill, \
             self.assertRaisesRegex(PipelineError, 'cancelled'):
            TrustedAdapterRunner._execute(plan, AdapterLimits(), self.root, cancel_check=lambda: next(counter))
        kill.assert_called_once()
        process.wait.assert_called_once()

    def test_children_share_remaining_deadline_without_reset(self):
        self.sources(5)
        now = [10.0]
        def consume_time(*args, **kwargs):
            self.decode(*args, **kwargs)
            now[0] += 5
        with patch('resource_pipeline.textures.time.monotonic', side_effect=lambda: now[0]), \
             patch.object(TrustedAdapterRunner, '_execute', side_effect=consume_time):
            self.extractor.extract(self.source, self.job, deadline=30)
        self.assertEqual([limits.timeout_seconds for _, limits, _ in self.calls], [20, 15, 10])

    def test_timeout_after_child_cleans_output_and_stops_next_child(self):
        self.sources(5)
        now = [10.0]
        def exceed_deadline(*args, **kwargs):
            self.decode(*args, **kwargs)
            now[0] = 31
        with patch('resource_pipeline.textures.time.monotonic', side_effect=lambda: now[0]), \
             patch.object(TrustedAdapterRunner, '_execute', side_effect=exceed_deadline), \
             self.assertRaisesRegex(PipelineError, 'timed out'):
            self.extractor.extract(self.source, self.job, deadline=30)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.job.exists())

    def test_diagnostic_preserves_safe_stage_and_exit_code(self):
        self.sources(1)
        def rejected(plan, limits, job, **kwargs):
            (job / 'output/texture-error.json').write_bytes(canonical_json(
                {'stage': 'LZX_DECODE', 'error': 'InvalidDataException', 'private': 'never exposed'}))
            raise PipelineError('Trusted adapter or OS sandbox failed; no output was approved (exit=1)')
        with patch.object(TrustedAdapterRunner, '_execute', side_effect=rejected), \
             self.assertRaisesRegex(PipelineError, r'^TEXTURE_REJECTED:LZX_DECODE:InvalidDataException\(exit=1\)$'):
            self.extractor.extract(self.source, self.job)
        self.assertFalse(self.job.exists())

    def test_cancel_during_first_snapshot_never_starts_tool_snapshot(self):
        self.sources(1)
        from resource_pipeline.adapters import _copy_tree
        cancelled = False
        destinations = []
        def copy_and_cancel(source, destination, limits, **kwargs):
            nonlocal cancelled
            destinations.append(destination.relative_to(self.job).as_posix())
            _copy_tree(source, destination, limits, **kwargs)
            cancelled = True
        with patch('resource_pipeline.textures._copy_tree', side_effect=copy_and_cancel), \
             patch.object(TrustedAdapterRunner, '_execute') as decoder, \
             self.assertRaisesRegex(PipelineError, 'cancelled'):
            self.extractor.extract(self.source, self.job, cancel_check=lambda: cancelled)
        self.assertEqual(destinations, ['inputs/package'])
        decoder.assert_not_called()
        self.assertFalse(self.job.exists())

    def test_cancelled_source_inventory_hash_stops_after_first_mib(self):
        self.sources(1)
        (self.source / 'large-original.bin').write_bytes(b'x' * (4 * 1024**2))
        import hashlib
        original_sha256 = hashlib.sha256
        cancelled = False
        hashed_chunks = []
        class CancellableDigest:
            def __init__(inner, *args, **kwargs):
                inner.digest = original_sha256(*args, **kwargs)
            def update(inner, data):
                nonlocal cancelled
                hashed_chunks.append(len(data))
                inner.digest.update(data)
                if len(data) == 1024**2:
                    cancelled = True
            def hexdigest(inner):
                return inner.digest.hexdigest()
        with patch('resource_pipeline.adapters.hashlib.sha256', side_effect=CancellableDigest), \
             patch('resource_pipeline.textures._copy_tree') as copier, \
             patch.object(TrustedAdapterRunner, '_execute') as decoder, \
             self.assertRaisesRegex(PipelineError, 'cancelled'):
            self.extractor.extract(self.source, self.job, cancel_check=lambda: cancelled)
        self.assertEqual(hashed_chunks, [1024**2])
        copier.assert_not_called()
        decoder.assert_not_called()
        self.assertFalse(self.job.exists())

    def test_copy_helper_checks_between_mib_chunks(self):
        from resource_pipeline.adapters import _copy_tree
        source = self.root / 'copy-source'
        source.mkdir()
        blob = source / 'synthetic.bin'
        blob.write_bytes(b'x' * (4 * 1024**2))
        destination = self.root / 'copy-target'
        original_open = Path.open
        cancelled = False
        class Reader:
            def __init__(inner, stream):
                inner.stream = stream
            def __enter__(inner):
                inner.stream.__enter__()
                return inner
            def __exit__(inner, *args):
                return inner.stream.__exit__(*args)
            def read(inner, count=-1):
                nonlocal cancelled
                result = inner.stream.read(count)
                if result:
                    cancelled = True
                return result
        def opening(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            if path == blob and destination.exists():
                return Reader(stream)
            return stream
        def check():
            if cancelled:
                raise PipelineError('synthetic copy cancelled')
        with patch.object(Path, 'open', new=opening), self.assertRaisesRegex(PipelineError, 'copy cancelled'):
            _copy_tree(source, destination, AdapterLimits(), checkpoint=check)
        self.assertEqual((destination / 'synthetic.bin').stat().st_size, 1024**2)

    def test_deadline_during_first_snapshot_never_starts_tool_snapshot(self):
        self.sources(1)
        from resource_pipeline.adapters import _copy_tree
        now = [10.0]
        destinations = []
        def copy_and_expire(source, destination, limits, **kwargs):
            destinations.append(destination.relative_to(self.job).as_posix())
            _copy_tree(source, destination, limits, **kwargs)
            now[0] = 31
        with patch('resource_pipeline.textures.time.monotonic', side_effect=lambda: now[0]), \
             patch('resource_pipeline.textures._copy_tree', side_effect=copy_and_expire), \
             self.assertRaisesRegex(PipelineError, 'timed out'):
            self.extractor.extract(self.source, self.job, deadline=30)
        self.assertEqual(destinations, ['inputs/package'])
        self.assertFalse(self.job.exists())
