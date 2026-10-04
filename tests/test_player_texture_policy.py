import json
import os
from io import BytesIO
from pathlib import Path
import subprocess
import tempfile
import unittest
from PIL import Image
from resource_pipeline.player_texture_policy import map_player_texture_paths, build_player_walk_recipes, build_player_choice_recipes, build_player_frame_repairs, assemble_player_from_fresh_textures, WALK_REFERENCE, PREVIEW_REFERENCE, REPAIR_REFERENCE
from resource_pipeline.player_assembler import _Renderer, _pack_frames
from resource_pipeline.security import PipelineError, sha256


def png(w, h, transparent=False):
    image = Image.new('RGBA', (w, h))
    if not transparent:
        for y in range(h):
            for x in range(w): image.putpixel((x, y), (x % 251, y % 251, (x + y) % 251, 255))
    stream = BytesIO(); image.save(stream, format='PNG'); image.close(); return stream.getvalue()


class PlayerTexturePolicyTests(unittest.TestCase):
    def test_complete_projection_requires_explicit_facts_and_retains_unverified_gates(self):
        from test_player_assembler import original_player_inputs
        from test_item_assembler import original_inputs
        from resource_pipeline.item_assembler import assemble_item_resources
        inputs = original_inputs(); inputs['gameVersion'] = '1.4.5.8'
        items, _ = assemble_item_resources(inputs)
        facts = original_player_inputs()['policy']['facts']; facts['selection']['clothes'] = list(range(12))
        textures = {'Player_0_0.png': png(40, 56), 'Player_Hair_1.png': png(40, 784)}
        objects, receipt = assemble_player_from_fresh_textures(textures=textures, facts=facts, item_objects=items,
                                                             source_hashes={'original-facts': sha256(b'synthetic facts')})
        self.assertEqual(set(objects), {'player.presentation', 'player.walk', 'player.atlas'})
        self.assertFalse(receipt['runtimeFactsVerified']); self.assertFalse(receipt['complete'])
        self.assertFalse(receipt['publishable']); self.assertFalse(receipt['sourceDomainVerified'])
        self.assertTrue(receipt['texturePolicyReceipts']['player-choice-policy']['missingStandingPieces'])
        with self.assertRaises(PipelineError):
            assemble_player_from_fresh_textures(textures=textures, facts=facts, item_objects=[], source_hashes={'original-facts': sha256(b'synthetic facts')})
        with self.assertRaises(PipelineError):
            assemble_player_from_fresh_textures(textures=textures, facts={}, item_objects=items,
                                                source_hashes={'original-facts': sha256(b'synthetic facts')})

    def test_authoritative_directory_mapping_and_exclusions(self):
        mapping, excluded = map_player_texture_paths(['Armor/Armor_2.png', 'Accessories/Acc_HandsOn_3.xnb.png',
                                                       'Player_0_0.png', 'Item_1.png'])
        self.assertEqual(mapping, {'accessories_acc_handson_3': 'Accessories/Acc_HandsOn_3.xnb.png',
                                  'armor_composite_2': 'Armor/Armor_2.png', 'player_0_0': 'Player_0_0.png'})
        self.assertEqual(excluded, ['Item_1.png'])

    def test_traversal_duplicate_alias_case_collision_and_unsupported_rejected(self):
        for paths in (['../Player_0_0.png'], ['/Player_0_0.png'], ['x/../Player_0_0.png'],
                      ['x\\Player_0_0.png'], ['Player_0_0.svg'], ['Player_0_0.png'] * 2,
                      ['Player_0_0.png', 'player_0_0.png'], ['Player_0_0.png', 'Player_0_0.xnb.png'],
                      ['Armor/Armor_1.png', 'Armor_Composite_1.png'], ['Player_Hair_01.png'],
                      ['Player_Hair_01.png', 'Player_Hair_1.png'], ['Player_00_0.png'], ['Player_0_00.png']):
            with self.subTest(paths=paths), self.assertRaises(PipelineError): map_player_texture_paths(paths)

    def test_repair_rejects_female_variant_for_noncomposite_key(self):
        with self.assertRaises(PipelineError):
            build_player_frame_repairs({'Armor_Head_1.png': png(3, 1064)}, ['armor_head_1:female'])

    def test_transparent_absences_require_fresh_render_evidence(self):
        source = {'Player_0_0.png': png(40, 56, True), 'Player_0_3.png': png(40, 56), 'Player_0_14.png': png(40, 56)}
        recipes, textures, receipt = build_player_walk_recipes(source)
        self.assertEqual([r['key'] for r in recipes], ['player_0_3'])
        self.assertEqual(list(textures), ['Player_0_3.png'])
        self.assertEqual({x['reason'] for x in receipt['omissions']},
                         {'fresh-render-all-14-frames-transparent', 'app-excludes-player-piece-above-13'})
        self.assertFalse(receipt['complete']); self.assertFalse(receipt['publishable'])
        self.assertEqual(receipt['sourceTextures']['Player_0_0.png'], sha256(source['Player_0_0.png']))

    @unittest.skipUnless(os.environ.get('TERRARIA_CONSUMER_ROOT'), 'optional original app repair oracle')
    def test_short_sheet_repairs_match_original_pixels_and_translations(self):
        import base64, zlib
        app = Path(os.environ['TERRARIA_CONSUMER_ROOT']); script = app / REPAIR_REFERENCE['path']
        self.assertEqual(sha256(script.read_bytes()), REPAIR_REFERENCE['sha256'])
        translated = Image.new('RGBA', (40, 1119))
        translated.paste((120, 90, 60, 255), (2, 3, 5, 6))
        for f in range(6, 20): translated.paste((120, 90, 60, 255), (2 + f % 2, f * 56 + 3, 5 + f % 2, f * 56 + 6))
        stream = BytesIO(); translated.save(stream, format='PNG')
        hidden = translated.copy(); translated.close()
        hidden.putpixel((3, 4), (7, 8, 9, 0))
        for f in range(6, 20): hidden.putpixel((3 + f % 2, f * 56 + 4), (f, 8, 9, 0))
        hidden_stream = BytesIO(); hidden.save(hidden_stream, format='PNG'); hidden.close()
        sources = {'Armor_Head_1.png': stream.getvalue(), 'Armor_Head_2.png': png(40, 1064), 'Armor_Head_3.png': hidden_stream.getvalue()}
        repairs, selected, receipt = build_player_frame_repairs(sources, ['armor_head_1', 'armor_head_2', 'armor_head_3'])
        self.assertEqual(len(repairs['translations']), 1); self.assertEqual(len(repairs['textures']), 2)
        self.assertFalse(receipt['complete'])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); atlas = root / 'assets/terraria/1.4.5.8/player-atlas'; atlas.mkdir(parents=True)
            atlases, assets = [], []
            for index, (path, raw) in enumerate(sources.items()):
                filename = str(index) + '.png'; (atlas / filename).write_bytes(raw)
                with Image.open(BytesIO(raw)) as image: width, height = image.size
                atlases.append({'file': filename}); assets.append({'key': path[:-4].lower(), 'width': width, 'height': height, 'x': 0, 'y': 0, 'atlas': index})
            (atlas / 'manifest.json').write_text(json.dumps({'atlases': atlases, 'assets': assets}))
            output = root / 'output'; (output / 'features/player-editor/pages/services/render').mkdir(parents=True)
            subprocess.run(['python', str(script), str(root)], env=dict(os.environ, VIEWER_APP_OUTPUT=str(output)), check=True, capture_output=True, timeout=30)
            module = (output / 'features/player-editor/pages/services/render/frame-repairs-data.mjs').read_text()
            old = json.loads(zlib.decompress(base64.b64decode(json.loads(module.split('export default ', 1)[1].strip().removesuffix(';')))))
            self.assertEqual(repairs['translations'], old['translations'])
            renderer = _Renderer(selected, lambda: None)
            try:
                for recipe in repairs['textures']:
                    packed, geometry = _pack_frames(renderer, recipe, palette_codec=False)
                    w, h, x, y, ow, oh, frames = geometry
                    self.assertEqual(old['textures'][recipe['key']], {'width': w, 'height': h, 'x': x, 'y': y, 'originalWidth': ow,
                                                                       'frames': frames, 'data': base64.b64encode(packed).decode('ascii')})
            finally: renderer.close()

    @unittest.skipUnless(os.environ.get('TERRARIA_CONSUMER_ROOT'), 'optional original app preview oracle')
    def test_preview_composite_then_tint_exact_pixels(self):
        app = Path(os.environ['TERRARIA_CONSUMER_ROOT']); script = app / PREVIEW_REFERENCE['path']
        self.assertEqual(sha256(script.read_bytes()), PREVIEW_REFERENCE['sha256'])
        sources = {'Player_0_3.png': png(360, 224), 'Player_0_4.png': png(360, 224),
                   'Player_4_5.png': png(360, 224), 'Player_Hair_1.png': png(44, 56)}
        # Semi-transparent layers expose tint/composition rounding-order defects.
        for path, raw in list(sources.items()):
            with Image.open(BytesIO(raw)) as image:
                image.putalpha(Image.new('L', image.size, 117)); stream = BytesIO()
                image.save(stream, format='PNG'); sources[path] = stream.getvalue()
        choices, derived, receipt = build_player_choice_recipes(sources)
        self.assertTrue(receipt['missingStandingPieces']); self.assertFalse(receipt['complete'])
        mapping, _ = map_player_texture_paths(list(sources))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); atlas = root / 'input'; atlas.mkdir()
            atlases, assets = [], []
            for index, (key, path) in enumerate(mapping.items()):
                raw = sources[path]; filename = str(index) + '.png'; (atlas / filename).write_bytes(raw)
                with Image.open(BytesIO(raw)) as image: width, height = image.size
                atlases.append({'file': filename}); assets.append({'key': key, 'width': width, 'height': height, 'x': 0, 'y': 0, 'atlas': index})
            (atlas / 'manifest.json').write_text(json.dumps({'terrariaVersion': '1.4.5.8', 'atlases': atlases, 'assets': assets}))
            output = root / 'output'
            subprocess.run(['python', str(script), str(atlas), '--output', str(output)], check=True, capture_output=True, timeout=30)
            renderer = _Renderer(derived, lambda: None)
            composed = Image.new('RGBA', (640, ((len(choices['rows']) + 15) // 16) * 56))
            try:
                for index, row in enumerate(choices['rows']):
                    image = renderer.render([40, 56], row['layers'])
                    composed.paste(image, (index % 16 * 40, index // 16 * 56)); image.close()
                with Image.open(output / 'runtime/choices.png') as old:
                    self.assertEqual(composed.size, old.size)
                    self.assertEqual(composed.tobytes(), old.convert('RGBA').tobytes())
            finally: renderer.close(); composed.close()

    @unittest.skipUnless(os.environ.get('TERRARIA_CONSUMER_ROOT'), 'optional original app recipe oracle')
    def test_exact_synthetic_pixels_against_original_app_generator(self):
        app = Path(os.environ['TERRARIA_CONSUMER_ROOT']); script = app / WALK_REFERENCE['path']
        self.assertEqual(sha256(script.read_bytes()), WALK_REFERENCE['sha256'])
        sources = {'Player_0_3.png': png(360, 224), 'Player_5_4.png': png(360, 224),
                   'Armor/Armor_1.png': png(360, 224), 'Accessories/Acc_HandsOn_1.png': png(360, 224),
                   'Accessories/Acc_HandsOff_1.png': png(360, 224), 'Player_Hair_1.png': png(40, 784),
                   'Wings_22.png': png(42, 392), 'Wings_40.png': png(42, 784),
                   'Acc_Balloon_1.png': png(40, 224), 'Armor_Head_1.png': png(40, 1120)}
        hidden = Image.new('RGBA', (3, 3), (100, 100, 100, 255)); hidden.putpixel((1, 1), (7, 8, 9, 0))
        hidden_stream = BytesIO(); hidden.save(hidden_stream, format='PNG'); hidden.close()
        sources['Armor_Head_3.png'] = hidden_stream.getvalue()
        recipes, selected, receipt = build_player_walk_recipes(sources)
        mapping, _ = map_player_texture_paths(list(sources))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); atlas = root / 'assets/terraria/1.4.5.8/player-atlas'; atlas.mkdir(parents=True)
            atlases, assets = [], []
            for index, (key, path) in enumerate(mapping.items()):
                raw = sources[path]; filename = str(index) + '.png'; (atlas / filename).write_bytes(raw)
                with Image.open(BytesIO(raw)) as im: width, height = im.size
                atlases.append({'file': filename, 'sha256': sha256(raw)})
                assets.append({'key': key, 'width': width, 'height': height, 'x': 0, 'y': 0, 'atlas': index})
            (atlas / 'manifest.json').write_text(json.dumps({'atlases': atlases, 'assets': assets}))
            output = root / 'output'; (output / 'features/player-editor/pages/runtime').mkdir(parents=True)
            (output / 'features/player-editor/pages/services').mkdir(parents=True)
            env = dict(os.environ, VIEWER_APP_OUTPUT=str(output))
            subprocess.run(['python', str(script), str(root)], env=env, check=True, capture_output=True, timeout=30)
            import base64, zlib
            module = (output / 'features/player-editor/pages/services/walk-index.mjs').read_text()
            old = json.loads(zlib.decompress(base64.b64decode(json.loads(module.removeprefix('export default ').strip().removesuffix(';')))))
            payload = (output / 'features/player-editor/pages/runtime/walk.bin').read_bytes()
            renderer = _Renderer(selected, lambda: None)
            try:
                self.assertEqual(set(old['textures']), {r['key'] for r in recipes})
                for recipe in recipes:
                    packed, geometry = _pack_frames(renderer, recipe)
                    at, length, *expected = old['textures'][recipe['key']]
                    self.assertEqual(geometry, expected, recipe['key'])
                    self.assertEqual(packed, payload[at:at + length], recipe['key'])
            finally: renderer.close()
