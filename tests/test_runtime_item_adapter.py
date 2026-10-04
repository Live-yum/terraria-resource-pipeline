import copy
import unittest
from resource_pipeline.runtime_item_adapter import adapt_observed_items, ITEM_SETS, DERIVED_FIELDS
from resource_pipeline.item_assembler import GROUPS
from resource_pipeline.security import PipelineError
from test_item_assembler import original_inputs


def observed():
    source = original_inputs(); count = max(source['itemDomain']) + 1
    rows = copy.deepcopy(source['records'])
    for row in rows:
        for key in DERIVED_FIELDS: row['gameplay'].pop(key)
    return {k: source[k] for k in ('gameVersion', 'itemDomain', 'prefixes', 'pools', 'sourceHashes')} | {
        'itemCount': count, 'records': rows, 'groups': {k: [False] * count for k in GROUPS},
        'itemSets': {k: [k == 'CanGetPrefixes'] * count for k in ITEM_SETS},
        'priorityDomain': ['SortingPriorityToolsMisc'],
        'priorities': {'SortingPriorityToolsMisc': {'default': -1, 'overrides': [[1, -1], [2, 0]],
                                                 'values': [-1, -1, 0, -1][:count]}}}


class RuntimeItemAdapterTests(unittest.TestCase):
    def test_exact_policy_precedence_and_sparse_override_membership(self):
        value = observed(); value['groups']['Magic'][1] = True
        value['groups']['BoomerangsChakrams'][1] = True
        value['itemSets']['IsFood'][1] = True; value['itemSets']['CanGetPrefixes'][2] = False
        result, receipt = adapt_observed_items(value)
        self.assertEqual(result['records'][0]['gameplay']['damageClass'], 0)
        self.assertEqual(result['records'][0]['gameplay']['food'], 1)
        self.assertEqual(result['priorities']['SortingPriorityToolsMisc'], {'1': -1, '2': 0})
        self.assertEqual(result['noAccessoryPrefix'], [2])
        self.assertFalse(receipt['publishable']); self.assertFalse(receipt['complete'])
        self.assertFalse(receipt['runtimeInitializationVerified'])

    def test_all_classes_and_derived_flags_are_observed_not_defaulted(self):
        cases = [(None, 4), ('SwordsHammersAxesPicks', 0), ('SpearsMacesChainsawsDrillsPunchCannon', 0),
                 ('BoomerangsChakrams', 0), ('ItemsThatCanHaveLegendary2', 0),
                 ('GunsBows', 1), ('Magic', 2), ('Summon', 3)]
        for group, expected in cases:
            value = observed()
            if group: value['groups'][group][1] = True
            for key in ('IsFood', 'IsBasicFish', 'IsFishingCrate'): value['itemSets'][key][1] = True
            before = copy.deepcopy(value)
            result, receipt = adapt_observed_items(value)
            self.assertEqual(value, before)
            self.assertEqual(result['records'][0]['gameplay']['damageClass'], expected)
            self.assertEqual([result['records'][0]['gameplay'][k] for k in ('food', 'fish', 'crate')], [1, 1, 1])
            self.assertEqual((result, receipt), adapt_observed_items(value))

    def test_missing_or_conflicting_facts_fail_closed(self):
        for edit in (lambda v: v['itemSets'].pop('IsFood'),
                     lambda v: v['groups']['Magic'].pop(),
                     lambda v: v['groups']['Magic'].__setitem__(1, 1),
                     lambda v: v['priorities']['SortingPriorityToolsMisc']['values'].__setitem__(1, 0),
                     lambda v: v['records'][0]['gameplay'].pop('damage'),
                     lambda v: v['records'][0]['gameplay'].update(damageClass=0),
                     lambda v: v.update(priorityDomain=[])):
            value = observed(); edit(value)
            with self.assertRaises(PipelineError): adapt_observed_items(value)

    def test_priority_duplicate_last_write_keeps_explicit_sentinel(self):
        value = observed(); table = value['priorities']['SortingPriorityToolsMisc']
        table['overrides'] = [[1, 7], [1, -1], [2, 0]]
        self.assertEqual(adapt_observed_items(value)[0]['priorities']['SortingPriorityToolsMisc']['1'], -1)
