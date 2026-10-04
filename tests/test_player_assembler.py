import base64
import copy
from io import BytesIO
import json
import os
from pathlib import Path
import struct
import subprocess
import unittest
import zlib

from PIL import Image
from resource_pipeline.player_assembler import ALGORITHM, HAIR_SETS, UNLOCKS, assemble_player_resources
from resource_pipeline.item_assembler import assemble_item_resources
from resource_pipeline.security import PipelineError, sha256
from resource_pipeline.security import canonical_json
from test_item_assembler import original_inputs


def original_player_inputs():
    item_objects, _ = assemble_item_resources(original_inputs())
    image = Image.new('RGBA', (8, 8))
    for y in range(8):
        for x in range(8): image.putpixel((x, y), (x * 20, y * 20, 80, 255))
    output = BytesIO(); image.save(output, format='PNG'); texture = output.getvalue()
    def layer(x=0, dest=(3, 4)):
        return {'texture': 'Original.png', 'textureSha256': sha256(texture),
                'source': [x, 0, 2, 3], 'destination': list(dest), 'tint': [255, 255, 255, 255]}
    recipe = {'key': 'player_0_0', 'canvas': [10, 10], 'frames': [[layer(n % 2 * 2)] for n in range(14)]}
    wing = {'frameCount': 4, 'offsetX': 0, 'offsetY': 0, 'cropRight': 0, 'cropBottom': 0,
            'anchorMode': 'common-wing', 'special': None, 'requires': [], 'animateWhenIdle': False}
    facts = {'buffs': {'1': ['Original buff', 'Original description']},
             'dyes': [{'itemId': 1, 'class': 'Original', 'pass': 'Original'}],
             'hairRules': {**{k: [] for k in HAIR_SETS}, 'backHairStyle': {'lowerExclusive': 0, 'upperExclusive': 3,
                           'excludedRanges': [], 'excludedIds': [], 'includedIds': []}},
             'wingRules': {'default': wing, 'slots': {}},
             'selection': {'clothes': [0], 'hairDyeItems': [0],
                           'unlockItems': [[key, 'Original unlock', 1] for key in UNLOCKS],
                           'negativeBuffs': [], 'commonBuffThrough': 1, 'commonBuffs': [1], 'maxBuffId': 1},
             'versionLabels': {'38': '0.0.1'}}
    return {'item_objects': item_objects, 'textures': {'Original.png': texture}, 'policy': {
        'schemaVersion': 1, 'algorithm': ALGORITHM, 'gameVersion': '0.0.1',
        'sourceHashes': {'original-policy': sha256(b'original synthetic player policy')},
        'itemHashes': {k: sha256(v) for k, v in item_objects.items()},
        'walk': [recipe, {**copy.deepcopy(recipe), 'key': 'original_alias'}],
        'choices': {'columns': 2, 'rows': [{'key': 'clothes:0', 'layers': [layer()]}, {'key': 'hair:0', 'layers': [layer(2)]}]},
        'repairs': {'translations': {'player_0_0': [[0, 0] for _ in range(14)]}, 'textures': [copy.deepcopy(recipe)]},
        'facts': facts}}


def expand(raw, count):
    unpacked = zlib.decompress(raw)
    palette_size = struct.unpack_from('<H', unpacked)[0]
    if palette_size:
        palette = [unpacked[2 + i * 4:6 + i * 4] for i in range(palette_size)]
        indexes = unpacked[2 + palette_size * 4:]
        assert len(indexes) == count
        return b''.join(palette[index] for index in indexes)
    assert len(unpacked) == 2 + count * 4
    return unpacked[2:]


class PlayerAssemblerTests(unittest.TestCase):
    def test_frame_dedup_palette_pixels_alias_and_gapless_pack(self):
        inputs = original_player_inputs(); before = copy.deepcopy(inputs)
        objects, receipt = assemble_player_resources(**inputs)
        self.assertEqual((objects, receipt), assemble_player_resources(**inputs))
        self.assertEqual(inputs, before)
        presentation = json.loads(objects['player.presentation'])
        entries = presentation['walkIndex']['textures']
        entry = entries['player_0_0']
        self.assertEqual(entries['original_alias'], entry)
        at, length, w, h, x, y, ow, oh, frames = entry
        self.assertEqual((at, w, h, x, y, ow, oh), (0, 2, 3, 3, 4, 10, 10))
        self.assertEqual(frames, [0, 1] * 7)
        rgba = expand(objects['player.walk'], w * h * 2)
        self.assertEqual(rgba[:4], bytes((0, 0, 80, 255)))
        self.assertEqual(rgba[24:28], bytes((40, 0, 80, 255)))
        self.assertEqual(length, len(objects['player.walk']))
        repair = presentation['frameRepairs']['textures']['player_0_0']
        self.assertEqual(expand(base64.b64decode(repair['data']), w * h * 2), rgba)
        self.assertEqual(presentation['items'], {'gameVersion': '0.0.1', **inputs['policy']['itemHashes']})
        with Image.open(BytesIO(objects['player.atlas'])) as atlas:
            self.assertEqual(atlas.size, (80, 56))
            self.assertEqual(atlas.getpixel((3, 4)), (0, 0, 80, 255))
            self.assertEqual(atlas.getpixel((43, 4)), (40, 0, 80, 255))
        self.assertEqual(receipt['status'], 'DERIVED_ONLY')
        self.assertFalse(receipt['sourceSemanticsVerified'])
        self.assertFalse(receipt['publicationApproved'])

    def test_more_than_256_colors_uses_exact_raw_rgba(self):
        inputs = original_player_inputs()
        image = Image.new('RGBA', (32, 32))
        for y in range(32):
            for x in range(32): image.putpixel((x, y), (x, y, 120, 255))
        output = BytesIO(); image.save(output, format='PNG'); raw = output.getvalue()
        inputs['textures']['Original.png'] = raw
        policies = inputs['policy']['walk'] + inputs['policy']['repairs']['textures']
        for recipe in policies:
            recipe['canvas'] = [32, 32]
            for frame in recipe['frames']:
                frame[0].update(textureSha256=sha256(raw), source=[0, 0, 32, 32], destination=[0, 0])
        for row in inputs['policy']['choices']['rows']: row['layers'][0]['textureSha256'] = sha256(raw)
        objects, _ = assemble_player_resources(**inputs)
        self.assertEqual(zlib.decompress(objects['player.walk'])[:2], b'\0\0')
        self.assertEqual(expand(objects['player.walk'], 1024), image.tobytes())

    def test_reject_missing_facts_domains_policy_geometry_and_hashes(self):
        edits = [
            lambda x: x['policy']['facts'].pop('dyes'),
            lambda x: x['policy']['facts']['selection']['unlockItems'].pop(),
            lambda x: x['policy']['facts']['selection'].update(hairDyeItems=[0, 1]),
            lambda x: x['policy']['facts']['selection'].update(maxBuffId=2),
            lambda x: x['policy']['facts']['selection'].update(clothes=[1]),
            lambda x: x['policy']['facts']['hairRules']['backHairStyle'].update(excludedRanges=[[1, 1], [1, 2]]),
            lambda x: x['policy']['facts']['wingRules']['default'].update(special='execute-upload'),
            lambda x: x['policy']['facts']['versionLabels'].update({'٣٨': '0.0.1'}),
            lambda x: x['policy']['facts']['buffs']['1'].__setitem__(0, '\ud800'),
            lambda x: x['policy']['facts']['dyes'][0].update(color=[10**1000, 0, 0]),
            lambda x: x['policy']['itemHashes'].update({'items.rules': 'f' * 64}),
            lambda x: x['textures'].update({'unused.png': x['textures']['Original.png']}),
            lambda x: x['policy']['walk'][0]['frames'].pop(),
            lambda x: x['policy']['walk'].append(copy.deepcopy(x['policy']['walk'][0])),
            lambda x: x['policy']['walk'][0]['frames'][0][0].update(source=[7, 0, 3, 3]),
            lambda x: x['policy']['walk'][0]['frames'][0][0].update(textureSha256='0' * 64),
            lambda x: x['policy']['walk'][0]['frames'][0][0].update(destination=[True, 1]),
            lambda x: x['policy']['walk'][0]['frames'][0][0].update(tint=[256, 0, 0, 255]),
            lambda x: x['policy']['walk'][0].update(frames=[[] for _ in range(14)]),
            lambda x: x['policy']['repairs']['translations'].update(unknown=[[0, 0]] * 14),
            lambda x: x['policy']['choices']['rows'].append(copy.deepcopy(x['policy']['choices']['rows'][0])),
            lambda x: x['policy'].update(gameVersion='١.٢.٣'),
            lambda x: x['policy'].update(approved=True),
        ]
        for edit in edits:
            with self.subTest(edit=edit):
                inputs = original_player_inputs(); edit(inputs)
                with self.assertRaises(PipelineError): assemble_player_resources(**inputs)

    def test_cancellation_propagates_no_partial_result(self):
        def cancel(): raise RuntimeError('original cancelled assembly')
        with self.assertRaisesRegex(RuntimeError, 'cancelled assembly'):
            assemble_player_resources(**original_player_inputs(), checkpoint=cancel)

    def test_reject_cyclic_deep_policy_before_hashing(self):
        inputs = original_player_inputs()
        inputs['policy']['repairs']['translations']['player_0_0'] = inputs['policy']
        with self.assertRaisesRegex(PipelineError, 'Circular'):
            assemble_player_resources(**inputs)
        inputs = original_player_inputs()
        nested = 0
        for _ in range(30): nested = [nested]
        inputs['policy']['repairs']['translations']['player_0_0'] = nested
        with self.assertRaisesRegex(PipelineError, 'structure budget'):
            assemble_player_resources(**inputs)

    def test_hashed_but_structurally_invalid_item_foundation_rejected(self):
        for role, mutate in (
            ('items.rules', lambda _: b'not json'),
            ('items.rules', lambda raw: canonical_json({**json.loads(raw), 'version': '9.9.9'})),
            ('items.catalog', lambda raw: canonical_json([json.loads(raw)[0], [None] * 3, *json.loads(raw)[2:]])),
            ('items.categories', lambda raw: canonical_json({**json.loads(raw), 'items': []})),
        ):
            with self.subTest(role=role):
                inputs = original_player_inputs()
                raw = mutate(inputs['item_objects'][role])
                inputs['item_objects'][role] = raw
                inputs['policy']['itemHashes'][role] = sha256(raw)
                with self.assertRaises(PipelineError): assemble_player_resources(**inputs)

    @unittest.skipUnless(os.environ.get('TERRARIA_CONSUMER_ROOT'), 'optional actual player contract oracle')
    def test_actual_app_validator_and_inflater(self):
        inputs = original_player_inputs(); objects, _ = assemble_player_resources(**inputs)
        root = Path(os.environ['TERRARIA_CONSUMER_ROOT']).resolve()
        payload = {'presentation': json.loads(objects['player.presentation']), 'walk': list(objects['player.walk']),
                   'atlas': list(objects['player.atlas']), 'items': {k: json.loads(v) for k, v in inputs['item_objects'].items()},
                   'itemHashes': inputs['policy']['itemHashes'], 'walkHash': sha256(objects['player.walk'])}
        code = '''import fs from 'node:fs';import assert from 'node:assert/strict';import {pathToFileURL} from 'node:url';
const root=process.argv[1],p=JSON.parse(fs.readFileSync(0,'utf8'));
const {validatePlayerResourceSnapshot}=await import(pathToFileURL(root+'/shared/game/player-resource-contract.mjs'));
const {inflatePlayerTexture}=await import(pathToFileURL(root+'/shared/game/player-resource-inflate.mjs'));
const sourceBinding={serverSha256:'a'.repeat(64),clientTreeSha:'b'.repeat(40),consumerCommit:'c'.repeat(40)};
const itemSnapshot={gameVersion:'0.0.1',manifest:{sourceBinding,objects:Object.fromEntries(Object.entries(p.itemHashes).map(([k,v])=>[k,{rawSha256:v}]))},readJson:r=>p.items[r]};
validatePlayerResourceSnapshot({gameVersion:'0.0.1',manifest:{sourceBinding,objects:{'player.walk':{rawSha256:p.walkHash}}},
readJson:()=>p.presentation,readBytes:r=>new Uint8Array(r==='player.walk'?p.walk:p.atlas)},itemSnapshot);
for(const entry of Object.values(p.presentation.walkIndex.textures)){
 const [at,length,w,h,,,,,frames]=entry,n=w*h*(Math.max(...frames)+1), raw=inflatePlayerTexture(new Uint8Array(p.walk.slice(at,at+length)),2+Math.max(n*4,1024+n));
 const count=raw[0]|(raw[1]<<8);assert(count<=256);assert.equal(raw.length,count?2+count*4+n:2+n*4);
 if(count)for(const index of raw.subarray(2+count*4))assert(index<count);
}console.log('Actual player contract and inflater passed');'''
        result = subprocess.run(['node', '--input-type=module', '-e', code, str(root)], input=json.dumps(payload),
                                capture_output=True, text=True, check=True, timeout=30)
        self.assertIn('passed', result.stdout)


if __name__ == '__main__': unittest.main()
