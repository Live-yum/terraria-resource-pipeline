"""Automatic raw XNB decoding with a fixed installed, hash-pinned converter.

Texture success is separate from complete semantic extraction/publication.
"""
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import os
import re
import shutil
import time
from threading import Lock

from .adapters import (AdapterLimits, BubblewrapSandbox, CommandPlan, TrustedAdapterRunner,
                       _copy_tree, _working_size, file_digest, tree_digest, tree_inventory, WorkingUsage, ReadOnlyTreeReservation)
from .contracts import package_file
from .decoded_images import inspect_png
from .texture_batches import TextureBatchPolicy, plan_texture_batches
from .security import PipelineError, atomic_write, canonical_json, read_json, relative_path, sha256


def texture_surface_formats(rows: list[dict]) -> dict[str, int]:
    """Count only explicit supported format IDs; legacy receipts remain unknown."""
    counts = {}
    for row in rows:
        if not isinstance(row, dict):
            raise PipelineError('Invalid texture format row')
        if 'surfaceFormat' not in row:
            key = 'unknown'
        else:
            value = row['surfaceFormat']
            if type(value) is not int or value not in (0, 4, 5, 6):
                raise PipelineError('Invalid texture SurfaceFormat receipt')
            key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


@dataclass(frozen=True)
class TextureTool:
    root: Path
    sha256: str
    dotnet: Path


@dataclass(frozen=True)
class _TextureOutputLedger:
    """A frozen reservation for verified files outside the live child sandbox.

    Entry bytes/hashes come from the parent's filesystem inventory, not receipt
    claims. Only successfully validated PNGs are merged. Parent-created paths
    are accounted exactly, including directory sharing and file/dir conflicts.
    """
    files: frozenset[tuple[str, int, str]] = frozenset()
    directories: frozenset[str] = frozenset()
    bytes: int = 0

    def __post_init__(self):
        if (type(self.files) is not frozenset or type(self.directories) is not frozenset
                or type(self.bytes) is not int or self.bytes < 0
                or self.bytes != sum(size for _, size, _ in self.files)):
            raise ValueError('Texture ledger must equal its frozen actual-file inventory')

    @property
    def usage(self) -> WorkingUsage:
        return WorkingUsage(bytes=self.bytes, paths=len(self.files) + len(self.directories))

    def advance(self, artifacts: list[dict], check) -> '_TextureOutputLedger':
        files = set(self.files)
        directories = set(self.directories)
        file_names = {name.casefold(): name for name, _, _ in files}
        directory_names = {name.casefold(): name for name in directories}
        total = self.bytes
        for item in artifacts:
            check()
            name = relative_path(item['path'])
            size, digest = item['bytes'], item['sha256']
            if (not name.lower().endswith('.png') or type(size) is not int or size < 0
                    or not isinstance(digest, str) or re.fullmatch('[a-f0-9]{64}', digest) is None):
                raise PipelineError('Invalid verified texture output inventory')
            if name.casefold() in file_names or name.casefold() in directory_names:
                raise PipelineError('Texture output ledger path collision')
            for parent in Path(name).parents:
                if parent == Path('.'):
                    continue
                directory = parent.as_posix()
                key = directory.casefold()
                if key in file_names or (key in directory_names and directory_names[key] != directory):
                    raise PipelineError('Texture output ledger path collision')
                directories.add(directory)
                directory_names[key] = directory
            files.add((name, size, digest))
            file_names[name.casefold()] = name
            total += size
        return _TextureOutputLedger(frozenset(files), frozenset(directories), total)

    def verify(self, actual: list[dict]) -> None:
        pngs = frozenset((row['path'], row['bytes'], row['sha256']) for row in actual
                         if row['path'] != 'texture-report.json')
        if pngs != self.files:
            raise PipelineError('Merged texture output differs from verified ledger')


# One active decoder job per worker process, including separately constructed
# service instances. There is no upload-controlled parallelism or retry storm.
_TEXTURE_SLOT = Lock()


class TextureExtractor:
    supports_cancellation = True

    def __init__(self, tool: TextureTool, sandbox=None, *, policy=TextureBatchPolicy()):
        self.tool = tool
        self.sandbox = sandbox or BubblewrapSandbox()
        self.policy = policy

    def extract(self, source: Path, job: Path, *, cancel_check=None, deadline=None) -> dict:
        # The supplied deadline can only reduce the existing 120-second budget.
        deadline = min(time.monotonic() + 120, deadline) if deadline is not None else time.monotonic() + 120
        def check():
            if cancel_check is not None and cancel_check():
                raise PipelineError('Texture job cancelled; no output was approved')
            if time.monotonic() >= deadline:
                raise PipelineError('Texture job timed out; no output was approved')
        check()
        if not _TEXTURE_SLOT.acquire(blocking=False):
            raise PipelineError('Texture worker is busy; no second decoder was started')
        try:
            return self._extract(source, job, check, cancel_check, deadline)
        finally:
            _TEXTURE_SLOT.release()

    def _extract(self, source, job, check, cancel_check, deadline):
        limits = AdapterLimits(timeout_seconds=120, memory_bytes=2 * 1024**3)
        inventory = tree_inventory(source, limits, checkpoint=check)
        check()
        xnb = [row for row in inventory if row['path'].lower().endswith('.xnb')]
        if not xnb:
            return {'status': 'NO_XNB_PAYLOAD', 'imageCount': 0, 'executedInput': False,
                    'extractionComplete': False, 'publishable': False}
        expected = {str(Path(row['path']).with_suffix('.png')): row for row in xnb}
        if len({name.casefold() for name in expected}) != len(xnb):
            raise PipelineError('Texture output path collision')
        batches = plan_texture_batches(source, xnb, self.policy, check)
        tool_inventory = tree_inventory(self.tool.root, limits, checkpoint=check)
        if sha256(canonical_json(tool_inventory)) != self.tool.sha256:
            raise PipelineError('Installed texture converter fingerprint mismatch')
        snapshot_bytes = sum(row['bytes'] for row in inventory) + sum(row['bytes'] for row in tool_inventory)
        request_bytes = len(canonical_json({'input': '/inputs/package'}))
        if snapshot_bytes + request_bytes >= limits.total_bytes:
            raise PipelineError('Texture snapshots exceed whole-job capacity')
        if not self.tool.dotnet.is_absolute() or not self.tool.dotnet.is_file():
            raise PipelineError('Configured .NET runtime is unavailable')
        job = job.absolute()
        if job.exists() or any(p.is_symlink() for p in [job, *job.parents]):
            raise PipelineError('Texture job must be a new private directory')
        if any(job == root.resolve() or root.resolve() in job.parents for root in (source, self.tool.root)):
            raise PipelineError('Texture workspace must be separate from input/tool roots')
        job.mkdir(parents=True, mode=0o700)
        try:
            for name in ('inputs', 'output', 'work', 'work/home', 'work/tmp'):
                (job / name).mkdir()
            check()
            source_snapshot = _copy_tree(source, job / 'inputs/package', limits, checkpoint=check,
                                         expected_inventory=inventory)
            check()
            tool_snapshot = _copy_tree(self.tool.root, job / 'tool', limits, checkpoint=check,
                                       expected_inventory=tool_inventory)
            check()
            # _copy_tree has just independently rehashed both destination and
            # original source. Reuse that proof instead of hashing the same
            # destination twice before any process can access it.
            if source_snapshot != inventory or tool_snapshot != tool_inventory:
                raise PipelineError('Texture source/tool snapshot changed after planning')
            atomic_write(job / 'inputs/request.json', canonical_json({'input': '/inputs/package'}))
            # Read-only snapshot bytes are reserved once; all mutable directories
            # plus the sole active child are watched together, never per batch.
            immutable_bytes = immutable_paths = 0
            for root in (job / 'inputs', job / 'tool'):
                for path in root.rglob('*'):
                    check()
                    immutable_paths += 1
                    if path.is_file():
                        immutable_bytes += path.stat().st_size
            working = replace(limits, total_bytes=limits.total_bytes - immutable_bytes,
                              files=limits.files - immutable_paths)
            if working.total_bytes <= 0 or working.files <= 0:
                raise PipelineError('Texture snapshots exceed whole-job capacity')
            all_rows, all_skipped, total_pixels = [], [], 0
            isolation = None
            batched = len(batches.batches) > 1
            ledger = _TextureOutputLedger()
            for batch_index, batch in enumerate(batches.batches):
                check()
                if not batched:
                    child = job
                else:
                    # The ledger charges already-validated PNGs exactly once.
                    # Parent output is NOT mounted by this unique child; scan all
                    # parent work (including the child) against the same budget.
                    usage = _working_size(job, working, checkpoint=check, reserved_output=ledger.usage)
                    copies = sum(row['bytes'] for row in batch) + sum(row['bytes'] for row in tool_inventory) + request_bytes
                    if usage.bytes + copies > working.total_bytes:
                        raise PipelineError('Texture batch snapshots exceed whole-job capacity')
                    child = job / 'work' / f'child-{batch_index:04d}'
                    for folder in ('inputs/package', 'output', 'work/home', 'work/tmp'):
                        (child / folder).mkdir(parents=True)
                    self._copy_batch(job / 'inputs/package', child / 'inputs/package', batch, check)
                    check()
                    _copy_tree(job / 'tool', child / 'tool', limits, checkpoint=check, expected_inventory=tool_inventory)
                    check()
                    atomic_write(child / 'inputs/request.json', canonical_json({'input': '/inputs/package'}))
                reservation = ledger.usage if batched else None
                _working_size(job, working, checkpoint=check, reserved_output=reservation)
                report, child_isolation = self._run_child(child, job, working, cancel_check, deadline, check,
                                                          reserved_output=reservation,
                                                          readonly_expected=(batch, tool_inventory) if batched else None)
                rows, skipped, pixels, png_inventory = self._validate_report(child / 'output', batch, limits, check)
                total_pixels += pixels
                if total_pixels > self.policy.total_pixels:
                    raise PipelineError('Texture job total decoded-pixel budget exceeded')
                if isolation is not None and isolation != child_isolation:
                    raise PipelineError('Texture child isolation changed')
                isolation = child_isolation
                if child != job:
                    next_ledger = ledger.advance(png_inventory, check)
                    usage = _working_size(job, working, checkpoint=check, reserved_output=ledger.usage)
                    # Renames add no file bytes or file entries. New parent
                    # directories temporarily coexist with child directories.
                    if usage.paths + len(next_ledger.directories - ledger.directories) > working.files:
                        raise PipelineError('Texture merge exceeds whole-job path capacity')
                    for row in rows:
                        check()
                        target = job / 'output' / row['output']
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if target.exists():
                            raise PipelineError('Duplicate merged texture output')
                        os.replace(package_file(child / 'output', row['output']), target)
                        target.chmod(0o400)
                    shutil.rmtree(child)
                    ledger = next_ledger
                all_rows.extend(rows)
                all_skipped.extend(skipped)
                _working_size(job, working, checkpoint=check, reserved_output=ledger.usage if batched else None)
            check()
            # No partial receipt is reusable or publishable. Only the verified
            # complete union is written at the established output-root contract.
            report = {**report, 'images': all_rows, 'skipped': all_skipped, 'imageCount': len(all_rows)}
            report['batching'] = {'batchCount': len(batches.batches), 'maxChildFiles': self.policy.files_per_child,
                'maxChildPixels': self.policy.child_pixels, 'maxChildren': self.policy.max_children,
                'totalPixels': total_pixels, 'maxTotalPixels': self.policy.total_pixels,
                'declaredExpandedBytes': batches.declared_expanded_bytes,
                'maxDeclaredExpandedBytes': self.policy.expanded_bytes,
                'workingByteLimit': limits.total_bytes, 'maxConcurrentChildren': 1,
                'jobTimeoutSeconds': 120, 'partialReuse': False}
            report_bytes = canonical_json(report)
            usage = _working_size(job, working, checkpoint=check, reserved_output=ledger.usage if batched else None)
            # atomic_write temporarily retains the new report alongside an old
            # single-child report, if any; charge that temporary file too.
            if (len(report_bytes) > limits.file_bytes or usage.bytes + len(report_bytes) > working.total_bytes
                    or usage.paths + 1 > working.files):
                raise PipelineError('Texture report exceeds whole-job capacity')
            atomic_write(job / 'output/texture-report.json', report_bytes)
            # Final full scans are mandatory: the optimization only removes
            # repeated traversal while a sandboxed child is running.
            _working_size(job, working, checkpoint=check)
            actual = tree_inventory(job / 'output', limits, checkpoint=check)
            if batched:
                ledger.verify(actual)
                actual_directories = set()
                for path in (job / 'output').rglob('*'):
                    check()
                    if path.is_dir():
                        actual_directories.add(path.relative_to(job / 'output').as_posix())
                if actual_directories != ledger.directories:
                    raise PipelineError('Merged texture directories differ from verified ledger')
            if {r['path'] for r in actual} != {*(row['output'] for row in all_rows), 'texture-report.json'}:
                raise PipelineError('Unexpected merged texture output artifact')
            output_hashes = {row['path']: row['sha256'] for row in actual}
            if any(output_hashes[row['output']] != row['sha256'] for row in all_rows):
                raise PipelineError('Merged texture output changed after verification')
            if tree_inventory(source, limits, checkpoint=check) != inventory:
                raise PipelineError('Original texture inputs changed')
            if tree_digest(self.tool.root, limits, checkpoint=check) != self.tool.sha256:
                raise PipelineError('Original texture converter changed')
            check()
            result = {**report, 'status': 'TEXTURES_DECODED' if all_rows else 'NO_SUPPORTED_TEXTURES',
                'isolation': isolation, 'toolSha256': self.tool.sha256,
                'surfaceFormatCounts': texture_surface_formats(all_rows),
                'outputSha256': sha256(canonical_json(actual))}
            return result
        except BaseException:
            # Fresh retry only. Never leave an accepted-looking receipt or PNG
            # subset behind; uploaded archives and source evidence are untouched.
            shutil.rmtree(job)
            raise

    @staticmethod
    def _read_checked(path, maximum, check):
        """Read bounded PNG bytes with the same per-MiB cancellation budget."""
        result = BytesIO()
        with path.open('rb') as stream:
            while True:
                check()
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                if result.tell() + len(chunk) > maximum:
                    raise PipelineError('Texture output exceeds file limit')
                result.write(chunk)
        check()
        return result.getvalue()

    @staticmethod
    def _copy_batch(source, destination, batch, check):
        for row in batch:
            check()
            target = destination / row['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            copied = 0
            with package_file(source, row['path']).open('rb') as read, target.open('xb') as write:
                while True:
                    check()
                    chunk = read.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > row['bytes']:
                        raise PipelineError('Texture batch snapshot mismatch')
                    write.write(chunk)
            check()
            if target.stat().st_size != row['bytes'] or file_digest(target, checkpoint=check) != row['sha256']:
                raise PipelineError('Texture batch snapshot mismatch')
            target.chmod(0o400)

    @staticmethod
    def _validate_readonly_plan(plan, child):
        """Accept only exact fixed bwrap RO bindings with no bind/FD aliases."""
        args = plan.argv[:plan.argv.index('--')]
        arities = {'--unshare-all': 0, '--die-with-parent': 0, '--new-session': 0, '--clearenv': 0,
                   '--cap-drop': 1, '--proc': 1, '--dev': 1, '--remount-ro': 1, '--chdir': 1,
                   '--bind': 2, '--ro-bind': 2, '--bind-try': 2, '--ro-bind-try': 2, '--setenv': 2}
        cursor = 1
        while cursor < len(args):
            arity = arities.get(args[cursor])
            if arity is None or cursor + arity >= len(args):
                raise PipelineError('Read-only snapshot reservation rejects unknown sandbox options')
            cursor += arity + 1
        for flag in ('--unshare-all', '--die-with-parent', '--new-session', '--clearenv'):
            if flag not in args:
                raise PipelineError('Read-only snapshot reservation requires fixed sandbox isolation')
        if not any(args[index:index + 2] == ('--cap-drop', 'ALL') for index in range(len(args) - 1)):
            raise PipelineError('Read-only snapshot reservation requires dropped capabilities')
        expected = {(child / 'inputs').resolve(): '/inputs', (child / 'tool').resolve(): '/tool'}
        found = set()
        for index, argument in enumerate(args):
            if argument in ('--file', '--preserve-fds', '--sync-fd') or ('bind' in argument and argument.startswith('--')
                    and argument not in ('--bind', '--ro-bind', '--bind-try', '--ro-bind-try')):
                raise PipelineError('Read-only snapshot reservation cannot use FD or unknown bind aliases')
            if argument not in ('--bind', '--ro-bind', '--bind-try', '--ro-bind-try'):
                continue
            if index + 2 >= len(args):
                raise PipelineError('Invalid sandbox bind plan')
            mounted, destination = Path(args[index + 1]).resolve(), Path(args[index + 2])
            if mounted in expected and destination.as_posix() == expected[mounted]:
                if argument != '--ro-bind' or mounted in found:
                    raise PipelineError('Snapshot must have exactly one read-only binding')
                found.add(mounted)
                continue
            for root, target in expected.items():
                namespace = Path(target)
                if (mounted == root or mounted in root.parents or root in mounted.parents
                        or destination == namespace or destination in namespace.parents or namespace in destination.parents):
                    raise PipelineError('Read-only snapshot cannot have overlapping mount aliases')
        if found != set(expected):
            raise PipelineError('Read-only snapshot bindings are missing')

    def _run_child(self, child, job, limits, cancel_check, deadline, check, *, reserved_output=None,
                   readonly_expected=None):
        check()
        if reserved_output is not None and child == job:
            raise PipelineError('Cannot reserve output exposed to the live child')
        spec = SimpleNamespace(command=(str(self.tool.dotnet), '/tool/TextureExtractor.dll'))
        plan = self.sandbox.plan(spec, child)
        if reserved_output is not None:
            output = (job / 'output').resolve()
            for index, argument in enumerate(plan.argv[:-1]):
                if argument in ('--bind', '--ro-bind', '--bind-try', '--ro-bind-try'):
                    mounted = Path(plan.argv[index + 1]).resolve()
                    if mounted == output or mounted in output.parents or output in mounted.parents:
                        raise PipelineError('Reserved parent output must stay outside the child sandbox')
        readonly = ()
        # Custom/injected sandbox implementations keep the complete scan. The
        # pruning optimization requires the concrete fixed Bubblewrap backend.
        if type(self.sandbox) is BubblewrapSandbox and child != job and readonly_expected is not None:
            self._validate_readonly_plan(plan, child)
            batch, tool_inventory = readonly_expected
            request = canonical_json({'input': '/inputs/package'})
            input_inventory = [{'path': 'package/' + row['path'], 'bytes': row['bytes'], 'sha256': row['sha256']}
                               for row in batch]
            input_inventory.append({'path': 'request.json', 'bytes': len(request), 'sha256': sha256(request)})
            readonly = (ReadOnlyTreeReservation.capture(child / 'inputs', input_inventory, limits, check),
                        ReadOnlyTreeReservation.capture(child / 'tool', tool_inventory, limits, check))
        # Bound .NET GC reservation too, so RLIMIT_AS remains meaningful.
        argv = list(plan.argv)
        end = argv.index('--')
        argv[end:end] = ['--setenv', 'DOTNET_GCHeapHardLimit', '0x20000000',
                         '--setenv', 'DOTNET_EnableDiagnostics', '0',
                         '--setenv', 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT', '1',
                         '--setenv', 'DOTNET_CLI_TELEMETRY_OPTOUT', '1']
        plan = CommandPlan(tuple(argv), plan.cwd, plan.env, plan.isolation)
        remaining = replace(limits, timeout_seconds=max(0.001, deadline - time.monotonic()))
        try:
            TrustedAdapterRunner._execute(plan, remaining, child, cancel_check=cancel_check, watchdog_job=job,
                                          reserved_output=reserved_output, reserved_readonly=readonly)
        except PipelineError as failure:
            check()
            diagnostic = child / 'output/texture-error.json'
            if diagnostic.is_file() and not diagnostic.is_symlink():
                report = read_json(diagnostic, 4096)
                stages = {'INPUT', 'HEADER', 'LZX_FRAMING', 'LZX_DECODE', 'TEXTURE_PAYLOAD'}
                errors = {'InvalidDataException', 'EndOfStreamException', 'OverflowException',
                          'ArgumentException', 'IndexOutOfRangeException', 'OutOfMemoryException'}
                if isinstance(report, dict) and report.get('stage') in stages and report.get('error') in errors:
                    code = re.search(r"\(exit=(-?[0-9]+)\)", str(failure))
                    suffix = f"(exit={code[1]})" if code else ''
                    raise PipelineError(f"TEXTURE_REJECTED:{report['stage']}:{report['error']}" + suffix) from failure
            raise
        check()
        # The process group is gone before full snapshot hashes are rechecked.
        for reservation in readonly:
            reservation.verify(limits, check)
        report = read_json(package_file(child / 'output', 'texture-report.json'))
        check()
        return report, plan.isolation

    def _validate_report(self, output, xnb, limits, check):
        # The child and all descendants have stopped. Enumerate bounded file
        # metadata once; the independent PNG decode below computes each actual
        # file SHA256 and RGBA SHA256. The final merged tree is rehashed too.
        actual = tree_inventory(output, limits, checkpoint=check, include_hashes=False)
        actual_by_path = {entry['path']: entry for entry in actual}
        check()
        report = read_json(package_file(output, 'texture-report.json'))
        check()
        if not isinstance(report, dict):
            raise PipelineError('Invalid texture conversion receipt')
        rows, skipped = report.get('images'), report.get('skipped')
        if (not isinstance(rows, list) or not isinstance(skipped, list)
                or type(report.get('imageCount')) is not int or report['imageCount'] != len(rows)
                or len(rows) + len(skipped) != len(xnb)
                or report.get('executedInput') is not False or report.get('publishable') is not False
                or report.get('extractionComplete') is not False):
            raise PipelineError('Incomplete or invalid texture conversion receipt')
        texture_surface_formats(rows)
        expected = {str(Path(row['path']).with_suffix('.png')): row for row in xnb}
        seen, produced, pixels = set(), set(), 0
        for item in skipped:
            check()
            if not isinstance(item, dict) or not isinstance(item.get('input'), str):
                raise PipelineError('Invalid skipped XNB receipt')
            name = str(Path(item['input']).with_suffix('.png'))
            if (name not in expected or name in seen or item['input'] != expected[name]['path']
                    or item.get('sourceSha256') != expected[name]['sha256']):
                raise PipelineError('Invalid skipped XNB receipt')
            seen.add(name)
        for row in rows:
            check()
            name = row.get('output')
            if not isinstance(name, str) or name in seen or name not in expected:
                raise PipelineError('Unexpected or duplicate texture output')
            seen.add(name)
            produced.add(name)
            original = expected[name]
            if row.get('input') != original['path'] or row.get('sourceSha256') != original['sha256']:
                raise PipelineError('Texture source receipt mismatch')
            decoded = inspect_png(self._read_checked(package_file(output, name), limits.file_bytes, check))
            check()
            if name not in actual_by_path or actual_by_path[name]['bytes'] != decoded['bytes']:
                raise PipelineError('Texture output changed during receipt verification')
            actual_by_path[name]['sha256'] = decoded['sha256']
            if (any(row.get(k) != decoded[k] for k in ('sha256', 'width', 'height'))
                    or any(type(row.get(k)) is not int for k in ('width', 'height'))
                    or ('rgbaSha256' in row and row['rgbaSha256'] != decoded['rgbaSha256'])
                    or ('bytes' in row and (type(row['bytes']) is not int or row['bytes'] != decoded['bytes']))):
                raise PipelineError('Texture pixel/output receipt mismatch')
            if 'mipLevels' in row or 'exportedMip' in row:
                if (type(row.get('mipLevels')) is not int or type(row.get('exportedMip')) is not int
                        or not 1 <= row['mipLevels'] <= max(decoded['width'], decoded['height']).bit_length()
                        or row['exportedMip'] != 0):
                    raise PipelineError('Invalid texture mip receipt')
            # Legacy receipts omit mip metadata; do not invent a mip count.
            row['rgbaSha256'] = decoded['rgbaSha256']
            pixels += decoded['width'] * decoded['height']
            if decoded['width'] * decoded['height'] > self.policy.image_pixels or pixels > self.policy.child_pixels:
                raise PipelineError('Texture child decoded-pixel budget exceeded')
            check()
        if {r['path'] for r in actual} != {*produced, 'texture-report.json'}:
            raise PipelineError('Unexpected texture output artifact')
        return rows, skipped, pixels, [actual_by_path[row['output']] for row in rows]


def configured_texture_extractor():
    """Operator environment only; no upload field controls executable or flags."""
    root, digest, runtime = (os.environ.get(name) for name in
        ('RESOURCE_TEXTURE_TOOL_ROOT', 'RESOURCE_TEXTURE_TOOL_SHA256', 'RESOURCE_DOTNET_PATH'))
    if not any((root, digest, runtime)):
        return None
    if not all((root, digest, runtime)):
        raise PipelineError('Incomplete texture tool installation configuration')
    return TextureExtractor(TextureTool(Path(root), digest, Path(runtime)))
