from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from prefix_coefficient_fixture import fixture, code, arg, single, tok
from resource_pipeline.prefix_coefficient_semantics import _prove, extract_prefix_coefficient_semantics
from resource_pipeline.item_texture_aliases import _Program, _Budget, ItemTextureAliasLimits
from resource_pipeline.static_il import ILUnsupported


def proof(raw, count=4, limits=None):
    p = _Program(raw, _Budget(limits or ItemTextureAliasLimits(), None))
    return _prove(p, p.method('Terraria.Item', 'TryGetPrefixStatMultipliersForItem'), count)


class PrefixCoefficientSemanticsTests(unittest.TestCase):
    def test_all_out_cells_and_both_tail_branches(self):
        result = proof(fixture())
        self.assertTrue(result['coefficientModelComplete'])
        self.assertEqual(len(result['records']), 4)
        for row in result['records']:
            self.assertEqual(row['coefficients']['dmg'], 1.25 if row['prefixId'] == 2 else 1)
            self.assertEqual(row['coefficients']['kb'], 1)
            self.assertEqual(row['coefficients']['arpen'], 0)
        self.assertTrue(result['tail']['selectedOutCellsPreserved'])
        self.assertFalse(result['tail']['eligibilityResultProven'])
        self.assertFalse(result['tail']['priceMultiplierProven'])

    def test_tail_cannot_write_coefficient_or_accept_foreign_round(self):
        for raw in (fixture(code(extra_tail=arg(2) + single(9) + b'\x56')),
                    fixture(round_name='Mutate'), fixture(core_key=b'bad key!'),
                    fixture(code(extra_tail=tok(0x28, 0x06000001)))):
            with self.subTest(raw=raw[-32:].hex()), self.assertRaises(ILUnsupported): proof(raw)

    def test_definite_initialization_finite_literals_and_stack(self):
        for raw in (fixture(code(missing=9)), fixture(code(change=float('nan'))),
                    fixture(maxstack=1), fixture(maxstack=0), fixture(signature=b'\x20\x00\x02')):
            with self.subTest(raw=raw[-32:].hex()), self.assertRaises(ILUnsupported): proof(raw)
        self.assertTrue(proof(fixture(maxstack=2))['coefficientModelComplete'])

    def test_backward_branch_and_wrong_ref_type_rejected(self):
        original = code()
        # The original dispatch uses a long bne; turn it into a backward edge.
        import struct
        at = original.index(b'\x40'); changed = bytearray(original)
        struct.pack_into('<i', changed, at + 1, -5)
        for value in (bytes(changed), original.replace(arg(2) + single(1) + b'\x56', arg(2) + single(1) + b'\x54', 1),
                      original[:-1], original + b'\x2a'):
            # A final unreachable ret is benign for selected-out preservation;
            # the other mutations must be rejected.
            if value == original + b'\x2a': continue
            with self.subTest(value=value.hex()), self.assertRaises(ILUnsupported): proof(fixture(value))

    def test_domain_budget_and_closed_public_profile(self):
        for count in (0, 257, True):
            with self.assertRaises(ILUnsupported): proof(fixture(), count)
        with self.assertRaises(ILUnsupported): proof(fixture(), limits=replace(ItemTextureAliasLimits(), instructions=2))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'Terraria.exe'; path.write_bytes(fixture())
            with self.assertRaisesRegex(ILUnsupported, 'UNSUPPORTED_INPUT_PROFILE'):
                extract_prefix_coefficient_semantics(path)
            def cancel(): raise RuntimeError('original cancelled coefficient proof')
            with self.assertRaisesRegex(RuntimeError, 'cancelled coefficient proof'):
                extract_prefix_coefficient_semantics(path, cancel)


if __name__ == '__main__': unittest.main()
