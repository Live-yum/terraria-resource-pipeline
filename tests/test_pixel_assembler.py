"""Original-only pixel fixtures; no baseline palettes or game assets are used."""
from dataclasses import replace
import json
import os
from pathlib import Path
import random
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

from resource_pipeline import pixel_assembler as pixel
from resource_pipeline.security import canonical_json, sha256

APP_ROOT = os.environ.get('PIXEL_ASSEMBLER_APP_ROOT')


def fixture():
    return {
        'tiles': [[0, 'Original amber tile', '#123456'], [2, 'Original rose tile', '#f27b13']],
        'walls': [[1, 'Original amber wall', '#123456'], [3, 'Original blue wall', '#1358d4']],
        'paints': [[1, 'Original paint', '#e34579'], [29, 'Original dark input', '#81c8ff'],
                   [30, 'Original inverse input', '#4b9d23']],
    }


def policy():
    return {'schemaVersion': 1, 'policyId': 'original-fixture-v1', 'gameVersion': '0.0.1',
            'consumerCommit': 'c' * 40, 'algorithm': pixel.ALGORITHM,
            'tileIds': [2, 0], 'wallIds': [1, 3], 'paintIds': [30, 1, 29]}


def assemble(base=None, selection=None, **kwargs):
    raw = canonical_json(fixture() if base is None else base)
    return pixel.assemble_pixel_resources(raw, game_version='0.0.1', verified_base_sha256=sha256(raw),
                                          policy=policy() if selection is None else selection, **kwargs)


def decode(raw, rgb, prefer_wall=False, size=256):
    red, green, blue = rgb
    line = red * size + green
    first, last = struct.unpack_from('<II', raw, 12 + line * 4)
    payload = 12 + (size * size + 1) * 4
    for run in range(first, last):
        end, tile, wall = struct.unpack_from('<BHH', raw, payload + run * 5)
        if blue <= end:
            return wall if prefer_wall else tile
    raise AssertionError('Missing scanline endpoint')


def brute(candidates, rgb, prefer_wall):
    # Independent finite reference: full candidate scan, with no envelope code,
    # float arithmetic, grouping, rank table, or assembler decoder internals.
    def key(index):
        entry = candidates[index]
        point = (entry['rgb'] >> 16, (entry['rgb'] >> 8) & 255, entry['rgb'] & 255)
        return (sum((a - b) ** 2 for a, b in zip(rgb, point)),
                1 if entry['paint'] else 0,
                0 if not prefer_wall or entry['wall'] else 1, index)
    return min(range(len(candidates)), key=key)


class PixelFiniteReferenceTests(unittest.TestCase):
    def test_exhaustive_finite_cubes_match_independent_brute_force(self):
        rng = random.Random(670104)
        for size in (2, 3, 5, 8):
            for count in (1, 2, 7, 31):
                candidates = []
                for index in range(count):
                    r, g, b = (rng.randrange(size) for _ in range(3))
                    candidates.append({'rgb': r << 16 | g << 8 | b,
                                       'paint': bool(rng.randrange(2)), 'wall': bool(rng.randrange(2)), 'order': index})
                budget = pixel._Budget(pixel.PixelAssemblyLimits())
                raw = pixel._build_index(candidates, budget, size=size)
                for r in range(size):
                    for g in range(size):
                        for b in range(size):
                            for wall in (False, True):
                                self.assertEqual(decode(raw, (r, g, b), wall, size),
                                                 brute(candidates, (r, g, b), wall),
                                                 (size, count, (r, g, b), wall))

    def test_equal_distance_duplicate_and_paint_wall_ties(self):
        candidates = [
            {'rgb': 0, 'paint': True, 'wall': True, 'order': 0},
            {'rgb': 2, 'paint': False, 'wall': False, 'order': 1},
            {'rgb': 2, 'paint': False, 'wall': True, 'order': 2},
            {'rgb': 0, 'paint': False, 'wall': False, 'order': 3},
        ]
        raw = pixel._build_index(candidates, pixel._Budget(pixel.PixelAssemblyLimits()), size=3)
        self.assertEqual(decode(raw, (0, 0, 0), False, 3), 3)  # Exact unpainted beats painted wall.
        self.assertEqual(decode(raw, (0, 0, 0), True, 3), 3)
        self.assertEqual(decode(raw, (0, 0, 1), False, 3), 1)  # Distance tie: original order.
        self.assertEqual(decode(raw, (0, 0, 1), True, 3), 2)   # Wall preference only after paint.
        self.assertEqual(decode(raw, (0, 0, 2), False, 3), 1)
        self.assertEqual(decode(raw, (0, 0, 2), True, 3), 2)

    def test_envelope_exact_crossings_and_negative_intercepts(self):
        rng = random.Random(821)
        for _ in range(200):
            count = rng.randrange(1, 12)
            positions = sorted(rng.sample(range(16), count))
            ranks = rng.sample(range(count), count)
            points = [(p, rng.randrange(100), i) for i, p in enumerate(positions)]
            envelope = pixel._envelope(points, ranks, 16, pixel._Budget(pixel.PixelAssemblyLimits()))
            ds, labels = pixel._sample(envelope, 16, pixel._Budget(pixel.PixelAssemblyLimits()))
            for x in range(16):
                best = min(points, key=lambda row: (row[1] + (x - row[0]) ** 2, ranks[row[2]]))
                self.assertEqual((ds[x], labels[x]), (best[1] + (x - best[0]) ** 2, best[2]))


class PixelFullIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = assemble()
        budget = pixel._Budget(pixel.PixelAssemblyLimits())
        raw = canonical_json(fixture())
        tables = pixel._read_base(raw, sha256(raw), budget)
        selected, _ = pixel._read_policy(policy(), '0.0.1', tables, budget)
        cls.candidates = pixel._candidates(tables, selected)

    def test_every_scanline_and_run_obeys_app_binary_contract(self):
        raw = self.result.rgb
        magic, count, runs = struct.unpack_from('<4sII', raw)
        self.assertEqual((magic, count), (b'SRGB', 16))
        self.assertEqual(len(raw), 12 + 65537 * 4 + runs * 5)
        self.assertGreaterEqual(runs, 65536)
        previous = 0
        self.assertEqual(struct.unpack_from('<I', raw, 12)[0], 0)
        for line in range(65536):
            end = struct.unpack_from('<I', raw, 16 + line * 4)[0]
            self.assertTrue(previous < end <= runs)
            prior_endpoint, prior_pair = -1, None
            for run in range(previous, end):
                endpoint, label0, label1 = struct.unpack_from('<BHH', raw, pixel._HEADER_BYTES + run * 5)
                self.assertTrue(prior_endpoint < endpoint <= 255)
                self.assertTrue(label0 < count and label1 < count)
                self.assertNotEqual((label0, label1), prior_pair)
                prior_endpoint, prior_pair = endpoint, (label0, label1)
            self.assertEqual(prior_endpoint, 255)
            previous = end
        self.assertEqual(previous, runs)

    def test_full_cube_samples_and_all_candidate_colors_match_reference(self):
        rng = random.Random(149)
        colors = [(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(4096)]
        colors += [(c['rgb'] >> 16, c['rgb'] >> 8 & 255, c['rgb'] & 255) for c in self.candidates]
        colors += [(r, g, b) for r in (0, 127, 128, 255) for g in (0, 127, 128, 255) for b in (0, 127, 128, 255)]
        for color in colors:
            for wall in (False, True):
                self.assertEqual(decode(self.result.rgb, color, wall), brute(self.candidates, color, wall))

    def test_deterministic_role_outputs_and_honest_source_boundary(self):
        second = assemble()
        self.assertEqual(second, self.result)
        self.assertEqual(set(second.objects), {'pixel.catalog', 'pixel.rgb'})
        self.assertEqual(second.evidence['rgbSha256'], sha256(second.rgb))
        self.assertEqual(second.evidence['catalogSha256'], sha256(second.objects['pixel.catalog']))
        self.assertEqual(second.evidence['policySha256'], sha256(canonical_json(policy())))
        self.assertEqual(second.evidence['baseSha256'], sha256(canonical_json(fixture())))
        self.assertFalse(second.evidence['sourceCompletenessEstablished'])
        self.assertEqual(second.evidence['scanlines'], 65536)
        self.assertEqual(second.evidence['colors'], 16777216)
        self.assertGreater(len({decode(second.rgb, (c, c, c)) for c in range(256)}), 1)

    def test_candidate_order_color_and_label_fingerprint_contract(self):
        renamed = fixture()
        renamed['tiles'][0][1] = '不同语言标签'
        self.assertEqual(assemble(renamed).catalog['rgbCatalogSha256'], self.result.catalog['rgbCatalogSha256'])
        reversed_policy = policy()
        reversed_policy['paintIds'].reverse()
        self.assertNotEqual(assemble(selection=reversed_policy).catalog['rgbCatalogSha256'], self.result.catalog['rgbCatalogSha256'])
        changed = fixture()
        changed['tiles'][0][2] = '#123457'
        self.assertNotEqual(assemble(changed).catalog['rgbCatalogSha256'], self.result.catalog['rgbCatalogSha256'])


class PixelRejectionTests(unittest.TestCase):
    def test_reject_invalid_hash_and_immutable_byte_boundary(self):
        raw = canonical_json(fixture())
        for data, digest in ((raw, '0' * 64), (raw, 'A' * 64), (raw, None),
                             (bytearray(raw), sha256(raw)), (memoryview(raw), sha256(raw)), (b'', sha256(b''))):
            with self.subTest(kind=type(data), digest=digest), self.assertRaises(pixel.PixelAssemblyError):
                pixel.assemble_pixel_resources(data, game_version='0.0.1', verified_base_sha256=digest, policy=policy())

    def test_reject_malformed_and_hostile_json_before_transform(self):
        cases = [b'not JSON', b'\xff', b'{"tiles":[],"tiles":[]}', b'{"tiles":NaN}',
                 b'[' * 2000 + b']' * 2000, b'{"tiles":[[[[]]]]}',
                 b'{"extra":"' + b'x' * 12289 + b'"}']
        for raw in cases:
            with self.subTest(raw=raw[:40]), mock.patch.object(pixel, '_build_index') as build:
                with self.assertRaises(pixel.PixelAssemblyError):
                    pixel.assemble_pixel_resources(raw, game_version='0.0.1', verified_base_sha256=sha256(raw), policy=policy())
                build.assert_not_called()

    def test_base_contract_fail_closed(self):
        mutations = [lambda b: b.update(extra=True), lambda b: b.update(tiles=[]),
                     lambda b: b['tiles'].append(b['tiles'][0]),
                     lambda b: b['tiles'][0].__setitem__(0, True),
                     lambda b: b['tiles'][0].__setitem__(0, -1),
                     lambda b: b['tiles'][0].__setitem__(0, 65536),
                     lambda b: b['tiles'][0].__setitem__(1, ''),
                     lambda b: b['tiles'][0].__setitem__(1, 5),
                     lambda b: b['tiles'][0].__setitem__(2, '#123'),
                     lambda b: b['tiles'][0].__setitem__(2, '#12345g'),
                     lambda b: b['tiles'][0].append(5)]
        for mutation in mutations:
            base = fixture()
            mutation(base)
            with self.subTest(base=base), mock.patch.object(pixel, '_build_index') as build:
                with self.assertRaises(pixel.PixelAssemblyError):
                    assemble(base)
                build.assert_not_called()

    def test_policy_is_versioned_closed_nonempty_and_foreign_key_checked(self):
        mutations = [lambda p: p.update(schemaVersion=True), lambda p: p.update(gameVersion='0.0.2'),
                     lambda p: p.update(consumerCommit='main'), lambda p: p.update(policyId='../bad'),
                     lambda p: p.update(algorithm='fixed-fake-rgb'), lambda p: p.update(extra=True),
                     lambda p: p.update(tileIds=[]), lambda p: p.update(wallIds=[]),
                     lambda p: p.update(paintIds=[]), lambda p: p.update(tileIds=[0, 0]),
                     lambda p: p.update(tileIds=[True]), lambda p: p.update(tileIds=[1]),
                     lambda p: p.update(paintIds=[0]), lambda p: p.update(paintIds=[31]),
                     lambda p: p.update(paintIds=[1, 1]), lambda p: p.update(wallIds='1')]
        for mutation in mutations:
            selection = policy()
            mutation(selection)
            with self.subTest(selection=selection), mock.patch.object(pixel, '_build_index') as build:
                with self.assertRaises(pixel.PixelAssemblyError):
                    assemble(selection=selection)
                build.assert_not_called()

    def test_each_budget_is_fatal_without_partial_result(self):
        cases = [('input_bytes', 4, 'INPUT_BYTES'), ('base_rows', 2, 'BASE_ROWS'),
                 ('candidates', 15, 'CANDIDATES'), ('working_bytes', 1, 'WORKING_BYTES'),
                 ('output_bytes', pixel._HEADER_BYTES + 65536 * 5 - 1, 'OUTPUT_BYTES'),
                 ('work', 10, 'WORK')]
        for field, value, code in cases:
            with self.subTest(field=field), self.assertRaisesRegex(pixel.PixelAssemblyBudgetExceeded, code):
                assemble(limits=replace(pixel.PixelAssemblyLimits(), **{field: value}))
        # Output budget exhaustion after some legal runs have already been made.
        with self.assertRaisesRegex(pixel.PixelAssemblyBudgetExceeded, 'OUTPUT_BYTES'):
            assemble(limits=replace(pixel.PixelAssemblyLimits(), output_bytes=pixel._HEADER_BYTES + 65536 * 5))

    def test_candidate_uint16_capacity_rejected_before_allocating_cube(self):
        base = {
            'tiles': [[i, f'Original tile {i}', '#123456'] for i in range(1070)],
            'walls': [[i, f'Original wall {i}', '#654321'] for i in range(1070)],
            'paints': [[i, f'Original paint {i}', '#abcdef'] for i in range(1, 31)],
        }
        selection = policy()
        selection.update(tileIds=list(range(1070)), wallIds=list(range(1070)), paintIds=list(range(1, 31)))
        with mock.patch.object(pixel, '_build_index') as build:
            with self.assertRaisesRegex(pixel.PixelAssemblyBudgetExceeded, 'CANDIDATES_LIMIT'):
                assemble(base, selection)
            build.assert_not_called()

    def test_runtime_timeout_and_cancellation(self):
        with mock.patch.object(pixel.time, 'monotonic', side_effect=(0, 181)):
            with self.assertRaisesRegex(pixel.PixelAssemblyBudgetExceeded, 'WALL_TIME'):
                assemble()
        calls = 0
        def cancel():
            nonlocal calls
            calls += 1
            if calls >= 30:
                raise RuntimeError('original cancellation')
        with self.assertRaisesRegex(RuntimeError, 'original cancellation'):
            assemble(checkpoint=cancel)
        self.assertEqual(calls, 30)

    def test_limit_validation(self):
        for changes in ({'candidates': 65536}, {'work': True}, {'input_bytes': 0},
                        {'wall_seconds': float('nan')}, {'wall_seconds': float('inf')},
                        {'wall_seconds': True}, {'wall_seconds': 601}, {'output_bytes': 33 * 1024 * 1024}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                pixel.PixelAssemblyLimits(**changes)


@unittest.skipUnless(APP_ROOT, 'Set PIXEL_ASSEMBLER_APP_ROOT to run the real app validator/generator')
class PixelActualAppParityTests(unittest.TestCase):
    def test_actual_app_blending_fingerprint_validator_and_complete_binary_parity(self):
        root = Path(APP_ROOT).resolve()
        self.assertTrue((root / 'shared/game/material-resource-contract.mjs').is_file())
        self.assertIsNotNone(shutil.which('node'))
        self.assertIsNotNone(shutil.which('g++'))
        result = assemble()
        # Original synthetic material rules suffice to exercise the real atomic validator.
        rules = {'materials': [[0, 0, 0, 0, 'Original synthetic', None, None, None, None, None, None, None, None, None, 0]],
                 'variants': [], 'shapes': [[0, 'original', 0, None, None, None, None, None, None, 'exact']],
                 'frameImportant': [False, False, False]}
        vectors = []
        for component in range(256):
            for color in ((component, 129, 243), (215, component, 17), (5, 181, component)):
                for paint_id in (0, 1, 13, 29, 30):
                    for wall in (False, True):
                        paint_rgb = (181, 239, 37)
                        vectors.append([color, paint_rgb, paint_id, wall, pixel._blend(color, paint_rgb, paint_id, wall)])
        with tempfile.TemporaryDirectory(prefix='original-pixel-proof-') as directory:
            output = Path(directory)
            (output / 'base.json').write_bytes(canonical_json(fixture()))
            (output / 'catalog.json').write_bytes(result.objects['pixel.catalog'])
            (output / 'rules.json').write_bytes(canonical_json(rules))
            (output / 'pixel.rgb').write_bytes(result.rgb)
            (output / 'vectors.json').write_bytes(canonical_json(vectors))
            code = r'''
import fs from 'node:fs';
import assert from 'node:assert/strict';
const [root,dir]=process.argv.slice(1);
const {pathToFileURL}=await import('node:url');
const {validateMaterialResourceSnapshot,materialCandidateFingerprint}=await import(pathToFileURL(root+'/shared/game/material-resource-contract.mjs'));
const {blendColors}=await import(pathToFileURL(root+'/shared/data/color.js'));
const base=JSON.parse(fs.readFileSync(dir+'/base.json'));
const catalog=JSON.parse(fs.readFileSync(dir+'/catalog.json'));
const rules=JSON.parse(fs.readFileSync(dir+'/rules.json'));
assert.equal(materialCandidateFingerprint(base,catalog),catalog.rgbCatalogSha256);
validateMaterialResourceSnapshot({gameVersion:'0.0.1',readJson:key=>({'materials.base':base,'materials.rules':rules,'pixel.catalog':catalog}[key]),readBytes:()=>fs.readFileSync(dir+'/pixel.rgb')});
for(const [base,paint,id,wall,expected] of JSON.parse(fs.readFileSync(dir+'/vectors.json'))) assert.deepEqual(blendColors(base,paint,id,wall),expected);
console.log('Actual app validator/fingerprint and 7,680 float32 paint vectors passed');
'''
            checked = subprocess.run(['node', '--input-type=module', '-e', code, str(root), str(output)],
                                     check=True, text=True, capture_output=True, timeout=30)
            self.assertIn('passed', checked.stdout)
            # Runs only trusted app generator code against original JSON data, never a game/CLR.
            subprocess.run(['node', str(root / 'scripts/generate-stable-rgb.mjs'), str(output / 'base.json'),
                            str(output / 'catalog.json'), str(output / 'app-index')],
                           check=True, text=True, capture_output=True, timeout=120)
            self.assertEqual((output / 'app-index/pixel.rgb.bin').read_bytes(), result.rgb)


if __name__ == '__main__':
    unittest.main()
