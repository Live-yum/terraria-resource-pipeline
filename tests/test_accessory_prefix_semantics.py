from dataclasses import replace
from pathlib import Path
import struct
import tempfile
import unittest

from prefix_effect_fixture import fixture, body, token
from resource_pipeline.accessory_prefix_semantics import _prove, extract_accessory_prefix_semantics
from resource_pipeline.item_texture_aliases import _Program, _Budget, ItemTextureAliasLimits
from resource_pipeline.static_il import ILUnsupported


def proof(raw, limits=None):
    p = _Program(raw, _Budget(limits or ItemTextureAliasLimits(), None))
    return _prove(p, p.method('Terraria.Player', 'GrantPrefixBenefits'))


class AccessoryPrefixSemanticsTests(unittest.TestCase):
    def test_original_integer_and_float_percentage_effects(self):
        result = proof(fixture())
        self.assertEqual([r['consumerStats'] for r in result['effects']], [{'defense': 2}, {'moveBonus': 3}])
        self.assertEqual([r['prefixId'] for r in result['effects']], [3, 4])
        self.assertTrue(result['methodModelComplete'])
        self.assertTrue(result['allBytePrefixInputsCovered'])
        self.assertEqual(result['defaultEffectForUnlistedPrefix'], {})
        self.assertEqual(result['effects'][1]['fieldAdds'][0]['literalFloat32LE'], struct.pack('<f', .03).hex())

    def test_complete_equal_groups_project_once(self):
        cases = [(2, [('meleeCrit', 7), ('rangedCrit', 7), ('magicCrit', 7)]),
                 (250, [('meleeDamage', .07), ('rangedDamage', .07), ('magicDamage', .07), ('minionDamage', .07)])]
        result = proof(fixture(body(cases)))
        self.assertEqual([r['consumerStats'] for r in result['effects']], [{'critBonus': 7}, {'damageBonus': 7}])

    def test_reject_partial_mismatched_duplicate_or_unknown_effect_groups(self):
        cases = [[(2, [('meleeCrit', 1)])],
                 [(2, [('meleeCrit', 1), ('rangedCrit', 1), ('magicCrit', 2)])],
                 [(2, [('statDefense', 1), ('statDefense', 2)])],
                 [(2, [('statDefense', 1)]), (2, [('statDefense', 1)])],
                 [(256, [('statDefense', 1)])], [(2, [('moveSpeed', .031)])],
                 [(2, [('moveSpeed', float('nan'))])], [(2, [('statDefense', 0)])]]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ILUnsupported): proof(fixture(body(case)))

    def test_field_owner_signature_flags_and_layout(self):
        for options in ({'explicit_layout': True}, {'field_flags': 0x16}, {'field_flags': 0x26},
                        {'field_names': {1: 'statDefense'}}, {'field_names': {0: 'unknownStat'}},
                        {'signature': b'\x20\x01\x01\x1c'}):
            with self.subTest(options=options), self.assertRaises(ILUnsupported): proof(fixture(**options))

    def test_every_control_flow_and_store_is_bound(self):
        good = body()
        bad_branch = bytearray(good); struct.pack_into('<i', bad_branch, 12, 0)
        cases = [bytes(bad_branch), good.replace(token(0x7d, 0x04000002), token(0x7d, 0x04000003), 1),
                 good.replace(b'\x58', b'\x59', 1), good[:-1] + token(0x28, 0x06000001) + b'\x2a',
                 b'\x2a' + good, good[:-1], good.replace(b'\x02\x02', b'\x02\x03', 1)]
        for code in cases:
            with self.subTest(code=code.hex()), self.assertRaises(ILUnsupported): proof(fixture(code))

    def test_closed_public_profile_does_not_accept_named_fake_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'Terraria.exe'; path.write_bytes(fixture())
            with self.assertRaisesRegex(ILUnsupported, 'UNSUPPORTED_INPUT_PROFILE'):
                extract_accessory_prefix_semantics(path)

    def test_budget_and_cancellation(self):
        with self.assertRaises(ILUnsupported): proof(fixture(), replace(ItemTextureAliasLimits(), instructions=2))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'original.exe'; path.write_bytes(fixture())
            def cancel(): raise RuntimeError('original cancellation')
            with self.assertRaisesRegex(RuntimeError, 'original cancellation'):
                extract_accessory_prefix_semantics(path, cancel)

    def test_invalid_declared_stack_depth_cannot_prove_transform(self):
        original = fixture()
        p = _Program(original, _Budget(ItemTextureAliasLimits(), None))
        method = p.method('Terraria.Player', 'GrantPrefixBenefits')
        for value in (0, 1, 2, 3):
            raw = bytearray(original)
            struct.pack_into('<H', raw, method['evidence']['bodyOffset'] + 2, value)
            with self.subTest(maxstack=value):
                if value < 3:
                    with self.assertRaisesRegex(ILUnsupported, 'MAXSTACK'): proof(bytes(raw))
                else: self.assertTrue(proof(bytes(raw))['methodModelComplete'])


if __name__ == '__main__': unittest.main()
