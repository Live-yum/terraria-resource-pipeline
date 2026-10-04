"""Data-only pixel derivation from a hash-bound base and a versioned app policy.

This module does not extract or certify Terraria map colors. The caller must
supply independently reviewed materials.base bytes; a matching digest proves
identity, not their semantic completeness. No game code or external executable
is loaded. The two exact RGB distance transforms use only Python's stdlib.
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass
import hashlib
import json
import math
import re
import struct
import time
from typing import Callable

from .security import PipelineError, canonical_json

ALGORITHM = 'terraria-app-rgb-squared-f32-v1'
_HEX = re.compile(r'#[0-9a-fA-F]{6}\Z')
_VERSION = re.compile(r'[0-9]+(?:\.[0-9]+){2,3}\Z')
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
_COMMIT = re.compile(r'[0-9a-f]{40}\Z')
_HEADER_BYTES = 12 + 65537 * 4


class PixelAssemblyError(PipelineError):
    """Rejected input/policy; never a partial or fallback RGB index."""


class PixelAssemblyBudgetExceeded(PixelAssemblyError):
    """Fatal resource bound; caller must not publish partial output."""


@dataclass(frozen=True)
class PixelAssemblyLimits:
    input_bytes: int = 16 * 1024 * 1024
    base_rows: int = 131_072
    candidates: int = 65_535
    output_bytes: int = 32 * 1024 * 1024
    working_bytes: int = 384 * 1024 * 1024
    work: int = 300_000_000
    wall_seconds: float = 180

    def __post_init__(self):
        ceilings = dict(input_bytes=32 * 1024 * 1024, base_rows=196_608,
                        candidates=65_535, output_bytes=32 * 1024 * 1024,
                        working_bytes=512 * 1024 * 1024, work=400_000_000)
        for name, ceiling in ceilings.items():
            value = getattr(self, name)
            if type(value) is not int or not 0 < value <= ceiling:
                raise ValueError(f'Invalid pixel {name} limit')
        if (type(self.wall_seconds) not in (int, float)
                or not math.isfinite(self.wall_seconds) or not 0 < self.wall_seconds <= 600):
            raise ValueError('Invalid pixel wall-time limit')


@dataclass(frozen=True)
class PixelAssembly:
    catalog: dict
    rgb: bytes
    evidence: dict

    @property
    def objects(self) -> dict[str, bytes]:
        return {'pixel.catalog': canonical_json(self.catalog), 'pixel.rgb': self.rgb}


class _Budget:
    def __init__(self, limits: PixelAssemblyLimits, checkpoint: Callable | None = None):
        self.limits, self.checkpoint = limits, checkpoint
        self.started, self.work = time.monotonic(), 0
        self.base_memory = 0
        self.spend(0)

    def spend(self, work: int):
        self.work += work
        if self.work > self.limits.work:
            raise PixelAssemblyBudgetExceeded('PIXEL_WORK_LIMIT')
        if time.monotonic() - self.started > self.limits.wall_seconds:
            raise PixelAssemblyBudgetExceeded('PIXEL_WALL_TIME_LIMIT')
        if self.checkpoint is not None:
            self.checkpoint()


def _require(condition, code):
    if not condition:
        raise PixelAssemblyError(code)


def _integer(value, maximum=65535):
    return type(value) is int and 0 <= value <= maximum


def _pairs(items):
    result = {}
    for key, value in items:
        _require(key not in result, 'PIXEL_DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def _invalid_constant(_):
    raise PixelAssemblyError('PIXEL_NONFINITE_JSON')


def _check_json_shape(raw, budget):
    # Bound container construction before json.loads allocates an object graph.
    # Valid base data is one object, three arrays, and flat scalar rows.
    depth = arrays = objects = string_bytes = 0
    quoted = escaped = False
    for at, char in enumerate(raw):
        if at % 4096 == 0:
            budget.spend(min(4096, len(raw) - at))
        if quoted:
            string_bytes += 1
            if string_bytes > 12 * 1024:  # Also accepts escaped 1,024-char labels.
                raise PixelAssemblyBudgetExceeded('PIXEL_JSON_STRING_LIMIT')
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted, string_bytes = True, 0
        elif char in (91, 123):
            depth += 1
            arrays += char == 91
            objects += char == 123
            _require(depth <= 3 and objects <= 1, 'PIXEL_JSON_SHAPE')
            if arrays > budget.limits.base_rows + 3:
                raise PixelAssemblyBudgetExceeded('PIXEL_BASE_ROWS_LIMIT')
        elif char in (93, 125):
            depth -= 1
            _require(depth >= 0, 'PIXEL_JSON_SHAPE')


def _read_base(raw: bytes, digest: str, budget: _Budget):
    _require(type(raw) is bytes and bool(raw), 'PIXEL_BASE_EXPECTED_BYTES')
    if len(raw) > budget.limits.input_bytes:
        raise PixelAssemblyBudgetExceeded('PIXEL_INPUT_BYTES_LIMIT')
    _require(isinstance(digest, str) and _DIGEST.fullmatch(digest), 'PIXEL_BASE_DIGEST_FORMAT')
    _require(hashlib.sha256(raw).hexdigest() == digest, 'PIXEL_BASE_DIGEST_MISMATCH')
    _check_json_shape(raw, budget)
    try:
        base = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, PixelAssemblyError):
            raise
        raise PixelAssemblyError('PIXEL_BASE_INVALID_JSON') from exc
    _require(isinstance(base, dict) and set(base) == {'tiles', 'walls', 'paints'}, 'PIXEL_BASE_FIELDS')
    rows = 0
    tables = {}
    for kind in ('tiles', 'walls', 'paints'):
        values = base[kind]
        _require(isinstance(values, list) and 0 < len(values) <= 65536, 'PIXEL_BASE_CATALOG')
        rows += len(values)
        if rows > budget.limits.base_rows:
            raise PixelAssemblyBudgetExceeded('PIXEL_BASE_ROWS_LIMIT')
        table = {}
        for row in values:
            _require(isinstance(row, list) and len(row) in (3, 4), 'PIXEL_BASE_ROW')
            ident, name, color = row[:3]
            _require(_integer(ident) and ident not in table, 'PIXEL_BASE_ID')
            _require(isinstance(name, str) and 0 < len(name) <= 1024, 'PIXEL_BASE_NAME')
            _require(isinstance(color, str) and _HEX.fullmatch(color), 'PIXEL_BASE_COLOR')
            _require(len(row) == 3 or row[3] is None or
                     (isinstance(row[3], str) and len(row[3]) <= 1024), 'PIXEL_BASE_LABEL')
            table[ident] = tuple(bytes.fromhex(color[1:]))
        tables[kind] = table
        budget.spend(len(values))
    budget.base_memory = len(raw) * 4 + rows * 256
    return tables


def _read_policy(policy: dict, version: str, tables, budget: _Budget):
    fields = {'schemaVersion', 'policyId', 'gameVersion', 'consumerCommit', 'algorithm',
              'tileIds', 'wallIds', 'paintIds'}
    _require(isinstance(policy, dict) and set(policy) == fields, 'PIXEL_POLICY_FIELDS')
    _require(type(policy['schemaVersion']) is int and policy['schemaVersion'] == 1, 'PIXEL_POLICY_SCHEMA')
    _require(isinstance(version, str) and _VERSION.fullmatch(version)
             and policy['gameVersion'] == version, 'PIXEL_POLICY_GAME_VERSION')
    _require(isinstance(policy['consumerCommit'], str) and _COMMIT.fullmatch(policy['consumerCommit']),
             'PIXEL_POLICY_CONSUMER_COMMIT')
    _require(isinstance(policy['policyId'], str) and
             re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}', policy['policyId']), 'PIXEL_POLICY_ID')
    _require(policy['algorithm'] == ALGORITHM, 'PIXEL_POLICY_ALGORITHM')
    selected = {}
    for field, kind in (('tileIds', 'tiles'), ('wallIds', 'walls'), ('paintIds', 'paints')):
        values = policy[field]
        _require(isinstance(values, list) and 0 < len(values) <= (30 if kind == 'paints' else 65536),
                 'PIXEL_POLICY_SELECTION')
        seen = set()
        for value in values:
            _require(_integer(value) and value in tables[kind] and value not in seen
                     and (kind != 'paints' or 1 <= value <= 30), 'PIXEL_POLICY_SELECTION_ID')
            seen.add(value)
        selected[field] = list(values)
    count = (len(selected['tileIds']) + len(selected['wallIds'])) * (len(selected['paintIds']) + 1)
    if count > budget.limits.candidates:
        raise PixelAssemblyBudgetExceeded('PIXEL_CANDIDATES_LIMIT')
    snapshot = {key: policy[key] for key in fields - set(selected)} | selected
    budget.spend(count)
    return selected, snapshot


def _f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def _blend(base, paint, paint_id, wall):
    """Bit-for-bit Math.fround/floor ordering from app shared/data/color.js."""
    if not paint_id:
        return base
    r, g, b = base
    brightness, gn, bn = (_f32(value / 255) for value in base)
    if gn > brightness:
        brightness, gn = gn, brightness
    if bn > brightness:
        brightness, bn = bn, brightness
    if paint_id == 29:
        darkness = _f32(bn * _f32(0.3))
        return tuple(math.floor(_f32(value * darkness)) for value in paint)
    if paint_id == 30:
        return tuple((255 - value) // 2 if wall else 255 - value for value in (r, g, b))
    return tuple(math.floor(_f32(value * brightness)) for value in paint)


def _candidates(tables, selected):
    materials = [(tables['tiles'][ident], False) for ident in selected['tileIds']]
    materials += [(tables['walls'][ident], True) for ident in selected['wallIds']]
    result = []
    for paint_id in [0, *selected['paintIds']]:
        paint = tables['paints'][paint_id] if paint_id else (255, 255, 255)
        for base, wall in materials:
            red, green, blue = _blend(base, paint, paint_id, wall)
            # Property insertion order is the app's JSON.stringify wire contract.
            result.append({'rgb': red << 16 | green << 8 | blue, 'paint': bool(paint_id),
                           'wall': wall, 'order': len(result)})
    return result


def _fingerprint(candidates):
    return hashlib.sha256(json.dumps(candidates, separators=(',', ':'), ensure_ascii=True,
                                     allow_nan=False).encode('ascii')).hexdigest()


def _ranks(candidates, prefer_wall):
    order = sorted(range(len(candidates)), key=lambda i: (
        candidates[i]['paint'], not candidates[i]['wall'] if prefer_wall else False, i))
    rank = [0] * len(order)
    for place, index in enumerate(order):
        rank[index] = place
    return rank


def _envelope(points, ranks, size, budget):
    """Exact lower envelope of (x-position)^2 + distance, integer tie ranks.

    Points are sorted by distinct position. Each output tuple is
    (first integer coordinate, position, distance, candidate index).
    A lower rank wins equal distance, including an exact parabola crossing.
    """
    budget.spend(3 * len(points))  # Includes the amortized stack pops.
    result = []
    for position, distance, label in points:
        start = 0
        while result:
            prior_start, prior_pos, prior_distance, prior_label = result[-1]
            numerator = distance + position * position - prior_distance - prior_pos * prior_pos
            denominator = 2 * (position - prior_pos)
            start = -(-numerator // denominator)
            if numerator % denominator == 0 and ranks[label] > ranks[prior_label]:
                start += 1
            if start > prior_start:
                break
            result.pop()
        if not result:
            start = 0
        if start < size:
            result.append((start, position, distance, label))
    return result


def _sample(envelope, size, budget):
    budget.spend(size)
    distances, labels = array('I'), array('H')
    for i, (start, position, distance, label) in enumerate(envelope):
        end = envelope[i + 1][0] if i + 1 < len(envelope) else size
        for coordinate in range(start, end):
            distances.append(distance + (coordinate - position) ** 2)
            labels.append(label)
    return distances, labels


def _build_index(candidates, budget, size=256):
    """Separable exact EDT; size is a private finite-cube test seam only.

    Only occupied blue planes need intermediate storage. Red then green passes
    derive their 2-D distances. The final blue envelopes directly emit paired
    runs; no 16M-pixel final cube or brute-force candidate/color cross product.
    """
    ranks = (_ranks(candidates, False), _ranks(candidates, True))
    sources = {}
    for label, entry in enumerate(candidates):
        rgb = entry['rgb']
        red, green, blue = rgb >> 16, rgb >> 8 & 255, rgb & 255
        _require(max(red, green, blue) < size, 'PIXEL_TEST_CUBE_COORDINATE')
        row = sources.setdefault(blue, {}).setdefault(green, {})
        previous = row.get(red)
        if previous is None:
            row[red] = (label, label)
        else:
            row[red] = tuple(min((previous[mode], label), key=ranks[mode].__getitem__) for mode in (0, 1))
    # Arrays use native widths only internally. All published bytes use explicit LE.
    _require(array('I').itemsize == 4 and array('H').itemsize == 2, 'PIXEL_ARRAY_WIDTH')
    estimated = (budget.base_memory + len(sources) * size * size * 8 + size * size * 24
                 + len(candidates) * 1024 + 2 * budget.limits.output_bytes)
    if estimated > budget.limits.working_bytes:
        raise PixelAssemblyBudgetExceeded('PIXEL_WORKING_BYTES_LIMIT')
    planes = []
    for blue, source in sorted(sources.items()):
        columns = []
        for green, row in sorted(source.items()):
            points = sorted(row.items())
            env0 = _envelope([(red, 0, labels[0]) for red, labels in points], ranks[0], size, budget)
            env1 = _envelope([(red, 0, labels[1]) for red, labels in points], ranks[1], size, budget)
            distances, labels0 = _sample(env0, size, budget)
            _, labels1 = _sample(env1, size, budget)
            columns.append((green, distances, labels0, labels1))
        distances, labels0, labels1 = array('I'), array('H'), array('H')
        for red in range(size):
            env0 = _envelope([(g, ds[red], ls[red]) for g, ds, ls, _ in columns], ranks[0], size, budget)
            env1 = _envelope([(g, ds[red], ls[red]) for g, ds, _, ls in columns], ranks[1], size, budget)
            ds, ls = _sample(env0, size, budget)
            distances.extend(ds)
            labels0.extend(ls)
            _, ls = _sample(env1, size, budget)
            labels1.extend(ls)
        planes.append((blue, distances, labels0, labels1))
    header_bytes = 12 + (size * size + 1) * 4
    if header_bytes + size * size * 5 > budget.limits.output_bytes:
        raise PixelAssemblyBudgetExceeded('PIXEL_OUTPUT_BYTES_LIMIT')
    directory, records = bytearray((size * size + 1) * 4), bytearray()
    pack_run = struct.Struct('<BHH').pack
    for line in range(size * size):
        struct.pack_into('<I', directory, line * 4, len(records) // 5)
        env0 = _envelope([(b, ds[line], ls[line]) for b, ds, ls, _ in planes], ranks[0], size, budget)
        env1 = _envelope([(b, ds[line], ls[line]) for b, ds, _, ls in planes], ranks[1], size, budget)
        i = j = 0
        previous = None
        while i < len(env0) and j < len(env1):
            end0 = env0[i + 1][0] - 1 if i + 1 < len(env0) else size - 1
            end1 = env1[j + 1][0] - 1 if j + 1 < len(env1) else size - 1
            end, pair = min(end0, end1), (env0[i][3], env1[j][3])
            budget.spend(1)
            if pair == previous:
                records[-5] = end
            else:
                if header_bytes + len(records) + 5 > budget.limits.output_bytes:
                    raise PixelAssemblyBudgetExceeded('PIXEL_OUTPUT_BYTES_LIMIT')
                records.extend(pack_run(end, *pair))
            previous = pair
            if end == end0:
                i += 1
            if end == end1:
                j += 1
    runs = len(records) // 5
    struct.pack_into('<I', directory, size * size * 4, runs)
    budget.spend(0)
    return b''.join((struct.pack('<4sII', b'SRGB', len(candidates), runs), directory, records))


def assemble_pixel_resources(materials_base: bytes, *, game_version: str,
                             verified_base_sha256: str, policy: dict,
                             limits: PixelAssemblyLimits | None = None,
                             checkpoint: Callable | None = None) -> PixelAssembly:
    """Derive pixel.catalog/pixel.rgb, without asserting base/source completeness.

    policy is a *server-owned reviewed input*, not an upload's claim of trusted
    selection. Its version/consumer pin and ordered ID lists are all bound in the
    evidence. Hash verification is mandatory but does not replace source review.
    The returned binary always covers all 65,536 scanlines and 16,777,216 colors.
    """
    budget = _Budget(limits or PixelAssemblyLimits(), checkpoint)
    tables = _read_base(materials_base, verified_base_sha256, budget)
    selected, policy = _read_policy(policy, game_version, tables, budget)
    candidates = _candidates(tables, selected)
    fingerprint = _fingerprint(candidates)
    catalog = {'schemaVersion': 1, 'version': game_version, **selected, 'rgbCatalogSha256': fingerprint}
    rgb = _build_index(candidates, budget)
    catalog_raw = canonical_json(catalog)
    evidence = {
        'schemaVersion': 1, 'algorithm': ALGORITHM, 'gameVersion': game_version,
        'policyId': policy['policyId'], 'policySha256': hashlib.sha256(canonical_json(policy)).hexdigest(),
        'consumerCommit': policy['consumerCommit'], 'baseSha256': verified_base_sha256,
        'rgbCatalogSha256': fingerprint, 'catalogSha256': hashlib.sha256(catalog_raw).hexdigest(),
        'rgbSha256': hashlib.sha256(rgb).hexdigest(), 'candidates': len(candidates),
        'scanlines': 65536, 'colors': 16777216, 'runs': struct.unpack_from('<I', rgb, 8)[0],
        'rawBytes': len(rgb), 'sourceCompletenessEstablished': False,
        'boundary': 'Derived from supplied base; base colors, extraction coverage and publication rights require independent review.',
    }
    budget.spend(0)
    return PixelAssembly(catalog, rgb, evidence)
