"""Complete *data join* of a fixed Item observation, never runtime acceptance.

The public APIs derive their static inputs afresh from fixed PE bytes and pin the
application source policy. Count is an array bound, not selectable membership:
only positive named ItemID Constant rows select records. No old catalog is read.
"""
from __future__ import annotations

import ast
import copy
import math
from pathlib import Path
import hashlib
import tempfile
import struct

from .accessory_prefix_semantics import extract_accessory_prefix_semantics, _instance_field
from .item_assembler import BOOL_FIELDS, GROUPS, POOLS, assemble_item_resources
from .item_dispatch_sets import ItemDispatchSetLimits, _bool_factory
from .item_texture_aliases import _Program, _Budget, _CheckpointCancelled, _constant, _linear_store
from .prefix_coefficient_semantics import extract_prefix_coefficient_semantics, NAMES
from .prefix_group_semantics import extract_prefix_group_semantics
from .prefix_pool_semantics import extract_prefix_pool_semantics
from .runtime_item_adapter import ITEM_SETS, OBSERVED_FIELDS, adapt_observed_items
from .security import PipelineError, canonical_json, sha256
from .server_semantics import SemanticLimits, read_assembly_bytes, _id_constants
from .set_factory_lifecycle import _int_array
from .sorting_priority_literals import extract_sorting_priority_literals

SOURCE_SHA256 = '960a03bff6050cf7be16dfc1a7b19e10fc2c4f8f835a6a3b135a50dd9e6ba2f3'
GAME_VERSION = '1.4.5.8'
APP_SOURCE_COMMIT = '638e770db844174ff990e42e40a7e96290ff6968'
APP_SOURCE_BLOBS = {
    'scripts/generate-player-item-rules.py': '722cc1f29a9b627c7b84c5c68e4330974b1d82ce',
    'shared/game/item-prefixes.mjs': '2d1fccc1584f3a1c92ec6d9c835a4a7c09f5e24a',
}
APP_SOURCE_PINS = {
    'scripts/generate-player-item-rules.py': 'a58392c22c8fd27c8a29c88c5f790f309b5f46fa640e8a62b210050bb3702fb7',
    'shared/game/item-prefixes.mjs': 'e73da9a4d3b167bdee4010e359ea2f1c57d71fbc24c9c32953fa47d07cb53984',
}
ROOT_FIELDS = frozenset(('schemaVersion', 'kind', 'status', 'gameVersion', 'culture', 'context',
    'sourceSha256', 'itemCount', 'prefixCount', 'playerObservation', 'records', 'groups', 'itemSets',
    'priorities', 'pools', 'prefixNames', 'initializersReturned', 'worldFlags', 'managedAssemblyHashes',
    'nativeModuleHashes', 'randomSeed', 'collectorExecutableSha256', 'osVersion', 'clrVersion',
    'requestedExecutionMode', 'isolationVerified', 'initializationVerified', 'sourceSemanticsVerified',
    'complete', 'publishable', 'missingJoins'))
FALSE_GATES = ('isolationVerified', 'initializationVerified', 'sourceSemanticsVerified', 'complete', 'publishable')
WORLD_FLAGS = ('drunkWorld', 'getGoodWorld', 'tenthAnniversaryWorld', 'dontStarveWorld', 'notTheBeesWorld',
               'remixWorld', 'noTrapsWorld', 'zenithWorld', 'skyblockWorld', 'infectedSeed')
CONTEXT_FIELDS = ('gameMode', 'difficulty', 'expertMode', 'masterMode', 'mechdusa',
                  'activeWorldFileDataPresent', 'difficultyOverride', 'dedServ', 'netMode', 'localPlayerIndex')
COEFFICIENT_DEFAULTS = {key: 1 if arg < 8 else 0 for arg, key in NAMES.items()}
OBSERVED_ITEM_SETS = (*ITEM_SETS, 'IsAMaterial')
ACCESSORY_KEYS = ('defense', 'maxMana', 'critBonus', 'damageBonus', 'moveBonus', 'meleeSpeedBonus')
RESEARCH_POLICY = 'observed-research-out-count-v1'


def _need(ok, why):
    if not ok: raise PipelineError('Observed Item join: ' + why)


def _exact(value, keys): return type(value) is dict and set(value) == set(keys)
def _int(value, low=0, high=65535): return type(value) is int and low <= value <= high


def _text(value, empty=False):
    if type(value) is not str or not (0 if empty else 1) <= len(value) <= 4096: return False
    try: value.encode('utf-8')
    except UnicodeEncodeError: return False
    return True


def _bounded_json(value, checkpoint):
    """Bound the entire root, including fields only carried as observation evidence."""
    stack, active, nodes, chars = [(value, 0, False)], set(), 0, 0
    while stack:
        value, depth, leaving = stack.pop()
        if leaving: active.remove(id(value)); continue
        nodes += 1
        _need(nodes <= 2000000 and depth <= 24, 'observation structure budget')
        if nodes % 1000 == 0: checkpoint()
        if type(value) in (dict, list):
            _need(id(value) not in active, 'circular observation')
            _need(len(value) <= 65536 and len(stack) + len(value) * 2 + nodes <= 2000000,
                  'observation container budget')
            active.add(id(value)); stack.append((value, depth, True))
            if type(value) is dict:
                _need(all(type(k) is str for k in value), 'non-string observation key')
                for key, item in value.items():
                    stack.append((key, depth + 1, False)); stack.append((item, depth + 1, False))
            else: stack.extend((item, depth + 1, False) for item in value)
        elif type(value) is str:
            _need(_text(value, empty=True), 'invalid/big observation string')
            chars += len(value.encode('utf-8')); _need(chars <= 32 * 1024 * 1024, 'observation text budget')
        elif type(value) in (int, float):
            _need(abs(value) <= 2**53 - 1 and math.isfinite(value), 'nonfinite/large observation number')
        else: _need(value is None or type(value) is bool, 'non-JSON observation value')
    raw = canonical_json(value)
    _need(len(raw) <= 64 * 1024 * 1024, 'observation byte budget')
    return raw


def _app_policy(sources):
    _need(_exact(sources, APP_SOURCE_PINS), 'exact two application source files required')
    for path, digest in APP_SOURCE_PINS.items():
        _need(type(sources[path]) is bytes and 0 < len(sources[path]) <= 256 * 1024
              and sha256(sources[path]) == digest, 'application source pin mismatch: ' + path)
        blob = b'blob ' + str(len(sources[path])).encode('ascii') + b'\0' + sources[path]
        _need(hashlib.sha1(blob).hexdigest() == APP_SOURCE_BLOBS[path], 'application Git blob pin mismatch')
    # Parse an original source literal, not the generator's output; never run it.
    generator = ast.parse(sources['scripts/generate-player-item-rules.py'].decode('utf-8'))
    definitions = [node for node in generator.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'prefixes' for target in node.targets)]
    _need(len(definitions) == 1, 'no-prefix source declaration')
    literal = ast.literal_eval(definitions[0].value)
    _need(type(literal) is dict and set(literal) == {0} and _exact(literal[0], ('name', 'stats'))
          and _text(literal[0]['name']) and literal[0]['stats'] == {}, 'no-prefix caption source literal')
    return {'zeroCaption': literal[0]['name'], 'receipt': {
        'kind': 'pinned-application-source-policy', 'files': dict(APP_SOURCE_PINS),
        'noPrefixCaption': {'source': 'scripts/generate-player-item-rules.py',
                            'line': definitions[0].lineno, 'value': literal[0]['name'], 'gameObserved': False},
        'coefficientEncoding': 'omit neutral 1 multipliers and neutral 0 additive stats; retain exact decoded float32 values',
        'statScales': {'dmg/kb/spd/size/shtspd/mcst': 'multipliers, neutral 1',
                      'crt': 'percentage points', 'tagdmg/arpen/defense/maxMana': 'absolute additive values',
                      'critBonus/damageBonus/moveBonus/meleeSpeedBonus': 'nominal percentage points'},
        'researchEncoding': {'policy': RESEARCH_POLICY, 'rule':
            'copy the observed out-count; absent membership is allowed only with observed count 0 and remains separately evidenced'},
        'sourceCommit': APP_SOURCE_COMMIT, 'gitBlobs': dict(APP_SOURCE_BLOBS)}}


def _named_domain(ids, evidence, unsupported, owner, count):
    fields = ids.get(owner)
    _need(type(fields) is dict and fields, 'missing named Constant domain: ' + owner)
    _need(not any(row['type'] == 'Terraria.ID.' + owner and
                  row['reason'] != 'non-literal-or-no-constant; runtime state is not evaluated'
                  for row in unsupported), 'unsupported named Constant field: ' + owner)
    rows, excluded = [], []
    for name, value in fields.items():
        _need(_text(name) and type(value) is int, 'named Constant must be integer')
        entry = {'name': name, 'value': value, 'evidence': evidence[owner][name]}
        if name.casefold() == 'count' or value <= 0: excluded.append(entry); continue
        _need(value < count, 'positive named Constant outside independent Count')
        rows.append(entry)
    domain = sorted({row['value'] for row in rows})
    _need(domain and len(domain) < count, 'empty/oversize named domain')
    return domain, {'policy': 'positive-named-' + owner + '-constant-values-v1', 'owner': 'Terraria.ID.' + owner,
                    'selectedDomain': domain, 'namedConstantRows': rows, 'excludedConstantRows': excluded,
                    'nonliteralFields': [row for row in unsupported if row['type'] == 'Terraria.ID.' + owner],
                    'countUsedAs': 'upper-exclusive registry bound, never selectable membership',
                    'allSelectableGameItemsProven': False}


def _item_sets(p, count):
    wrapper, factory, getter, transform = _bool_factory(p)
    method = p.method('Terraria.ID.ItemID+Sets', '.cctor', b'\x00\x00\x01')
    factory_field = p.field('Terraria.ID.ItemID+Sets', 'Factory')
    result, proof = {}, {}
    for name in ITEM_SETS:
        token = p.field('Terraria.ID.ItemID+Sets', name, b'\x06\x1d\x02')
        store = _linear_store(method, token, 0)[0]
        end = next(n for n, ins in enumerate(method['instructions']) if ins.offset == store.offset)
        start = end - 1
        while start >= 0 and method['instructions'][start].opcode != 0x80: start -= 1
        part = method['instructions'][start + 1:end + 1]
        _need(len(part) in (8, 9) and part[0].opcode == 0x7e and part[0].operand == factory_field,
              'unsupported item-set source recipe')
        explicit = len(part) == 9
        default = _constant(part[1]) if explicit else 0
        _need(default in (0, 1), 'item-set nonboolean default')
        at, ids, calls, array = _int_array(p, part, 2 if explicit else 1)
        _need(at == len(part) - 2 and part[at].opcode == 0x6f
              and part[at].operand == (factory if explicit else wrapper)['token'], 'item-set factory binding')
        _need(all(_int(identity, 0, count - 1) for identity in ids), 'item-set ID outside source Count')
        maximum = 8 if method['header'] == 1 else p.meta.reader.uint(method['evidence']['bodyOffset'] + 2, 2)
        _need(maximum >= (5 if explicit else 4), 'item-set maxstack')
        values = [bool(default)] * count
        for identity in ids: values[identity] = not bool(default)
        result[name] = values
        proof[name] = {'fieldToken': f'0x{token:08x}', 'defaultValue': bool(default),
                       'overrideValue': not bool(default), 'literalIds': ids, 'array': array,
                       'callIlOffset': part[at].offset, 'storeIlOffset': store.offset}
    return result, {'status': 'CONDITIONAL_BOOL_CALLSITE_RECIPES', 'fields': proof,
                    'initializer': method['evidence'], 'factory': transform,
                    'preconditions': ['selected store is reached normally', transform['precondition'],
                                      'no mutation of RVA literals before copying'],
                    'wholeInitializerProven': False, 'finalRuntimeValuesProven': False}


def _source_bindings(p):
    fields = {}
    _, item = p.owner('Terraria.Item')
    for name in (*OBSERVED_FIELDS, 'type'):
        tokens = [0x04000000 | rid for rid in range(item['firstField'], item['lastField'])
                  if p.meta.string(p.row(4, rid)[0][1]) == name]
        _need(len(tokens) == 1, 'missing/ambiguous observed Item field')
        _, signature, evidence = _instance_field(p, tokens[0], 'Terraria.Item', name)
        expected = (b'\x06\x02',) if name in BOOL_FIELDS else (b'\x06\x0c',) if name == 'knockBack' else (
            b'\x06\x04', b'\x06\x05', b'\x06\x06', b'\x06\x07', b'\x06\x08')
        _need(signature in expected, 'observed Item field primitive type')
        fields[name] = {**evidence, 'signatureHex': signature.hex()}
    for owner, name in (('Terraria.Lang', 'prefix'),
                        ('Terraria.GameContent.Creative.CreativeItemSacrificesCatalog', 'Instance'),
                        ('Terraria.ID.ContentSamples', 'ItemPersistentIdsByNetIds'),
                        ('Terraria.ID.ContentSamples', 'ItemsByType')):
        p.field(owner, name)
    methods = {}
    for owner, name in (('Terraria.Item', 'get_Name'), ('Terraria.Localization.LocalizedText', 'get_Value'),
                        ('Terraria.GameContent.Creative.CreativeItemSacrificesCatalog',
                         'TryGetSacrificeCountCapToUnlockInfiniteItems')):
        method = p.method(owner, name)
        methods[owner + '.' + name] = method['evidence']
    material_token = p.field('Terraria.ID.ItemID+Sets', 'IsAMaterial', b'\x06\x1d\x02')
    material = next(row for row in reversed(p.evidence) if row.get('fieldToken') == f'0x{material_token:08x}')
    return {'itemFields': fields, 'methods': methods, 'materialRegistry': material,
            'registryFields': [entry for entry in p.evidence if 'fieldToken' in entry],
            'scope': 'exact source metadata/IL identities only; observation authentication and initialization not established'}


def _extract_static(raw, checkpoint):
    _need(type(raw) is bytes and sha256(raw) == SOURCE_SHA256, 'unsupported PE source hash')
    # Extractors accept paths; write a private immutable input snapshot, never run
    # a subprocess, executable, CLR, app generator, or game/vendor code.
    with tempfile.TemporaryDirectory(prefix='observed-item-static-') as tmp:
        path = Path(tmp) / 'source.pe'; path.write_bytes(raw)
        proofs = {
            'groups': extract_prefix_group_semantics(path, checkpoint),
            'pools': extract_prefix_pool_semantics(path, checkpoint),
            'coefficients': extract_prefix_coefficient_semantics(path, checkpoint),
            'accessories': extract_accessory_prefix_semantics(path, checkpoint),
            'priorities': extract_sorting_priority_literals(path, checkpoint=checkpoint),
        }
    _need(all(proof['inputSha256'] == SOURCE_SHA256 for proof in proofs.values()), 'source proof identity')
    count = proofs['groups']['declaredDomain']['count']
    prefix_count = proofs['pools']['declaredDomain']['count']
    _need(proofs['coefficients']['prefixCount'] == prefix_count, 'prefix Count source disagreement')
    limits = ItemDispatchSetLimits(instructions=1000000, steps=20000000, wall_seconds=120)
    p = _Program(raw, _Budget(limits, checkpoint))
    _need(p.meta.rows[32] == 1, 'assembly identity count')
    assembly, _ = p.meta.row(32, 1)
    _need('.'.join(map(str, assembly[1:5])) == GAME_VERSION and p.meta.string(assembly[7]) == 'Terraria',
          'assembly version/name disagreement')
    ids, evidence, unsupported, _ = _id_constants(p.meta, p.types)
    domain, domain_proof = _named_domain(ids, evidence, unsupported, 'ItemID', count)
    prefix_domain, prefix_proof = _named_domain(ids, evidence, unsupported, 'PrefixID', prefix_count)
    _need(prefix_domain == list(range(1, prefix_count)), 'unnamed prefix ID cannot be filled from Count')
    sets, set_proof = _item_sets(p, count)
    return {'gameVersion': GAME_VERSION, 'itemCount': count, 'prefixCount': prefix_count,
            'itemDomain': domain, 'prefixDomain': [0, *prefix_domain],
            'groups': proofs['groups']['groups'], 'pools': proofs['pools']['pools'], 'itemSets': sets,
            'priorityDomain': proofs['priorities']['priorityDomain'], 'priorities': proofs['priorities']['priorities'],
            'coefficients': proofs['coefficients']['records'], 'accessories': proofs['accessories']['effects'],
            'proofs': proofs, 'itemSetProof': set_proof, 'itemDomainProof': domain_proof,
            'prefixDomainProof': prefix_proof, 'sourceBindings': _source_bindings(p)}


def _prefixes(observation, static, policy):
    count = static['prefixCount']; rows = observation['prefixNames']
    _need(type(rows) is list and len(rows) == count, 'incomplete observed prefix names')
    names = {}
    for row in rows:
        _need(_exact(row, ('id', 'name')) and _int(row['id'], 0, count - 1) and row['id'] not in names
              and (_text(row['name'], empty=row['id'] == 0) or row['id'] == 0 and row['name'] is None),
              'missing/duplicate/malformed observed prefix name')
        names[row['id']] = row['name']
    _need(set(names) == set(static['prefixDomain']), 'observed prefix/source Constant domain mismatch')
    coefficients = {}
    for row in static['coefficients']:
        identity, stats = row['prefixId'], row['coefficients']
        _need(_int(identity, 0, count - 1) and identity not in coefficients
              and _exact(stats, COEFFICIENT_DEFAULTS), 'source coefficient coverage')
        _need(all(type(v) in (int, float) and math.isfinite(v) for v in stats.values()), 'source coefficient type')
        coefficients[identity] = {key: value for key, value in stats.items() if value != COEFFICIENT_DEFAULTS[key]}
    _need(set(coefficients) == set(names), 'incomplete source coefficients')
    effects = {}
    for row in static['accessories']:
        identity, stats = row['prefixId'], row['consumerStats']
        _need(_int(identity, 0, count - 1) and identity not in effects and type(stats) is dict
              and set(stats) <= set(ACCESSORY_KEYS) and stats
              and all(_int(value, 1, 1000) for value in stats.values()), 'invalid source accessory effects')
        effects[identity] = stats
    _need(not coefficients[0] and not effects.get(0), 'non-neutral no-prefix source effects')
    use_caption = names[0] in ('', None)
    prefixes = {str(identity): {'name': policy['zeroCaption'] if identity == 0 and use_caption else names[identity],
                'stats': {**coefficients[identity], **effects.get(identity, {})}} for identity in sorted(names)}
    return prefixes, {'observedNamesSha256': sha256(canonical_json(rows)),
                      'zeroName': {'observedValue': names[0], 'representation': prefixes['0']['name'],
                                  'origin': 'pinned-application-caption' if use_caption else 'observed-Lang.prefix-LocalizedText.Value',
                                  'gameObserved': not use_caption},
                      'effectScope': 'finite method transforms with undischargeable runtime/caller preconditions retained'}


def _join_observed_item_facts(observation, static, policy, checkpoint=lambda: None):
    """Internal fixture-test seam. Public callers cannot provide static dictionaries."""
    raw = _bounded_json(observation, checkpoint)
    _need(_exact(observation, ROOT_FIELDS), 'unexpected fixed collector root schema')
    _need(_int(observation['schemaVersion'], 1, 1) and observation['kind'] == 'pinned-item-player-data-observation-fragment'
          and observation['status'] == 'PARTIAL', 'unsupported collector schema/kind/status')
    _need(observation['sourceSha256'] == SOURCE_SHA256 and observation['gameVersion'] == static['gameVersion']
          and observation['culture'] == 'zh-Hans', 'observation source/version/culture mismatch')
    _need(all(observation[key] is False for key in FALSE_GATES), 'observation must not claim trust/acceptance')
    count = static['itemCount']; domain = static['itemDomain']
    _need(_int(observation['itemCount'], 2, 65536) and observation['itemCount'] == count
          and _int(observation['prefixCount'], 2, 256) and observation['prefixCount'] == static['prefixCount'],
          'observed/source Count disagreement')
    _need(type(domain) is list and domain == sorted(set(domain))
          and all(_int(n, 1, count - 1) for n in domain), 'independent named Item domain')
    context = observation['context']
    _need(_exact(context, CONTEXT_FIELDS) and _int(context['gameMode'], 0, 0)
          and _int(context['difficulty'], 0, 0) and context['difficultyOverride'] is None
          and all(context[key] is False for key in ('expertMode', 'masterMode', 'mechdusa'))
          and type(context['activeWorldFileDataPresent']) is bool and context['dedServ'] is True
          and _int(context['netMode'], 2, 2) and _int(context['localPlayerIndex'], 0, 255),
          'unsupported observed world/mode context')
    _need(_exact(observation['worldFlags'], WORLD_FLAGS) and all(v is False for v in observation['worldFlags'].values())
          and _int(observation['randomSeed'], 0, 0), 'unsupported observed world/random state')
    _need(observation['requestedExecutionMode'] == 'isolated-windows-x86-clr4-dedServ', 'execution mode label')
    for key in ('managedAssemblyHashes', 'nativeModuleHashes'):
        values = observation[key]
        _need(type(values) is dict and 0 < len(values) <= 256 and all(_text(k) and type(v) is str
              and len(v) == 64 and all(c in '0123456789abcdef' for c in v) for k, v in values.items()),
              'malformed diagnostic module hashes')
    for key in ('initializersReturned', 'missingJoins'):
        _need(type(observation[key]) is list and len(observation[key]) <= 256
              and all(_text(value) for value in observation[key]), 'malformed diagnostic list')
    _need(_text(observation['osVersion']) and _text(observation['clrVersion'])
          and type(observation['collectorExecutableSha256']) is str
          and len(observation['collectorExecutableSha256']) == 64
          and all(c in '0123456789abcdef' for c in observation['collectorExecutableSha256']), 'collector diagnostics')
    registry_receipts = {}
    for kind, names in (('groups', GROUPS), ('itemSets', ITEM_SETS)):
        table = observation[kind]
        _need(_exact(table, OBSERVED_ITEM_SETS if kind == 'itemSets' else names), 'incomplete observed ' + kind)
        registry_receipts[kind] = {}
        for name in names:
            values = table[name]
            _need(type(values) is list and len(values) == count and all(type(v) is bool for v in values),
                  'incomplete/wrong-type observed boolean array: ' + name)
            expected = static[kind][name]
            if kind == 'groups':
                members = set(expected); expected = [n in members for n in range(count)]
            _need(values == expected, 'observed array disagrees with source recipe: ' + name)
            registry_receipts[kind][name] = {'length': count, 'sha256': sha256(canonical_json(values)),
                                           'sourceComparison': 'equal-to-source-boundary-or-conditional-recipe'}
    material = observation['itemSets']['IsAMaterial']
    _need(type(material) is list and len(material) == count and all(type(v) is bool for v in material),
          'incomplete/wrong-type diagnostic IsAMaterial array')
    registry_receipts['itemSets']['IsAMaterial'] = {
        'length': count, 'sha256': sha256(canonical_json(material)), 'values': list(material),
        'sourceField': static['sourceBindings']['materialRegistry'],
        'sourceComparison': 'field identity and Count bound only; mutable observed diagnostic values',
        'finalItemMaterialEqualityClaimed': False}
    _need(_exact(observation['pools'], POOLS), 'incomplete observed pools')
    for name in POOLS:
        pool = observation['pools'][name]
        _need(type(pool) is list and all(_int(n, 1, static['prefixCount'] - 1) for n in pool)
              and pool == static['pools'][name], 'observed ordered pool disagrees with source: ' + name)
    priority_domain = static['priorityDomain']
    _need(_exact(observation['priorities'], priority_domain), 'incomplete observed priority registry')
    priorities = {}
    for name in priority_domain:
        priorities[name] = {**copy.deepcopy(static['priorities'][name]), 'values': copy.deepcopy(observation['priorities'][name])}
    records = observation['records']
    _need(type(records) is list and len(records) == len(domain), 'record count differs from named Item domain; no row may be dropped')
    identities, seen, normalized, absence = set(domain), set(), [], []
    for row in records:
        checkpoint()
        _need(_exact(row, ('requestedId', 'resolvedType', 'name', 'persistentIdPresent', 'persistentId',
                          'research', 'gameplay', 'variantIsNull')), 'unexpected raw item record schema')
        identity = row['requestedId']
        _need(_int(identity, 1, count - 1) and identity in identities and identity not in seen
              and _int(row['resolvedType'], 1, count - 1) and row['resolvedType'] == identity
              and row['variantIsNull'] is True, 'duplicate/foreign/remapped/variant Item record')
        _need(_text(row['name']) and row['persistentIdPresent'] is True and _text(row['persistentId']),
              'missing observed Item name/persistent ID; no guessing permitted')
        research = row['research']
        _need(_exact(research, ('present', 'count')) and type(research['present']) is bool and _int(research['count'])
              and (research['present'] or research['count'] == 0), 'invalid research observation/absent out-count')
        if not research['present']: absence.append({'id': identity, 'present': False, 'observedOutCount': research['count']})
        _need(_exact(row['gameplay'], OBSERVED_FIELDS), 'missing/extra observed gameplay field')
        for name, value in row['gameplay'].items():
            signature = static['sourceBindings']['itemFields'][name]['signatureHex']
            if signature == '0602':
                _need(type(value) is bool, 'observed bool field type: ' + name)
            elif signature == '060c':
                _need(type(value) in (int, float) and math.isfinite(value)
                      and struct.unpack('<f', struct.pack('<f', value))[0] == value,
                      'observed float field is not exact finite float32: ' + name)
            else:
                ranges = {'0604': (-128, 127), '0605': (0, 255), '0606': (-32768, 32767),
                          '0607': (0, 65535), '0608': (-2**31, 2**31 - 1)}
                _need(signature in ranges and _int(value, *ranges[signature]),
                      'observed integral field outside source primitive range: ' + name)
        seen.add(identity)
        normalized.append({'id': identity, 'name': row['name'], 'persistentId': row['persistentId'],
                           'research': research['count'], 'gameplay': copy.deepcopy(row['gameplay'])})
    _need(seen == identities, 'missing selected Item record')
    normalized.sort(key=lambda row: row['id']); absence.sort(key=lambda row: row['id'])
    prefixes, prefix_receipt = _prefixes(observation, static, policy)
    adapter_input = {'gameVersion': static['gameVersion'], 'itemCount': count, 'itemDomain': list(domain),
                     'records': normalized, 'prefixes': prefixes, 'pools': copy.deepcopy(observation['pools']),
                     'groups': copy.deepcopy(observation['groups']),
                     'itemSets': {key: copy.deepcopy(observation['itemSets'][key]) for key in ITEM_SETS},
                     'priorityDomain': list(priority_domain), 'priorities': priorities,
                     'sourceHashes': {'pinned-game-pe': SOURCE_SHA256, 'raw-observation': sha256(raw),
                                      'prefix-generator-policy': APP_SOURCE_PINS['scripts/generate-player-item-rules.py'],
                                      'prefix-consumer-policy': APP_SOURCE_PINS['shared/game/item-prefixes.mjs']}}
    inputs, adapter_receipt = adapt_observed_items(adapter_input)
    receipt = {'schemaVersion': 1, 'status': 'ITEM_OBSERVATION_DATA_JOINED',
               'sourceSha256': SOURCE_SHA256, 'observationSha256': sha256(raw),
               'normalizedSha256': sha256(canonical_json(inputs)), 'selectedItemRows': len(domain),
               'selectionScope': 'positive named ItemID Constant values only, not a universal selectable-item proof',
               'itemDomainEvidence': static['itemDomainProof'], 'prefixDomainEvidence': static['prefixDomainProof'],
               'sourceProofs': static['proofs'], 'itemSetProof': static['itemSetProof'],
               'sourceBindings': static['sourceBindings'], 'applicationPolicy': policy['receipt'],
               'registryComparisons': registry_receipts, 'prefixProjection': prefix_receipt,
               'researchRepresentation': {'policy': RESEARCH_POLICY, 'absenceRows': absence,
                    'rule': 'observed absent membership plus observed out-count 0 encodes consumer research 0; presence is not invented'},
               'observationContext': {key: copy.deepcopy(observation[key]) for key in ('culture', 'context', 'worldFlags', 'randomSeed')},
               'adapterReceipt': adapter_receipt, 'executedInput': False, 'observationAuthenticated': False,
               'runtimeInitializationVerified': False, 'sourceSemanticsVerified': False,
               'runtimeSnapshotUsable': False, 'complete': False, 'publishable': False,
               'remainingGaps': ['isolated runtime and initializer acceptance', 'collector/observation authentication',
                   'runtime dependency/OS image acceptance and mutable-state closure',
                   'proof preconditions and actual normal-return/caller behavior remain unverified',
                   'named Constant selection policy is not universal selectable-item completeness',
                   'source proof/observation joins do not grant publication approval']}
    checkpoint()
    return inputs, receipt


def adapt_observed_item_facts(observation, *, pe_bytes, app_sources, checkpoint=lambda: None):
    """Return normalized Item assembler inputs plus a non-acceptance receipt.

    pe_bytes accepts immutable bytes or pathlib.Path. app_sources is exactly the
    two APP_SOURCE_PINS source blobs, not extracted facts or historical payloads.
    The raw observation must contain the complete fixed collector root (including
    prefixNames). No claimed caller-supplied static table or trust bit is accepted.
    """
    checkpoint()
    _bounded_json(observation, checkpoint)
    raw = read_assembly_bytes(pe_bytes, SemanticLimits(), checkpoint) if isinstance(pe_bytes, Path) else pe_bytes
    _need(type(raw) is bytes and len(raw) <= 128 * 1024 * 1024, 'bounded PE bytes or Path required')
    policy = _app_policy(app_sources)
    try: static = _extract_static(raw, checkpoint)
    except _CheckpointCancelled as exc: raise exc.original
    return _join_observed_item_facts(observation, static, policy, checkpoint)


def assemble_observed_item_resources(observation, *, pe_bytes, app_sources, checkpoint=lambda: None):
    """Return all three Item consumer objects, retaining the full join receipt."""
    inputs, receipt = adapt_observed_item_facts(observation, pe_bytes=pe_bytes, app_sources=app_sources,
                                               checkpoint=checkpoint)
    objects, assembly = assemble_item_resources(inputs)
    receipt['assemblyReceipt'] = assembly
    receipt['objects'] = assembly['objects']
    checkpoint()
    return objects, receipt
