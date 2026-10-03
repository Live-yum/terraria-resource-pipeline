"""Synthetic dispatch recipe tests; no proprietary binaries or tables."""
from dataclasses import replace
import hashlib
import unittest
from unittest.mock import patch

from dispatch_fixture import dispatch_pe, tok, ldc
from resource_pipeline.item_dispatch_sets import ItemDispatchSetLimits, extract_item_dispatch_sets
from resource_pipeline.security import PipelineError


def proof(**options):
    return extract_item_dispatch_sets(dispatch_pe(**options))


class DispatchSetTests(unittest.TestCase):
    def assert_unusable(self, result):
        for key in ('complete', 'finalItemDefaults', 'publishable', 'executedInput',
                    'cctorNormalReturnSnapshotUsable', 'runtimeSnapshotUsable'):
            self.assertFalse(result[key], key)

    def test_four_literal_recipes_and_consumer_bindings(self):
        data = dispatch_pe()
        result = extract_item_dispatch_sets(data)
        self.assertEqual('PROVEN_CONDITIONAL_INITIALIZER_RECIPES', result['status'])
        self.assertEqual(hashlib.sha256(data).hexdigest(), result['inputSha256'])
        self.assertEqual(13, result['declaredDomain']['count'])
        self.assertEqual(4, len(result['sets']))
        self.assertEqual('PROVEN_DIRECT_READ_BINDINGS_ONLY', result['consumer']['status'])
        self.assertEqual(4, len(result['consumer']['reads']))
        for recipe in result['sets']:
            self.assertEqual({'minInclusive': 0, 'maxExclusive': 13}, recipe['domain'])
            self.assertFalse(recipe['defaultValue'])
            self.assertTrue(recipe['overrideValue'])
            self.assertEqual(recipe['literalCount'] * 4, recipe['rvaEvidence']['dataBytes'])
            self.assertFalse(recipe['cctorNormalReturnSnapshotUsable'])
            self.assertFalse(recipe['runtimeSnapshotUsable'])
        self.assert_unusable(result)

    def test_duplicate_assignments_are_idempotent_and_preserve_literal_flow(self):
        first = proof()['sets'][0]
        self.assertEqual([1, 4, 4], first['literalIds'])
        self.assertEqual([1, 4], first['distinctOverrideIds'])
        self.assertEqual((3, 2, 1), (first['literalCount'], first['distinctCount'], first['duplicateCount']))

    def test_true_default_means_listed_ids_are_false(self):
        result = proof(explicit={0: True, 1: False})
        self.assertEqual('PROVEN_CONDITIONAL_INITIALIZER_RECIPES', result['status'])
        self.assertTrue(result['sets'][0]['defaultValue'])
        self.assertFalse(result['sets'][0]['overrideValue'])
        self.assertFalse(result['sets'][1]['defaultValue'])
        self.assertTrue(result['sets'][1]['overrideValue'])

    def test_unmodeled_calls_remain_explicit_effect_blockers(self):
        result = proof(count_tail=tok(0x28, 0x0a000001), sets_tail=tok(0x28, 0x0a000001))
        self.assertEqual('PROVEN_CONDITIONAL_INITIALIZER_RECIPES', result['status'])
        effects = result['residualEffects']
        self.assertEqual('UNSUPPORTED_WHOLE_CCTOR_EFFECT_CLOSURE', effects['status'])
        self.assertEqual({'UNSUPPORTED_EFFECT_SUMMARY'}, {r['status'] for r in effects['unmodeledCalls']})
        self.assert_unusable(result)

    def test_factory_transform_side_effect_is_rejected(self):
        result = proof(factory=b'\x02' + tok(0x28, 0x06000006) + tok(0x80, 0x04000003) + b'\x14\x2a')
        self.assertNotEqual('PROVEN_CONDITIONAL_INITIALIZER_RECIPES', result['status'])
        self.assertEqual([], result['sets'])
        self.assert_unusable(result)

    def test_wrong_overload_and_wrapper_default_are_rejected(self):
        for opts in ({'signatures': {4: b'\x20\x01\x1d\x02\x1d\x07'}},
                     {'wrapper': b'\x02\x17\x03' + tok(0x28, 0x06000005) + b'\x2a'}):
            with self.subTest(opts=opts):
                result = proof(**opts)
                self.assertEqual([], result['sets'])
                self.assert_unusable(result)

    def test_bad_intrinsic_identity_signature_and_layout_are_rejected(self):
        for opts in ({'initialize_name': 'OriginalUntrustedHelper'}, {'key': b'notcore!'},
                     {'type_flags': {7:0x130}}, {'type_flags': {7:0x190}},
                     {'element_type': 0x01000003}, {'layout_delta': {0: 4}},
                     {'field_signatures': {9: b'\x06\x08'}}, {'field_flags': {9: 0x11}},
                     {'factory_locals': b'\x07\x03\x1d\x08\x08\x08'}):
            with self.subTest(opts=opts):
                result = proof(**opts)
                self.assertEqual([], result['sets'])
                self.assert_unusable(result)

    def test_negative_equal_to_count_and_unbounded_domain_are_rejected(self):
        for ids in ((-1, 4), (13, 4)):
            result = proof(ids=(ids, (3, 8), (2, 11), (6,)))
            self.assertEqual('DISPATCH_LITERAL_ID_OUT_OF_DOMAIN', result['status'])
            self.assertEqual([], result['sets'])
        for count in (0, -5, 100001):
            self.assertEqual('DISPATCH_ITEM_COUNT_LIMIT', proof(count=count)['status'])

    def test_duplicate_target_store_and_field_address_are_rejected(self):
        for tail in (b'\x14' + tok(0x80, 0x04000003), tok(0x7f, 0x04000003) + b'\x26'):
            result = proof(sets_tail=tail)
            self.assertEqual([], result['sets'])
            self.assert_unusable(result)

    def test_unknown_getter_profile_is_explicitly_unsupported(self):
        result = proof(getter=b'\x14\x2a')
        self.assertEqual([], result['sets'])
        self.assert_unusable(result)

    def test_missing_consumer_does_not_become_runtime_closure(self):
        result = proof(consumer=b'\x2a')
        self.assertEqual('PROVEN_CONDITIONAL_INITIALIZER_RECIPES', result['status'])
        self.assertEqual('DISPATCH_CONSUMER_READ_MISSING', result['consumer']['status'])
        self.assertFalse(result['consumer']['controlFlowOrRuntimeClosure'])
        self.assert_unusable(result)

    def test_optional_consumer_cannot_swallow_shared_work_limits(self):
        for changes in ({'instructions':95}, {'total_method_bytes':257}):
            limits = replace(ItemDispatchSetLimits(), **changes)
            result = extract_item_dispatch_sets(dispatch_pe(), limits=limits)
            self.assertIn('LIMIT', result['status'])
            self.assertEqual([],result['sets'])
            self.assertLessEqual(result['work']['decodedInstructions'],limits.instructions)
            self.assertLessEqual(result['work']['decodedMethodBytes'],limits.total_method_bytes)
            self.assert_unusable(result)

    def test_method_instruction_step_and_literal_budgets(self):
        data = dispatch_pe()
        for field, value in (('method_bytes', 20), ('total_method_bytes', 30), ('instructions', 10),
                             ('steps', 10), ('literal_ids', 2), ('literal_bytes', 8)):
            with self.subTest(field=field):
                result = extract_item_dispatch_sets(data, limits=replace(ItemDispatchSetLimits(), **{field: value}))
                self.assertEqual([], result['sets'])
                self.assert_unusable(result)

    def test_evidence_budget_discards_all_usable_tables(self):
        result = extract_item_dispatch_sets(dispatch_pe(count=1200, ids=(tuple(range(1000)), (1,), (2,), (3,))),
                                           limits=replace(ItemDispatchSetLimits(), evidence_bytes=16384))
        self.assertEqual([], result['sets'])
        self.assert_unusable(result)

    def test_cancel_propagates_original_even_for_parser_exception_types(self):
        for exception in (RuntimeError('stop'), PipelineError('stop'), ValueError('stop')):
            def cancel(): raise exception
            with self.subTest(type=type(exception)), self.assertRaises(type(exception)) as captured:
                extract_item_dispatch_sets(dispatch_pe(), checkpoint=cancel)
            self.assertIs(exception, captured.exception)

    def test_time_limit_and_invalid_limit_values(self):
        with patch('resource_pipeline.item_texture_aliases.time.monotonic', side_effect=[0] + [5] * 10):
            result = extract_item_dispatch_sets(dispatch_pe(), limits=replace(ItemDispatchSetLimits(), wall_seconds=1))
        self.assertIn('TIME_LIMIT', result['status'])
        self.assert_unusable(result)
        for opts in ({'wall_seconds': float('nan')}, {'wall_seconds': float('inf')}, {'steps': True},
                     {'literal_ids': 0}, {'evidence_bytes': 4096}):
            with self.subTest(opts=opts), self.assertRaises(ValueError): ItemDispatchSetLimits(**opts)

    def test_file_input_limits_and_malformed_input(self):
        with self.assertRaises(PipelineError): extract_item_dispatch_sets(bytearray(dispatch_pe()))
        with self.assertRaises(PipelineError):
            extract_item_dispatch_sets(dispatch_pe(), limits=replace(ItemDispatchSetLimits(), input_bytes=1))
        result = extract_item_dispatch_sets(b'not a PE file')
        self.assertEqual('DISPATCH_MALFORMED_INPUT', result['status'])
        self.assertEqual([], result['sets'])
        self.assert_unusable(result)

    def test_pooled_bool_getter_and_finally_are_bound_without_freshness_claim(self):
        result = proof(pooled=True)
        self.assertEqual('PROVEN_CONDITIONAL_INITIALIZER_RECIPES', result['status'])
        self.assertIn('dequeue', result['factory']['bufferProvider'])
        self.assertEqual('UNPROVEN_AT_CALL_SITE', result['factory']['bufferLengthStatus'])
        self.assertTrue(any('exceptionSectionSha256' in r for r in result['metadataEvidence']))
        self.assert_unusable(result)
        for options in ({'dequeue_name': 'OriginalMutator'},
                        {'getter_eh': lambda b: b[:4] + b'\x00\x00' + b[6:]}):
            with self.subTest(options=options):
                result = proof(pooled=True, **options)
                self.assertEqual([], result['sets'])
                self.assert_unusable(result)

    def test_wrong_factory_eh_and_control_flow_profile(self):
        result = proof(header_flags={5: 0x301b})
        self.assertEqual([], result['sets'])
        self.assertIn('EXCEPTION', result['status'])


if __name__ == '__main__': unittest.main()
