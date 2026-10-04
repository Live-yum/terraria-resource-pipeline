from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from resource_pipeline.sorting_priority_literals import _literal_array, extract_sorting_priority_literals
from resource_pipeline.item_texture_aliases import _Program, _Budget, ItemTextureAliasLimits, _linear_store
from resource_pipeline.static_il import Instruction, ILUnsupported
from resource_pipeline.security import PipelineError
from test_item_texture_aliases import fixture


def program():
    p = _Program(fixture(), _Budget(ItemTextureAliasLimits(), None))
    field = p.field('Terraria.ID.ItemID+Sets', 'TextureCopyLoad')
    part = _linear_store(p.method('Terraria.ID.ItemID+Sets', '.cctor'), field, 8)
    return p, part


def inline(part):
    def ins(op, operand=None): return Instruction(0, op, operand, 0)
    return [part[0], part[1], ins(0x18), part[3], ins(0x25), ins(0x16), ins(0x1a), ins(0x9e),
            ins(0x25), ins(0x17), ins(0x15), ins(0x9e), part[-2], part[-1]]


class SortingPriorityLiteralTests(unittest.TestCase):
    def test_original_rva_metadata_and_inline_pair(self):
        p, part = program(); pairs, evidence = _literal_array(p, part)
        self.assertTrue(pairs); self.assertGreater(evidence['dataBytes'], 0)
        self.assertEqual(_literal_array(p, inline(part))[0], [(4, -1)])

    def test_missing_duplicate_or_wrong_type_assignments_reject(self):
        p, part = program()
        for at, op in [(2, 0x19), (5, 0x17), (9, 0x16), (7, 0x9c), (8, 0x00)]:
            bad = inline(part); bad[at] = replace(bad[at], opcode=op)
            with self.assertRaises(ILUnsupported): _literal_array(p, bad)
        bad = inline(part); bad[3] = replace(bad[3], operand=0x01000001)
        with self.assertRaises(ILUnsupported): _literal_array(p, bad)
        with self.assertRaises(ILUnsupported): _literal_array(p, part[:-1])

    def test_unknown_game_input_never_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'Original.exe'; path.write_bytes(fixture())
            with self.assertRaisesRegex(PipelineError, 'source hash'): extract_sorting_priority_literals(path)
