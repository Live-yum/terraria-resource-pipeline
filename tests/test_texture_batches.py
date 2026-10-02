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
        with patch('resource_pipeline.adapters.subprocess.Popen', return_value=process) as launch, \
             patch('resource_pipeline.adapters.os.killpg') as kill, \
             self.assertRaisesRegex(PipelineError, 'cancelled'):
            TrustedAdapterRunner._execute(plan, AdapterLimits(), self.root, cancel_check=lambda: next(counter))
        kill.assert_called_once()
        process.wait.assert_called_once()
        self.assertIs(launch.call_args.kwargs['close_fds'], True)
        self.assertNotIn('pass_fds', launch.call_args.kwargs)

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

    def test_ledger_reserves_actual_png_bytes_and_shared_paths(self):
        self.sources(5)
        with patch.object(TrustedAdapterRunner, '_execute', side_effect=self.decode):
            self.extractor.extract(self.source, self.job)
        reservations = [kwargs['reserved_output'] for _, _, kwargs in self.calls]
        self.assertEqual([entry.bytes for entry in reservations], [0, 2 * len(self.png), 4 * len(self.png)])
        self.assertEqual([entry.paths for entry in reservations], [0, 4, 6])
        self.assertEqual(len({job for job, _, _ in self.calls}), 3)
        self.assertEqual([job.name for job, _, _ in self.calls], ['child-0000', 'child-0001', 'child-0002'])

    def test_small_job_does_not_reserve_mounted_output(self):
        self.sources(1)
        with patch.object(TrustedAdapterRunner, '_execute', side_effect=self.decode):
            self.extractor.extract(self.source, self.job)
        self.assertIsNone(self.calls[0][2]['reserved_output'])

    def test_reserved_watchdog_skips_parent_output_but_charges_byte_and_path_caps(self):
        from resource_pipeline.adapters import WorkingUsage, _working_size
        root = self.root / 'watched'
        (root / 'output').mkdir(parents=True)
        (root / 'work').mkdir()
        (root / 'output/verified.png').write_bytes(b'x' * 20)
        (root / 'work/live').write_bytes(b'y' * 10)
        reservation = WorkingUsage(bytes=20, paths=1)
        import os
        original_walk = os.walk
        def walk(path, *args, **kwargs):
            self.assertNotEqual(path, root / 'output')
            return original_walk(path, *args, **kwargs)
        with patch('resource_pipeline.adapters.os.walk', side_effect=walk):
            usage = _working_size(root, AdapterLimits(total_bytes=30, files=2), reserved_output=reservation)
            self.assertEqual(usage, WorkingUsage(bytes=30, paths=2))
            for limits in (AdapterLimits(total_bytes=29), AdapterLimits(files=1)):
                with self.subTest(limits=limits), self.assertRaisesRegex(PipelineError, 'capacity'):
                    _working_size(root, limits, reserved_output=reservation)
        with self.assertRaises(ValueError):
            WorkingUsage(bytes=-1)
        with self.assertRaises(ValueError):
            WorkingUsage(paths=True)

    def test_active_child_cannot_mount_reserved_parent_output(self):
        self.sources(3)
        class UnsafeSandbox:
            def plan(inner, spec, child):
                return CommandPlan(('never-executed', '--bind', str(self.job), '/unsafe', '--', *spec.command), child, {}, 'synthetic-only')
        self.extractor.sandbox = UnsafeSandbox()
        with patch.object(TrustedAdapterRunner, '_execute') as decoder, self.assertRaisesRegex(PipelineError, 'outside the child sandbox'):
            self.extractor.extract(self.source, self.job)
        decoder.assert_not_called()
        self.assertFalse(self.job.exists())

    def test_active_child_cannot_mount_reserved_output_descendant(self):
        self.sources(3)
        class UnsafeSandbox:
            def plan(inner, spec, child):
                return CommandPlan(('never-executed', '--bind', str(self.job / 'output/Content'), '/unsafe', '--', *spec.command), child, {}, 'synthetic-only')
        self.extractor.sandbox = UnsafeSandbox()
        with patch.object(TrustedAdapterRunner, '_execute') as decoder, self.assertRaisesRegex(PipelineError, 'outside the child sandbox'):
            self.extractor.extract(self.source, self.job)
        decoder.assert_not_called()
        self.assertFalse(self.job.exists())

    def test_real_sandbox_plan_only_mounts_unique_child_not_merged_output(self):
        from resource_pipeline.adapters import BubblewrapSandbox
        self.sources(3)
        self.extractor.sandbox = BubblewrapSandbox(executable=self.runtime, runtime_roots=())
        plans = []
        def collect(plan, limits, job, **kwargs):
            plans.append(plan)
            self.decode(plan, limits, job, **kwargs)
        with patch.object(TrustedAdapterRunner, '_execute', side_effect=collect):
            self.extractor.extract(self.source, self.job)
        for plan, (child, _, _) in zip(plans, self.calls):
            self.assertIn(str(child / 'output'), plan.argv)
            self.assertNotIn(str(self.job / 'output'), plan.argv)
            self.assertNotIn(str(self.job), plan.argv)

    def test_ledger_rejects_file_directory_collisions_and_inconsistent_totals(self):
        from resource_pipeline.textures import _TextureOutputLedger
        ledger = _TextureOutputLedger().advance([{'path': 'a.png', 'bytes': 3, 'sha256': 'a' * 64}], lambda: None)
        for name in ('a.png', 'A.png', 'a.png/b.png'):
            with self.subTest(name=name), self.assertRaisesRegex(PipelineError, 'collision'):
                ledger.advance([{'path': name, 'bytes': 2, 'sha256': 'b' * 64}], lambda: None)
        with self.assertRaises(ValueError):
            _TextureOutputLedger(files=ledger.files, bytes=0)
        from dataclasses import FrozenInstanceError
        with self.assertRaises(FrozenInstanceError):
            ledger.bytes = 0

    def test_final_full_verification_rejects_reserved_output_tamper(self):
        self.sources(5)
        for kind in ('bytes', 'extra-file', 'extra-directory'):
            self.calls.clear()
            def tamper(plan, limits, job, **kwargs):
                self.decode(plan, limits, job, **kwargs)
                if len(self.calls) == 2:
                    output = self.job / 'output'
                    if kind == 'bytes':
                        target = next(output.rglob('*.png'))
                        target.unlink()
                        target.write_bytes(b'changed after verified merge')
                    elif kind == 'extra-file':
                        (output / 'injected.txt').write_text('synthetic tamper')
                    else:
                        (output / 'unexpected-empty-directory').mkdir()
            with self.subTest(kind=kind), patch.object(TrustedAdapterRunner, '_execute', side_effect=tamper), self.assertRaisesRegex(PipelineError, 'ledger'):
                self.extractor.extract(self.source, self.job)
            self.assertFalse(self.job.exists())

    def test_batched_byte_capacity_stops_before_third_child(self):
        from dataclasses import replace
        self.sources(5)
        # Incompressible, synthetic 32x32 pixels make two accumulated batches
        # exceed 13 kB although either child fits alone.
        import random
        buf = BytesIO()
        Image.frombytes('RGBA', (32, 32), random.Random(1).randbytes(4096)).save(buf, format='PNG')
        self.png = buf.getvalue()
        def decode_large(plan, limits, job, **kwargs):
            self.decode(plan, limits, job, **kwargs)
            import json
            path = job / 'output/texture-report.json'
            report = json.loads(path.read_text())
            for row in report['images']:
                row.update(width=32, height=32)
            path.write_bytes(canonical_json(report))
        def limits(**kwargs):
            return replace(AdapterLimits(**kwargs), total_bytes=13_000)
        with patch('resource_pipeline.textures.AdapterLimits', side_effect=limits), \
             patch.object(TrustedAdapterRunner, '_execute', side_effect=decode_large), \
             self.assertRaisesRegex(PipelineError, 'capacity'):
            self.extractor.extract(self.source, self.job)
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(self.job.exists())

    def test_forged_receipt_size_is_rejected_before_ledger_merge(self):
        self.sources(3)
        import json
        def forged(plan, limits, job, **kwargs):
            self.decode(plan, limits, job, **kwargs)
            path = job / 'output/texture-report.json'
            report = json.loads(path.read_text())
            report['images'][0]['bytes'] = 0
            path.write_bytes(canonical_json(report))
        with patch.object(TrustedAdapterRunner, '_execute', side_effect=forged), self.assertRaisesRegex(PipelineError, 'receipt mismatch'):
            self.extractor.extract(self.source, self.job)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.job.exists())

    def test_batched_path_capacity_stops_before_fourth_child(self):
        from dataclasses import replace
        self.sources(5)
        self.extractor.policy = TextureBatchPolicy(files_per_child=1)
        def limits(**kwargs):
            return replace(AdapterLimits(**kwargs), files=32)
        with patch('resource_pipeline.textures.AdapterLimits', side_effect=limits), \
             patch.object(TrustedAdapterRunner, '_execute', side_effect=self.decode), \
             self.assertRaisesRegex(PipelineError, 'capacity'):
            self.extractor.extract(self.source, self.job)
        self.assertEqual(len(self.calls), 3)
        self.assertFalse(self.job.exists())

    def test_merge_reserves_transient_new_parent_directories(self):
        from dataclasses import replace
        self.sources(5)
        self.extractor.policy = TextureBatchPolicy(files_per_child=1)
        def limits(**kwargs):
            return replace(AdapterLimits(**kwargs), files=29)
        with patch('resource_pipeline.textures.AdapterLimits', side_effect=limits), \
             patch.object(TrustedAdapterRunner, '_execute', side_effect=self.decode), \
             self.assertRaisesRegex(PipelineError, 'merge exceeds whole-job path capacity'):
            self.extractor.extract(self.source, self.job)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.job.exists())

    def test_readonly_reservation_counts_equal_full_watchdog_and_prunes_only_snapshots(self):
        from resource_pipeline.adapters import BubblewrapSandbox, _working_size
        self.sources(3)
        self.extractor.sandbox = BubblewrapSandbox(executable=self.runtime, runtime_roots=())
        def verify_watchdog(plan, limits, job, **kwargs):
            self.decode(plan, limits, job, **kwargs)
            sealed = kwargs['reserved_readonly']
            self.assertEqual({item.root for item in sealed}, {job / 'inputs', job / 'tool'})
            full = _working_size(self.job, limits, reserved_output=kwargs['reserved_output'])
            pruned = _working_size(self.job, limits, reserved_output=kwargs['reserved_output'], reserved_readonly=sealed)
            self.assertEqual(pruned, full)
            import os
            original_walk = os.walk
            visited = []
            def walk(root, *args, **options):
                for directory, dirs, files in original_walk(root, *args, **options):
                    visited.append(Path(directory))
                    yield directory, dirs, files
            with patch('resource_pipeline.adapters.os.walk', side_effect=walk):
                _working_size(self.job, limits, reserved_output=kwargs['reserved_output'], reserved_readonly=sealed)
            self.assertNotIn(job / 'inputs', visited)
            self.assertNotIn(job / 'tool', visited)
            self.assertIn(job / 'work', visited)
            self.assertIn(job / 'output', visited)
        with patch.object(TrustedAdapterRunner, '_execute', side_effect=verify_watchdog):
            self.extractor.extract(self.source, self.job)

    def test_readonly_plan_rejects_writable_alias_fd_and_capability_changes(self):
        from types import SimpleNamespace
        from resource_pipeline.adapters import BubblewrapSandbox
        child = self.job / 'work/child-0000'
        sandbox = BubblewrapSandbox(executable=self.runtime, runtime_roots=())
        plan = sandbox.plan(SimpleNamespace(command=(str(self.runtime), '/tool/TextureExtractor.dll')), child)
        self.extractor._validate_readonly_plan(plan, child)
        additions = [('--bind', str(child / 'inputs/package'), '/alias'),
                     ('--ro-bind', str(child / 'tool'), '/alias'),
                     ('--bind', str(child), '/parent-alias'),
                     ('--bind', str(child / 'work'), '/inputs/alias'),
                     ('--preserve-fds', '1'),
                     ('--cap-add', 'SYS_ADMIN'), ('--args', '9'),
                     ('--share-net',), ('--overlay-src', str(child / 'inputs')),
                     ('--ro-bind-data', '1', '/inputs')]
        for addition in additions:
            argv = list(plan.argv)
            end = argv.index('--')
            argv[end:end] = addition
            with self.subTest(addition=addition), self.assertRaises(PipelineError):
                self.extractor._validate_readonly_plan(CommandPlan(tuple(argv), plan.cwd, plan.env, plan.isolation), child)
        argv = list(plan.argv)
        argv[argv.index(str(child / 'inputs')) - 1] = '--bind'
        with self.assertRaises(PipelineError):
            self.extractor._validate_readonly_plan(CommandPlan(tuple(argv), plan.cwd, plan.env, plan.isolation), child)
        argv = list(plan.argv)
        start = argv.index('--cap-drop')
        del argv[start:start + 2]
        with self.assertRaises(PipelineError):
            self.extractor._validate_readonly_plan(CommandPlan(tuple(argv), plan.cwd, plan.env, plan.isolation), child)

    def test_readonly_capture_rejects_hardlinks_and_special_files(self):
        import os
        from resource_pipeline.adapters import ReadOnlyTreeReservation
        root = self.root / 'snapshot'
        root.mkdir()
        data = root / 'file'
        data.write_bytes(b'synthetic')
        expected = tree_inventory(root)
        outside = self.root / 'writable-alias'
        os.link(data, outside)
        with self.assertRaisesRegex(PipelineError, 'links or special'):
            ReadOnlyTreeReservation.capture(root, expected, AdapterLimits(), lambda: None)
        outside.unlink()
        data.unlink()
        os.mkfifo(data)
        with self.assertRaisesRegex(PipelineError, 'links or special'):
            ReadOnlyTreeReservation.capture(root, expected, AdapterLimits(), lambda: None)

    def test_readonly_mutation_is_rehashed_after_child_and_cleans_job(self):
        from resource_pipeline.adapters import BubblewrapSandbox
        self.sources(3)
        self.extractor.sandbox = BubblewrapSandbox(executable=self.runtime, runtime_roots=())
        def mutate_after_decode(plan, limits, job, **kwargs):
            self.decode(plan, limits, job, **kwargs)
            target = next((job / 'inputs/package').rglob('*.xnb'))
            data = target.read_bytes()
            target.unlink()
            target.write_bytes(data[:-1] + b'\x01')
        with patch.object(TrustedAdapterRunner, '_execute', side_effect=mutate_after_decode), self.assertRaisesRegex(PipelineError, 'snapshot hash changed'):
            self.extractor.extract(self.source, self.job)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.job.exists())

    def test_readonly_watchdog_rejects_replaced_root_and_keeps_mutable_caps(self):
        from dataclasses import replace
        from resource_pipeline.adapters import BubblewrapSandbox, _working_size
        self.sources(3)
        self.extractor.sandbox = BubblewrapSandbox(executable=self.runtime, runtime_roots=())
        def live_checks(plan, limits, job, **kwargs):
            self.decode(plan, limits, job, **kwargs)
            sealed = kwargs['reserved_readonly']
            options = {'reserved_output': kwargs['reserved_output'], 'reserved_readonly': sealed}
            usage = _working_size(self.job, limits, **options)
            for bound in (replace(limits, total_bytes=usage.bytes - 1), replace(limits, files=usage.paths - 1)):
                with self.subTest(bound=bound), self.assertRaisesRegex(PipelineError, 'capacity'):
                    _working_size(self.job, bound, **options)
            original = job / 'inputs'
            original.rename(job / 'changed-inputs')
            original.mkdir()
            with self.assertRaisesRegex(PipelineError, 'root changed'):
                _working_size(self.job, limits, **options)
            original.rmdir()
            (job / 'changed-inputs').rename(original)
        with patch.object(TrustedAdapterRunner, '_execute', side_effect=live_checks):
            self.extractor.extract(self.source, self.job)

    def test_copy_reuses_verified_inventory_but_rejects_drift_and_rehashes_both_trees(self):
        from resource_pipeline.adapters import _copy_tree
        self.sources(2)
        expected = tree_inventory(self.source)
        target = self.root / 'proof-copy'
        with patch('resource_pipeline.adapters.tree_inventory', wraps=tree_inventory) as inventory:
            result = _copy_tree(self.source, target, AdapterLimits(), expected_inventory=expected)
        self.assertEqual(result, expected)
        self.assertEqual([call.args[0] for call in inventory.call_args_list], [target, self.source])
        next(self.source.rglob('*.xnb')).write_bytes(header(129))
        with self.assertRaisesRegex(PipelineError, 'changed'):
            _copy_tree(self.source, self.root / 'drift-copy', AdapterLimits(), expected_inventory=expected)

    def test_custom_sandbox_never_uses_readonly_pruning(self):
        self.sources(3)
        with patch.object(TrustedAdapterRunner, '_execute', side_effect=self.decode):
            self.extractor.extract(self.source, self.job)
        self.assertTrue(all(kwargs['reserved_readonly'] == () for _, _, kwargs in self.calls))

    def test_child_metadata_inventory_keeps_limits_and_uses_decoded_file_hash(self):
        self.sources(3)
        # The mocked converter's hash is checked against inspect_png; no hash
        # claimed by the child is used as the ledger's filesystem evidence.
        with patch('resource_pipeline.textures.tree_inventory', wraps=tree_inventory) as inventory, \
             patch.object(TrustedAdapterRunner, '_execute', side_effect=self.decode):
            result = self.extractor.extract(self.source, self.job)
        child_scans = [call for call in inventory.call_args_list if call.kwargs.get('include_hashes') is False]
        self.assertEqual(len(child_scans), 2)
        self.assertTrue(all(row['sha256'] == sha256(self.png) for row in result['images']))
        # Final merged tree and original-source inventories are still full hashes.
        self.assertTrue(any(call.args[0] == self.job / 'output' and call.kwargs.get('include_hashes', True)
                            for call in inventory.call_args_list))
        root = self.root / 'metadata-only'
        root.mkdir()
        (root / 'file').write_bytes(b'1234')
        self.assertEqual(tree_inventory(root, include_hashes=False), [{'path': 'file', 'bytes': 4}])
        with self.assertRaisesRegex(PipelineError, 'byte budget'):
            tree_inventory(root, AdapterLimits(total_bytes=3), include_hashes=False)
        (root / 'link').symlink_to(root / 'file')
        with self.assertRaisesRegex(PipelineError, 'Links and special'):
            tree_inventory(root, include_hashes=False)
