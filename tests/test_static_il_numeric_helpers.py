"""Adversarial tests for enum metadata and call-free same-assembly helpers."""
from dataclasses import replace
import hashlib
import struct
import unittest
from unittest.mock import patch

from numeric_helper_fixture import numeric_helper_pe, token
from resource_pipeline.security import PipelineError, canonical_json
from resource_pipeline.server_semantics import _Metadata, _types, SemanticLimits
from resource_pipeline.static_il import (
    EvidenceBudget, ILUnsupported, MetadataProgram,
    StaticILLimits, Value, _typed_value, extract_item_default_stages,
)

ENTRY = 0x06000001
HELPER = 0x06000007


def program(limits=StaticILLimits(), **options):
    data = numeric_helper_pe(**options)
    meta = _Metadata(data, SemanticLimits())
    return MetadataProgram(meta, _types(meta), limits)


def stages(limits=StaticILLimits(), **options):
    data = numeric_helper_pe(**options)
    meta = _Metadata(data, SemanticLimits())
    return extract_item_default_stages(meta, _types(meta), [1], limits)


class EnumMetadataTests(unittest.TestCase):
    def test_real_pe_metadata_route_preserves_evidence_and_scope(self):
        data = numeric_helper_pe()
        digest = hashlib.sha256(data).hexdigest()
        meta = _Metadata(data, SemanticLimits())
        proof = extract_item_default_stages(meta, _types(meta), [1], input_sha256=digest)
        self.assertEqual([5, 13], [x['value'] for x in proof['records'][0]['fields']])
        self.assertEqual(8, proof['records'][1]['fields'][0]['value'])
        self.assertEqual(digest, hashlib.sha256(data).hexdigest())
        self.assertFalse(proof['complete'])
        self.assertFalse(proof['finalItemDefaults'])
        self.assertFalse(proof['executedInput'])
        enum_method = next(x for x in proof['methods'] if x['methodToken'] == '0x06000006')
        enum = enum_method['enumTypes'][0]
        self.assertEqual('i2', enum['underlyingType'])
        self.assertEqual('0x04000003', enum['valueFieldToken'])
        self.assertTrue(enum['valueSignatureSha256'])
        helper = next(x for x in proof['methods'] if x['methodToken'] == '0x06000007')
        self.assertEqual('same-assembly-call-free-integer-helper', helper['resolution'])
        self.assertEqual('Fixture.OriginalArithmetic', helper['declaringType'])
        self.assertEqual(['0x06000002', '0x06000007'], proof['records'][1]['fields'][0]['evidence']['dependencyMethodTokens'])

    def test_only_eight_integral_enum_underlying_types_are_accepted(self):
        for element, kind in [(4, 'i1'), (5, 'u1'), (6, 'i2'), (7, 'u2'), (8, 'i4'), (9, 'u4'), (10, 'i8'), (11, 'u8')]:
            with self.subTest(kind=kind):
                item = program(underlying_signature=bytes((6, element))).get(0x06000006)
                self.assertEqual((kind, 'i4'), item.args)
        # Boolean, char, native int/uint, floats, reference and nested valuetype.
        for signature in (b'\x06\x02', b'\x06\x03', b'\x06\x18', b'\x06\x19', b'\x06\x0c', b'\x06\x0d', b'\x06\x0e', b'\x06\x11\x10', b'\x06', b'\x06\x08\x00'):
            with self.subTest(signature=signature):
                self.assert_enum_rejected(underlying_signature=signature)

    def assert_enum_rejected(self, **options):
        result = stages(**options)['records'][0]
        self.assertEqual('UNSUPPORTED_NONNUMERIC_HELPER_SIGNATURE', result['status'])
        self.assertEqual([], result['fields'])
        self.assertEqual([], result['prefixFields'])

    def test_arbitrary_struct_spoofed_enum_and_bad_scope_are_rejected(self):
        for options in [
            {'enum_flags': 1}, {'enum_flags': 0x121}, {'enum_flags': 0x103}, {'enum_flags': 0x181}, {'enum_flags': 0x200101},
            {'extends': 0}, {'extends': 8}, {'extends': 6},
            {'base_name': 'ValueType'}, {'base_namespace': 'Pretend'},
            {'base_scope': 0}, {'base_scope': 4}, {'base_scope': 5}, {'base_scope': 7},
            {'assembly_name': 'Pretend.Core'}, {'assembly_key': bytes(8)}, {'assembly_flags': 1}, {'assembly_flags': 0x100}, {'assembly_culture': 'en'},
            {'enum_coded': 17}, {'enum_coded': 18}, {'enum_coded': 0},
        ]:
            with self.subTest(options=options):
                self.assert_enum_rejected(**options)

    def test_extra_storage_layout_generic_and_bad_constants_are_rejected(self):
        for options in [
            {'extra_instance': True}, {'enum_flags': 0x111}, {'enum_flags': 0x109},
            {'field_layout': True}, {'class_layout': True}, {'generic_owner': 8},
            {'underlying_flags': 6}, {'underlying_flags': 0x616}, {'underlying_flags': 0x646},
            {'underlying_name': 'arbitraryStorage'}, {'literal_flags': 0x16},
            {'literal_signature': b'\x06\x08'}, {'literal_signature': b'\x06\x11\x0c'},
            {'literal_signature': b'\x06\x11\x10\x00'},
        ]:
            with self.subTest(options=options):
                self.assert_enum_rejected(**options)

    def test_integer_boundary_narrowing_and_sign_extension(self):
        for kind, incoming, expected in [
            ('i1', 0xff, -1), ('u1', -1, 255), ('i1', 0x80, -128),
            ('i2', 0xffff, -1), ('u2', -1, 65535), ('i2', 0x8000, -32768),
            ('i4', 0x80000000, -2147483648), ('u4', -1, -1),
            ('i8', (1 << 63), -(1 << 63)), ('u8', -1, -1),
        ]:
            with self.subTest(kind=kind):
                stack_kind = 'i8' if kind in ('i8', 'u8') else 'i4'
                self.assertEqual(Value(stack_kind, expected), _typed_value(Value(stack_kind, incoming), kind))
        with self.assertRaisesRegex(ILUnsupported, 'INVALID_NUMERIC_STACK_TYPE'):
            _typed_value(Value('i4', 1), 'i8')

    def test_core_library_identities_and_malformed_owner_ranges(self):
        for name, key in [('mscorlib', 'b77a5c561934e089'), ('System.Private.CoreLib', '7cec85d7bea7798e'), ('System.Runtime', 'b03f5f7f11d50a3a')]:
            with self.subTest(name=name):
                item = program(assembly_name=name, assembly_key=bytes.fromhex(key)).get(0x06000006)
                self.assertEqual(('i2', 'i4'), item.args)
        p = program()
        p.types[2]['lastMethod'] = 8
        with self.assertRaisesRegex(ILUnsupported, 'INVALID_METHOD_OWNER_RANGE'):
            p.get(HELPER)

    def test_malformed_enum_metadata_or_signature_never_produces_facts(self):
        for options in [{'extends': 0xfffd}, {'base_scope': 0xfffe}]:
            with self.subTest(options=options), self.assertRaises(PipelineError):
                stages(**options)
        for signature in (b'\x20\x02\x01\x11\x80', b'\x20\x02\x01\x11\x80\x10\x08', b'\x20\x02\x01\x11\xe0\x08'):
            with self.subTest(signature=signature):
                row = stages(enum_signature=signature)['records'][0]
                self.assertIn(row['status'], ('INVALID_SIGNATURE_INTEGER', 'TRUNCATED_SIGNATURE'))
                self.assertEqual([], row['fields'])


class PureHelperTests(unittest.TestCase):
    def test_helper_identity_is_from_il_not_its_name(self):
        row = stages(helper_name='DifferentName', helper_code=b'\x02\x1b\x5a\x2a')['records'][1]
        self.assertEqual(5, row['fields'][0]['value'])

    def test_primitive_signatures_only(self):
        for signature in (b'\x00\x01\x01\x02', b'\x00\x01\x0c\x02', b'\x00\x01\x08\x0e',
                          b'\x00\x01\x08\x18', b'\x00\x01\x08\x10\x02', b'\x10\x01\x01\x08\x02',
                          b'\x05\x01\x08\x02', b'\x00\x01\x08\x1f\x05\x02', b'\x00\x01\x08\x11\x10',
                          b'\x00\x01\x08\x01', b'\x00\x01\x08\x02\x00'):
            with self.subTest(signature=signature):
                row = stages(helper_signature=signature)['records'][1]
                self.assertNotEqual('PROVEN_NUMERIC_STAGE_WRITES', row['status'])
                self.assertEqual([], row['fields'])

    def test_instance_generic_owner_generic_method_and_nested_owner_rejected(self):
        for options in [
            {'helper_signature': b'\x20\x01\x08\x02', 'helper_flags': 6},
            {'generic_owner': 6}, {'generic_owner': 15}, {'owner_flags': 0x183}, {'owner_flags': 0x1a1},
        ]:
            with self.subTest(options=options):
                row = stages(**options)['records'][1]
                self.assertNotEqual('PROVEN_NUMERIC_STAGE_WRITES', row['status'])
                self.assertEqual([], row['fields'])

    def test_native_virtual_pinvoke_internalcall_and_reserved_flags_rejected(self):
        options = [{'impl_flags': flag} for flag in (1, 2, 3, 4, 0x20, 0x1000)]
        options += [{'helper_flags': 0x16 | flag} for flag in (0x8, 0x20, 0x40, 0x100, 0x200, 0x400, 0x800, 0x1000, 0x2000, 0x4000, 0x8000)]
        options += [{'helper_flags': 0x17}, {'helper_flags': 0x10}, {'no_body': True}, {'helper_name': '.cctor'}, {'header_flags': 0x301b}, {'header_flags': 0x3033}, {'header_flags': 0x4013}]
        for value in options:
            with self.subTest(value=value):
                row = stages(**value)['records'][1]
                self.assertNotEqual('PROVEN_NUMERIC_STAGE_WRITES', row['status'])
                self.assertEqual([], row['fields'])

    def test_all_instructions_checked_even_when_side_effects_are_unreachable(self):
        bodies = [token(op, 0x04000001) for op in (0x7b, 0x7d, 0x7e, 0x80)]
        bodies += [token(op, 0x0a000001) for op in (0x28, 0x29, 0x6f, 0x27, 0x73)]
        bodies += [b'\x14', token(0x72, 0x70000001), b'\x22' + struct.pack('<f', 1.0), b'\x6b', b'\xfe\x13', b'\x54']
        for body in bodies:
            with self.subTest(body=body):
                row = stages(helper_code=b'\x17\x2a' + body + b'\x2a')['records'][1]
                self.assertEqual('UNSUPPORTED_IMPURE_HELPER_BODY', row['status'])
                self.assertEqual([], row['fields'])
        for target in (HELPER, 0x06000006, 0x0a000001, 0x2b000001):
            row = stages(helper_code=b'\x17\x2a' + token(0x28, target) + b'\x2a')['records'][1]
            self.assertEqual('UNSUPPORTED_IMPURE_HELPER_BODY', row['status'])

    def test_memberref_methodspec_and_invalid_methoddef_are_not_resolved(self):
        for target in (0x0a000001, 0x2b000001, 0x06000000, 0x0600ffff):
            with self.subTest(target=target):
                row = stages(call_token=target)['records'][1]
                self.assertEqual('UNSUPPORTED_EXTERNAL_OR_GENERIC_CALL', row['status'])
                self.assertEqual([], row['fields'])

    def test_locals_and_malformed_bodies_fail_closed(self):
        for signature in (b'\x07\x01\x0c', b'\x07\x01\x0e', b'\x07\x01\x18', b'\x07\x01\x10\x08', b'\x07\x01\x1f\x05\x08', b'\x07\x01\x11\x10'):
            row = stages(local_signature=signature)['records'][1]
            self.assertEqual('UNSUPPORTED_PURE_HELPER_SIGNATURE', row['status'])
        for code in (b'\x20\x01', b'\x2b\x7f', b'\x45\xff\xff\xff\xff', b'\xfe', b'\x24'):
            row = stages(helper_code=code)['records'][1]
            self.assertEqual([], row['fields'])
            self.assertNotEqual('PROVEN_NUMERIC_STAGE_WRITES', row['status'])
        row = stages(helper_code=b'\x2b\xfe')['records'][1]
        self.assertEqual('UNSUPPORTED_LOOP', row['status'])

    def test_numeric_local_helper_still_uses_runtime_stack_checks(self):
        row = stages(local_signature=b'\x07\x01\x08', helper_code=b'\x02\x0a\x06\x1e\x58\x2a')['records'][1]
        self.assertEqual(9, row['fields'][0]['value'])
        for code, expected in [(b'\x26\x2a', 'STACK_UNDERFLOW'), (b'\x03\x2a', 'INVALID_VARIABLE_INDEX'),
                               (b'\x17\x17\x2a', 'NONEMPTY_RETURN_STACK'), (b'\x00', 'FALLTHROUGH_OUTSIDE_METHOD')]:
            self.assertEqual(expected, stages(helper_code=code)['records'][1]['status'])


class HelperBudgetTests(unittest.TestCase):
    def test_method_and_metadata_budgets_are_not_bypassed_by_lazy_resolution(self):
        for change, expected in [({'methods': 1}, 'METHOD_COUNT_LIMIT'),
                                 ({'method_index': 7}, 'METADATA_SCAN_LIMIT'),
                                 ({'fields': 2}, 'FIELD_COUNT_LIMIT'),
                                 ({'method_bytes': 2}, 'METHOD_BYTE_LIMIT'),
                                 ({'method_instructions': 2}, 'INSTRUCTION_LIMIT'),
                                 ({'total_method_bytes': 5}, 'TOTAL_METHOD_BYTE_LIMIT'),
                                 ({'total_decoded_instructions': 2}, 'TOTAL_DECODED_INSTRUCTION_LIMIT')]:
            with self.subTest(change=change):
                result = stages(replace(StaticILLimits(), **change))
                statuses = {row['status'] for row in result['records']}
                self.assertIn(expected, statuses)
                self.assertFalse(result['complete'])
        p = program()
        p.limits = replace(p.limits, method_index=6)
        with self.assertRaisesRegex(ILUnsupported, 'METHOD_INDEX_COUNT_LIMIT'):
            p.get(HELPER)

    def test_step_depth_name_evidence_time_and_cancellation_caps_remain_active(self):
        p = program()
        p.get(HELPER)
        p.limits = replace(p.limits, metadata_name_bytes=p.name_bytes)
        with self.assertRaisesRegex(ILUnsupported, 'METADATA_NAME_BYTE_LIMIT'):
            p.get(0x06000006)
        for change, expected in [({'call_depth': 1}, 'CALL_DEPTH_LIMIT'),
                                 ({'method_steps': 2}, 'METHOD_STEP_LIMIT'),
                                 ({'total_steps': 2}, 'TOTAL_STEP_LIMIT')]:
            with self.subTest(change=change):
                result = stages(replace(StaticILLimits(), **change))
                self.assertIn(expected, [row['status'] for row in result['records']])
        p = program()
        p.evidence_budget = EvidenceBudget(16 * 1024)
        p.evidence_budget.used = 16 * 1024 - 1
        with self.assertRaisesRegex(ILUnsupported, 'TOTAL_EVIDENCE_BYTE_LIMIT'):
            p.get(HELPER)
        p = program()
        def cancel():
            raise PipelineError('cancelled')
        p.checkpoint = cancel
        with self.assertRaisesRegex(PipelineError, 'cancelled'):
            p.get(HELPER)
        with patch('resource_pipeline.static_il.time.monotonic', side_effect=[0, 100]):
            result = stages()
        self.assertEqual('TOTAL_TIME_LIMIT', result['diagnostic']['code'])
        self.assertEqual([], result['records'])
        self.assertLessEqual(len(canonical_json(result)), StaticILLimits().evidence_bytes)

    def test_rejected_helper_is_cached_without_redecoding_and_counts_evidence(self):
        p = program(helper_code=b'\x17\x2a' + token(0x7e, 0x04000001) + b'\x2a')
        for _ in range(2):
            with self.assertRaisesRegex(ILUnsupported, 'UNSUPPORTED_IMPURE_HELPER_BODY'):
                p.get(HELPER)
        self.assertEqual(8, p.decoded_bytes)
        self.assertEqual(4, p.decoded_instructions)
        self.assertEqual({}, p.used)
        self.assertGreater(p.evidence_budget.used, 8192)


if __name__ == '__main__':
    unittest.main()
