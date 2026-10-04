"""Finite consumer projection, not a producer of final Terraria Item facts.

No missing property is defaulted. The independent positive-ID domain, final
records, prefix registries and app sorting inputs must all be present. Receipts
bind exact inputs/outputs but deliberately do not assert semantic verification
or publication approval. A caller must retain the producer proof separately.
"""
from __future__ import annotations

import math
import re
from .security import PipelineError, canonical_json, sha256

POLICY = "viewer-item-categories-v1"
# Reference algorithm, not a table of game facts.
CATEGORY_REFERENCE_SHA256 = "6b2cea2715f1237f1e99bf3ad917f6eed37798adf59256c78f857d6f694e5d7d"
RULE_FIELDS = ('damage', 'useAnimation', 'mana', 'knockBack', 'accessory', 'vanity',
               'headSlot', 'bodySlot', 'legSlot', 'handOnSlot', 'handOffSlot',
               'backSlot', 'frontSlot', 'shoeSlot', 'waistSlot', 'wingSlot',
               'shieldSlot', 'neckSlot', 'faceSlot', 'balloonSlot', 'beardSlot', 'dye')
CATEGORY_FIELDS = ('maxStack', 'rare', 'damage', 'defense', 'value', 'pick', 'axe',
                   'hammer', 'fishingPole', 'bait', 'healLife', 'healMana', 'ammo',
                   'buffType', 'createTile', 'createWall', 'mountType', 'makeNPC',
                   'dye', 'hairDye', 'headSlot', 'bodySlot', 'legSlot', 'accessory',
                   'vanity', 'consumable', 'material', 'questItem', 'paint',
                   'paintCoating', 'notAmmo', 'useAmmo', 'damageClass', 'food', 'fish', 'crate')
BOOL_FIELDS = {'accessory', 'vanity', 'consumable', 'material', 'questItem', 'notAmmo'}
ALL_FIELDS = tuple(dict.fromkeys((*RULE_FIELDS, *CATEGORY_FIELDS)))
POOLS = ('PrefixesForSwords', 'PrefixesForSpears', 'PrefixesForGunsBows',
         'PrefixesForMagic', 'PrefixesForSummons', 'PrefixesForBoomeransAndChakrums',
         'PrefixesForBoomeransAndChakrums_TerrarianYoyo', 'PrefixesForAccessories')
GROUPS = ('SwordsHammersAxesPicks', 'SpearsMacesChainsawsDrillsPunchCannon',
          'GunsBows', 'Magic', 'Summon', 'BoomerangsChakrams', 'ItemsThatCanHaveLegendary2')
CATEGORIES = ('all', 'weapons', 'tools', 'armor', 'accessories', 'vanity', 'dyes',
              'consumables', 'ammo', 'blocks', 'materials', 'pets', 'other')


def _int(value, low=0, high=65535):
    return type(value) is int and low <= value <= high


def _number(value):
    return type(value) in (int, float) and abs(value) <= 2**53 - 1 and math.isfinite(value)


def _text(value):
    if not isinstance(value, str) or not 0 < len(value) <= 4096: return False
    try:
        value.encode('utf-8')
        return True
    except UnicodeEncodeError:
        return False


def _ids(values, domain, label):
    if (type(values) is not list or len(values) > 65535
            or any(not _int(v) or v not in domain for v in values)
            or len(set(values)) != len(values)):
        raise PipelineError(f"Invalid or duplicate {label} domain references")


def _categories(identity, g, priorities):
    out = []
    if (g['pick'] or g['axe'] or g['hammer'] or g['fishingPole']
            or any(re.search('Tools|Terraforming|Wiring|Painting', k) and str(identity) in v
                   for k, v in priorities.items())):
        out.append('tools')
    if g['damage'] > 0 and not g['ammo'] and 'tools' not in out:
        out.append('weapons')
    if g['ammo'] > 0: out.append('ammo')
    if any(g[k] >= 0 for k in ('headSlot', 'bodySlot', 'legSlot')):
        out.append('vanity' if g['vanity'] else 'armor')
    if g['accessory']: out.append('vanity' if g['vanity'] else 'accessories')
    if g['dye'] > 0 or g['hairDye'] >= 0: out.append('dyes')
    if g['healLife'] or g['healMana'] or g['food'] or (g['buffType'] > 0 and g['consumable']): out.append('consumables')
    if g['mountType'] >= 0 or g['makeNPC'] > 0 or (g['buffType'] > 0 and not g['consumable'] and g['damage'] <= 0): out.append('pets')
    if g['createTile'] >= 0 or g['createWall'] > 0: out.append('blocks')
    if g['material']: out.append('materials')
    return sum(1 << CATEGORIES.index(c) for c in set(out or ['other']))


def _sort_key(identity, g, priorities):
    def p(name): return priorities.get('SortingPriority' + name, {}).get(str(identity))
    def rank(name): return p(name) if p(name) is not None else 1000000
    if g['damage'] > 0 and not g['consumable'] and not any(g[k] for k in ('ammo', 'pick', 'axe', 'hammer')):
        return [g['damageClass'], -g['rare'], -g['damage']]
    if g['damage'] > 0 and not any(g[k] for k in ('ammo', 'pick', 'axe', 'hammer')): return [4, -g['rare'], -g['damage']]
    if g['ammo'] > 0 and g['damage'] > 0: return [5, -g['rare'], -g['damage']]
    if g['pick'] > 0 and g['axe'] > 0: return [6, g['pick']]
    if g['hammer'] > 0 and g['axe'] > 0: return [7, g['axe']]
    for index, field in enumerate(('pick', 'axe', 'hammer'), 8):
        if g[field]: return [index, g[field]]
    if p('Terraforming') is not None: return [11, rank('Terraforming')]
    if any(g[k] for k in ('fishingPole', 'bait', 'questItem', 'fish', 'crate')) or p('ToolsFishing') is not None:
        return [12, rank('ToolsFishing'), -g['fishingPole'], -g['bait'], -int(g['questItem']), -g['crate'], -g['fish'], -g['rare']]
    for index, key in enumerate(('ToolsGolf', 'ToolsInstruments', 'ToolsKeys', 'ToolsKites'), 13):
        if p(key) is not None: return [index, rank(key), -g['rare']]
    if g['ammo'] and not g['food'] and p('MiscAcorns') is None: return [17, -g['rare'], -g['damage']]
    if p('ToolsMisc') is not None: return [18, rank('ToolsMisc'), -g['rare']]
    if any(g[k] >= 0 for k in ('headSlot', 'bodySlot', 'legSlot')): return [20 if g['vanity'] else 19, -g['rare'], -g['defense']]
    if g['accessory']: return [21, int(g['vanity']), -g['rare']]
    if g['mountType'] >= 0: return [23, g['mountType']]
    if g['buffType'] > 0 and not g['consumable'] and g['damage'] == 0: return [25, g['buffType']]
    if g['dye'] > 0: return [27, g['dye']]
    if g['hairDye'] >= 0: return [28, g['hairDye']]
    if g['healLife'] > 0: return [29, -g['healLife']]
    if identity == 5: return [30]
    if g['healMana'] > 0: return [31, -g['healMana']]
    if g['food']: return [34, -g['rare']]
    if g['buffType'] > 0 and g['consumable']: return [33, rank('PotionsBuffs'), g['buffType']]
    names = ('MiscImportants', 'Painting', 'Wiring', 'Materials', 'MiscGlowingMushroom',
             'Ropes', 'MiscHerbsAndSeeds', 'MiscAcorns', 'MiscGems', 'MiscBossBags')
    for index, key in enumerate(names):
        if p(key) is not None: return [35 + index, rank(key) * (-1 if index in (1, 2, 3, 4, 6, 8) else 1), -g['rare']]
    if identity == 183: return [39]
    if g['makeNPC'] > 0: return [45, g['makeNPC']]
    if p('Extractibles') is not None: return [46, -rank('Extractibles')]
    if g['createTile'] < 0 and g['createWall'] < 1 and g['rare'] != -1: return [47, -g['rare'], -g['value']]
    if g['createTile'] >= 0 or g['createWall'] > 0: return [48]
    return [50 if g['rare'] >= 0 else 51, -g['rare'], -g['value']]


def assemble_item_resources(inputs: dict) -> tuple[dict[str, bytes], dict]:
    """Project complete normalized facts; never fill unknowns or infer coverage.

    ``itemDomain`` is an independently produced explicit selected domain, not
    inferred from records. The exact same domain is required in every join.
    This API does not authenticate the supplied game facts. Its receipt states
    DERIVED_ONLY; it cannot be submitted as schema-2 producer evidence.
    """
    fields = {'gameVersion', 'itemDomain', 'records', 'prefixes', 'pools', 'groups',
              'noAccessoryPrefix', 'priorities', 'sourceHashes'}
    if type(inputs) is not dict or set(inputs) != fields:
        raise PipelineError("Incomplete item assembly inputs")
    if not isinstance(inputs['gameVersion'], str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+){2,3}', inputs['gameVersion']):
        raise PipelineError("Invalid item game version")
    hashes = inputs['sourceHashes']
    if (type(hashes) is not dict or not hashes or len(hashes) > 64
            or any(not isinstance(k, str) or not re.fullmatch(r'[a-z][a-z0-9.-]{0,63}', k)
                   or not isinstance(v, str) or not re.fullmatch(r'[a-f0-9]{64}', v) for k, v in hashes.items())):
        raise PipelineError("Item source hashes are required")
    domain = inputs['itemDomain']
    if (type(domain) is not list or not domain or len(domain) > 65535
            or any(not _int(n, 1) for n in domain) or domain != sorted(set(domain))):
        raise PipelineError("Independent item domain must be sorted unique positive IDs")
    identities = set(domain)
    records = inputs['records']
    if type(records) is not list or len(records) != len(domain):
        raise PipelineError("Final item record domain is incomplete")
    by_id = {}
    persistent_ids = set()
    for record in records:
        if type(record) is not dict or set(record) != {'id', 'name', 'persistentId', 'research', 'gameplay'}:
            raise PipelineError("Invalid final item record")
        identity = record['id']
        if not _int(identity, 1) or identity not in identities or identity in by_id:
            raise PipelineError("Unexpected or duplicate final item ID")
        if any(not _text(record[k]) for k in ('name', 'persistentId')):
            raise PipelineError("Missing final item name/persistent ID")
        if record['persistentId'] in persistent_ids or not _int(record['research']):
            raise PipelineError("Duplicate persistent ID or invalid research value")
        persistent_ids.add(record['persistentId'])
        g = record['gameplay']
        if type(g) is not dict or set(g) != set(ALL_FIELDS):
            raise PipelineError("Missing final gameplay fields; implicit defaults forbidden")
        if any(type(g[k]) is not bool if k in BOOL_FIELDS else not _number(g[k]) for k in ALL_FIELDS):
            raise PipelineError("Invalid final gameplay field type")
        if any(not _int(g[k], -2**31, 2**31 - 1) for k in ALL_FIELDS if k not in BOOL_FIELDS and k != 'knockBack'):
            raise PipelineError("Integral gameplay fields require signed 32-bit integers")
        if any(g[k] < -1 for k in ALL_FIELDS if k.endswith('Slot') or k in ('createTile', 'createWall', 'mountType')):
            raise PipelineError("Invalid gameplay sentinel")
        if (not _int(g['maxStack'], 1) or not _int(g['hairDye'], -1, 255)
                or not _int(g['buffType']) or not _int(g['ammo'])
                or not _int(g['damageClass'], 0, 4)
                or any(not _int(g[k], 0, 1) for k in ('food', 'fish', 'crate'))):
            raise PipelineError("Final item values exceed consumer domain")
        by_id[identity] = record
    prefixes = inputs['prefixes']
    if type(prefixes) is not dict or not prefixes or len(prefixes) > 256 or '0' not in prefixes:
        raise PipelineError("Incomplete prefix registry")
    for key, row in prefixes.items():
        if (not isinstance(key, str) or not re.fullmatch(r'0|[1-9][0-9]{0,2}', key) or not _int(int(key), 0, 255)
                or type(row) is not dict or set(row) != {'name', 'stats'}
                or not _text(row['name'])
                or type(row['stats']) is not dict or len(row['stats']) > 64
                or any(not _text(k) or len(k) > 64 or not _number(v) for k, v in row['stats'].items())):
            raise PipelineError("Invalid prefix definition")
    for name, keys, target in (('pools', POOLS, {int(k) for k in prefixes}), ('groups', GROUPS, identities)):
        value = inputs[name]
        if type(value) is not dict or set(value) != set(keys):
            raise PipelineError(f"Missing {name} registry; no implicit empty set")
        for key in keys: _ids(value[key], target, key)
    _ids(inputs['noAccessoryPrefix'], identities, 'prefix exclusions')
    priorities = inputs['priorities']
    if type(priorities) is not dict or len(priorities) > 64:
        raise PipelineError("Invalid category priority registry")
    for key, rows in priorities.items():
        if not isinstance(key, str) or not re.fullmatch(r'SortingPriority[A-Za-z]{1,64}', key) or type(rows) is not dict or len(rows) > len(domain):
            raise PipelineError("Invalid category priority table")
        for identity, value in rows.items():
            if (not isinstance(identity, str) or not re.fullmatch(r'[1-9][0-9]{0,4}', identity)
                    or int(identity) not in identities or not _int(value, -2**31, 2**31 - 1)):
                raise PipelineError("Invalid category priority reference")
    ordered = [by_id[n] for n in domain]
    catalog = [[r[k] for r in ordered] for k in ('id', 'name', 'persistentId', 'research')]
    catalog.extend([[r['gameplay'][k] for r in ordered] for k in ('maxStack', 'hairDye', 'buffType')])
    # Keep every row rather than silently dropping items via eligibility heuristics.
    rules = {'version': inputs['gameVersion'], **{k: inputs[k] for k in ('prefixes', 'pools', 'groups', 'noAccessoryPrefix')},
             'fields': list(RULE_FIELDS), 'items': {str(r['id']): [r['gameplay'][k] for k in RULE_FIELDS] for r in ordered}}
    keys, known, rows = [], {}, []
    for r in ordered:
        identity, g = r['id'], r['gameplay']
        key = _sort_key(identity, g, priorities)
        fingerprint = tuple(key)
        if fingerprint not in known:
            known[fingerprint] = len(keys)
            keys.append(key)
        ammo = (not g['notAmmo'] and (g['ammo'] > 0 or identity == 530) and g['bait'] <= 0
                and not g['paint'] and not g['paintCoating'] and identity not in (353, 849, 169, 75, 23, 408, 370, 1246))
        rows.append([identity, _categories(identity, g, priorities), known[fingerprint], int(ammo), g['ammo']])
    objects = {role: canonical_json(value) for role, value in (
        ('items.catalog', catalog), ('items.rules', rules), ('items.categories', {'keys': keys, 'items': rows}))}
    receipt = {'schemaVersion': 1, 'kind': 'consumer-derivation', 'status': 'DERIVED_ONLY',
               'policy': POLICY, 'policyReferenceSha256': CATEGORY_REFERENCE_SHA256,
               'inputSha256': sha256(canonical_json(inputs)), 'sourceHashes': dict(hashes),
               'domainSha256': sha256(canonical_json(domain)), 'rows': len(domain),
               'objects': {role: {'sha256': sha256(raw), 'bytes': len(raw)} for role, raw in objects.items()},
               'sourceSemanticsVerified': False, 'publicationApproved': False,
               'missingProducerProof': ['final Item.SetDefaults fields', 'prefix registry/effects',
                                        'selected item domain', 'localized names/persistent IDs/research', 'category priority registries']}
    return objects, receipt
