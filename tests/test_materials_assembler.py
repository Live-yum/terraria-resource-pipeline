import copy
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from resource_pipeline.materials_assembler import ALGORITHM, assemble_material_resources
from resource_pipeline.security import PipelineError, canonical_json, sha256


def original_material_inputs():
    def record(ident, name):
        return {'id': ident, 'name': name, 'color': '#AABBCC', 'internalName': name}
    tiles = [dict(record(i, 'Original tile ' + str(i)), frameImportant=i == 1) for i in range(2)]
    records = {'schemaVersion': 1, 'gameVersion': '0.0.1', 'tiles': tiles,
               'walls': [record(1, 'Original wall')], 'paints': [record(1, 'Original paint')]}
    layout = {'width': 2, 'height': 2, 'coordinateWidth': 16, 'coordinateHeights': [16, 18], 'padding': 2}
    policy = {'schemaVersion': 1, 'gameVersion': '0.0.1', 'consumerCommit': 'c' * 40,
              'policyId': 'synthetic-original-v1', 'algorithm': ALGORITHM,
              'materials': [{'tileId': i, 'style': 0, 'alternate': 0, 'random': 0,
                             'name': 'Original choice ' + str(i), 'frameX': 0 if i else None,
                             'frameY': 0 if i else None, 'layout': copy.deepcopy(layout) if i else None,
                             'itemId': i} for i in range(2)],
              'shapes': [{'tileId': 1, 'name': 'Original exact shape', 'frameX': 0,
                          'frameY': None, 'layout': None, 'mode': 'exact'}],
              'variants': [{'tileId': 1, 'subId': 'original', 'name': 'Original alias', 'layoutKey': '1:0:0:0:1'}]}
    return {'records': records, 'policy': policy}


class MaterialsAssemblerTests(unittest.TestCase):
    def test_projection_determinism_no_mutation_and_hash_binding(self):
        inputs = original_material_inputs()
        before = copy.deepcopy(inputs)
        objects, receipt = assemble_material_resources(**inputs)
        self.assertEqual(inputs, before)
        self.assertEqual((objects, receipt), assemble_material_resources(**inputs))
        base, rules = (json.loads(objects[r]) for r in ('materials.base', 'materials.rules'))
        self.assertEqual(base['tiles'][0], [0, 'Original tile 0', '#aabbcc', 'Original tile 0'])
        self.assertEqual(rules['materials'][1], [1, 0, 0, 0, 'Original choice 1', 0, 0, 2, 2, 16, [16, 18], 2, None, None, 1])
        self.assertEqual(rules['materials'][0][5:14], [None] * 9)
        self.assertEqual(rules['shapes'][0], [1, 'Original exact shape', 0, None, None, None, None, None, None, 'exact'])
        self.assertEqual(rules['frameImportant'], [False, True])
        self.assertEqual(receipt['materialBaseSha256'], sha256(objects['materials.base']))
        self.assertEqual(receipt['objects']['materials.rules']['baseSha256'], receipt['materialBaseSha256'])
        self.assertEqual(receipt['recordsSha256'], sha256(canonical_json(inputs['records'])))
        self.assertEqual(receipt['policySha256'], sha256(canonical_json(inputs['policy'])))
        self.assertEqual(receipt['status'], 'DERIVED_ONLY')
        self.assertFalse(receipt['sourceSemanticsVerified'])
        self.assertFalse(receipt['publicationApproved'])
        inputs['records']['tiles'].reverse()
        self.assertEqual(objects, assemble_material_resources(**inputs)[0])

    def test_invalid_inputs_fail_closed(self):
        edits = [
            lambda x: x['records'].update(schemaVersion=True),
            lambda x: x['records'].update(extra='proof'),
            lambda x: x['records']['tiles'].pop(0),
            lambda x: x['records']['tiles'][1].update(id=0),
            lambda x: x['records']['tiles'][1].update(id=True),
            lambda x: x['records']['tiles'][1].update(frameImportant=1),
            lambda x: x['records']['paints'].clear(),
            lambda x: x['records']['walls'][0].update(color='#fff'),
            lambda x: x['records']['walls'][0].update(name='\ud800'),
            lambda x: x['records']['walls'][0].update(internalName={}),
            lambda x: x['policy'].update(gameVersion='0.0.2'),
            lambda x: x['policy'].update(algorithm='guess'),
            lambda x: x['policy'].update(publicationApproved=True),
            lambda x: x['policy'].update(consumerCommit='g' * 40),
            lambda x: x['policy']['materials'].append(copy.deepcopy(x['policy']['materials'][0])),
            lambda x: x['policy']['materials'][0].update(tileId=5),
            lambda x: x['policy']['materials'][0].update(itemId=False),
            lambda x: x['policy']['materials'][0].update(frameX=0),
            lambda x: x['policy']['materials'][1]['layout'].update(coordinateHeights=[16]),
            lambda x: x['policy']['materials'][1]['layout'].update(coordinateWidth=0),
            lambda x: x['policy']['materials'][1].update(frameY=None),
            lambda x: x['policy']['shapes'].clear(),
            lambda x: x['policy']['shapes'][0].update(mode='layout'),
            lambda x: x['policy']['shapes'][0].update(frameX=True),
            lambda x: x['policy']['shapes'][0].update(mode={}),
            lambda x: x['policy']['variants'][0].update(layoutKey='0:0:0:0:0'),
            lambda x: x['policy']['variants'][0].update(layoutKey='missing'),
            lambda x: x['policy']['variants'].append(copy.deepcopy(x['policy']['variants'][0])),
            lambda x: x['policy']['variants'][0].update(subId=[]),
        ]
        for edit in edits:
            inputs = original_material_inputs()
            edit(inputs)
            with self.subTest(edit=edit), self.assertRaises(PipelineError):
                assemble_material_resources(**inputs)

    def test_framed_missing_layout_requires_same_tile_shape(self):
        inputs = original_material_inputs()
        inputs['policy']['materials'][1].update(layout=None, frameX=None, frameY=None)
        assemble_material_resources(**inputs)
        inputs['policy']['shapes'][0]['tileId'] = 0
        with self.assertRaisesRegex(PipelineError, 'MISSING_FRAMED_SHAPE'):
            assemble_material_resources(**inputs)

    def test_layout_and_auto_shapes_and_unbound_alias(self):
        inputs = original_material_inputs()
        for mode in ('layout', 'auto'):
            inputs['policy']['shapes'][0].update(mode=mode, frameY=2, layout=copy.deepcopy(inputs['policy']['materials'][1]['layout']))
            inputs['policy']['variants'][0]['layoutKey'] = None
            _, receipt = assemble_material_resources(**inputs)
            self.assertFalse(receipt['sourceSemanticsVerified'])

    def test_explicit_bounds(self):
        for name, limit in [('MAX_ROWS', 1), ('MAX_BYTES', 50), ('MAX_LAYOUT_VALUES', 1)]:
            with self.subTest(name=name), patch('resource_pipeline.materials_assembler.' + name, limit):
                with self.assertRaises(PipelineError): assemble_material_resources(**original_material_inputs())

    @unittest.skipUnless(os.environ.get('TERRARIA_CONSUMER_ROOT'), 'optional actual app oracle')
    def test_actual_consumer_accepts_both_roles_with_bound_pixel_inputs(self):
        objects, receipt = assemble_material_resources(**original_material_inputs())
        from resource_pipeline.pixel_assembler import assemble_pixel_resources, ALGORITHM as PIXEL_ALGORITHM
        selection = {'schemaVersion': 1, 'gameVersion': '0.0.1', 'policyId': 'original-pixel-v1',
                     'consumerCommit': 'c' * 40, 'algorithm': PIXEL_ALGORITHM,
                     'tileIds': [0], 'wallIds': [1], 'paintIds': [1]}
        pixel = assemble_pixel_resources(objects['materials.base'], game_version='0.0.1',
                                        verified_base_sha256=receipt['materialBaseSha256'], policy=selection)
        self.assertEqual(pixel.evidence['baseSha256'], receipt['materialBaseSha256'])
        with self.assertRaises(PipelineError):
            assemble_pixel_resources(objects['materials.base'] + b' ', game_version='0.0.1',
                                     verified_base_sha256=receipt['materialBaseSha256'], policy=selection)
        payload = {'base': json.loads(objects['materials.base']), 'rules': json.loads(objects['materials.rules']),
                   'rgb': list(pixel.rgb), 'palette': pixel.catalog}
        code = '''import fs from 'node:fs'; import {pathToFileURL} from 'node:url';
const p=JSON.parse(fs.readFileSync(0,'utf8'));
const {validateMaterialResourceSnapshot,materialCandidateFingerprint}=await import(pathToFileURL(process.argv[1]+'/shared/game/material-resource-contract.mjs'));
const palette=p.palette;
if(palette.rgbCatalogSha256!==materialCandidateFingerprint(p.base,palette))throw Error('fingerprint mismatch');
const roles={'materials.base':p.base,'materials.rules':p.rules,'pixel.catalog':palette};
const snapshot={gameVersion:'0.0.1',releaseId:'original',baseSha256:'original',readJson:r=>roles[r],readBytes:()=>new Uint8Array(p.rgb)};
validateMaterialResourceSnapshot(snapshot);
const {getMaterialCatalog}=await import(pathToFileURL(process.argv[1]+'/shared/game/material-catalog.js'));
const catalog=getMaterialCatalog(snapshot);
if(catalog.tiles[1].serialNo!==1||catalog.tiles[1].name!=='Original tile 1'||!Object.isFrozen(catalog.tiles))throw Error('catalog projection mismatch');
p.base.tiles[0][2]='#000000';
let rejected=false;try{validateMaterialResourceSnapshot(snapshot)}catch{rejected=true}
if(!rejected)throw Error('stale pixel fingerprint accepted');
console.log('Original material assembly accepted');'''
        root = Path(os.environ['TERRARIA_CONSUMER_ROOT']).resolve()
        result = subprocess.run(['node', '--loader', str(root / 'scripts/node-alias-loader.mjs'),
                                 '--input-type=module', '-e', code, str(root)],
                                input=json.dumps(payload), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('accepted', result.stdout)


if __name__ == '__main__': unittest.main()
