"""Adversarial synthetic fresh-instance evidence, without CLR execution."""
from dataclasses import replace
import hashlib
import unittest
from unittest.mock import patch

from baseline_fixture import baseline_pe, ldc, put_field, token
from resource_pipeline.item_baseline import BaselineProgram, BaselineEvaluator, extract_fresh_item_baseline
from resource_pipeline.security import PipelineError, canonical_json
from resource_pipeline.server_semantics import _Metadata, _types, SemanticLimits
from resource_pipeline.static_il import AbstractEvaluator, MetadataProgram, StaticILLimits


def proof(*, limits=StaticILLimits(), reset_type=0, checkpoint=None, **options):
    data = baseline_pe(**options)
    meta = _Metadata(data, SemanticLimits())
    return extract_fresh_item_baseline(meta, _types(meta), limits=limits, reset_type=reset_type,
                                     checkpoint=checkpoint, input_sha256=hashlib.sha256(data).hexdigest())


def by_name(result):
    return {row['fieldName']: row for row in result['fields']}


class FreshBaselineTests(unittest.TestCase):
    def test_fresh_zero_constructor_then_reset_and_all_seven_readonly_prices(self):
        result = proof(reset_type=29)
        self.assertEqual('PROVEN_FRESH_PRIMITIVE_BASELINE', result['status'])
        self.assertTrue(result['baselineComplete'])
        self.assertEqual(15, result['provenPrimitiveFields'])
        fields = by_name(result)
        for name in ('width', 'height', 'stringColor'):
            self.assertEqual(0, fields[name]['value'])
            self.assertEqual('fresh-zero', fields[name]['evidence']['phase'])
            self.assertTrue(fields[name]['evidence']['signatureSha256'])
        self.assertEqual(29, fields['OriginalCounter']['value'])
        self.assertEqual('reset', fields['OriginalCounter']['evidence']['phase'])
        self.assertEqual(1.25, fields['OriginalRatio']['value'])
        self.assertEqual('constructor', fields['OriginalRatio']['evidence']['phase'])
        for n in range(7):
            field = fields['OriginalCost' + str(n)]
            self.assertEqual((n + 2) * 11 + 7, field['value'])
            self.assertEqual('constructor', field['evidence']['phase'])
            self.assertIn('0x06000003', field['evidence']['dependencyMethodTokens'])
        self.assertFalse(result['complete'])
        self.assertFalse(result['finalItemDefaults'])
        self.assertFalse(result['executedInput'])
        self.assertIn('fresh allocation only', result['initialObjectPolicy'])
        self.assertEqual([29], result['resetArguments'])
        self.assertEqual(1, len(result['baseConstructorIntrinsics']))
        self.assertNotIn('0x06000006', [m['methodToken'] for m in result['methods']])

    def test_reused_unknown_stage_does_not_receive_fresh_seed(self):
        data = baseline_pe(reset_code=put_field(4, b'\x03') + b'\x2a')
        meta = _Metadata(data, SemanticLimits())
        stage = AbstractEvaluator(MetadataProgram(meta, _types(meta))).stage(0x06000002, 29)
        self.assertEqual(['OriginalCounter'], [f['fieldName'] for f in stage['fields']])
        self.assertEqual('unknown', stage['initialInstanceFields'])
        with self.assertRaises(TypeError):
            extract_fresh_item_baseline(meta, _types(meta), initial_state={'width': 17})

    def test_opaque_nullable_and_null_reference_are_excluded(self):
        result = proof()
        fields = {f['fieldName']: f for f in result['excludedFields']}
        self.assertEqual({'OriginalReference', 'OriginalOptional', 'OriginalOpaque'}, set(fields))
        self.assertEqual('null', fields['OriginalReference']['trackedState'])
        self.assertEqual('opaque-zero', fields['OriginalOptional']['trackedState'])
        self.assertEqual('opaque-zero', fields['OriginalOpaque']['trackedState'])
        for field in fields.values():
            self.assertEqual('EXCLUDED_NONPRIMITIVE', field['status'])
            self.assertNotIn('value', field)

    def test_bare_primitive_zero_storage_has_correct_boolean_and_numeric_types(self):
        for element in range(2, 14):
            with self.subTest(element=element):
                field = by_name(proof(field_signatures={1: bytes((6, element))}))['width']
                self.assertEqual('fresh-zero', field['evidence']['phase'])
                self.assertEqual(0, field['value'])
                self.assertIs(type(field['value']), bool if element == 2 else float if element >= 12 else int)

    def test_closed_scalar_initializers_carry_actual_body_and_dependency_evidence(self):
        item_cctor = token(0x7e, 0x04000014) + b'\x17\x58' + token(0x80, 0x04000013) + b'\x2a'
        result = proof(item_cctor=item_cctor)
        self.assertEqual(38, by_name(result)['OriginalMaximum']['value'])
        self.assertEqual(37, by_name(result)['OriginalCategory']['value'])
        self.assertEqual([], result['unresolvedStaticReads'])
        self.assertEqual(2, len(result['staticInitializers']))
        for record in result['staticInitializers']:
            self.assertEqual('CLOSED_SCALAR_INITIALIZER', record['status'])
            self.assertTrue(record['fields'][0]['ilSha256'])
            self.assertTrue(record['fields'][0]['signatureSha256'])
            self.assertTrue(record['fields'][0]['mutable'])
            self.assertIn('no subsequent', record['phase'])

    def test_factory_after_static_write_invalidates_prefix_constant(self):
        for call in (0x28, 0x6f, 0x73):
            code = ldc(83) + token(0x80, 0x04000013) + token(call, 0x0a000002) + b'\x2a'
            with self.subTest(call=call):
                result = proof(item_cctor=code)
                field = by_name(result)['OriginalMaximum']
                self.assertEqual('UNKNOWN', field['status'])
                self.assertNotIn('value', field)
                self.assertFalse(result['baselineComplete'])
                self.assertEqual('PARTIAL_FRESH_PRIMITIVE_BASELINE', result['status'])
                initializer = next(r for r in result['staticInitializers'] if r['declaringType'] == 'Terraria.Item')
                self.assertEqual([], initializer['fields'])
                self.assertEqual(83, initializer['prefixFields'][0]['value'])
                self.assertEqual('UNSUPPORTED_BASELINE_STATIC_EFFECT', initializer['diagnostic']['code'])
                self.assertEqual(['0x04000013', '0x04000014'], result['unresolvedStaticReads'])

    def test_unknown_overwrites_constructor_value_and_never_falls_back_to_zero(self):
        reset = put_field(4, token(0x7e, 0x04000013)) + b'\x2a'
        result = proof(reset_code=reset, item_cctor=b'\x2a')
        field = by_name(result)['OriginalCounter']
        self.assertEqual('UNKNOWN', field['status'])
        self.assertEqual('UNKNOWN_VALUE', field['reason'])
        self.assertEqual('reset', field['evidence']['phase'])
        self.assertNotIn('value', field)
        self.assertEqual(0, by_name(result)['width']['value'])
        result = proof(method_names={5: 'OriginalNotInitializer'})
        self.assertEqual('UNKNOWN', by_name(result)['OriginalMaximum']['status'])

    def test_static_dependency_cycle_and_foreign_writes_are_not_certified(self):
        result = proof(item_cctor=token(0x7e, 0x04000014) + token(0x80, 0x04000013) + b'\x2a',
                       scalar_cctor=token(0x7e, 0x04000013) + token(0x80, 0x04000014) + b'\x2a')
        self.assertEqual('UNKNOWN', by_name(result)['OriginalCategory']['status'])
        self.assertEqual('UNKNOWN', by_name(result)['OriginalMaximum']['status'])
        result = proof(item_cctor=ldc(5) + token(0x80, 0x04000014) + b'\x2a')
        self.assertEqual('UNKNOWN', by_name(result)['OriginalMaximum']['status'])
        self.assertIn('BASELINE_FOREIGN_STATIC_WRITE', [r.get('diagnostic', {}).get('code') for r in result['staticInitializers']])

    def assert_rejected(self, expected=None, **options):
        result = proof(**options)
        self.assertEqual([], result['fields'])
        self.assertFalse(result['baselineComplete'])
        self.assertFalse(result['finalItemDefaults'])
        if expected is not None:
            self.assertEqual(expected, result['status'])
        return result

    def test_root_base_identity_and_layout_are_not_name_only(self):
        for options in ({'base_coded': 0}, {'base_coded': 16}, {'base_name': 'Entity'},
                        {'base_namespace': 'Pretend'}, {'base_scope': 0}, {'base_scope': 4},
                        {'assembly_name': 'Pretend.Core'}, {'assembly_key': bytes(8)},
                        {'assembly_flags': 1}, {'assembly_culture': 'en'}, {'item_flags': 0x21},
                        {'item_flags': 0x11}, {'item_flags': 9}, {'field_layout': True},
                        {'class_layout': True}, {'generic_owner': 4}):
            with self.subTest(options=options):
                self.assert_rejected(**options)

    def test_base_constructor_exact_target_signature_and_once_only(self):
        for options in ({'member_parent': 17}, {'member_name': 'FakeCtor'},
                        {'member_signature': b'\x20\x01\x01\x08'}, {'base_call': 0x06000006},
                        {'ctor_code': b'\x2a'},
                        {'ctor_code': (b'\x02' + token(0x28, 0x0a000001)) * 2 + b'\x2a'},
                        {'reset_code': b'\x02' + token(0x28, 0x0a000001) + b'\x2a'}):
            with self.subTest(options=options):
                self.assert_rejected(**options)

    def test_wrong_overloads_native_virtual_and_bad_signatures_rejected(self):
        for options in ({'method_names': {6: 'ResetStats'}}, {'method_signatures': {1: b'\x20\x01\x01\x08'}},
                        {'method_signatures': {2: b'\x20\x00\x01'}}, {'method_signatures': {6: b'\x20\x00\x01'}},
                        {'method_flags': {1: 6}}, {'method_impl': {1: 1}}, {'method_impl': {2: 4}},
                        {'method_flags': {2: 0xc6}}, {'method_flags': {3: 0x2096}},
                        {'method_flags': {1: 0x1c86}}, {'method_flags': {2: 0x486}},
                        {'generic_owner': 3}, {'generic_owner': 5}, {'generic_owner': 7},
                        {'method_signatures': {2: b'\x20\x01\x01\x18'}}):
            with self.subTest(options=options):
                self.assert_rejected(**options)

    def test_unverified_field_types_are_not_seeded(self):
        for signature in (b'\x06\x18', b'\x06\x19', b'\x06\x0f\x08', b'\x06\x10\x08',
                          b'\x06\x1f\x05\x08', b'\x06\x08\x00', b'\x06\x1d\x08', b'\x06'):
            with self.subTest(signature=signature):
                self.assert_rejected(field_signatures={1: signature})
        self.assert_rejected(field_flags={1: 0x46})

    def test_field_addresses_are_owned_exact_typed_and_immediately_consumed(self):
        address = b'\x02' + token(0x7c, 0x04000008)
        cases = [b'\x14' + token(0x7c, 0x04000008) + token(0xfe15, 0x1b000001),
                 b'\x02' + token(0x7c, 0x04000014) + token(0xfe15, 0x1b000001),
                 b'\x02' + token(0x7c, 0x04000001) + token(0xfe15, 0x02000004),
                 address + token(0xfe15, 0x02000004),
                 address + b'\x25' + token(0xfe15, 0x1b000001),
                 address + b'\x26', address + token(0x28, 0x0a000002),
                 address + b'\x0a', address + b'\x2b\x00' + token(0xfe15, 0x1b000001),
                 token(0xfe15, 0x1b000001)]
        for code in cases:
            with self.subTest(code=code.hex()):
                self.assert_rejected(reset_code=code + b'\x2a')
        self.assert_rejected(typespec=b'\x15\x11\x09\x01\x0a')

    def test_reference_setter_is_exact_and_cannot_escape_this_or_call_objects(self):
        for code in (b'\x02\x03' + token(0x7d, 0x04000007) + token(0x28, 0x0a000002) + b'\x2a',
                     b'\x02\x03' + token(0x7d, 0x04000008) + b'\x2a', b'\x2a'):
            self.assert_rejected(setter_code=code)
        self.assert_rejected(reset_code=b'\x02\x02' + token(0x28, 0x06000004) + b'\x2a')
        self.assert_rejected(reset_code=put_field(7, b'\x02') + b'\x2a')
        self.assert_rejected(reset_code=b'\x02' + token(0x6f, 0x0a000002) + b'\x2a')
        self.assert_rejected(reset_code=b'\x02' + token(0x7b, 0x04000007) + b'\x26\x2a')
        self.assert_rejected(max_stack={4: 1})

    def test_readonly_constructor_fields_cannot_be_reset_or_set_by_reference(self):
        self.assert_rejected('BASELINE_READONLY_FIELD_WRITE', reset_code=put_field(12, ldc(4)) + b'\x2a')
        self.assert_rejected(field_flags={7: 0x26})

    def test_unknown_call_or_effect_returns_prefix_only(self):
        result = self.assert_rejected(reset_code=put_field(1, ldc(7)) + token(0x28, 0x0a000002) + b'\x2a')
        self.assertTrue(result['prefixFields'])
        self.assertEqual(7, next(f['value'] for f in result['prefixFields'] if f['fieldName'] == 'width'))
        self.assert_rejected(reset_code=b'\x02\x03' + token(0x28, 0x06000002) + b'\x2a')
        self.assert_rejected(helper_code=b'\x02\x2a' + token(0x28, 0x0a000002))

    def test_foreign_pure_helper_does_not_bypass_type_initialization_effects(self):
        self.assert_rejected('UNSUPPORTED_BASELINE_HELPER_OWNER', scalar_first_method=6,
                             method_names={6: 'OriginalForeignPure'}, method_flags={6: 0x96},
                             method_signatures={6: b'\x00\x01\x08\x08'}, typed_ctor=b'\x02\x2a',
                             reset_code=put_field(4, ldc(9) + token(0x28, 0x06000006)) + b'\x2a')

    def test_per_method_total_metadata_stack_and_output_budgets(self):
        for limits in (replace(StaticILLimits(), method_steps=3), replace(StaticILLimits(), total_steps=30),
                       replace(StaticILLimits(), stack=2), replace(StaticILLimits(), call_depth=1),
                       replace(StaticILLimits(), methods=1), replace(StaticILLimits(), method_index=25),
                       replace(StaticILLimits(), fields=3), replace(StaticILLimits(), metadata_name_bytes=10),
                       replace(StaticILLimits(), method_bytes=20), replace(StaticILLimits(), total_method_bytes=250),
                       replace(StaticILLimits(), total_decoded_instructions=30)):
            with self.subTest(limits=limits):
                result = self.assert_rejected(limits=limits)
                self.assertTrue(result['status'].endswith('_LIMIT'), result['status'])
        result = self.assert_rejected(limits=replace(StaticILLimits(), evidence_bytes=16384))
        self.assertLessEqual(len(canonical_json(result)), 16384)
        self.assertEqual('TOTAL_EVIDENCE_BYTE_LIMIT', result['status'])

    def test_checkpoint_cancellation_errors_and_deadline_propagate(self):
        calls = 0
        def cancel():
            nonlocal calls
            calls += 1
            if calls == 40:
                raise PipelineError('original cancellation')
        with self.assertRaisesRegex(PipelineError, 'original cancellation'):
            proof(checkpoint=cancel)
        with patch('resource_pipeline.item_baseline.time.monotonic', side_effect=[1, 999]):
            result = proof()
        self.assertEqual('TOTAL_TIME_LIMIT', result['status'])
        self.assertEqual([], result['fields'])
        for value in (True, 1.0, '1', -0x80000001, 0x80000000):
            with self.assertRaises(PipelineError):
                proof(reset_type=value)

    def test_exceptions_loops_malformed_il_and_metadata_errors_fail_closed(self):
        self.assert_rejected(reset_code=b'\x2b\xfe')
        self.assert_rejected(reset_code=b'\x20\x01')
        self.assert_rejected(header_flags={2: 0x301b})
        self.assert_rejected(header_flags={2: 0x3033})
        self.assert_rejected(locals=b'\x07\x01\x1c')
        with self.assertRaises(PipelineError):
            proof(base_coded=0xfffd)


    def test_unclosed_nested_initializer_cannot_certify_callers_prefix(self):
        result=proof(ctor_code=b'\x02'+token(0x28,0x0a000001)+b'\x2a',
            item_cctor=ldc(1)+token(0x80,0x04000013)+token(0x7e,0x04000014)+b'\x26\x2a',
            scalar_cctor=ldc(2)+token(0x80,0x04000013)+ldc(3)+token(0x80,0x04000014)+b'\x2a',
            reset_code=put_field(10,token(0x7e,0x04000013))+b'\x2a')
        self.assertEqual('UNKNOWN',by_name(result)['OriginalMaximum']['status'])
        self.assertFalse(result['baselineComplete'])
        item=next(r for r in result['staticInitializers'] if r['declaringType']=='Terraria.Item')
        self.assertEqual('BASELINE_UNCLOSED_STATIC_DEPENDENCY',item['diagnostic']['code'])
        self.assertEqual([],item['fields'])
        self.assertTrue(all(not r['snapshotUsable'] for r in result['staticInitializers']))

    def test_cached_unclosed_dependency_still_taints_initializer_caller(self):
        data=baseline_pe(item_cctor=token(0x7e,0x04000014)+b'\x26'+ldc(1)+token(0x80,0x04000013)+b'\x2a',
                         scalar_cctor=token(0x28,0x0a000002)+b'\x2a')
        meta=_Metadata(data,SemanticLimits());program=BaselineProgram(meta,_types(meta))
        evaluator=BaselineEvaluator(program)
        self.assertEqual('unknown',evaluator.read_static(0x04000014).kind)
        self.assertEqual('unknown',evaluator.read_static(0x04000013).kind)
        item=next(r for r in program.initializer_records if r['declaringType']=='Terraria.Item')
        self.assertEqual('BASELINE_UNCLOSED_STATIC_DEPENDENCY',item['diagnostic']['code'])

    def test_closed_unknown_dependency_does_not_taint_independent_constant(self):
        result=proof(item_cctor=token(0x7e,0x04000014)+b'\x26'+ldc(9)+token(0x80,0x04000013)+b'\x2a',scalar_cctor=b'\x2a')
        self.assertEqual(9,by_name(result)['OriginalMaximum']['value'])
        self.assertTrue(all(r['snapshotUsable'] for r in result['staticInitializers']))

    def test_later_unclosed_initializer_invalidates_prior_static_dependent_fields(self):
        result=proof(ctor_code=b'\x02'+token(0x28,0x0a000001)+b'\x2a',item_cctor=ldc(1)+token(0x80,0x04000013)+b'\x2a',
            scalar_cctor=ldc(2)+token(0x80,0x04000013)+b'\x2a',
            reset_code=put_field(10,token(0x7e,0x04000013))+token(0x7e,0x04000014)+b'\x26\x2a')
        self.assertEqual('BASELINE_LATE_STATIC_EFFECTS',result['status'])
        self.assertEqual([],result['fields']);self.assertFalse(result['baselineComplete'])
        self.assertTrue(result['prefixFields'])

    def test_absent_item_initializer_is_not_an_unknown_effect(self):
        result=proof(method_names={5:'OriginalNotInitializer'})
        self.assertEqual(37,by_name(result)['OriginalCategory']['value'])
        self.assertEqual('UNKNOWN',by_name(result)['OriginalMaximum']['status'])
        self.assertTrue(all(r['snapshotUsable'] for r in result['staticInitializers']))

    def test_late_static_uncertainty_rejects_control_dependent_skipped_write(self):
        skipped=put_field(1,ldc(9))
        result=proof(ctor_code=b'\x02'+token(0x28,0x0a000001)+b'\x2a',
            item_cctor=ldc(1)+token(0x80,0x04000013)+b'\x2a',
            scalar_cctor=ldc(2)+token(0x80,0x04000013)+b'\x2a',
            reset_code=token(0x7e,0x04000013)+b'\x2d'+bytes([len(skipped)])+skipped+token(0x7e,0x04000014)+b'\x26\x2a')
        self.assertEqual('BASELINE_LATE_STATIC_EFFECTS',result['status'])
        self.assertEqual([],result['fields']);self.assertFalse(result['baselineComplete'])


if __name__ == '__main__':
    unittest.main()
