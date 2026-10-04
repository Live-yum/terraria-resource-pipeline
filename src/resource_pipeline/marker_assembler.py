"""Bounded fresh marker crops from PNGs and a separately reviewed crop policy.

The policy is app presentation input, not automatically proven game semantics.
Source/texture hashes are verified against supplied bytes; the receipt cannot
upgrade a selector/crop to producer proof or authorize publication.
"""
from __future__ import annotations

from io import BytesIO
import json
import re
import struct
import zlib
from PIL import Image

from .security import PipelineError, canonical_json, sha256

ALGORITHM = 'marker-rgba-crop-spacing2-v1'
MAX_TEXTURE_BYTES = 32 * 1024 * 1024
MAX_TEXTURE_PIXELS = 16 * 1024 * 1024
MAX_PACKAGE_BYTES = 4 * 1024 * 1024


def _int(v, low=0, high=65535):
    return type(v) is int and low <= v <= high


def _hash(v):
    return isinstance(v, str) and re.fullmatch(r'[a-f0-9]{64}', v) is not None


def _name(v):
    if not isinstance(v, str): return False
    try:
        # The JS consumer bounds UTF-16 code units, not Unicode scalar count.
        return 0 < len(v.encode('utf-16-le')) // 2 <= 128
    except UnicodeEncodeError:
        return False


def _png_pixels(raw):
    if (type(raw) is not bytes or not 45 <= len(raw) <= MAX_TEXTURE_BYTES
            or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR'
            or struct.unpack_from('>I', raw, 8)[0] != 13):
        raise PipelineError('Invalid bounded PNG texture')
    width, height = struct.unpack_from('>II', raw, 16)
    if not 0 < width <= 16384 or not 0 < height <= 16384 or width * height > MAX_TEXTURE_PIXELS:
        raise PipelineError('Texture pixel budget exceeded')
    # Verify all checksums, exact final IEND and no animation before native decode.
    offset, chunks, ended, palette, pixels, idat_ended = 8, 0, False, False, False, False
    while offset < len(raw):
        if offset + 12 > len(raw): raise PipelineError('Truncated PNG chunk')
        length = struct.unpack_from('>I', raw, offset)[0]
        kind = raw[offset + 4:offset + 8]
        if length > len(raw) - offset - 12 or chunks >= 1024:
            raise PipelineError('PNG chunk limit exceeded')
        if kind in (b'acTL', b'fcTL', b'fdAT') or (chunks and kind == b'IHDR'):
            raise PipelineError('Animated or duplicate-header PNG is unsupported')
        if (not re.fullmatch(rb'[A-Za-z]{4}', kind) or kind[2] & 32
                or not kind[0] & 32 and kind not in (b'IHDR', b'PLTE', b'IDAT', b'IEND')):
            raise PipelineError('Unknown or malformed PNG critical chunk')
        if kind == b'PLTE':
            if palette or pixels or not length or length > 768 or length % 3:
                raise PipelineError('Invalid PNG palette ordering or length')
            palette = True
        if kind == b'IDAT':
            if idat_ended or raw[25] == 3 and not palette:
                raise PipelineError('Invalid PNG image ordering')
            pixels = True
        elif pixels:
            idat_ended = True
        end = offset + length + 12
        if zlib.crc32(raw[offset + 4:end - 4]) & 0xffffffff != struct.unpack_from('>I', raw, end - 4)[0]:
            raise PipelineError('PNG checksum mismatch')
        if kind == b'IEND':
            if length or end != len(raw) or not pixels: raise PipelineError('PNG has trailing or absent image data')
            ended = True
        offset, chunks = end, chunks + 1
    if not ended: raise PipelineError('PNG missing IEND')
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format != 'PNG' or image.size != (width, height): raise PipelineError('Unexpected PNG decoder result')
            image.load()
            return image.convert('RGBA')
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise PipelineError('PNG decode failed') from exc


def assemble_marker_resources(*, material_base: bytes, material_base_sha256: str,
                              policy: dict, textures: dict[str, bytes],
                              source_files: dict[str, bytes]) -> tuple[dict[str, bytes], dict]:
    """Crop from fresh bytes, concatenate gaplessly, bind exact material base.

    Policy keys: schemaVersion=1, algorithm, gameVersion, sourceFiles (hashes),
    rows (key/name/selector/crop). Crop keys: textureSha256/frameX/frameY/columns/
    rowHeights. Never consumes an old markers.catalog or its image pack.
    """
    if (type(material_base) is not bytes or not 0 < len(material_base) <= 32 * 1024 * 1024
            or not _hash(material_base_sha256) or sha256(material_base) != material_base_sha256):
        raise PipelineError('Material foundation hash mismatch')
    if (type(policy) is not dict or set(policy) != {'schemaVersion', 'algorithm', 'gameVersion', 'sourceFiles', 'rows'}
            or type(policy['schemaVersion']) is not int or policy['schemaVersion'] != 1
            or policy['algorithm'] != ALGORITHM or not isinstance(policy['gameVersion'], str)
            or not re.fullmatch(r'[0-9]+(?:\.[0-9]+){2,3}', policy['gameVersion'])):
        raise PipelineError('Invalid marker assembly policy')
    def pairs(entries):
        result = {}
        for k, v in entries:
            if k in result: raise PipelineError('Duplicate material JSON key')
            result[k] = v
        return result
    try:
        base = json.loads(material_base, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(PipelineError('Nonfinite material JSON')))
    except (ValueError, UnicodeError) as exc:
        raise PipelineError('Invalid material JSON') from exc
    if type(base) is not dict or type(base.get('tiles')) is not list or not 0 < len(base['tiles']) <= 65536:
        raise PipelineError('Material tile domain is absent')
    ids = []
    for row in base['tiles']:
        if type(row) is not list or len(row) < 3 or not _int(row[0]): raise PipelineError('Invalid material tile row')
        ids.append(row[0])
    if sorted(ids) != list(range(len(ids))): raise PipelineError('Material tile domain must be contiguous and unique')
    sources = policy['sourceFiles']
    if (type(sources) is not dict or not 0 < len(sources) <= 32 or type(source_files) is not dict
            or set(source_files) != set(sources)):
        raise PipelineError('Marker source file set mismatch')
    for path, digest in sources.items():
        if (not isinstance(path, str) or len(path) > 256 or not re.fullmatch(r'Terraria[.A-Za-z/]+\.cs', path)
                or '..' in path.split('/') or not _hash(digest)
                or type(source_files[path]) is not bytes or not 0 < len(source_files[path]) <= 16 * 1024 * 1024
                or sha256(source_files[path]) != digest):
            raise PipelineError('Marker source provenance mismatch')
    if sum(map(len, source_files.values())) > 64 * 1024 * 1024:
        raise PipelineError('Marker aggregate source budget exceeded')
    rows = policy['rows']
    if type(rows) is not list or not 0 < len(rows) <= 256 or type(textures) is not dict:
        raise PipelineError('Invalid marker row/texture collection')
    seen, wanted, validated = set(), set(), []
    for row in rows:
        if (type(row) is not dict or set(row) != {'key', 'name', 'selector', 'crop'}
                or not isinstance(row['key'], str) or not re.fullmatch(r'(0|[1-9][0-9]{0,4})(?::[a-z]{1,24})?', row['key'])
                or row['key'] in seen or not _name(row['name'])):
            raise PipelineError('Invalid or duplicate marker policy row')
        seen.add(row['key'])
        selector, crop = row['selector'], row['crop']
        if (type(selector) is not dict or not _int(selector.get('tile_type')) or selector['tile_type'] >= len(ids)
                or selector['tile_type'] != int(row['key'].split(':')[0]) or not _int(selector.get('locate'), 1, 2)
                or set(selector) != ({'tile_type', 'locate'} if selector['locate'] == 2 else
                                     {'tile_type', 'locate', 'frame_x', 'frame_y', 'frame_x_mod', 'frame_y_mod'})):
            raise PipelineError('Invalid marker selector')
        if selector['locate'] == 1 and (any(not _int(selector[k], -1) for k in ('frame_x', 'frame_y'))
                or any(not _int(selector[k]) for k in ('frame_x_mod', 'frame_y_mod'))):
            raise PipelineError('Invalid marker frame selector')
        if (type(crop) is not dict or set(crop) != {'textureSha256', 'frameX', 'frameY', 'columns', 'rowHeights'}
                or not _hash(crop['textureSha256']) or any(not _int(crop[k]) for k in ('frameX', 'frameY'))
                or not _int(crop['columns'], 1, 8) or type(crop['rowHeights']) is not list
                or not 0 < len(crop['rowHeights']) <= 8 or any(not _int(n, 1, 32) for n in crop['rowHeights'])
                or sum(crop['rowHeights']) > 128):
            raise PipelineError('Invalid marker crop geometry')
        name = f"Tiles_{selector['tile_type']}.png"
        wanted.add(name)
        validated.append((row, name))
    if set(textures) != wanted or sum(len(b) for b in textures.values() if type(b) is bytes) > 128 * 1024 * 1024:
        raise PipelineError('Marker texture set or aggregate budget mismatch')
    # Hash every input before decoding any. Each texture is decoded only once.
    for row, name in validated:
        raw = textures[name]
        if type(raw) is not bytes or len(raw) > MAX_TEXTURE_BYTES or sha256(raw) != row['crop']['textureSha256']:
            raise PipelineError('Marker texture provenance mismatch')
    outputs, parts, offset = [], [], 0
    for name in sorted(wanted):
        image = _png_pixels(textures[name])
        try:
            for row, texture in validated:
                if texture != name: continue
                crop = row['crop']
                width, height = crop['columns'] * 16, sum(crop['rowHeights'])
                out = Image.new('RGBA', (width, height))
                y, dest = crop['frameY'], 0
                for h in crop['rowHeights']:
                    for x in range(crop['columns']):
                        source_x = crop['frameX'] + x * 18
                        if source_x + 16 > image.width or y + h > image.height:
                            raise PipelineError('Marker crop outside source texture')
                        out.paste(image.crop((source_x, y, source_x + 16, y + h)), (x * 16, dest))
                    y += h + 2
                    dest += h
                buffer = BytesIO()
                out.save(buffer, format='PNG', compress_level=9)
                out.close()
                raw = buffer.getvalue()
                if not 45 <= len(raw) <= 65536: raise PipelineError('Marker PNG output exceeds consumer bound')
                outputs.append((row['key'], raw, width, height))
        finally:
            image.close()
    rendered = {key: (raw, width, height) for key, raw, width, height in outputs}
    packed_rows = []
    for index, (row, name) in enumerate(validated):
        raw, width, height = rendered[row['key']]
        image = {'offset': offset, 'bytes': len(raw), 'sha256': sha256(raw), 'width': width, 'height': height,
                 'texture': name, **row['crop'], 'path': f"static/entity-markers/tile-{row['key'].replace(':', '-')}.png"}
        packed_rows.append({'key': row['key'], 'name': row['name'], 'selector': dict(row['selector']),
                            'iconId': 1000000 + index, 'image': image})
        parts.append(raw)
        offset += len(raw)
        if offset > MAX_PACKAGE_BYTES: raise PipelineError('Marker image pack exceeds consumer bound')
    catalog = {'schemaVersion': 1, 'gameVersion': policy['gameVersion'],
               'materials': {'gameVersion': policy['gameVersion'], 'baseSha256': material_base_sha256},
               'maxTileId': len(ids) - 1, 'sourceFiles': dict(sources), 'rows': packed_rows}
    objects = {'markers.catalog': canonical_json(catalog), 'markers.images': b''.join(parts)}
    receipt = {'schemaVersion': 1, 'kind': 'consumer-derivation', 'status': 'DERIVED_ONLY',
               'algorithm': ALGORITHM, 'policySha256': sha256(canonical_json(policy)),
               'materialBaseSha256': material_base_sha256, 'sourceFiles': dict(sources),
               'textures': {name: sha256(textures[name]) for name in sorted(wanted)},
               'objects': {role: {'sha256': sha256(raw), 'bytes': len(raw)} for role, raw in objects.items()},
               'sourceSemanticsVerified': False, 'publicationApproved': False,
               'missingProducerProof': ['marker selector/crop policy derivation', 'complete material tile domain']}
    return objects, receipt
