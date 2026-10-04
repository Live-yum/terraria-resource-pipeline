"""Original invented join-only facts; no Terraria payload or accepted PE profile."""
import copy
from resource_pipeline.item_assembler import ALL_FIELDS, BOOL_FIELDS, GROUPS, POOLS
from resource_pipeline.runtime_item_adapter import DERIVED_FIELDS, ITEM_SETS, OBSERVED_FIELDS
from resource_pipeline.observed_item_join import (SOURCE_SHA256, GAME_VERSION, WORLD_FLAGS, FALSE_GATES,
                                                   COEFFICIENT_DEFAULTS)


def original_join():
    count, prefix_count = 4, 4
    domain = [1, 2, 3]
    groups = {name: [] for name in GROUPS}; groups['Magic'] = [2]
    sets = {name: [name == 'CanGetPrefixes'] * count for name in ITEM_SETS}
    sets['IsFood'][1] = True; sets['IsBasicFish'][3] = True; sets['CanGetPrefixes'][3] = False
    coefficients = [{'prefixId': n, 'coefficients': dict(COEFFICIENT_DEFAULTS)} for n in range(prefix_count)]
    coefficients[1]['coefficients']['dmg'] = 1.25
    coefficients[2]['coefficients']['crt'] = 7
    coefficients[2]['coefficients']['arpen'] = 3
    static = {'gameVersion': GAME_VERSION, 'itemCount': count, 'prefixCount': prefix_count,
              'itemDomain': domain, 'prefixDomain': list(range(prefix_count)), 'groups': groups,
              'pools': {name: [2, 1, 3] for name in POOLS}, 'itemSets': sets,
              'priorityDomain': ['SortingPriorityToolsMisc'],
              'priorities': {'SortingPriorityToolsMisc': {'default': -1, 'overrides': [[1, 5], [1, -1], [3, 0]]}},
              'coefficients': coefficients,
              'accessories': [{'prefixId': 3, 'consumerStats': {'moveBonus': 6}}],
              'proofs': {'original': {'preconditions': ['original precondition, not discharged']}},
              'itemSetProof': {'wholeInitializerProven': False},
              'itemDomainProof': {'selectedDomain': domain, 'allSelectableGameItemsProven': False},
              'prefixDomainProof': {'selectedDomain': [1, 2, 3]},
              'sourceBindings': {'materialRegistry': {'fieldToken': 'original-material-field'}, 'itemFields': {key: {'signatureHex': '0602' if key in BOOL_FIELDS else
                                '060c' if key == 'knockBack' else '0608'} for key in OBSERVED_FIELDS}}}
    rows = []
    for n in domain:
        fields = {key: False if key in BOOL_FIELDS else 0 for key in ALL_FIELDS if key not in DERIVED_FIELDS}
        for name in ('headSlot', 'bodySlot', 'legSlot', 'hairDye', 'mountType', 'createTile'): fields[name] = -1
        fields['maxStack'] = 1
        rows.append({'requestedId': n, 'resolvedType': n, 'name': f'Invented item {n}',
                     'persistentIdPresent': True, 'persistentId': f'Invented_{n}', 'variantIsNull': True,
                     'research': {'present': n != 2, 'count': n if n != 2 else 0}, 'gameplay': fields})
    observation = {'schemaVersion': 1, 'kind': 'pinned-item-player-data-observation-fragment', 'status': 'PARTIAL',
        'gameVersion': GAME_VERSION, 'sourceSha256': SOURCE_SHA256, 'culture': 'zh-Hans',
        'itemCount': count, 'prefixCount': prefix_count, 'records': rows, 'playerObservation': {'original': True},
        'groups': {name: [n in values for n in range(count)] for name, values in groups.items()},
        'itemSets': {**copy.deepcopy(sets), 'IsAMaterial': [False, True, False, False]}, 'priorities': {'SortingPriorityToolsMisc': [-1, -1, -1, 0]},
        'pools': copy.deepcopy(static['pools']),
        'prefixNames': [{'id': n, 'name': f'Invented prefix {n}' if n else ''} for n in range(prefix_count)],
        'context': {'gameMode': 0, 'difficulty': 0, 'expertMode': False, 'masterMode': False, 'mechdusa': False,
                    'difficultyOverride': None, 'dedServ': True, 'netMode': 2, 'localPlayerIndex': 0,
                    'activeWorldFileDataPresent': False},
        'worldFlags': {key: False for key in WORLD_FLAGS}, 'randomSeed': 0,
        'requestedExecutionMode': 'isolated-windows-x86-clr4-dedServ',
        'initializersReturned': ['original-not-authenticated'], 'missingJoins': ['initialization-acceptance'],
        'managedAssemblyHashes': {'Original': '0' * 64}, 'nativeModuleHashes': {'Original': '1' * 64},
        'collectorExecutableSha256': '2' * 64, 'osVersion': 'Original fixture OS', 'clrVersion': 'Original fixture CLR',
        **{gate: False for gate in FALSE_GATES}}
    policy = {'zeroCaption': 'Original no prefix', 'receipt': {'kind': 'original-app-policy-fixture'}}
    return observation, static, policy
