"""Strict, data-only projection of normalized material facts and app policy.

A digest binds input identity, not truth. No game code is run and no private
baseline is read. Both emitted roles and the receipt remain DERIVED_ONLY.
"""
from __future__ import annotations

import re
from .security import PipelineError, canonical_json, sha256

ALGORITHM = 'viewer-materials-projection-v1'
MAX_ROWS = 65536
MAX_BYTES = 32 * 1024 * 1024
MAX_LAYOUT_VALUES = 1_000_000


def _require(ok, code):
    if not ok: raise PipelineError('MATERIALS_' + code)


def _int(value, low=0):
    return type(value) is int and low <= value <= 65535


def _fields(value, fields, code):
    _require(type(value) is dict and set(value) == set(fields.split()), code)


def _text(value, nullable=False):
    if nullable and value is None: return True
    if type(value) is not str or not 0 < len(value) <= 1024: return False
    try:
        return len(value.encode('utf-16-le')) // 2 <= 1024
    except UnicodeError:
        return False


def assemble_material_resources(*, records: dict, policy: dict) -> tuple[dict[str, bytes], dict]:
    """Produce materials.base/rules; policy list order is consumer precedence.

    See docs/materials-assembly.md for the closed named-record interchange.
    Completeness, names, colors, layouts and app choices require independent
    source proof. This projection deliberately cannot mint that proof.
    """
    _fields(records, 'schemaVersion gameVersion tiles walls paints', 'RECORD_FIELDS')
    _require(type(records['schemaVersion']) is int and records['schemaVersion'] == 1, 'RECORD_SCHEMA')
    version = records['gameVersion']
    _require(type(version) is str and re.fullmatch(r'[0-9]+(?:\.[0-9]+){2,3}', version), 'VERSION')
    _fields(policy, 'schemaVersion gameVersion consumerCommit policyId algorithm materials shapes variants', 'POLICY_FIELDS')
    _require(type(policy['schemaVersion']) is int and policy['schemaVersion'] == 1
             and policy['gameVersion'] == version and policy['algorithm'] == ALGORITHM, 'POLICY_VERSION')
    _require(type(policy['consumerCommit']) is str and re.fullmatch(r'[a-f0-9]{40}', policy['consumerCommit']), 'CONSUMER_COMMIT')
    _require(type(policy['policyId']) is str and re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}', policy['policyId']), 'POLICY_ID')
    base, domains, flags = {}, {}, {}
    text_bytes = 0

    def text(value, nullable=False):
        nonlocal text_bytes
        _require(_text(value, nullable), 'TEXT')
        if value is not None: text_bytes += len(value.encode('utf-8'))
        _require(text_bytes <= MAX_BYTES // 2, 'TEXT_BUDGET')
        return value

    for kind in ('tiles', 'walls', 'paints'):
        rows = records[kind]
        _require(type(rows) is list and 0 < len(rows) <= MAX_ROWS, 'RECORD_ROWS')
        projected, domain = [], set()
        for row in rows:
            _fields(row, 'id name color internalName' + (' frameImportant' if kind == 'tiles' else ''), 'RECORD_ROW')
            ident = row['id']
            _require(_int(ident) and ident not in domain, 'RECORD_ID')
            domain.add(ident)
            _require(type(row['color']) is str and re.fullmatch(r'#[a-fA-F0-9]{6}', row['color']), 'COLOR')
            projected.append([ident, text(row['name']), row['color'].lower(), text(row['internalName'], True)])
            if kind == 'tiles':
                _require(type(row['frameImportant']) is bool, 'FRAME_IMPORTANT')
                flags[ident] = row['frameImportant']
        base[kind], domains[kind] = sorted(projected, key=lambda row: row[0]), domain
    _require(domains['tiles'] == set(range(len(domains['tiles']))), 'TILE_DOMAIN')
    for name in ('materials', 'shapes', 'variants'):
        _require(type(policy[name]) is list and (0 if name == 'variants' else 1) <= len(policy[name]) <= MAX_ROWS, 'POLICY_ROWS')
    layout_values = 0

    def layout(value):
        nonlocal layout_values
        if value is None: return [None] * 5
        _fields(value, 'width height coordinateWidth coordinateHeights padding', 'LAYOUT_FIELDS')
        _require(_int(value['width'], 1) and _int(value['height'], 1)
                 and _int(value['coordinateWidth'], 1) and _int(value['padding']), 'LAYOUT_GEOMETRY')
        heights = value['coordinateHeights']
        _require(type(heights) is list and len(heights) == value['height']
                 and all(_int(h, 1) for h in heights), 'LAYOUT_HEIGHTS')
        layout_values += len(heights)
        _require(layout_values <= MAX_LAYOUT_VALUES, 'LAYOUT_BUDGET')
        return [value['width'], value['height'], value['coordinateWidth'], list(heights), value['padding']]

    materials, shapes, variants, keys, material_tiles, shape_tiles = [], [], [], {}, set(), set()
    for row in policy['materials']:
        _fields(row, 'tileId style alternate random name frameX frameY layout itemId', 'MATERIAL_FIELDS')
        _require(_int(row['tileId']) and row['tileId'] in domains['tiles']
                 and all(_int(row[k]) for k in ('style', 'alternate', 'random', 'itemId')), 'MATERIAL_ID')
        key = ':'.join(str(row[k]) for k in ('tileId', 'style', 'alternate', 'random', 'itemId'))
        _require(key not in keys, 'MATERIAL_DUPLICATE')
        keys[key] = row['tileId']
        material_tiles.add(row['tileId'])
        geometry = layout(row['layout'])
        _require((row['frameX'] is None and row['frameY'] is None) if row['layout'] is None
                 else _int(row['frameX']) and _int(row['frameY']), 'MATERIAL_FRAME')
        materials.append([row['tileId'], row['style'], row['alternate'], row['random'], text(row['name']),
                          row['frameX'], row['frameY'], *geometry, None, None, row['itemId']])
    for row in policy['shapes']:
        _fields(row, 'tileId name frameX frameY layout mode', 'SHAPE_FIELDS')
        _require(_int(row['tileId']) and row['tileId'] in domains['tiles']
                 and type(row['mode']) is str and row['mode'] in ('exact', 'layout', 'auto'), 'SHAPE_ID_MODE')
        _require(_int(row['frameX']) and (_int(row['frameY']) or row['frameY'] is None and row['mode'] == 'exact'), 'SHAPE_FRAME')
        _require(row['layout'] is not None or row['mode'] == 'exact', 'SHAPE_LAYOUT_REQUIRED')
        geometry = layout(row['layout'])
        shapes.append([row['tileId'], text(row['name']), row['frameX'], row['frameY'], *geometry, row['mode']])
        shape_tiles.add(row['tileId'])
    for row in materials:
        # Actual tile-materials.js falls back to the first explicit shape here.
        _require(not flags[row[0]] or row[5] is not None and row[0] != 171
                 or row[0] in shape_tiles, 'MISSING_FRAMED_SHAPE')
    aliases = set()
    for row in policy['variants']:
        _fields(row, 'tileId subId name layoutKey', 'VARIANT_FIELDS')
        _require(_int(row['tileId']) and row['tileId'] in material_tiles | shape_tiles, 'VARIANT_TILE')
        sub = text(row['subId'])
        _require((row['tileId'], sub) not in aliases, 'VARIANT_DUPLICATE')
        aliases.add((row['tileId'], sub))
        key = row['layoutKey']
        _require(key is None or type(key) is str and keys.get(key) == row['tileId'], 'VARIANT_LAYOUT_KEY')
        variants.append([row['tileId'], sub, text(row['name']), key])
    rules = {'materials': materials, 'shapes': shapes, 'variants': variants,
             'frameImportant': [flags[i] for i in range(len(flags))]}
    # Serialization follows closed shape validation, never arbitrary recursion.
    inputs = {'records': canonical_json(records), 'policy': canonical_json(policy)}
    objects = {'materials.base': canonical_json(base), 'materials.rules': canonical_json(rules)}
    _require(all(len(raw) <= MAX_BYTES for raw in (*inputs.values(), *objects.values())), 'BYTE_BUDGET')
    base_hash = sha256(objects['materials.base'])
    receipt = {'schemaVersion': 1, 'kind': 'consumer-derivation', 'status': 'DERIVED_ONLY',
               'algorithm': ALGORITHM, 'gameVersion': version, 'consumerCommit': policy['consumerCommit'],
               'policyId': policy['policyId'], 'recordsSha256': sha256(inputs['records']),
               'policySha256': sha256(inputs['policy']), 'materialBaseSha256': base_hash,
               'objects': {role: {'bytes': len(raw), 'sha256': sha256(raw),
                                  **({'baseSha256': base_hash} if role == 'materials.rules' else {})}
                           for role, raw in objects.items()},
               'sourceSemanticsVerified': False, 'publicationApproved': False,
               'missingProducerProof': ['complete tile/wall/paint identity domains and localized names',
                   'final map colors, paint inputs, map/shader/coating semantics',
                   'frame-important facts, TileObjectData layouts, styles/alternates/randoms and item links',
                   'independent app shape/variant selection and precedence policy review']}
    return objects, receipt
