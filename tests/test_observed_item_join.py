import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from observed_item_join_fixture import original_join
from resource_pipeline.item_assembler import assemble_item_resources, GROUPS, POOLS
from resource_pipeline.observed_item_join import (
    _join_observed_item_facts, _named_domain, _app_policy, _bounded_json, _extract_static,
    adapt_observed_item_facts, assemble_observed_item_resources, APP_SOURCE_PINS,
    FALSE_GATES, COEFFICIENT_DEFAULTS, SOURCE_SHA256)
from resource_pipeline.security import PipelineError, canonical_json, sha256


class ObservedItemJoinTests(unittest.TestCase):
    def join(self, value=None, static=None, policy=None):
        original = original_join()
        return _join_observed_item_facts(value if value is not None else original[0],
                                         static if static is not None else original[1],
                                         policy if policy is not None else original[2])

    def test_complete_invented_data_join_without_trust_promotion(self):
        observation, static, policy = original_join(); before = copy.deepcopy((observation, static, policy))
        inputs, receipt = self.join(observation, static, policy)
        objects, assembly = assemble_item_resources(inputs)
        self.assertEqual(set(objects), {'items.catalog', 'items.rules', 'items.categories'})
        catalog, rules = json.loads(objects['items.catalog']), json.loads(objects['items.rules'])
        self.assertEqual(catalog[0], [1, 2, 3]); self.assertEqual(catalog[3], [1, 0, 3])
        self.assertEqual(set(rules['items']), {'1', '2', '3'})
        self.assertEqual(inputs['records'][1]['gameplay']['damageClass'], 2)
        self.assertEqual(inputs['records'][0]['gameplay']['food'], 1)
        self.assertEqual(inputs['records'][2]['gameplay']['fish'], 1)
        self.assertEqual(inputs['priorities']['SortingPriorityToolsMisc'], {'1': -1, '3': 0})
        self.assertEqual(inputs['noAccessoryPrefix'], [3])
        self.assertEqual(receipt['researchRepresentation']['absenceRows'], [{'id': 2, 'present': False, 'observedOutCount': 0}])
        self.assertEqual(receipt['sourceProofs']['original']['preconditions'], ['original precondition, not discharged'])
        self.assertEqual(receipt['normalizedSha256'], sha256(canonical_json(inputs)))
        for key in ('sourceSemanticsVerified', 'runtimeInitializationVerified', 'complete', 'publishable',
                    'observationAuthenticated', 'runtimeSnapshotUsable', 'executedInput'):
            self.assertIs(receipt[key], False)
        self.assertTrue(receipt['remainingGaps'])
        self.assertEqual((observation, static, policy), before)
        self.assertEqual(self.join(observation, static, policy), (inputs, receipt))

    def test_prefix_key_scale_and_neutral_omission_policy(self):
        inputs, receipt = self.join()
        self.assertEqual(inputs['prefixes']['0'], {'name': 'Original no prefix', 'stats': {}})
        self.assertEqual(inputs['prefixes']['1']['stats'], {'dmg': 1.25})
        self.assertEqual(inputs['prefixes']['2']['stats'], {'crt': 7, 'arpen': 3})
        self.assertEqual(inputs['prefixes']['3']['stats'], {'moveBonus': 6})
        self.assertIs(receipt['prefixProjection']['zeroName']['gameObserved'], False)
        for zero in ('Observed original zero label', None):
            observation, static, policy = original_join(); observation['prefixNames'][0]['name'] = zero
            inputs, receipt = self.join(observation, static, policy)
            self.assertEqual(receipt['prefixProjection']['zeroName']['observedValue'], zero)
            self.assertIs(receipt['prefixProjection']['zeroName']['gameObserved'], bool(zero))
            self.assertEqual(inputs['prefixes']['0']['name'], zero or 'Original no prefix')

    def test_rows_and_prefix_names_may_be_reordered_but_never_dropped(self):
        observation, static, policy = original_join()
        base, _ = self.join(observation, static, policy)
        observation['records'].reverse(); observation['prefixNames'].reverse()
        reordered, _ = self.join(observation, static, policy)
        self.assertEqual(assemble_item_resources(reordered)[0], assemble_item_resources(base)[0])
        edits = [lambda o: o['records'].pop(), lambda o: o['records'].append(copy.deepcopy(o['records'][0])),
                 lambda o: o['records'][0].update(requestedId=2), lambda o: o['records'][0].update(requestedId=0),
                 lambda o: o['records'][0].update(resolvedType=2), lambda o: o['records'][0].update(variantIsNull=False),
                 lambda o: o['records'][0].update(requestedId=True)]
        for edit in edits:
            o, s, p = original_join(); edit(o)
            with self.subTest(edit=edit), self.assertRaises(PipelineError): self.join(o, s, p)
        o, s, p = original_join(); s['itemDomain'] = [1, 3]
        with self.assertRaisesRegex(PipelineError, 'no row may be dropped'): self.join(o, s, p)
        # A selected named subset can join when the observations themselves have
        # exactly that subset. Count 4 still must not manufacture Item 2.
        o['records'] = [row for row in o['records'] if row['requestedId'] != 2]
        inputs, receipt = self.join(o, s, p)
        self.assertEqual(inputs['itemDomain'], [1, 3])
        self.assertEqual(len(inputs['records']), 2)

    def test_all_observed_arrays_count_and_source_values_are_bound(self):
        edits = [lambda o: o.update(itemCount=5), lambda o: o.update(prefixCount=5),
                 lambda o: o['groups'].pop(GROUPS[0]), lambda o: o['groups'][GROUPS[0]].pop(),
                 lambda o: o['groups'][GROUPS[0]].__setitem__(0, True),
                 lambda o: o['groups'][GROUPS[0]].__setitem__(1, 0),
                 lambda o: o['itemSets']['CanGetPrefixes'].__setitem__(0, False),
                 lambda o: o['itemSets']['IsBasicFish'].__setitem__(2, True),
                 lambda o: o['itemSets']['IsFood'].append(False),
                 lambda o: o['pools'][POOLS[0]].reverse(), lambda o: o['pools'][POOLS[0]].append(0),
                 lambda o: o['pools'][POOLS[0]].__setitem__(0, True),
                 lambda o: o['priorities']['SortingPriorityToolsMisc'].__setitem__(1, 0),
                 lambda o: o['priorities']['SortingPriorityToolsMisc'].__setitem__(0, 0),
                 lambda o: o['priorities'].update(SortingPriorityForeign=[-1]*4),
                 lambda o: o['priorities']['SortingPriorityToolsMisc'].pop()]
        for edit in edits:
            o, s, p = original_join(); edit(o)
            with self.subTest(edit=edit), self.assertRaises(PipelineError): self.join(o, s, p)

    def test_material_registry_is_diagnostic_and_does_not_replace_final_material(self):
        observation, static, policy = original_join()
        inputs, receipt = self.join(observation, static, policy)
        diagnostic = receipt['registryComparisons']['itemSets']['IsAMaterial']
        self.assertEqual(diagnostic['values'], [False, True, False, False])
        self.assertEqual(diagnostic['sha256'], sha256(canonical_json(diagnostic['values'])))
        self.assertIs(diagnostic['finalItemMaterialEqualityClaimed'], False)
        self.assertIs(inputs['records'][0]['gameplay']['material'], False)
        observation['itemSets']['IsAMaterial'][1] = False
        changed, new_receipt = self.join(observation, static, policy)
        self.assertEqual(assemble_item_resources(inputs)[0], assemble_item_resources(changed)[0])
        self.assertNotEqual(receipt['observationSha256'], new_receipt['observationSha256'])
        for edit in (lambda o: o['itemSets'].pop('IsAMaterial'),
                     lambda o: o['itemSets']['IsAMaterial'].pop(),
                     lambda o: o['itemSets']['IsAMaterial'].__setitem__(0, 0),
                     lambda o: o['itemSets'].update(OtherDiagnostic=[False]*4)):
            o, s, p = original_join(); edit(o)
            with self.subTest(edit=edit), self.assertRaises(PipelineError): self.join(o, s, p)

    def test_names_research_persistent_ids_and_gameplay_are_not_guessed(self):
        edits = [lambda o: o['records'][0].update(name=''), lambda o: o['records'][0].update(name=None),
                 lambda o: o['records'][0].update(persistentIdPresent=False),
                 lambda o: o['records'][0].update(persistentId=None),
                 lambda o: o['records'][0].update(persistentId=o['records'][1]['persistentId']),
                 lambda o: o['records'][1]['research'].update(count=9),
                 lambda o: o['records'][1]['research'].update(present=0),
                 lambda o: o['records'][0]['research'].pop('present'),
                 lambda o: o['records'][0]['gameplay'].pop('mana'),
                 lambda o: o['records'][0]['gameplay'].update(damageClass=2),
                 lambda o: o['records'][0]['gameplay'].update(accessory=1),
                 lambda o: o['records'][0]['gameplay'].update(knockBack=.1),
                 lambda o: o['records'][0]['gameplay'].update(knockBack=float('nan')),
                 lambda o: o['records'][0]['gameplay'].update(maxStack=0),
                 lambda o: o['records'][0]['gameplay'].update(useAnimation=2**31),
                 lambda o: o['prefixNames'].pop(), lambda o: o['prefixNames'][1].update(name=None),
                 lambda o: o['prefixNames'][1].update(name=''), lambda o: o['prefixNames'][1].update(id=0),
                 lambda o: o['prefixNames'][1].update(id=99)]
        for edit in edits:
            o, s, p = original_join(); edit(o)
            with self.subTest(edit=edit), self.assertRaises(PipelineError): self.join(o, s, p)
        o, s, p = original_join(); s['sourceBindings']['itemFields']['dye']['signatureHex'] = '0605'
        o['records'][0]['gameplay']['dye'] = 256
        with self.assertRaisesRegex(PipelineError, 'source primitive range'): self.join(o, s, p)

    def test_context_and_observation_trust_claims_fail_closed(self):
        edits = [lambda o: o.update(sourceSha256='0'*64), lambda o: o.update(culture='en-US'),
                 lambda o: o.update(gameVersion='0.0.1'), lambda o: o.update(status='COMPLETE'),
                 lambda o: o.update(schemaVersion=True), lambda o: o.update(randomSeed=1),
                 lambda o: o['context'].update(gameMode=1), lambda o: o['context'].update(dedServ=False),
                 lambda o: o['context'].update(difficultyOverride=0),
                 lambda o: o['worldFlags'].update(drunkWorld=True), lambda o: o.update(arbitrarySourceFacts={})]
        edits += [lambda o, k=k: o.update({k: True}) for k in FALSE_GATES]
        for edit in edits:
            o, s, p = original_join(); edit(o)
            with self.subTest(edit=edit), self.assertRaises(PipelineError): self.join(o, s, p)

    def test_source_coefficient_and_effect_gaps_never_filled(self):
        for edit in (lambda s: s['coefficients'].pop(), lambda s: s['coefficients'][0]['coefficients'].pop('dmg'),
                     lambda s: s['coefficients'][1].update(prefixId=0),
                     lambda s: s['coefficients'][0]['coefficients'].update(dmg=1.25),
                     lambda s: s['accessories'][0].update(prefixId=0),
                     lambda s: s['accessories'][0]['consumerStats'].update(unknown=3)):
            o, s, p = original_join(); edit(s)
            with self.subTest(edit=edit), self.assertRaises(PipelineError): self.join(o, s, p)

    def test_named_domain_is_independent_of_count_and_retains_alias_evidence(self):
        values = {'Zero': 0, 'Earlier': -3, 'First': 1, 'Alias': 1, 'Third': 3, 'Count': 4}
        evidence = {'ItemID': {name: {'constantToken': f'original:{name}'} for name in values}}
        domain, receipt = _named_domain({'ItemID': values}, evidence, [], 'ItemID', 4)
        self.assertEqual(domain, [1, 3]); self.assertEqual(len(receipt['namedConstantRows']), 3)
        self.assertEqual(len(receipt['excludedConstantRows']), 3)
        self.assertIs(receipt['allSelectableGameItemsProven'], False)
        values['TooLarge'] = 4; evidence['ItemID']['TooLarge'] = {}
        with self.assertRaisesRegex(PipelineError, 'outside independent Count'):
            _named_domain({'ItemID': values}, evidence, [], 'ItemID', 4)

    def test_budget_cycle_unicode_exotic_objects_and_cancellation(self):
        circular = {}; circular['x'] = circular
        for value in (circular, {'x': '\ud800'}, {'x': float('inf')}, {'x': 2**100}, {'x': object()}, {0: 'bad'}):
            with self.subTest(value=type(value)), self.assertRaises(PipelineError): _bounded_json(value, lambda: None)
        nested = []
        for _ in range(26): nested = [nested]
        with self.assertRaises(PipelineError): _bounded_json(nested, lambda: None)
        def stop(): raise RuntimeError('original cancellation')
        with self.assertRaisesRegex(RuntimeError, 'original cancellation'):
            adapt_observed_item_facts({}, pe_bytes=b'', app_sources={}, checkpoint=stop)

    def test_public_source_and_app_pins_are_closed(self):
        with self.assertRaisesRegex(PipelineError, 'unsupported PE source hash'): _extract_static(b'Original PE', lambda: None)
        for sources in ({}, {name: b'Original wrong source' for name in APP_SOURCE_PINS}):
            with self.assertRaises(PipelineError): _app_policy(sources)
        with patch('resource_pipeline.observed_item_join._extract_static') as extract:
            with self.assertRaises(PipelineError): adapt_observed_item_facts(original_join()[0], pe_bytes={}, app_sources={})
            extract.assert_not_called()

    def test_public_assembly_seam_does_not_promote_internal_receipt(self):
        inputs, receipt = self.join()
        with patch('resource_pipeline.observed_item_join.adapt_observed_item_facts', return_value=(inputs, receipt)):
            objects, result = assemble_observed_item_resources({}, pe_bytes=b'original', app_sources={})
        self.assertEqual(result['objects'], result['assemblyReceipt']['objects'])
        self.assertEqual(set(objects), {'items.catalog', 'items.rules', 'items.categories'})
        self.assertIs(result['publishable'], False)

    @unittest.skipUnless(os.environ.get('TERRARIA_OBSERVED_ITEM_PE') and os.environ.get('TERRARIA_CONSUMER_ROOT'),
                         'optional private data-only PE and current application source checks')
    def test_private_pinned_source_facts_and_policy_only(self):
        pe = Path(os.environ['TERRARIA_OBSERVED_ITEM_PE'])
        app = Path(os.environ['TERRARIA_CONSUMER_ROOT'])
        raw = pe.read_bytes(); self.assertEqual(sha256(raw), SOURCE_SHA256)
        static = _extract_static(raw, lambda: None)
        self.assertEqual(len(static['itemDomain']), 6195); self.assertEqual(static['itemCount'], 6196)
        self.assertEqual(len(static['priorityDomain']), 20); self.assertEqual(static['prefixCount'], 98)
        self.assertEqual(len(static['coefficients']), 98); self.assertEqual(len(static['accessories']), 19)
        policy = _app_policy({name: (app/name).read_bytes() for name in APP_SOURCE_PINS})
        self.assertEqual(policy['zeroCaption'], '无前缀')
        # This is intentionally not an authentic runtime observation, so do not
        # manufacture one or claim a live end-to-end acceptance run here.


if __name__ == '__main__': unittest.main()
