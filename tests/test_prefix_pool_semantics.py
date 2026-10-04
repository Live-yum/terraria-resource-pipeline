"""Original synthetic whole-initializer, domain, bounds and refusal tests."""
from dataclasses import replace
from pathlib import Path
import inspect
import struct
import tempfile
import unittest
from unittest.mock import patch

from prefix_pool_fixture import VALUES, fixture, pool_body, ldc, token
from resource_pipeline.item_assembler import POOLS
from resource_pipeline.item_dispatch_sets import ItemDispatchSetLimits
from resource_pipeline.item_texture_aliases import _Program, _Budget, _CheckpointCancelled
from resource_pipeline.prefix_pool_semantics import _prove, extract_prefix_pool_semantics
from resource_pipeline.static_il import EvidenceSizeLimit, ILUnsupported


def program(raw=None, limits=None, checkpoint=None, **options):
    return _Program(fixture(**options) if raw is None else raw,
                    _Budget(limits or ItemDispatchSetLimits(), checkpoint))


def proof(**options):
    return _prove(program(**options))


class PrefixPoolSemanticsTests(unittest.TestCase):
    def test_all_eight_ordered_original_arrays_and_independent_domain(self):
        result = proof()
        self.assertEqual({name: list(values) for name, values in zip(POOLS, VALUES)}, result['pools'])
        self.assertEqual(list(POOLS), list(result['pools']))
        self.assertEqual({'poolCount': 8, 'poolEntryCount': 13, 'uniqueReferencedPrefixCount': 11,
                          'prefixDomainCount': 13}, result['stats'])
        self.assertEqual(13, result['declaredDomain']['maxExclusive'])
        self.assertEqual(0, result['declaredDomain']['minInclusive'])
        self.assertEqual('INITIALIZER_BOUNDARY', result['factScope'])
        self.assertTrue(result['wholeInitializerProven'])
        self.assertTrue(result['independentDomainInitializerProven'])
        self.assertEqual(8, len({row['allocationIdentity'] for row in result['poolEvidence']}))
        self.assertEqual(9, len(result['fieldEvidence']))
        for row in result['poolEvidence']:
            self.assertEqual(row['count'] * 4, row['arrayEvidence']['rvaEvidence']['dataBytes'])
            self.assertEqual(1, len(row['trustedCalls']))
        for key in ('complete', 'publishable', 'runtimeSnapshotUsable', 'executedInput', 'normalReturnGuaranteed'):
            self.assertFalse(result[key])

    def test_unsorted_ids_are_preserved_and_duplicates_are_not_deduplicated(self):
        self.assertEqual([9, 1], proof()['pools'][POOLS[0]])
        for inline in (False, True):
            with self.subTest(inline=inline), self.assertRaisesRegex(ILUnsupported, 'DUPLICATE_ID'):
                proof(values=((1, 1), *VALUES[1:]), inline=inline)

    def test_inline_arrays_and_empty_arrays_have_exact_stack_requirements(self):
        result = proof(inline=True, maxstack={2: 4})
        self.assertEqual(list(VALUES[0]), result['pools'][POOLS[0]])
        self.assertEqual(4, result['methodEvidence']['requiredMaxStack'])
        self.assertTrue(all(not row['trustedCalls'] for row in result['poolEvidence']))
        empty = proof(values=((),) * 8, maxstack={2: 1})
        self.assertEqual({name: [] for name in POOLS}, empty['pools'])
        self.assertEqual(1, empty['methodEvidence']['requiredMaxStack'])
        singleton = proof(values=((0,),) * 8, inline=True)
        self.assertEqual({name: [0] for name in POOLS}, singleton['pools'])

    def test_final_overwrite_semantics_are_preserved_before_publication(self):
        code = pool_body(inline=True)
        old = b'\x25' + ldc(0) + ldc(9) + b'\x9e'
        changed = old + b'\x25' + ldc(0) + ldc(12) + b'\x9e'
        self.assertEqual([12, 1], proof(pool_code=code.replace(old, changed, 1))['pools'][POOLS[0]])

    def test_mixed_rva_and_inline_overrides_require_stack_four(self):
        code = pool_body()
        store = token(0x80, 0x04000002)
        code = code.replace(store, b'\x25' + ldc(0) + ldc(12) + b'\x9e' + store, 1)
        self.assertEqual([12, 1], proof(pool_code=code, maxstack={2: 4})['pools'][POOLS[0]])
        with self.assertRaisesRegex(ILUnsupported, 'MAXSTACK'):
            proof(pool_code=code, maxstack={2: 3})

    def test_declared_stack_is_checked_for_both_whole_methods(self):
        for inline, required in ((False, 3), (True, 4)):
            for stack in range(required + 1):
                with self.subTest(inline=inline, stack=stack):
                    if stack < required:
                        with self.assertRaisesRegex(ILUnsupported, 'MAXSTACK'):
                            proof(inline=inline, maxstack={2: stack})
                    else:
                        self.assertEqual(required, proof(inline=inline, maxstack={2: stack})['methodEvidence']['requiredMaxStack'])
        with self.assertRaisesRegex(ILUnsupported, 'COUNT_MAXSTACK'):
            proof(maxstack={1: 0})

    def test_ids_cannot_escape_independent_count_domain(self):
        for value in (-1, 13, 2**31 - 1):
            with self.subTest(value=value), self.assertRaisesRegex(ILUnsupported, 'ID_OUT_OF_DOMAIN'):
                proof(values=((value,), *VALUES[1:]))
        for count in (-1, 0, 257):
            with self.subTest(count=count), self.assertRaisesRegex(ILUnsupported, 'DOMAIN_LIMIT'):
                proof(count=count)
        self.assertEqual(256, proof(count=256)['declaredDomain']['count'])

    def test_count_initializer_has_no_hidden_tail_or_alternate_store(self):
        base = ldc(13) + token(0x80, 0x04000001)
        for code in (base + b'\x00\x2a', base + token(0x28, 0x0a000001) + b'\x2a',
                     ldc(13) + token(0x80, 0x04000002) + b'\x2a', b'\x2a' + base + b'\x2a',
                     base + b'\x2a\x00', base):
            with self.subTest(code=code.hex()), self.assertRaises(ILUnsupported):
                proof(count_code=code)

    def test_pool_initializer_unknown_effects_and_trailing_instructions_fail_closed(self):
        base = pool_body()
        cases = [base[:-1] + token(0x28, 0x0a000001) + b'\x2a', b'\x00' + base,
                 base + b'\x00', b'\x2a' + base, base[:-1],
                 base[:-1] + token(0x7f, 0x04000002) + b'\x26\x2a',
                 base[:-1] + b'\x14' + token(0x80, 0x04000002) + b'\x2a',
                 base.replace(token(0x28, 0x0a000001), token(0x6f, 0x0a000001), 1),
                 b'\x2b\x00' + base]
        for code in cases:
            with self.subTest(code=code.hex()), self.assertRaises(ILUnsupported):
                proof(pool_code=code)

    def test_no_missing_extra_duplicate_or_foreign_pool_store(self):
        base = pool_body()
        cases = [pool_body(VALUES[:-1]),
                 base.replace(token(0x80, 0x04000003), token(0x80, 0x04000002)),
                 base.replace(token(0x80, 0x04000002), token(0x80, 0x04000001)),
                 base.replace(token(0x80, 0x04000002), token(0x80, 0x0400000a)),
                 base[:-1] + pool_body(VALUES[:1])]
        for code in cases:
            with self.subTest(code=code.hex()), self.assertRaises(ILUnsupported):
                proof(pool_code=code)
        for first in (9, 11):
            with self.subTest(first=first), self.assertRaises(ILUnsupported):
                proof(type_first_fields={5: first})

    def test_exact_owner_names_signatures_and_field_flags_are_required(self):
        options = [dict(field_names={2: 'AlmostPrefixesForSwords'}), dict(field_names={3: POOLS[0]}),
                   dict(field_signatures={2: b'\x06\x1d\x09'}), dict(field_signatures={1: b'\x06\x06'}),
                   dict(field_flags={1: 0x16}), dict(field_flags={1: 0x76}), dict(field_flags={2: 0x6}),
                   dict(field_flags={2: 0x36}), dict(field_flags={2: 0x116}), dict(field_flags={2: 0x2016})]
        for opts in options:
            with self.subTest(opts=opts), self.assertRaises(ILUnsupported): proof(**opts)

    def test_exact_cctor_signature_flags_locals_and_exception_contract(self):
        for i in (1, 2):
            for options in ({'signatures': {i: b'\x00\x00\x08'}}, {'method_flags': {i: 0x91}},
                            {'method_flags': {i: 0x1881}}, {'local': i}, {'header_flags': {i: 0x301b}}):
                with self.subTest(i=i, options=options), self.assertRaises(ILUnsupported): proof(**options)

    def test_primitive_and_intrinsic_core_identities_are_verified(self):
        options = [dict(ref_names={2: 'UInt32'}), dict(ref_names={3: 'Object'}),
                   dict(ref_names={4: 'IntPtr'}), dict(ref_names={5: 'FakeHelpers'}),
                   dict(initialize_name='OriginalUntrustedHelper'), dict(initialize_owner=9),
                   dict(initialize_signature=b'\x00\x01\x01\x12\x0d'),
                   dict(key=b'notcore!'), dict(assembly_name='Untrusted'), dict(assembly_flags=1)]
        for opts in options:
            with self.subTest(opts=opts), self.assertRaises(ILUnsupported): proof(**opts)

    def test_rva_type_layout_size_flags_and_location_are_verified(self):
        options = [dict(type_flags={6: 0x130}), dict(type_flags={6: 0x190}), dict(ref_names={6: 'Object'}),
                   dict(layout_pack={0: 4}), dict(layout_delta={0: 4}),
                   dict(field_flags={10: 0x11}), dict(field_flags={10: 0x151}),
                   dict(field_signatures={10: b'\x06\x08'})]
        for opts in options:
            with self.subTest(opts=opts), self.assertRaises(ILUnsupported): proof(**opts)
        original = fixture(); p = program(original)
        # A second row targeting the first RVA field is ambiguous even if bytes match.
        _, offset = p.row(29, 2)
        raw = bytearray(original); struct.pack_into('<H', raw, offset + 4, 10)
        with self.assertRaisesRegex(ILUnsupported, 'RVA_LOCATION'):
            proof(raw=bytes(raw))

    def test_negative_lengths_truncation_and_invalid_inline_indexes(self):
        base = pool_body(inline=True)
        cases = [base.replace(ldc(2), ldc(-1), 1),
                 base.replace(b'\x25' + ldc(0), b'\x25' + ldc(2), 1),
                 ldc(2) + token(0x8d, 0x01000002) + b'\x25\x2a',
                 ldc(2) + b'\x2a', ldc(2) + token(0x8d, 0x01000002) + b'\x2a']
        for code in cases:
            with self.subTest(code=code.hex()), self.assertRaises(ILUnsupported): proof(pool_code=code)

    def test_bounded_parsing_and_output(self):
        for key, value in (('method_bytes', 8), ('total_method_bytes', 10), ('instructions', 2),
                           ('steps', 10), ('literal_ids', 1), ('literal_bytes', 4)):
            with self.subTest(key=key), self.assertRaises(ILUnsupported):
                proof(limits=replace(ItemDispatchSetLimits(), **{key: value}))
        with self.assertRaisesRegex(EvidenceSizeLimit, 'EVIDENCE_JSON_BYTE_LIMIT'):
            proof(values=(tuple(range(256)),) * 8, count=256,
                  limits=replace(ItemDispatchSetLimits(), evidence_bytes=16384))
        p = program()
        with patch('resource_pipeline.item_texture_aliases.time.monotonic', return_value=p.budget.deadline + 1):
            with self.assertRaisesRegex(ILUnsupported, 'TIME_LIMIT'): _prove(p)

    def test_cancel_internal_and_public_extraction(self):
        p = program()
        error = RuntimeError('stop original proof')
        with patch.object(p.budget, 'external', side_effect=error):
            with self.assertRaises(_CheckpointCancelled) as caught: _prove(p)
        self.assertIs(error, caught.exception.original)
        def cancel(): raise error
        with self.assertRaises(RuntimeError) as caught:
            extract_prefix_pool_semantics(Path('unused'), cancel)
        self.assertIs(error, caught.exception)

    def test_public_entry_accepts_no_caller_profiles_or_named_fake_executable(self):
        self.assertEqual(['input_path', 'checkpoint'], list(inspect.signature(extract_prefix_pool_semantics).parameters))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'Terraria.exe'; path.write_bytes(fixture())
            with self.assertRaisesRegex(ILUnsupported, 'UNSUPPORTED_INPUT_PROFILE'):
                extract_prefix_pool_semantics(path)
            # Even a spoofed first-gate hash cannot pass the independent method pins.
            with patch('resource_pipeline.prefix_pool_semantics.sha256',
                       return_value='960a03bff6050cf7be16dfc1a7b19e10fc2c4f8f835a6a3b135a50dd9e6ba2f3'):
                with self.assertRaisesRegex(ILUnsupported, 'PROFILE_METHOD_PIN'):
                    extract_prefix_pool_semantics(path)


if __name__ == '__main__': unittest.main()
