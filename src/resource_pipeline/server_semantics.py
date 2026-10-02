"""Bounded data-only PE/CLI evidence extraction, never game execution.

Implements ECMA-335 II.22--24 (metadata) and II.25 (PE/CLI headers).
No Assembly.Load, runtime, subprocess, import of uploaded code, or dependency
resolution is used. This is an incomplete producer, not a trusted adapter or an
authoritative version/ID-domain policy. All results are private evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import tempfile
import zlib

from .contracts import REQUIRED_SUBMANIFESTS
from .security import PipelineError, canonical_json


@dataclass(frozen=True)
class SemanticLimits:
    input_bytes: int = 128 * 1024 * 1024
    metadata_bytes: int = 64 * 1024 * 1024
    resource_bytes: int = 64 * 1024 * 1024
    resource_file_bytes: int = 16 * 1024 * 1024
    decoded_total_bytes: int = 64 * 1024 * 1024
    table_rows: int = 1_000_000
    sections: int = 96
    streams: int = 32
    string_bytes: int = 4096
    json_depth: int = 64
    locale_keys: int = 500_000


# Table-column descriptions: fixed byte widths; s/b/g heaps; tN table indices;
# cNAME coded indices. Tables we don't consume still require correct row sizes.
_TABLES = {
    0: (2, 's', 'g', 'g', 'g'),
    1: ('cResolutionScope', 's', 's'),
    2: (4, 's', 's', 'cTypeDefOrRef', 't4', 't6'),
    3: ('t4',), 4: (2, 's', 'b'), 5: ('t6',),
    6: (4, 2, 2, 's', 'b', 't8'), 7: ('t8',), 8: (2, 2, 's'),
    9: ('t2', 'cTypeDefOrRef'), 10: ('cMemberRefParent', 's', 'b'),
    11: (2, 'cHasConstant', 'b'),
    12: ('cHasCustomAttribute', 'cCustomAttributeType', 'b'),
    13: ('cHasFieldMarshal', 'b'), 14: (2, 'cHasDeclSecurity', 'b'),
    15: (2, 4, 't2'), 16: (4, 't4'), 17: ('b',),
    18: ('t2', 't20'), 19: ('t20',), 20: (2, 's', 'cTypeDefOrRef'),
    21: ('t2', 't23'), 22: ('t23',), 23: (2, 's', 'b'),
    24: (2, 't6', 'cHasSemantics'),
    25: ('t2', 'cMethodDefOrRef', 'cMethodDefOrRef'), 26: ('s',),
    27: ('b',), 28: (2, 'cMemberForwarded', 's', 't26'),
    29: (4, 't4'), 30: (4, 4), 31: (4,),
    32: (4, 2, 2, 2, 2, 4, 'b', 's', 's'),
    33: (4,), 34: (4, 4, 4),
    35: (2, 2, 2, 2, 4, 'b', 's', 's', 'b'),
    36: (4, 't35'), 37: (4, 4, 4, 't35'), 38: (4, 's', 'b'),
    39: (4, 4, 's', 's', 'cImplementation'),
    40: (4, 4, 's', 'cImplementation'), 41: ('t2', 't2'),
    42: (2, 2, 'cTypeOrMethodDef', 's'),
    43: ('cMethodDefOrRef', 'b'), 44: ('t42', 'cTypeDefOrRef'),
}
_CODED = {
    'ResolutionScope': (2, (0, 26, 35, 1)),
    'TypeDefOrRef': (2, (2, 1, 27)),
    'MemberRefParent': (3, (2, 1, 26, 6, 27)),
    'HasConstant': (2, (4, 8, 23)),
    'HasCustomAttribute': (5, (6, 4, 1, 2, 8, 9, 10, 0, 14, 23, 20, 17,
                                26, 27, 32, 35, 38, 39, 40, 42, 44, 43)),
    'CustomAttributeType': (3, (6, 10)),
    'HasFieldMarshal': (1, (4, 8)), 'HasDeclSecurity': (2, (2, 6, 32)),
    'HasSemantics': (1, (20, 23)), 'MethodDefOrRef': (1, (6, 10)),
    'MemberForwarded': (1, (4, 6)), 'Implementation': (2, (38, 35, 39)),
    'TypeOrMethodDef': (1, (2, 6)),
}
_INTEGER_TYPES = {4: ('sbyte', 'b'), 5: ('byte', 'B'), 6: ('int16', 'h'),
                  7: ('uint16', 'H'), 8: ('int32', 'i'), 9: ('uint32', 'I'),
                  10: ('int64', 'q'), 11: ('uint64', 'Q')}
_FAMILY_ID_TYPES = {
    'items': ('ItemID',), 'tiles': ('TileID',), 'walls': ('WallID',),
    'paints': ('PaintID',), 'npcs': ('NPCID',), 'buffs': ('BuffID',),
    'prefixes': ('PrefixID',), 'player': ('ArmorIDs', 'HairID', 'HairDyeID'),
    'worldgen': (), 'markers': (), 'map': (), 'pixel': (),
}


class _Reader:
    def __init__(self, data: bytes, start: int = 0, size: int | None = None):
        self.data = data
        self.start = start
        self.end = len(data) if size is None else start + size
        if start < 0 or self.end < start or self.end > len(data):
            raise PipelineError('PE/CLI byte range is out of bounds')

    def take(self, offset: int, size: int) -> bytes:
        if offset < self.start or size < 0 or offset > self.end - size:
            raise PipelineError('Truncated or out-of-bounds PE/CLI data')
        return self.data[offset:offset + size]

    def uint(self, offset: int, size: int) -> int:
        return int.from_bytes(self.take(offset, size), 'little')


class _Metadata:
    def __init__(self, data: bytes, limits: SemanticLimits):
        self.limits = limits
        self.reader = r = _Reader(data)
        if r.take(0, 2) != b'MZ':
            raise PipelineError('Input is not a PE image')
        pe = r.uint(0x3c, 4)
        if r.take(pe, 4) != b'PE\x00\x00':
            raise PipelineError('Invalid PE signature')
        sections = r.uint(pe + 6, 2)
        if not 0 < sections <= limits.sections:
            raise PipelineError('PE section count exceeds limit')
        optional_size = r.uint(pe + 20, 2)
        optional = _Reader(data, pe + 24, optional_size)
        magic = optional.uint(optional.start, 2)
        if magic not in (0x10b, 0x20b):
            raise PipelineError('Unsupported PE optional header format')
        directory = optional.start + (96 if magic == 0x10b else 112)
        directory_count = optional.uint(directory - 4, 4)
        if directory_count <= 14:
            raise PipelineError('PE image has no CLI directory')
        cli_rva, cli_size = optional.uint(directory + 14 * 8, 4), optional.uint(directory + 14 * 8 + 4, 4)
        self.sections = []
        base = optional.end
        for number in range(sections):
            entry = base + number * 40
            raw_size, raw_offset = r.uint(entry + 16, 4), r.uint(entry + 20, 4)
            virtual_size, virtual_address = r.uint(entry + 8, 4), r.uint(entry + 12, 4)
            r.take(entry, 40)
            r.take(raw_offset, raw_size)
            self.sections.append((virtual_address, raw_offset, raw_size, virtual_size))
        if cli_size < 72:
            raise PipelineError('Invalid CLI header size')
        self.cli_offset = self.rva(cli_rva, cli_size)
        cli = _Reader(data, self.cli_offset, cli_size)
        if cli.uint(cli.start, 4) < 72:
            raise PipelineError('Invalid CLI header length')
        metadata_rva, metadata_size = cli.uint(cli.start + 8, 4), cli.uint(cli.start + 12, 4)
        if metadata_size > limits.metadata_bytes:
            raise PipelineError('CLI metadata exceeds limit')
        self.metadata_offset = self.rva(metadata_rva, metadata_size)
        meta = _Reader(data, self.metadata_offset, metadata_size)
        if meta.take(meta.start, 4) != b'BSJB':
            raise PipelineError('Invalid CLI metadata signature')
        version_size = meta.uint(meta.start + 12, 4)
        if version_size > limits.string_bytes:
            raise PipelineError('CLI runtime version exceeds limit')
        self.runtime_version = meta.take(meta.start + 16, version_size).rstrip(b'\x00').decode('utf-8', errors='strict')
        position = meta.start + ((16 + version_size + 3) & ~3)
        stream_count = meta.uint(position + 2, 2)
        if not 0 < stream_count <= limits.streams:
            raise PipelineError('CLI stream count exceeds limit')
        position += 4
        self.streams = {}
        spans = []
        for _ in range(stream_count):
            offset, size = meta.uint(position, 4), meta.uint(position + 4, 4)
            name_start = position + 8
            name_end = data.find(b'\x00', name_start, min(meta.end, name_start + 32))
            if name_end < 0:
                raise PipelineError('Unterminated CLI stream name')
            name = meta.take(name_start, name_end - name_start).decode('ascii', errors='strict')
            position = meta.start + ((name_end + 1 - meta.start + 3) & ~3)
            start = meta.start + offset
            meta.take(start, size)
            if name in self.streams:
                raise PipelineError('Duplicate CLI metadata stream')
            self.streams[name] = _Reader(data, start, size)
            if size:
                spans.append((start, start + size))
        spans.sort()
        if any(start < position for start, _ in spans) or any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
            raise PipelineError('Overlapping CLI metadata streams')
        if '#~' not in self.streams or '#-' in self.streams:
            raise PipelineError('Unsupported CLI table stream; only optimized #~ is supported')
        if '#Strings' not in self.streams or '#Blob' not in self.streams:
            raise PipelineError('Missing CLI metadata heaps')
        tables = self.streams['#~']
        self.heap_sizes = tables.uint(tables.start + 6, 1)
        if self.heap_sizes & ~7:
            raise PipelineError('Unsupported CLI heap-size flags')
        valid = tables.uint(tables.start + 8, 8)
        tables.take(tables.start, 24)
        if valid >> 45:
            raise PipelineError('Unsupported CLI metadata table')
        self.rows = {}
        position = tables.start + 24
        for index in range(45):
            count = tables.uint(position, 4) if valid & (1 << index) else 0
            self.rows[index] = count
            if valid & (1 << index):
                position += 4
        if sum(self.rows.values()) > limits.table_rows:
            raise PipelineError('CLI metadata row limit exceeded')
        if any(self.rows[x] for x in (3, 5, 7, 19, 22)):
            raise PipelineError('Pointer tables are unsupported in optimized metadata')
        self.layout = {}
        for index, columns in _TABLES.items():
            widths = tuple(self.width(column) for column in columns)
            row_size = sum(widths)
            tables.take(position, row_size * self.rows[index])
            self.layout[index] = (position, row_size, widths)
            position += row_size * self.rows[index]
        resources_rva, resources_size = cli.uint(cli.start + 24, 4), cli.uint(cli.start + 28, 4)
        if resources_size > limits.resource_bytes:
            raise PipelineError('CLI resource directory exceeds limit')
        self.resources = _Reader(data, self.rva(resources_rva, resources_size), resources_size) if resources_size else None

    def rva(self, address: int, size: int) -> int:
        matches = [offset + address - base for base, offset, raw_size, _ in self.sections
                   if address >= base and address - base <= raw_size - size]
        if len(matches) != 1:
            raise PipelineError('Unmapped or ambiguous PE relative virtual address')
        self.reader.take(matches[0], size)
        return matches[0]

    def width(self, column) -> int:
        if isinstance(column, int):
            return column
        if column in ('s', 'g', 'b'):
            return 4 if self.heap_sizes & {'s': 1, 'g': 2, 'b': 4}[column] else 2
        if column.startswith('t'):
            return 4 if self.rows[int(column[1:])] >= 65536 else 2
        bits, tables = _CODED[column[1:]]
        return 4 if max(self.rows[x] for x in tables) >= (1 << (16 - bits)) else 2

    def row(self, table: int, rid: int) -> tuple[tuple[int, ...], int]:
        if not 1 <= rid <= self.rows[table]:
            raise PipelineError('CLI table reference is out of bounds')
        start, size, widths = self.layout[table]
        position = start + (rid - 1) * size
        offset = position
        values = []
        for width in widths:
            values.append(self.reader.uint(position, width))
            position += width
        return tuple(values), offset

    def string(self, index: int) -> str:
        heap = self.streams['#Strings']
        start = heap.start + index
        heap.take(start, 1)
        end = heap.data.find(b'\x00', start, min(heap.end, start + self.limits.string_bytes + 1))
        if end < 0:
            raise PipelineError('Unterminated or oversized metadata string')
        return heap.take(start, end - start).decode('utf-8', errors='strict')

    def blob(self, index: int) -> tuple[bytes, int]:
        heap = self.streams['#Blob']
        start = heap.start + index
        first = heap.uint(start, 1)
        if first & 0x80 == 0:
            size, prefix = first, 1
        elif first & 0xc0 == 0x80:
            size, prefix = ((first & 0x3f) << 8) | heap.uint(start + 1, 1), 2
            if size < 0x80:
                raise PipelineError('Noncanonical compressed CLI integer')
        elif first & 0xe0 == 0xc0:
            size = ((first & 0x1f) << 24) | int.from_bytes(heap.take(start + 1, 3), 'big')
            prefix = 4
            if size < 0x4000:
                raise PipelineError('Noncanonical compressed CLI integer')
        else:
            raise PipelineError('Invalid compressed CLI integer')
        if size > self.limits.resource_file_bytes:
            raise PipelineError('CLI blob exceeds limit')
        return heap.take(start + prefix, size), start + prefix


def _types(meta: _Metadata) -> dict[int, dict]:
    nested = {}
    for rid in range(1, meta.rows[41] + 1):
        (child, parent), _ = meta.row(41, rid)
        if child in nested or not 1 <= child <= meta.rows[2] or not 1 <= parent <= meta.rows[2]:
            raise PipelineError('Invalid nested type relationship')
        nested[child] = parent
    output = {}
    previous_field = 1
    for rid in range(1, meta.rows[2] + 1):
        row, offset = meta.row(2, rid)
        if not previous_field <= row[4] <= meta.rows[4] + 1:
            raise PipelineError('Invalid TypeDef field range')
        previous_field = row[4]
        output[rid] = {'name': meta.string(row[1]), 'namespace': meta.string(row[2]),
                       'firstField': row[4], 'metadataOffset': offset}
    def resolve(rid, visited):
        if len(visited) >= 64 or rid in visited:
            raise PipelineError('Cyclic or oversized nested type relationship')
        row = output[rid]
        if 'fullName' not in row:
            row['fullName'] = ((resolve(nested[rid], visited | {rid}) + '+') if rid in nested
                               else (row['namespace'] + '.' if row['namespace'] else '')) + row['name']
        return row['fullName']
    for rid in output:
        resolve(rid, set())
        output[rid]['lastField'] = output[rid + 1]['firstField'] if rid < len(output) else meta.rows[4] + 1
    return output


def _constant(meta: _Metadata, kind: int, blob_index: int):
    payload, offset = meta.blob(blob_index)
    if kind in _INTEGER_TYPES:
        typename, code = _INTEGER_TYPES[kind]
        if len(payload) != struct.calcsize(code):
            raise PipelineError('Constant blob has invalid integer width')
        return struct.unpack('<' + code, payload)[0], typename, offset, len(payload)
    if kind == 14:
        if len(payload) & 1:
            raise PipelineError('Constant string has invalid UTF-16 width')
        return payload.decode('utf-16-le', errors='strict'), 'string', offset, len(payload)
    return None, None, offset, len(payload)


def _id_constants(meta: _Metadata, types: dict) -> tuple[dict, dict, list, list]:
    constants = {}
    for rid in range(1, meta.rows[11] + 1):
        (kind, parent, value), offset = meta.row(11, rid)
        if kind > 0xff:
            raise PipelineError('Invalid Constant table padding')
        tag, field = parent & 3, parent >> 2
        targets = (4, 8, 23)
        if tag >= len(targets) or not 1 <= field <= meta.rows[targets[tag]]:
            raise PipelineError('Invalid Constant parent reference')
        if tag == 0:
            if field in constants:
                raise PipelineError('Duplicate constant for field')
            constants[field] = (kind, value, offset, rid)
    ids, evidence, unsupported, versions = {}, {}, [], []
    for type_rid, typedef in types.items():
        full_name = typedef['fullName']
        is_id = full_name.startswith('Terraria.ID.')
        is_version = full_name == 'Terraria.Main'
        if not is_id and not is_version:
            continue
        group = full_name.removeprefix('Terraria.ID.')
        for field_rid in range(typedef['firstField'], typedef['lastField']):
            (flags, name_idx, signature_idx), field_offset = meta.row(4, field_rid)
            name = meta.string(name_idx)
            if is_version and name not in ('versionNumber', 'versionNumber2', 'curRelease'):
                continue
            base = {'type': full_name, 'field': name, 'fieldToken': f'0x{0x04000000 | field_rid:08x}',
                    'fieldMetadataOffset': field_offset, 'typeMetadataOffset': typedef['metadataOffset']}
            if flags & 0x50 != 0x50 or field_rid not in constants:
                if is_id:
                    unsupported.append({**base, 'reason': 'non-literal-or-no-constant; runtime state is not evaluated'})
                continue
            kind, value_idx, constant_offset, constant_rid = constants[field_rid]
            value, typename, value_offset, value_bytes = _constant(meta, kind, value_idx)
            signature, signature_offset = meta.blob(signature_idx)
            if typename is None or (is_id and kind not in _INTEGER_TYPES) or signature != bytes((6, kind)):
                if is_id:
                    unsupported.append({**base, 'reason': 'unsupported-constant-type-or-signature', 'constantType': kind})
                continue
            proof = {**base, 'constantToken': f'0x{0x0b000000 | constant_rid:08x}',
                     'constantMetadataOffset': constant_offset, 'valueBlobOffset': value_offset,
                     'valueBytes': value_bytes, 'signatureBlobOffset': signature_offset, 'valueType': typename}
            if is_version:
                versions.append({'value': value, 'evidence': proof})
            if is_id:
                if name in ids.setdefault(group, {}):
                    raise PipelineError('Duplicate ID field name')
                ids[group][name] = value
                evidence.setdefault(group, {})[name] = proof
    return ids, evidence, unsupported, versions


def _json_document(payload: bytes, limits: SemanticLimits) -> dict:
    """JSON with only comments/trailing commas allowed; no JS or object hooks."""
    text = payload.decode('utf-8-sig', errors='strict')
    chars = list(text)
    quoted = escaped = False
    depth = 0
    index = 0
    while index < len(chars):
        char = chars[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
            index += 1
            continue
        if char == '"':
            quoted = True
        elif char == '/' and index + 1 < len(chars) and chars[index + 1] in ('/', '*'):
            block = chars[index + 1] == '*'
            end = text.find('*/', index + 2) if block else text.find('\n', index + 2)
            if block and end < 0:
                raise PipelineError('Unterminated localization JSON comment')
            end = len(chars) if end < 0 else end + (2 if block else 0)
            for pos in range(index, end):
                if chars[pos] not in ('\n', '\r'):
                    chars[pos] = ' '
            index = end
            continue
        elif char in ('{', '['):
            depth += 1
            if depth > limits.json_depth:
                raise PipelineError('Localization JSON nesting exceeds limit')
        elif char in ('}', ']'):
            depth -= 1
            previous = index - 1
            while previous >= 0 and chars[previous].isspace():
                previous -= 1
            if previous >= 0 and chars[previous] == ',':
                chars[previous] = ' '
        index += 1
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PipelineError('Duplicate localization JSON key')
            result[key] = value
        return result
    def reject_constant(_):
        raise PipelineError('Non-finite localization JSON value')
    result = json.loads(''.join(chars), object_pairs_hook=object_pairs, parse_constant=reject_constant)
    if not isinstance(result, dict):
        raise PipelineError('Localization resource must be a JSON object')
    return result


def _flatten_locale(document: dict, limits: SemanticLimits) -> dict[str, str]:
    output = {}
    stack = [('', document)]
    while stack:
        prefix, node = stack.pop()
        for key, value in node.items():
            name = prefix + '.' + key if prefix else key
            if not key or len(name) > limits.string_bytes:
                raise PipelineError('Invalid or oversized localization key')
            if isinstance(value, dict):
                stack.append((name, value))
            elif isinstance(value, str):
                if name in output:
                    raise PipelineError('Ambiguous flattened localization key')
                output[name] = value
                if len(output) > limits.locale_keys:
                    raise PipelineError('Localization key count exceeds limit')
            else:
                raise PipelineError('Unsupported non-string localization value')
    return dict(sorted(output.items()))


def _decode_resource(payload: bytes, name: str, limits: SemanticLimits) -> tuple[bytes, str]:
    lower = name.lower()
    if payload.startswith(b'\x1f\x8b'):
        window, compression = 31, 'gzip'
    elif len(payload) >= 2 and payload[0] & 15 == 8 and (payload[0] * 256 + payload[1]) % 31 == 0:
        window, compression = 15, 'zlib'
    elif lower.endswith(('.gz', '.gzip', '.zlib')):
        raise PipelineError('Resource compression suffix does not match supported framing')
    else:
        return payload, 'none'
    decoder = zlib.decompressobj(window)
    result = decoder.decompress(payload, limits.resource_file_bytes + 1)
    if len(result) > limits.resource_file_bytes or decoder.unconsumed_tail:
        raise PipelineError('Localization decompression exceeds limit')
    result += decoder.flush(limits.resource_file_bytes + 1 - len(result))
    if len(result) > limits.resource_file_bytes or not decoder.eof or decoder.unused_data:
        raise PipelineError('Incomplete, concatenated, or oversized compressed resource')
    return result, compression


def _languages(meta: _Metadata) -> tuple[dict, list, dict]:
    languages, inventory, locales = {}, [], {}
    decoded_total = key_total = 0
    names = set()
    for rid in range(1, meta.rows[40] + 1):
        (offset, flags, name_idx, implementation), metadata_offset = meta.row(40, rid)
        name = meta.string(name_idx)
        if name in names:
            raise PipelineError('Duplicate manifest resource name')
        names.add(name)
        item = {'name': name, 'manifestResourceToken': f'0x{0x28000000 | rid:08x}',
                'metadataOffset': metadata_offset, 'resourceRelativeOffset': offset, 'flags': flags}
        if implementation:
            item.update(status='UNSUPPORTED_EXTERNAL_RESOURCE', implementation=implementation)
            inventory.append(item)
            continue
        if meta.resources is None:
            raise PipelineError('Embedded manifest resource has no resource directory')
        size_offset = meta.resources.start + offset
        size = meta.resources.uint(size_offset, 4)
        if size > meta.limits.resource_file_bytes:
            item.update(status='UNSUPPORTED_RESOURCE_SIZE', bytes=size)
            # Still check the claimed range without making a large copy.
            if size_offset + 4 > meta.resources.end - size:
                raise PipelineError('Embedded resource length is out of bounds')
            inventory.append(item)
            continue
        payload = meta.resources.take(size_offset + 4, size)
        item.update(dataOffset=size_offset + 4, bytes=size, sha256=hashlib.sha256(payload).hexdigest())
        match = re.fullmatch(r'Terraria\.Localization\.Content\.([a-z]{2}-[A-Za-z]{2,4})(?:\.[\w-]+)*\.json(?:\.(?:gz|gzip|zlib))?', name)
        if not match:
            item['status'] = 'UNSUPPORTED_RESOURCE_KIND'
            inventory.append(item)
            continue
        language = match.group(1)
        try:
            decoded, compression = _decode_resource(payload, name, meta.limits)
            decoded_total += len(decoded)
            if decoded_total > meta.limits.decoded_total_bytes:
                raise PipelineError('Total localization decoded bytes exceed limit')
            flat = _flatten_locale(_json_document(decoded, meta.limits), meta.limits)
            key_total += len(flat)
            if key_total > meta.limits.locale_keys:
                raise PipelineError('Total localization key count exceeds limit')
            duplicates = set(locales.setdefault(language, {})) & set(flat)
            if duplicates:
                raise PipelineError('Duplicate localization key across resources')
            locales[language].update(flat)
            item.update(status='EXTRACTED', language=language, compression=compression,
                        decodedBytes=len(decoded), decodedSha256=hashlib.sha256(decoded).hexdigest(), keyCount=len(flat))
            languages[name] = {'language': language, 'strings': flat, 'evidence': dict(item)}
        except (UnicodeError, json.JSONDecodeError, zlib.error) as exc:
            raise PipelineError('Malformed localization resource') from exc
        inventory.append(item)
    return languages, inventory, locales


def _coverage(ids: dict, unsupported: list, locales: dict) -> dict:
    families = {}
    for family, parts in REQUIRED_SUBMANIFESTS.items():
        groups = _FAMILY_ID_TYPES.get(family, ())
        selected = {name: rows for name, rows in ids.items() if any(name == group or name.startswith(group + '+') for group in groups)}
        constants = sum(len(rows) for rows in selected.values())
        families[family] = {'status': 'INCOMPLETE', 'literalConstantCount': constants,
                            'idGroups': sorted(selected), 'complete': False,
                            'gaps': list(parts)}
    families['ids'].update(literalConstantCount=sum(len(rows) for rows in ids.values()), idGroups=sorted(ids),
                           unsupportedFieldCount=len(unsupported),
                           extracted=['namespace-constants-subset', 'same-value-aliases', 'literal-count-fields'],
                           gaps=['runtime-and-nonintegral-fields', 'independently-pinned-authoritative-domain'])
    families['locales'].update(languages={name: len(rows) for name, rows in sorted(locales.items())},
                               keyCount=sum(len(rows) for rows in locales.values()),
                               extracted=['embedded-json-strings'],
                               gaps=['runtime-fallback-rules', 'runtime-interpolation', 'independent-key-domain'])
    families['player']['gaps'].append('server-has-no-client-texture-payload')
    return families


def extract_server_semantics(input_path: Path, limits: SemanticLimits = SemanticLimits()) -> dict:
    """Return immutable-file-derived evidence; never infer complete semantics.

    The binary's own assembly version is a declaration, not authentication. The
    caller must separately verify operator pins before trusting input identity.
    """
    path = Path(input_path)
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise PipelineError('Server input cannot traverse symlinks')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limits.input_bytes:
            raise PipelineError('Server input must be a bounded regular file')
        data = source.read(limits.input_bytes + 1)
        after = os.fstat(source.fileno())
    if len(data) != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise PipelineError('Server input changed during static inspection')
    try:
        meta = _Metadata(data, limits)
        if meta.rows[32] != 1:
            raise PipelineError('Input must contain exactly one CLI assembly definition')
        assembly, assembly_offset = meta.row(32, 1)
        version = '.'.join(str(number) for number in assembly[1:5])
        assembly_name = meta.string(assembly[7])
        ids, evidence, unsupported, version_fields = _id_constants(meta, _types(meta))
        languages, resources, locales = _languages(meta)
    except PipelineError:
        raise
    except (UnicodeError, struct.error, ValueError, RecursionError) as exc:
        raise PipelineError('Malformed or unsupported PE/CLI data') from exc
    aliases = {}
    counts = {}
    for group, fields in ids.items():
        by_value = {}
        for name, value in fields.items():
            by_value.setdefault(str(value), []).append(name)
            if name.lower() == 'count':
                counts[group] = value
        aliases[group] = {value: names for value, names in sorted(by_value.items()) if len(names) > 1}
    english = locales.get('en-US', {})
    chinese = locales.get('zh-Hans', {})
    reference_pattern = re.compile(r'\{\$([^{}]+)\}')
    references = {language: [{'key': key, 'reference': ref} for key, value in rows.items()
                              for ref in reference_pattern.findall(value) if ref not in rows]
                  for language, rows in locales.items()}
    id_families = {}
    for family, groups in _FAMILY_ID_TYPES.items():
        records, count_constants = [], []
        for group, fields in ids.items():
            if not any(group == prefix or group.startswith(prefix + '+') for prefix in groups):
                continue
            for name, value in fields.items():
                proof = evidence[group][name]
                row = {'id': value, 'name': name, 'type': proof['type'],
                       'metadataToken': proof['fieldToken'], 'offset': proof['valueBlobOffset'],
                       'evidence': proof}
                (count_constants if name.lower() == 'count' else records).append(row)
        id_families[family] = {'records': records, 'countConstants': count_constants,
                               'unsupportedFields': [row for row in unsupported if any(
                                   row['type'] == 'Terraria.ID.' + group or
                                   row['type'].startswith('Terraria.ID.' + group + '+') for group in groups)],
                               'complete': False}
    version_evidence = {'status': 'DECLARED_IN_ASSEMBLY', 'trusted': False,
                        'method': 'CLI-Assembly-table', 'assemblyMetadataOffset': assembly_offset,
                        'versionFileOffset': assembly_offset + 4, 'literalFields': version_fields}
    return {'schemaVersion': 1, 'extractor': 'static-pe-cli-python-v1',
            'status': 'PARTIAL', 'executedInput': False, 'complete': False, 'publishable': False,
            'inputSha256': hashlib.sha256(data).hexdigest(), 'inputBytes': len(data),
            'input': {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)},
            'assembly': {'name': assembly_name, 'version': version, 'metadataOffset': assembly_offset},
            'gameVersionEvidence': version_evidence, 'idFamilies': id_families,
            'localization': {locale: {'strings': dict(sorted(rows.items())),
                                     'resources': [row for row in resources if row.get('language') == locale]}
                             for locale, rows in sorted(locales.items())},
            'assemblyName': assembly_name, 'gameVersion': version,
            'versionEvidence': version_evidence,
            'metadata': {'cliHeaderOffset': meta.cli_offset, 'metadataOffset': meta.metadata_offset,
                         'runtimeVersion': meta.runtime_version,
                         'tableRows': {str(key): value for key, value in meta.rows.items() if value}},
            'ids': ids, 'idEvidence': evidence, 'aliases': aliases, 'literalCounts': counts,
            'unsupportedFields': unsupported, 'languages': languages, 'resources': resources,
            'localeDiagnostics': {'englishKeys': len(english), 'chineseKeys': len(chinese),
                                  'missingChineseKeys': sorted(set(english) - set(chinese)),
                                  'missingEnglishKeys': sorted(set(chinese) - set(english)),
                                  'unresolvedReferences': references,
                                  'fallbackEvaluated': False, 'interpolationEvaluated': False},
            'coverage': _coverage(ids, unsupported, locales),
            'unsupported': ['runtime-item-defaults', 'resolved-dynamic-tooltips', 'map-runtime-rules',
                            'client-textures', 'player-draw-rules', 'worldgen-runtime-rules',
                            'compressed-raw-deflate-resources', 'external-assembly-resource-resolution'],
            'sourcePolicy': 'Binary metadata declarations are evidence, not operator identity/version pins'}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Statically inspect managed server bytes; never execute game code')
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path, help='Private evidence JSON destination (never publish game strings)')
    args = parser.parse_args(argv)
    try:
        result = extract_server_semantics(args.input)
        if args.output.resolve() == args.input.resolve():
            raise PipelineError('Evidence output cannot overwrite the source')
        if any(part.is_symlink() for part in (args.output, *args.output.parents)):
            raise PipelineError('Evidence output cannot traverse symlinks')
        args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=args.output.parent, prefix='.server-semantics-', delete=False) as target:
                temporary = Path(target.name)
                target.write(canonical_json(result))
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, args.output)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        print(json.dumps({'status': result['status'], 'inputSha256': result['inputSha256'],
                          'gameVersion': result['gameVersion'], 'executedInput': False,
                          'idGroups': len(result['ids']), 'localizationFiles': len(result['languages'])}))
        return 0
    except (PipelineError, OSError) as exc:
        print(json.dumps({'status': 'REJECTED', 'executedInput': False, 'error': str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
