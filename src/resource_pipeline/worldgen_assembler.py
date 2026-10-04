"""Bounded worldgen projections. Only choices is a release role; options is service data.

Service schema/defaults and independent catalogs must be supplied. Selections,
labels and config FieldSpecs are app policy, not facts extracted from Terraria.
No receipt produced here authenticates sources or authorizes publication.
"""
from __future__ import annotations
import json
import math
import re
from .security import PipelineError, canonical_json, sha256

ALGORITHM = 'worldgen-service-and-choices-v1'
NUMERIC = ('items', 'tiles', 'walls', 'paints')
NAMED = ('ores', 'depths', 'environments', 'biomes', 'rooms', 'styles', 'generationStyles', 'seeds', 'passes')
KINDS = {'object', 'map', 'array', 'boolean', 'integer', 'number', 'string', 'any'}
BAD_KEYS = {'__proto__', 'prototype', 'constructor'}
MAX_BYTES = 16 * 1024 * 1024


def _need(condition, message):
    if not condition: raise PipelineError(message)


def _text(value, maximum=4096, empty=False):
    if type(value) is not str: return False
    try: return (0 if empty else 1) <= len(value.encode('utf-16-le')) // 2 <= maximum
    except UnicodeError: return False


def _number(value):
    return type(value) in (int, float) and abs(value) <= 2**53 - 1 and math.isfinite(value)


def _integer(value, low=0, high=65535):
    return type(value) is int and low <= value <= high


def _hash(value):
    return type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None


def _exact(value, keys):
    _need(type(value) is dict and set(value) == set(keys), 'Incomplete or unknown worldgen fields')


def _safe(value, limit=200000):
    stack, count, characters = [(value, 0)], 0, 0
    while stack:
        node, depth = stack.pop()
        count += 1
        _need(count <= limit and depth <= 32, 'Worldgen node/depth budget exceeded')
        if node is None or type(node) is bool: continue
        if type(node) in (int, float):
            _need(_number(node), 'Invalid worldgen number')
        elif type(node) is str:
            _need(_text(node, empty=True), 'Invalid worldgen text')
            characters += len(node.encode('utf-8'))
        elif type(node) in (dict, list):
            _need(len(node) + count + len(stack) <= limit, 'Worldgen node budget exceeded')
            if type(node) is dict:
                for key in node:
                    _need(_text(key, 160) and key not in BAD_KEYS and not any(ord(c) < 32 for c in key), 'Unsafe worldgen key')
                    characters += len(key.encode('utf-8'))
                children = node.values()
            else: children = node
            stack.extend((child, depth + 1) for child in children)
        else: raise PipelineError('Worldgen requires JSON data')
        _need(characters <= MAX_BYTES, 'Worldgen text budget exceeded')


def _fields(value, types, inline=False):
    _need(type(value) is dict, 'Invalid FieldSpec group')
    for spec in value.values(): _field(spec, types, inline)


def _field(spec, types, inline=False):
    _need(type(spec) is dict and type(spec.get('kind')) is str and spec['kind'] in KINDS, 'Invalid FieldSpec kind')
    kind = spec['kind']
    allowed = {'kind', 'label', 'help', 'readOnly'}
    allowed |= {'choices'} if kind == 'string' else set()
    allowed |= {'min', 'max'} if kind in ('number', 'integer') else set()
    allowed |= ({'fields'} if inline else {'ref'}) if kind == 'object' else set()
    allowed |= {'item'} if kind in ('map', 'array') else set()
    allowed |= {'keyKind'} if kind == 'map' else set()
    _need(set(spec) <= allowed, 'Unknown or incompatible FieldSpec properties')
    for key in ('label', 'help'):
        if key in spec: _need(_text(spec[key]), 'Invalid FieldSpec label/help')
    if 'readOnly' in spec: _need(type(spec['readOnly']) is bool, 'Invalid readOnly')
    if 'choices' in spec:
        values = spec['choices']
        _need(type(values) is list and 0 < len(values) <= 20000 and all(_text(v, 160) for v in values), 'Invalid enum choices')
        _need(len(set(values)) == len(values), 'Duplicate enum choices')
    for key in ('min', 'max'):
        if key in spec: _need(_number(spec[key]), 'Invalid FieldSpec bounds')
    _need(not ('min' in spec and 'max' in spec) or spec['min'] <= spec['max'], 'Reversed FieldSpec bounds')
    if kind == 'object':
        if inline: _fields(spec.get('fields'), types, True)
        else: _need(_text(spec.get('ref'), 160) and spec['ref'] in types, 'Unknown schema reference')
    if kind in ('array', 'map'): _field(spec.get('item'), types, inline)
    if 'keyKind' in spec: _need(spec['keyKind'] in ('string', 'integer'), 'Invalid map key kind')


def _defaults(value, spec, types, depth=0):
    _need(depth <= 16 and value is not None, 'Invalid default depth/null')
    kind = spec['kind']
    if kind == 'any':
        kind = ('boolean' if type(value) is bool else 'number' if type(value) in (int, float)
                else 'string' if type(value) is str else 'array' if type(value) is list else 'map')
        spec = {'kind': kind, 'item': {'kind': 'any'}}
    if kind == 'object':
        group = types[spec['ref']]
        _need(type(value) is dict and set(value) <= set(group), 'Unknown default fields')
        for key, child in value.items():
            _need(_text(key, 100) and not group[key].get('readOnly', False), 'Read-only or invalid default key')
            _defaults(child, group[key], types, depth + 1)
        for prefix in ('', 'x_ratio', 'y_ratio'):
            lo, hi = (f'min_{prefix}', f'max_{prefix}') if prefix else ('min', 'max')
            if lo in value and hi in value:
                _need(_number(value[lo]) and _number(value[hi]) and value[lo] <= value[hi], 'Reversed default range')
    elif kind in ('map', 'array'):
        _need(type(value) is (dict if kind == 'map' else list) and len(value) <= 256, 'Invalid default collection')
        if kind == 'map':
            for key in value:
                _need(_text(key, 100) and (spec.get('keyKind') != 'integer' or re.fullmatch('[0-9]{1,6}', key)), 'Invalid default map key')
        for child in value.values() if kind == 'map' else value:
            _defaults(child, spec['item'], types, depth + 1)
    elif kind == 'boolean': _need(type(value) is bool, 'Invalid boolean default')
    elif kind in ('integer', 'number'):
        _need(_number(value) and (kind != 'integer' or type(value) is int) and spec.get('min', -100000) <= value <= spec.get('max', 100000), 'Invalid numeric default')
    else:
        _need(_text(value, 160, empty=True) and not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
              and ('choices' not in spec or value in spec['choices']), 'Invalid string default')


def _rows(rows, numeric, maximum=20000):
    _need(type(rows) is list and 0 < len(rows) <= maximum, 'Empty or oversized authoritative catalog')
    seen = set()
    for row in rows:
        _need(type(row) is list and len(row) == 2 and _text(row[1], 1024), 'Invalid catalog row')
        _need(_integer(row[0]) if numeric else _text(row[0], 160), 'Invalid catalog identity type')
        _need(row[0] not in seen, 'Duplicate catalog identity')
        seen.add(row[0])
    return seen


def _load(raw):
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, 'Invalid foundation size')
    def pairs(rows):
        out = {}
        for key, value in rows:
            _need(key not in out, 'Duplicate foundation JSON key')
            out[key] = value
        return out
    def constant(_): raise PipelineError('Nonfinite foundation')
    try: value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc: raise PipelineError('Invalid foundation JSON') from exc
    _safe(value, 1000000)
    return value


def assemble_worldgen_resources(inputs: dict, *, foundations: dict) -> tuple[dict[str, bytes], dict]:
    """Validate independent normalized data, emit byte-stable roles without trust."""
    _safe(inputs)
    _exact(inputs, ('gameVersion', 'serverVersionKey', 'serviceOptions', 'defaults', 'authoritativeCatalogs', 'choices', 'sourceHashes'))
    game = inputs['gameVersion']
    _need(type(game) is str and re.fullmatch('[0-9]+(?:\\.[0-9]+){2,3}', game), 'Invalid game version')
    server = inputs['serverVersionKey']
    _need(type(server) is str and re.fullmatch('[0-9]{3,8}', server), 'Invalid server version')
    hashes = inputs['sourceHashes']
    _need(type(hashes) is dict and 0 < len(hashes) <= 64 and all(_hash(v) for v in hashes.values()), 'Missing source hashes')
    options = inputs['serviceOptions']
    _exact(options, ('enabled', 'versions', 'schema'))
    versions = options['versions']
    _need(type(options['enabled']) is bool and type(versions) is list and len(versions) <= 100
          and all(type(v) is str and re.fullmatch('[0-9]{3,8}', v) for v in versions), 'Invalid service versions')
    _need(len(set(versions)) == len(versions) and server in versions, 'Selected server missing or duplicate')
    schema = options['schema']
    _safe(schema, 40000)
    _exact(schema, ('revision', 'root', 'types'))
    types = schema['types']
    _need(_hash(schema['revision']) and type(types) is dict and 'HookConfiguration' in types, 'Invalid service schema')
    _fields(schema['root'], types)
    for group in types.values(): _fields(group, types)
    root = schema['root']
    _need(root.get('settings', {}).get('kind') == 'object' and root['settings'].get('ref') == 'HookConfiguration'
          and root.get('modules', {}).get('kind') == 'object', 'Required service root missing')
    _need(type(inputs['defaults']) is dict and set(inputs['defaults']) <= set(root), 'Invalid defaults root')
    for key, value in inputs['defaults'].items():
        _need(_text(key, 100) and not root[key].get('readOnly', False), 'Read-only root default')
        _defaults(value, root[key], types, depth=1)
    _need(len(canonical_json(inputs['defaults'])) <= 128000, 'Defaults size exceeded')
    _exact(foundations, ('items', 'materials'))
    bindings, bases = {}, {}
    for kind, dependency in foundations.items():
        _exact(dependency, ('gameVersion', 'releaseId', 'baseSha256', 'bytes'))
        _need(dependency['gameVersion'] == game and _hash(dependency['releaseId']) and _hash(dependency['baseSha256']), 'Foundation version/binding mismatch')
        base = _load(dependency['bytes'])
        _need(sha256(dependency['bytes']) == dependency['baseSha256'], 'Foundation bytes hash mismatch')
        bindings[kind] = {key: dependency[key] for key in ('releaseId', 'baseSha256')}
        bases[kind] = base
    items, materials = bases['items'], bases['materials']
    _need(type(items) is list and len(items) == 7 and all(type(col) is list and len(col) == len(items[0]) for col in items), 'Invalid item foundation columns')
    _need(0 < len(items[0]) <= 65535 and all(_integer(v, 1) for v in items[0])
          and items[0] == sorted(set(items[0])), 'Invalid item foundation IDs')
    _need(all(_text(v) for v in items[2]) and len(set(items[2])) == len(items[2]), 'Invalid item persistent IDs')
    for column, low, high in ((3, 0, 65535), (4, 1, 65535), (5, -1, 255), (6, 0, 65535)):
        _need(all(_integer(v, low, high) for v in items[column]), 'Invalid item foundation values')
    foundation_ids = {'items': _rows([[identity, name] for identity, name in zip(items[0], items[1])], True, 65535)}
    _need(type(materials) is dict, 'Invalid material foundation')
    for kind in NUMERIC[1:]:
        rows = materials.get(kind)
        _need(type(rows) is list and all(type(row) is list and 3 <= len(row) <= 32 and type(row[2]) is str and re.fullmatch('#[a-fA-F0-9]{6}', row[2])
                  and (len(row) == 3 or row[3] is None or _text(row[3], empty=True)) for row in rows), 'Missing material foundation rows')
        foundation_ids[kind] = _rows([row[:2] for row in rows], True, 65536)
    authority = inputs['authoritativeCatalogs']
    _exact(authority, NUMERIC + NAMED)
    domains = {kind: _rows(rows, kind in NUMERIC) for kind, rows in authority.items()}
    choices = inputs['choices']
    _exact(choices, ('gameChoices', 'catalogs', 'configOptions', 'choiceLabels', 'groupLabels'))
    _exact(choices['gameChoices'], NUMERIC)
    for kind, selected in choices['gameChoices'].items():
        _exact(selected, ('ids', 'names'))
        ids = selected['ids']
        _need(type(ids) is list and 0 < len(ids) <= 20000 and all(_integer(v) for v in ids), 'Invalid numeric choices')
        _need(len(set(ids)) == len(ids) and set(ids) <= domains[kind] and set(ids) <= foundation_ids[kind], 'Unknown or duplicate numeric choices')
        names = selected['names']
        _need(type(names) is list and len(names) <= min(len(ids), 2048), 'Invalid name override count')
        if names: _need(_rows(names, True) <= set(ids), 'Unknown name override')
    _exact(choices['catalogs'], NAMED)
    for kind, rows in choices['catalogs'].items():
        selected = _rows(rows, False)
        _need(selected <= domains[kind], 'Unknown named choice')
        if kind in ('passes', 'seeds'):
            _need(selected == domains[kind], 'Incomplete pass/seed authoritative domain')
    _exact(choices['configOptions'], ('biomes', 'passes'))
    for kind, group in choices['configOptions'].items():
        _fields(group, {}, True)
        _need(set(group) <= {row[0] for row in choices['catalogs'][kind]}, 'Config option missing catalog reference')
    labels = choices['choiceLabels']
    _need(type(labels) is dict and bool(labels) and all(_text(v, 1024) for v in labels.values()), 'Invalid choice labels')
    groups = choices['groupLabels']
    _need(type(groups) is dict and bool(groups), 'Missing group labels')
    for label in groups.values():
        _exact(label, ('label', 'help', 'icon'))
        _need(_text(label['label'], 1024) and _text(label['help']) and _text(label['icon'], 100), 'Invalid group labels')
    result = {'schemaVersion': 1, 'serverVersionKey': server, 'gameVersion': game,
              'schemaRevision': schema['revision'], 'foundations': bindings, **choices}
    _safe(result)
    objects = {'worldgen.choices': canonical_json(result), 'worldgen.options': canonical_json(options)}
    _need(all(len(raw) <= MAX_BYTES for raw in objects.values()), 'Worldgen output size exceeded')
    receipt = {'schemaVersion': 1, 'kind': 'consumer-derivation', 'status': 'DERIVED_ONLY', 'algorithm': ALGORITHM,
               'inputSha256': sha256(canonical_json(inputs)), 'sourceHashes': dict(hashes), 'foundations': bindings,
               'defaultsSha256': sha256(canonical_json(inputs['defaults'])),
               'authoritativeCatalogsSha256': sha256(canonical_json(authority)),
               'objects': {role: {'sha256': sha256(raw), 'bytes': len(raw)} for role, raw in objects.items()},
               'releaseRoles': ['worldgen.choices'], 'serviceArtifacts': ['worldgen.options'],
               'sourceSemanticsVerified': False, 'publicationApproved': False,
               'missingProducerProof': ['service schema/revision/default authority', 'independent catalog authority',
                                        'reviewed app selections/labels/FieldSpec policy', 'foundation release authenticity']}
    return objects, receipt
