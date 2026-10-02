"""Automatic raw XNB decoding with a fixed installed, hash-pinned converter.

Texture success is separate from complete semantic extraction/publication.
"""
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import os
import re

from .adapters import (AdapterLimits, BubblewrapSandbox, CommandPlan, TrustedAdapterRunner,
                       _copy_tree, tree_digest, tree_inventory)
from .contracts import package_file
from .decoded_images import inspect_png
from .security import PipelineError, atomic_write, canonical_json, read_json, sha256


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


class TextureExtractor:
    def __init__(self, tool: TextureTool, sandbox=None):
        self.tool = tool
        self.sandbox = sandbox or BubblewrapSandbox()

    def extract(self, source: Path, job: Path) -> dict:
        limits = AdapterLimits(timeout_seconds=120, memory_bytes=2 * 1024**3)
        inventory = tree_inventory(source, limits)
        xnb = [row for row in inventory if row['path'].lower().endswith('.xnb')]
        if not xnb:
            return {'status': 'NO_XNB_PAYLOAD', 'imageCount': 0, 'executedInput': False,
                    'extractionComplete': False, 'publishable': False}
        expected = {str(Path(row['path']).with_suffix('.png')): row for row in xnb}
        if len({name.casefold() for name in expected}) != len(xnb):
            raise PipelineError('Texture output path collision')
        if tree_digest(self.tool.root, limits) != self.tool.sha256:
            raise PipelineError('Installed texture converter fingerprint mismatch')
        if not self.tool.dotnet.is_absolute() or not self.tool.dotnet.is_file():
            raise PipelineError('Configured .NET runtime is unavailable')
        if job.exists() or any(p.is_symlink() for p in [job, *job.parents]):
            raise PipelineError('Texture job must be a new private directory')
        job.mkdir(parents=True)
        for name in ('inputs', 'output', 'work', 'work/home', 'work/tmp'):
            (job / name).mkdir()
        _copy_tree(source, job / 'inputs/package', limits)
        _copy_tree(self.tool.root, job / 'tool', limits)
        if tree_digest(job / 'tool', limits) != self.tool.sha256:
            raise PipelineError('Converter snapshot mismatch')
        atomic_write(job / 'inputs/request.json', canonical_json({'input': '/inputs/package'}))
        spec = SimpleNamespace(command=(str(self.tool.dotnet), '/tool/TextureExtractor.dll'))
        plan = self.sandbox.plan(spec, job)
        # Bound .NET GC reservation too, so RLIMIT_AS remains meaningful.
        argv = list(plan.argv)
        end = argv.index('--')
        argv[end:end] = ['--setenv', 'DOTNET_GCHeapHardLimit', '0x20000000',
                         '--setenv', 'DOTNET_EnableDiagnostics', '0',
                         '--setenv', 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT', '1',
                         '--setenv', 'DOTNET_CLI_TELEMETRY_OPTOUT', '1']
        plan = CommandPlan(tuple(argv), plan.cwd, plan.env, plan.isolation)
        try:
            TrustedAdapterRunner._execute(plan, limits, job)
        except PipelineError as failure:
            diagnostic = job / 'output/texture-error.json'
            if diagnostic.is_file() and not diagnostic.is_symlink():
                report = read_json(diagnostic, 4096)
                stages = {'INPUT', 'HEADER', 'LZX_FRAMING', 'LZX_DECODE', 'TEXTURE_PAYLOAD'}
                errors = {'InvalidDataException', 'EndOfStreamException', 'OverflowException',
                          'ArgumentException', 'IndexOutOfRangeException', 'OutOfMemoryException'}
                if report.get('stage') in stages and report.get('error') in errors:
                    code = re.search(r"\(exit=(-?[0-9]+)\)", str(failure))
                    suffix = f"(exit={code[1]})" if code else ""
                    raise PipelineError(f"TEXTURE_REJECTED:{report['stage']}:{report['error']}" + suffix) from failure
            raise

        actual = tree_inventory(job / 'output', limits)
        report = read_json(package_file(job / 'output', 'texture-report.json'))
        rows = report.get('images', [])
        skipped = report.get('skipped', [])
        if (report.get('imageCount') != len(rows) or len(rows) + len(skipped) != len(xnb)
                or report.get('executedInput') is not False
                or report.get('publishable') is not False
                or report.get('extractionComplete') is not False):
            raise PipelineError('Incomplete or invalid texture conversion receipt')
        format_counts = texture_surface_formats(rows)
        seen = set()
        for item in skipped:
            name = str(Path(item['input']).with_suffix('.png'))
            if name not in expected or name in seen or item['sourceSha256'] != expected[name]['sha256']:
                raise PipelineError('Invalid skipped XNB receipt')
            seen.add(name)
        produced = set()
        for row in rows:
            name = row['output']
            if name in seen or name not in expected:
                raise PipelineError('Unexpected or duplicate texture output')
            seen.add(name)
            produced.add(name)
            original = expected[name]
            if row['input'] != original['path'] or row['sourceSha256'] != original['sha256']:
                raise PipelineError('Texture source receipt mismatch')
            png = package_file(job / 'output', name).read_bytes()
            decoded = inspect_png(png)
            if any(row[k] != decoded[k] for k in ('sha256', 'width', 'height')):
                raise PipelineError('Texture pixel/output receipt mismatch')
        if {r['path'] for r in actual} != {*produced, 'texture-report.json'}:
            raise PipelineError('Unexpected texture output artifact')
        if tree_inventory(source, limits) != inventory:
            raise PipelineError('Original texture inputs changed')
        return {**report, 'status': 'TEXTURES_DECODED' if rows else 'NO_SUPPORTED_TEXTURES', 'isolation': plan.isolation,
                'toolSha256': self.tool.sha256, 'surfaceFormatCounts': format_counts,
                'outputSha256': tree_digest(job / 'output', limits)}


def configured_texture_extractor():
    """Operator environment only; no upload field controls executable or flags."""
    root, digest, runtime = (os.environ.get(name) for name in
        ('RESOURCE_TEXTURE_TOOL_ROOT', 'RESOURCE_TEXTURE_TOOL_SHA256', 'RESOURCE_DOTNET_PATH'))
    if not any((root, digest, runtime)):
        return None
    if not all((root, digest, runtime)):
        raise PipelineError('Incomplete texture tool installation configuration')
    return TextureExtractor(TextureTool(Path(root), digest, Path(runtime)))
