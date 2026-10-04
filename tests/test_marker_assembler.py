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
from resource_pipeline.marker_assembler import ALGORITHM, assemble_marker_resources
from resource_pipeline.security import PipelineError, canonical_json, sha256


def original_marker_inputs():
    image = Image.new('RGBA', (40, 40))
    for y in range(40):
        for x in range(40): image.putpixel((x, y), (x * 4, y * 4, 100, 255))
    stream = BytesIO()
    image.save(stream, format='PNG')
    texture = stream.getvalue()
    source = b'// Original test source; no Terraria game values.\n'
    base = canonical_json({'tiles': [[0, 'Original zero', '#123456'], [1, 'Original one', '#654321']]})
    crop = {'textureSha256': sha256(texture), 'frameX': 1, 'frameY': 2, 'columns': 2, 'rowHeights': [10, 12]}
    policy = {'schemaVersion': 1, 'algorithm': ALGORITHM, 'gameVersion': '0.0.1',
              'sourceFiles': {'Terraria/Original.cs': sha256(source)},
              'rows': [{'key': '1:test', 'name': 'Original marker',
                        'selector': {'tile_type': 1, 'locate': 1, 'frame_x': 0, 'frame_y': 0, 'frame_x_mod': 0, 'frame_y_mod': 0},
                        'crop': crop},
                       {'key': '1', 'name': 'Original vein', 'selector': {'tile_type': 1, 'locate': 2},
                        'crop': {**crop, 'columns': 1, 'rowHeights': [8]}}]}
    return {'material_base': base, 'material_base_sha256': sha256(base), 'policy': policy,
            'textures': {'Tiles_1.png': texture}, 'source_files': {'Terraria/Original.cs': source}}


class MarkerAssemblerTests(unittest.TestCase):
    def test_fresh_crop_pixels_gapless_order_and_receipt(self):
        inputs = original_marker_inputs()
        before = copy.deepcopy(inputs)
        objects, receipt = assemble_marker_resources(**inputs)
        self.assertEqual(inputs, before)
        self.assertEqual((objects, receipt), assemble_marker_resources(**inputs))
        catalog = json.loads(objects['markers.catalog'])
        self.assertEqual(catalog['maxTileId'], 1)
        self.assertEqual(catalog['materials']['baseSha256'], inputs['material_base_sha256'])
        end = 0
        for index, row in enumerate(catalog['rows']):
            image = row['image']
            self.assertEqual(row['iconId'], 1000000 + index)
            self.assertEqual(image['offset'], end)
            raw = objects['markers.images'][end:end + image['bytes']]
            self.assertEqual(sha256(raw), image['sha256'])
            with Image.open(BytesIO(raw)) as decoded:
                self.assertEqual(decoded.mode, 'RGBA')
                self.assertEqual(decoded.getpixel((0, 0)), (4, 8, 100, 255))
                if index == 0:
                    self.assertEqual(decoded.size, (32, 22))
                    self.assertEqual(decoded.getpixel((16, 10)), (76, 56, 100, 255))
            end += image['bytes']
        self.assertEqual(end, len(objects['markers.images']))
        self.assertEqual(receipt['status'], 'DERIVED_ONLY')
        self.assertFalse(receipt['sourceSemanticsVerified'])
        self.assertFalse(receipt['publicationApproved'])

    def test_reject_source_hash_domain_geometry_policy_and_texture_attacks(self):
        edits = [
            lambda x: x.update(material_base_sha256='f' * 64),
            lambda x: x['source_files'].update({'Terraria/Original.cs': b'mutated'}),
            lambda x: x['textures'].update({'Tiles_1.png': b'not a texture'}),
            lambda x: x['textures'].update({'unused.png': b'data'}),
            lambda x: x['policy']['rows'].append(copy.deepcopy(x['policy']['rows'][0])),
            lambda x: x['policy']['rows'][0]['crop'].update(frameX=30),
            lambda x: x['policy']['rows'][0]['crop'].update(frameY=39),
            lambda x: x['policy']['rows'][0]['crop'].update(rowHeights=[32] * 8),
            lambda x: x['policy']['rows'][0]['crop'].update(columns=True),
            lambda x: x['policy']['rows'][0]['crop'].update(rowHeights=[]),
            lambda x: x['policy']['rows'][0]['selector'].update(tile_type=3),
            lambda x: x['policy']['rows'][0]['selector'].update(locate=True),
            lambda x: x['policy']['rows'][0]['selector'].update(frame_x_mod=-1),
            lambda x: x['policy'].update(approved=True),
            lambda x: x['policy'].update(algorithm='guess-layout'),
            lambda x: x['policy']['rows'][0].update(name='😀' * 65),
            lambda x: x['policy']['rows'][0].update(name='\ud800'),
            lambda x: x['policy']['rows'][0].update(key='1١'),
            lambda x: x['policy'].update(gameVersion='١.٢.٣'),
        ]
        for edit in edits:
            with self.subTest(edit=edit):
                inputs = original_marker_inputs()
                edit(inputs)
                with self.assertRaises(PipelineError): assemble_marker_resources(**inputs)

    def test_sparse_or_duplicate_material_ids_rejected(self):
        for ids in ([0, 2], [0, 0], [1]):
            inputs = original_marker_inputs()
            inputs['material_base'] = canonical_json({'tiles': [[n, 'Original', '#ffffff'] for n in ids]})
            inputs['material_base_sha256'] = sha256(inputs['material_base'])
            with self.assertRaises(PipelineError): assemble_marker_resources(**inputs)

    def test_bad_png_even_if_hash_matches_is_rejected(self):
        originals = original_marker_inputs()
        texture = originals['textures']['Tiles_1.png']
        huge = bytearray(texture)
        struct.pack_into('>II', huge, 16, 16384, 16384)
        struct.pack_into('>I', huge, 29, zlib.crc32(huge[12:29]) & 0xffffffff)
        critical = b'BADXunsupported'
        unknown = texture[:33] + struct.pack('>I', len(critical) - 4) + critical + struct.pack('>I', zlib.crc32(critical) & 0xffffffff) + texture[33:]
        for raw in (texture + b'trailing', texture[:-4], b'x' + texture[1:], bytes(huge), unknown):
            inputs = original_marker_inputs()
            inputs['textures']['Tiles_1.png'] = raw
            for row in inputs['policy']['rows']: row['crop']['textureSha256'] = sha256(raw)
            with self.assertRaises(PipelineError): assemble_marker_resources(**inputs)

    @unittest.skipUnless(os.environ.get('TERRARIA_CONSUMER_ROOT'), 'optional actual app contract oracle')
    def test_actual_app_marker_contract(self):
        inputs = original_marker_inputs()
        objects, _ = assemble_marker_resources(**inputs)
        root = Path(os.environ['TERRARIA_CONSUMER_ROOT']).resolve()
        payload = {'catalog': json.loads(objects['markers.catalog']), 'image': list(objects['markers.images']),
                   'base': json.loads(inputs['material_base']), 'baseHash': inputs['material_base_sha256']}
        code = '''import fs from 'node:fs'; import {pathToFileURL} from 'node:url';
const root=process.argv[1], p=JSON.parse(fs.readFileSync(0,'utf8'));
const {validateMarkerResourceSnapshot}=await import(pathToFileURL(root+'/shared/game/marker-resource-contract.mjs'));
const sourceBinding={serverSha256:'a'.repeat(64),clientTreeSha:'b'.repeat(40),consumerCommit:'c'.repeat(40)};
validateMarkerResourceSnapshot({gameVersion:'0.0.1',manifest:{sourceBinding},readJson:()=>p.catalog,readBytes:()=>new Uint8Array(p.image)},
 {gameVersion:'0.0.1',manifest:{sourceBinding,objects:{'materials.base':{rawSha256:p.baseHash}}},readJson:()=>p.base});
console.log('Original marker assembly accepted');'''
        result = subprocess.run(['node', '--input-type=module', '-e', code, str(root)], input=json.dumps(payload),
                                text=True, capture_output=True, check=True)
        self.assertIn('accepted', result.stdout)


if __name__ == '__main__': unittest.main()
