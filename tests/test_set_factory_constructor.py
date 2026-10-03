"""Original synthetic constructor proofs; no input assemblies are executed."""
from dataclasses import replace
import hashlib
import unittest
from unittest.mock import patch, Mock

from dispatch_fixture import dispatch_pe, tok
from resource_pipeline.item_dispatch_sets import extract_item_dispatch_sets
from resource_pipeline.item_texture_aliases import ItemTextureAliasLimits
from resource_pipeline.security import PipelineError
from resource_pipeline.set_factory_constructor import extract_set_factory_constructor


class SetFactoryConstructorTests(unittest.TestCase):
    def proof(self, **opts):
        return extract_set_factory_constructor(dispatch_pe(**opts))

    def unusable(self, result):
        for key in ('complete', 'publishable', 'executedInput', 'runtimeSnapshotUsable'):
            self.assertFalse(result[key])

    def rejected(self, result):
        self.assertNotEqual('PROVEN_FRESH_CONSTRUCTOR_NORMAL_RETURN', result['status'])
        self.assertNotIn('proof', result)
        self.assertEqual([], result['metadataEvidence'])
        self.unusable(result)

    def test_exact_size_copy_and_hash_bound_evidence(self):
        raw = dispatch_pe()
        result = extract_set_factory_constructor(raw)
        self.assertEqual('PROVEN_FRESH_CONSTRUCTOR_NORMAL_RETURN', result['status'])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), result['inputSha256'])
        self.assertEqual('argument 1 unchanged', result['proof']['sizeValue'])
        self.assertFalse(result['proof']['rejectsZeroSize'])
        self.assertFalse(result['proof']['negativeSizeRejected'])
        self.assertFalse(result['proof']['thisEscapes'])
        self.assertEqual([], result['proof']['caches'])
        self.assertTrue(result['metadataEvidence'][0]['ilSha256'])
        self.unusable(result)

    def test_fresh_queue_and_lock_then_zero_guard(self):
        result = self.proof(pooled=True, fresh_constructor=True, reject_zero=True)
        self.assertEqual('PROVEN_FRESH_CONSTRUCTOR_NORMAL_RETURN', result['status'])
        proof = result['proof']
        self.assertTrue(proof['rejectsZeroSize'])
        self.assertFalse(proof['negativeSizeRejected'])
        self.assertEqual('_boolBufferCache', proof['caches'][0]['fieldName'])
        self.assertEqual('fresh distinct empty queue', proof['caches'][0]['state'])
        self.assertEqual('fresh distinct non-null object', proof['lock']['state'])
        self.assertFalse(proof['cacheStateAtLaterCallProven'])
        self.assertFalse(proof['wholeInitializerProven'])
        self.unusable(result)

    def test_missing_queue_or_lock_initialization_is_not_fresh(self):
        self.rejected(self.proof(pooled=True))
        self.rejected(self.proof(pooled=True, fresh_constructor=True,
                                constructor=lambda c: c[:11] + c[22:]))

    def test_side_effects_escape_extra_store_and_changed_size_rejected(self):
        mutations = [lambda c: c[:-1] + b'\x02' + tok(0x28, 0x06000007) + b'\x2a',
                     lambda c: c[:-1] + b'\x02\x03' + tok(0x7d, 0x04000007) + b'\x2a',
                     lambda c: c.replace(b'\x02\x03\x7d', b'\x02\x17\x7d'),
                     lambda c: c[:-1] + b'\x00\x2a',
                     lambda c: c.replace(tok(0x28, 0x0a000001), tok(0x28, 0x06000007))]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.rejected(self.proof(constructor=mutation))

    def test_queue_identity_signature_and_destination_checked(self):
        for opts in ({'key': b'notcore!'}, {'field_signatures': {8: b'\x06\x1c'}},
                     {'field_flags': {8: 0x11}}, {'type_flags': {4: 0x81}},
                     {'constructor': lambda c: c.replace(tok(0x73, 0x0a000007), tok(0x73, 0x0a000001))},
                     {'constructor': lambda c: c.replace(tok(0x7d, 0x04000009), tok(0x7d, 0x04000008))}):
            with self.subTest(opts=opts):
                self.rejected(self.proof(pooled=True, fresh_constructor=True, **opts))

    def test_each_typed_queue_is_bound_to_exact_element_type(self):
        for name, element in (('_boolBufferCache', 2), ('_intBufferCache', 8),
                              ('_ushortBufferCache', 7), ('_floatBufferCache', 12)):
            result = self.proof(pooled=True, fresh_constructor=True,
                                field_names={8: name}, queue_element=element)
            self.assertEqual('PROVEN_FRESH_CONSTRUCTOR_NORMAL_RETURN', result['status'])
            self.assertEqual(name, result['proof']['caches'][0]['fieldName'])
        self.rejected(self.proof(pooled=True, fresh_constructor=True,
                                 field_names={8: '_intBufferCache'}, queue_element=2))

    def test_member_parent_rid_cannot_alias_an_intrinsic_owner(self):
        from resource_pipeline.item_texture_aliases import _Program
        from resource_pipeline.static_il import ILUnsupported
        program = Mock()
        program.row.return_value = ([0x08000009, 0, 0], 0)
        program.meta.rows = {1: 8192}
        with self.assertRaises(ILUnsupported):
            _Program.member(program, 0x0a000001, 'System.Object', '.ctor', b'\x20\x00\x01')
        program.external_type.assert_not_called()

    def test_oversized_coded_typeref_does_not_alias_valid_token(self):
        self.rejected(self.proof(pooled=True, fresh_constructor=True,
            queue_signature=bytes.fromhex('15 12 c4 00 00 21 01 1d 02')))

    def test_overlapping_or_nonauto_field_layout_is_rejected(self):
        for flags in (0x11, 0x09, 0x01):
            self.rejected(self.proof(pooled=True, fresh_constructor=True,
                type_flags={4: flags}, field_layout=[(8, 7), (0, 8), (0, 9)]))
        for flags in (0x11, 0x09):
            self.rejected(self.proof(type_flags={4: flags}))

    def test_guard_branch_and_exception_are_closed(self):
        for mutation in (lambda c: c.replace(b'\x03\x2d\x0b', b'\x03\x2c\x0b'),
                         lambda c: c.replace(b'\x03\x2d\x0b', b'\x03\x2d\x00'),
                         lambda c: c.replace(tok(0x73, 0x0a000008), tok(0x73, 0x0a000001))):
            self.rejected(self.proof(pooled=True, fresh_constructor=True, reject_zero=True, constructor=mutation))

    def test_shared_budget_and_cancellation_fail_closed(self):
        for changes in ({'instructions': 1}, {'steps': 1}, {'method_bytes': 1}, {'total_method_bytes': 1}):
            self.rejected(extract_set_factory_constructor(dispatch_pe(), limits=replace(ItemTextureAliasLimits(), **changes)))
        error = PipelineError('cancelled')
        def cancel(): raise error
        with self.assertRaises(PipelineError) as caught:
            extract_set_factory_constructor(dispatch_pe(), checkpoint=cancel)
        self.assertIs(error, caught.exception)
        with patch('resource_pipeline.item_texture_aliases.time.monotonic', side_effect=[0, 60]):
            self.rejected(extract_set_factory_constructor(dispatch_pe()))

    def test_dispatch_preserves_separate_theorems_and_blockers(self):
        for fresh in (False, True):
            result = extract_item_dispatch_sets(dispatch_pe(pooled=True, fresh_constructor=fresh))
            self.assertEqual('PROVEN_CONDITIONAL_INITIALIZER_RECIPES', result['status'])
            ctor = result['factory']['freshConstructor']
            self.assertEqual('PROVEN_FRESH_CONSTRUCTOR_NORMAL_RETURN' if fresh else 'FACTORY_CTOR_UNINITIALIZED_FIELD', ctor['status'])
            self.assertFalse(ctor['cacheStateAtLaterCallProven'])
            self.assertEqual('UNPROVEN_AT_CALL_SITE', result['factory']['bufferLengthStatus'])
            self.assertFalse(result['cctorNormalReturnSnapshotUsable'])
            self.assertTrue(result['residualEffects']['unmodeledCalls'])


if __name__ == '__main__':
    unittest.main()
