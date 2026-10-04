"""Bounded observed Item/registry values -> existing item consumer inputs.

This is a data adapter, not a CLR runner or authenticated observation. It keeps
application classification policy distinct from runtime field observations.
"""
import re

from .item_assembler import ALL_FIELDS, GROUPS, assemble_item_resources
from .security import PipelineError, canonical_json, sha256

POLICY_SOURCE_SHA256 = '283c5b9f6b68094a99f9f5154425592498c3695061aa9471cbe8ad6a1ebf1baf'
DERIVED_FIELDS = ('damageClass', 'food', 'fish', 'crate')
OBSERVED_FIELDS = tuple(k for k in ALL_FIELDS if k not in DERIVED_FIELDS)
CLASS_ORDER = (('SwordsHammersAxesPicks', 0), ('SpearsMacesChainsawsDrillsPunchCannon', 0),
               ('BoomerangsChakrams', 0), ('ItemsThatCanHaveLegendary2', 0),
               ('GunsBows', 1), ('Magic', 2), ('Summon', 3))
ITEM_SETS = ('IsFood', 'IsBasicFish', 'IsFishingCrate', 'CanGetPrefixes')


def _need(ok, message):
    if not ok: raise PipelineError(message)


def _array(value, count, kind, label):
    _need(type(value) is list and len(value) == count, 'Incomplete observed array: ' + label)
    _need(all(type(x) is kind and (kind is bool or -2**31 <= x < 2**31) for x in value),
          'Invalid observed array values: ' + label)
    return value


def adapt_observed_items(observation):
    """Return normalized inputs and receipt; never grant source/publication trust.

    Each array includes ID zero and must cover the independently supplied Count.
    Selected domain is explicit. Sparse priority membership comes from override
    provenance, including explicit -1 and zero; final values must agree with it.
    """
    keys = ('gameVersion', 'itemCount', 'itemDomain', 'records', 'prefixes', 'pools',
            'groups', 'itemSets', 'priorityDomain', 'priorities', 'sourceHashes')
    _need(type(observation) is dict and set(observation) == set(keys), 'Invalid Item observation schema')
    count = observation['itemCount']; domain = observation['itemDomain']
    _need(type(count) is int and 1 < count <= 65536, 'Invalid observed Item Count')
    _need(type(domain) is list and 0 < len(domain) < count
          and all(type(n) is int and 0 < n < count for n in domain)
          and domain == sorted(set(domain)), 'Invalid independent selected Item domain')
    selected_ids = set(domain)
    groups, sets = observation['groups'], observation['itemSets']
    for table, names in ((groups, GROUPS), (sets, ITEM_SETS)):
        _need(type(table) is dict and set(table) == set(names), 'Incomplete observed boolean registry')
        for name in names: _array(table[name], count, bool, name)
    priorities = observation['priorities']; priority_domain = observation['priorityDomain']
    _need(type(priority_domain) is list and len(priority_domain) <= 64
          and all(type(n) is str and re.fullmatch(r'SortingPriority[A-Za-z]{1,64}', n) for n in priority_domain)
          and priority_domain == sorted(set(priority_domain)), 'Invalid independent priority domain')
    _need(type(priorities) is dict and set(priorities) == set(priority_domain), 'Incomplete observed priority registry')
    sparse = {}
    for name in priority_domain:
        table = priorities[name]
        _need(type(table) is dict and set(table) == {'default', 'overrides', 'values'}
              and type(table['default']) is int and table['default'] == -1,
              'Unsupported priority default or schema')
        actual = _array(table['values'], count, int, name)
        pairs = table['overrides']
        _need(type(pairs) is list and len(pairs) <= count * 2, 'Priority override budget exceeded')
        expected, explicit = [-1] * count, {}
        for pair in pairs:
            _need(type(pair) is list and len(pair) == 2 and type(pair[0]) is int
                  and 0 <= pair[0] < count and type(pair[1]) is int and -2**31 <= pair[1] < 2**31,
                  'Invalid explicit priority override')
            expected[pair[0]] = pair[1]; explicit[pair[0]] = pair[1]
        _need(actual == expected, 'Priority values disagree with override provenance')
        sparse[name] = {str(n): explicit[n] for n in domain if n in explicit}
    records = observation['records']
    _need(type(records) is list and len(records) == len(domain), 'Incomplete observed item records')
    normalized = []
    for row in records:
        _need(type(row) is dict and set(row) == {'id', 'name', 'persistentId', 'research', 'gameplay'},
              'Invalid observed item record')
        identity, fields = row['id'], row['gameplay']
        _need(type(identity) is int and identity in selected_ids, 'Observed item outside selected domain')
        _need(type(fields) is dict and set(fields) == set(OBSERVED_FIELDS), 'Incomplete observed gameplay fields')
        derived = {'damageClass': next((c for name, c in CLASS_ORDER if groups[name][identity]), 4)}
        derived.update({k: int(sets[s][identity]) for k, s in zip(('food', 'fish', 'crate'), ITEM_SETS)})
        normalized.append({**row, 'gameplay': {**fields, **derived}})
    result = {k: observation[k] for k in ('gameVersion', 'itemDomain', 'prefixes', 'pools', 'sourceHashes')}
    result.update(records=normalized, groups={name: [n for n in domain if groups[name][n]] for name in GROUPS},
                  noAccessoryPrefix=[n for n in domain if not sets['CanGetPrefixes'][n]], priorities=sparse)
    # Validate the complete joined contract before hashing or returning anything.
    assemble_item_resources(result)
    receipt = {'schemaVersion': 1, 'status': 'OBSERVATION_ADAPTED',
               'policySourceSha256': POLICY_SOURCE_SHA256,
               'observationSha256': sha256(canonical_json(observation)),
               'normalizedSha256': sha256(canonical_json(result)),
               'sourceSemanticsVerified': False, 'runtimeInitializationVerified': False,
               'complete': False, 'publishable': False}
    return result, receipt
