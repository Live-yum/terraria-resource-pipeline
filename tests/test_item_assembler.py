import copy
import json
import os
from pathlib import Path
import random
import subprocess
import unittest

from resource_pipeline.item_assembler import (ALL_FIELDS, BOOL_FIELDS, CATEGORY_FIELDS,
    GROUPS, POOLS, assemble_item_resources)
from resource_pipeline.security import PipelineError, sha256


def original_inputs(count=3):
    rows = []
    for identity in range(1, count + 1):
        g = {k: False if k in BOOL_FIELDS else 0 for k in ALL_FIELDS}
        for k in ('hairDye', 'headSlot', 'bodySlot', 'legSlot', 'mountType', 'createTile'):
            g[k] = -1
        g['maxStack'] = 1
        rows.append({'id': identity, 'name': f'Original {identity}', 'persistentId': f'Original_{identity}',
                     'research': identity, 'gameplay': g})
    return {'gameVersion': '0.0.1', 'itemDomain': list(range(1, count + 1)), 'records': rows,
            'prefixes': {'0': {'name': 'Original no prefix', 'stats': {}},
                         '1': {'name': 'Original test prefix', 'stats': {'damage': 1.1}}},
            'pools': {k: [1] for k in POOLS}, 'groups': {k: [] for k in GROUPS},
            'noAccessoryPrefix': [], 'priorities': {}, 'sourceHashes': {'original-fixture': sha256(b'original')}}


class ItemAssemblerTests(unittest.TestCase):
    def test_columns_rules_categories_and_exact_receipt(self):
        inputs = original_inputs()
        inputs['records'].reverse()
        objects, receipt = assemble_item_resources(inputs)
        catalog = json.loads(objects['items.catalog'])
        self.assertEqual(catalog[0], [1, 2, 3])
        self.assertEqual(catalog[1], ['Original 1', 'Original 2', 'Original 3'])
        self.assertEqual(catalog[3], [1, 2, 3])
        self.assertEqual(len(catalog), 7)
        self.assertEqual(len(json.loads(objects['items.rules'])['items']), 3)
        self.assertEqual(json.loads(objects['items.categories']),
                         {'keys': [[47, 0, 0]], 'items': [[1, 4096, 0, 0, 0], [2, 4096, 0, 0, 0], [3, 4096, 0, 0, 0]]})
        self.assertEqual(receipt['status'], 'DERIVED_ONLY')
        self.assertFalse(receipt['sourceSemanticsVerified'])
        self.assertFalse(receipt['publicationApproved'])
        for role, raw in objects.items():
            self.assertEqual(receipt['objects'][role], {'sha256': sha256(raw), 'bytes': len(raw)})
        before = copy.deepcopy(inputs)
        self.assertEqual(assemble_item_resources(inputs), (objects, receipt))
        self.assertEqual(inputs, before)

    def test_fail_closed_missing_wrong_duplicate_fields(self):
        edits = [
            lambda x: x['records'].pop(),
            lambda x: x['records'][0]['gameplay'].pop('maxStack'),
            lambda x: x['records'][0].update(id=2),
            lambda x: x['records'][0].update(persistentId='Original_2'),
            lambda x: x['itemDomain'].append(4),
            lambda x: x['itemDomain'].reverse(),
            lambda x: x['records'][0]['gameplay'].update(accessory=1),
            lambda x: x['records'][0]['gameplay'].update(mana=float('nan')),
            lambda x: x['records'][0]['gameplay'].update(damage=10**1000),
            lambda x: x['records'][0]['gameplay'].update(damage=1.5),
            lambda x: x['records'][0]['gameplay'].update(createTile=-999),
            lambda x: x['records'][0].update(name='\ud800'),
            lambda x: x['records'][0]['gameplay'].update(maxStack=0),
            lambda x: x['records'][0]['gameplay'].update(hairDye=-2),
            lambda x: x['records'][0]['gameplay'].update(food=True),
            lambda x: x['pools'].pop(POOLS[0]),
            lambda x: x['pools'][POOLS[0]].append(2),
            lambda x: x['groups'][GROUPS[0]].extend([1, 1]),
            lambda x: x['prefixes'].pop('0'),
            lambda x: x['prefixes'].update({'01': {'name': 'bad', 'stats': {}}}),
            lambda x: x['prefixes'].update({'1١': {'name': 'bad', 'stats': {}}}),
            lambda x: x['prefixes']['1']['stats'].update({'\ud800': 1}),
            lambda x: x.update(gameVersion='١.٢.٣'),
            lambda x: x['priorities'].update(SortingPriorityToolsMisc={'1١': 1}),
            lambda x: x['noAccessoryPrefix'].append(999),
            lambda x: x['priorities'].update(SortingPriorityToolsMisc={'4': 1}),
            lambda x: x['sourceHashes'].clear(),
            lambda x: x.update(complete=True),
        ]
        for edit in edits:
            with self.subTest(edit=edit):
                inputs = original_inputs()
                edit(inputs)
                with self.assertRaises(PipelineError): assemble_item_resources(inputs)

    def test_source_and_policy_changes_change_receipt(self):
        first = original_inputs()
        second = copy.deepcopy(first)
        second['sourceHashes']['original-fixture'] = sha256(b'another original')
        a, ra = assemble_item_resources(first)
        b, rb = assemble_item_resources(second)
        self.assertEqual(a, b)
        self.assertNotEqual(ra['inputSha256'], rb['inputSha256'])

    @unittest.skipUnless(os.environ.get('TERRARIA_CONSUMER_ROOT'), 'optional actual app differential oracle')
    def test_actual_app_contract_and_category_oracle(self):
        root = Path(os.environ['TERRARIA_CONSUMER_ROOT']).resolve()
        inputs = original_inputs(600)
        randomizer = random.Random(93478)
        for record in inputs['records']:
            g = record['gameplay']
            for k in ALL_FIELDS:
                if k in BOOL_FIELDS: g[k] = randomizer.choice([False, True])
                else: g[k] = randomizer.choice([0, 0, 0, 1, 4, 9])
            for k in ('headSlot', 'bodySlot', 'legSlot', 'hairDye', 'mountType', 'createTile'):
                g[k] = randomizer.choice([-1, -1, 0, 1])
            for k in ('food', 'fish', 'crate'): g[k] = randomizer.randrange(2)
            g['maxStack'] = 999
            g['damageClass'] = randomizer.randrange(5)
        priorities = ['Terraforming', 'ToolsFishing', 'ToolsGolf', 'ToolsInstruments', 'ToolsKeys', 'ToolsKites',
                      'ToolsMisc', 'PotionsBuffs', 'MiscImportants', 'Painting', 'Wiring', 'Materials',
                      'MiscGlowingMushroom', 'Ropes', 'MiscHerbsAndSeeds', 'MiscAcorns', 'MiscGems', 'MiscBossBags', 'Extractibles']
        for name in priorities:
            inputs['priorities']['SortingPriority' + name] = {str(n): randomizer.randint(-10, 10) for n in randomizer.sample(inputs['itemDomain'], 24)}
        objects, _ = assemble_item_resources(inputs)
        oracle = {'fields': list(CATEGORY_FIELDS), 'items': {str(r['id']): [r['gameplay'][k] for k in CATEGORY_FIELDS] for r in inputs['records']},
                  'priority': inputs['priorities']}
        code = '''import fs from 'node:fs'; import {pathToFileURL} from 'node:url';
const root = process.argv[1], input = JSON.parse(fs.readFileSync(0,'utf8'));
const {compileCategoryData} = await import(pathToFileURL(root+'/scripts/compile-player-category-index.mjs'));
const {validateItemResourceSnapshot} = await import(pathToFileURL(root+'/shared/game/item-resource-contract.mjs'));
validateItemResourceSnapshot({gameVersion:'0.0.1',readJson:role=>input.objects[role]});
const actual=compileCategoryData(input.oracle); delete actual.source;
console.log(JSON.stringify(actual));'''
        response = subprocess.run(['node', '--input-type=module', '-e', code, str(root)],
                                  input=json.dumps({'oracle': oracle, 'objects': {k: json.loads(v) for k, v in objects.items()}}),
                                  capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(response.stdout), json.loads(objects['items.categories']))


if __name__ == '__main__': unittest.main()
