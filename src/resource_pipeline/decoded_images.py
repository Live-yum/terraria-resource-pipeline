"""Bounded, data-only inspection of an existing PNG export, not an extractor.

No source code, game assemblies, paths or commands from a package are executed.
This inventory is intentionally not a normalized publication envelope.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import argparse
import hashlib
import json
import re
import tempfile
import warnings

from PIL import Image, UnidentifiedImageError

from .security import ArchiveLimits, PipelineError, extract_zip, sha256

MAX_PIXELS = 16_000_000


def inspect_png(data: bytes) -> dict:
    """Decode bytes with explicit limits; retain source and decoded pixel hashes."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format != 'PNG' or image.width * image.height > MAX_PIXELS:
                    raise PipelineError('Expected bounded PNG image')
                if getattr(image, 'n_frames', 1) != 1:
                    raise PipelineError('Animated PNG is not a single texture')
                image.verify()
            with Image.open(BytesIO(data)) as image:
                image.load()
                pixels = image.convert('RGBA')
                pixel_bytes = pixels.tobytes()
                dimensions = f'{image.width}x{image.height}:RGBA:'.encode('ascii')
                return {'bytes': len(data), 'sha256': sha256(data),
                        'width': image.width, 'height': image.height,
                        'rgbaSha256': sha256(dimensions + pixel_bytes)}
    except (OSError, SyntaxError, ValueError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise PipelineError('Invalid or oversized PNG export') from exc


def inspect_export(archive: Path, *, declared_version: str | None = None,
                   limits: ArchiveLimits = ArchiveLimits()) -> dict:
    """Inspect a local ZIP; never trust its name/version or publish its contents."""
    if declared_version is not None and not re.fullmatch(r'[0-9]+(?:\.[0-9]+){2,3}', declared_version):
        raise PipelineError('Invalid version hint')
    if archive.is_symlink() or not archive.is_file():
        raise PipelineError('Expected regular local archive')
    if archive.stat().st_size > limits.archive_bytes:
        raise PipelineError('Archive exceeds size limit')
    # Snapshot the bounded archive to prevent hashing different bytes from those
    # inspected if the caller changes the original while the job is running.
    with tempfile.TemporaryDirectory(prefix='decoded-export-') as temporary:
        root = Path(temporary)
        snapshot = root / 'source.zip'
        digest = hashlib.sha256()
        count = 0
        with archive.open('rb') as source, snapshot.open('xb') as target:
            while chunk := source.read(1024 * 1024):
                count += len(chunk)
                if count > limits.archive_bytes:
                    raise PipelineError('Archive exceeds size limit')
                digest.update(chunk)
                target.write(chunk)
        extracted = root / 'images'
        extract_zip(snapshot, extracted, limits)
        rows = []
        total_pixels = 0
        for path in sorted(extracted.rglob('*')):
            if path.is_dir():
                continue
            if path.suffix.lower() != '.png':
                raise PipelineError('Decoded export must contain PNG files only')
            row = inspect_png(path.read_bytes())
            total_pixels += row['width'] * row['height']
            if total_pixels > 256_000_000:
                raise PipelineError('Decoded pixel budget exceeded')
            rows.append({'path': path.relative_to(extracted).as_posix(), **row})
        if not rows:
            raise PipelineError('No PNG images in export')
        unique = len({row['rgbaSha256'] for row in rows})
        return {'schemaVersion': 1, 'kind': 'decoded-image-inventory',
                'sourceSha256': digest.hexdigest(), 'sourceBytes': count,
                'declaredVersion': declared_version, 'versionVerified': False,
                'executedInput': False, 'extractionComplete': False,
                'publishable': False, 'imageCount': len(rows),
                'uniquePixelImages': unique, 'duplicatePixelImages': len(rows) - unique,
                'images': rows,
                'blockers': ['ORIGINAL_XNB_PROVENANCE_UNVERIFIED',
                             'GAME_VERSION_UNVERIFIED', 'SEMANTIC_COVERAGE_MISSING',
                             'PUBLICATION_RIGHTS_UNVERIFIED']}


def main() -> None:
    parser = argparse.ArgumentParser(description='Inspect a private decoded PNG ZIP without publishing')
    parser.add_argument('archive', type=Path)
    parser.add_argument('--version-hint')
    args = parser.parse_args()
    print(json.dumps(inspect_export(args.archive, declared_version=args.version_hint),
                     ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
