"""Original synthetic first-call state proofs; no game bytes."""
import unittest
from dataclasses import replace
from dispatch_fixture import tok, ldc
from test_set_factory_empty_custom import fixture
from resource_pipeline.item_dispatch_sets import extract_item_dispatch_sets, ItemDispatchSetLimits

HEAD = tok(0x7e,0x04000001) + tok(0x73,0x06000003) + tok(0x80,0x04000002)
RECEIVER = tok(0x7e,0x04000002)
CALL = b'\x16' + tok(0x8d,0x01000001) + tok(0x6f,0x2b000001) + b'\x26'
STRUCT = (b'\x12\x00\xfe\x15' + (0x0200000b).to_bytes(4,'little')
          + b'\x12\x00\x15' + tok(0x7d,0x0400000d)
          + b'\x12\x00\x16' + tok(0x7d,0x0400000e) + b'\x06')


def raw(*, default=b'\x16', middle=b'', after=b'', **opts):
    # Prepend the custom call but preserve all four dispatch recipe fixtures.
    baseline = fixture(**{k:v for k,v in opts.items() if k != 'header_flags'})
    from resource_pipeline.item_texture_aliases import _Program, _Budget, ItemTextureAliasLimits
    p = _Program(baseline, _Budget(ItemTextureAliasLimits(),None))
    method = p.method('Terraria.ID.ItemID+Sets','.cctor',b'\x00\x00\x01')
    ev = next(e for e in p.evidence if e.get('methodToken') == '0x06000002')
    original = baseline[ev['codeOffset']:ev['codeOffset']+ev['codeBytes']]
    return fixture(sets_code=HEAD + middle + RECEIVER + default + CALL + after + original[len(HEAD):], **opts)


class FirstCustomCallTests(unittest.TestCase):
    def proof(self, data):
        result=extract_item_dispatch_sets(data)
        self.assertEqual('PROVEN_CONDITIONAL_INITIALIZER_RECIPES',result['status'])
        return result,result['factory']['firstCustomCall']

    def test_first_integer_call_and_exact_occurrence_only(self):
        result,p=self.proof(raw(default=ldc(23)))
        self.assertEqual('PROVEN_FIRST_CUSTOM_CALL_NORMAL_RETURN_SLICE',p['status'])
        self.assertEqual(23,p['defaultValue']['value'])
        self.assertFalse(p['numericSizeProven'])
        self.assertEqual('0x04000001',p['size']['fieldToken'])
        remaining=[r for r in result['residualEffects']['unmodeledCalls'] if r['targetToken']=='0x2b000001']
        self.assertEqual(1,remaining[0]['occurrences'])
        self.assertFalse(result['complete']); self.assertFalse(result['publishable'])
        self.assertFalse(result['runtimeSnapshotUsable'])

    def test_struct_local_init_and_field_values(self):
        _,p=self.proof(raw(default=STRUCT,custom_struct=True,custom_spec=b'\x0a\x01\x11\x2c'))
        self.assertEqual('PROVEN_FIRST_CUSTOM_CALL_NORMAL_RETURN_SLICE',p['status'])
        self.assertEqual([-1,0],[f['value'] for f in p['defaultValue']['fields']])

    def test_count_tail_unknown_effect_does_not_become_literal_size(self):
        _,p=self.proof(raw(count_tail=tok(0x28,0x06000008)))
        self.assertEqual('PROVEN_FIRST_CUSTOM_CALL_NORMAL_RETURN_SLICE',p['status'])
        self.assertEqual('captured static read',p['size']['kind'])
        self.assertFalse(p['size']['numericValueProven'])

    def test_prefix_calls_aliases_mutation_and_wrong_default_rejected(self):
        variants=[dict(middle=tok(0x28,0x06000008)),
            dict(middle=RECEIVER+b'\x16'+tok(0x7d,0x04000007)),dict(default=b'\x14'),
            dict(custom_spec=b'\x0a\x01\x1e\x00')]
        for opts in variants:
            with self.subTest(opts=opts):
                result,p=self.proof(raw(**opts))
                self.assertNotEqual('PROVEN_FIRST_CUSTOM_CALL_NORMAL_RETURN_SLICE',p['status'])
                rem=[r for r in result['residualEffects']['unmodeledCalls'] if r['targetToken']=='0x2b000001']
                self.assertEqual(2,rem[0]['occurrences'])

    def test_address_escape_branch_and_exception_region_fail_closed(self):
        for opts in [dict(middle=tok(0x7f,0x04000002)+b'\x26'),
                     dict(after=b'\x2b\x00'), dict(header_flags={2:0x301b})]:
            result=extract_item_dispatch_sets(raw(**opts))
            self.assertNotEqual('PROVEN_CONDITIONAL_INITIALIZER_RECIPES',result['status'])
            self.assertFalse(result['complete'])

    def test_wrong_struct_field_layout_and_alias_rejected(self):
        for opts in [dict(struct_flags=0x111),dict(field_layout=[(0,13)]),dict(custom_struct_attribute=True),
                     dict(field_flags={13:0x16}),dict(field_signatures={14:b'\x06\x1c'}),
                     dict(default=STRUCT.replace(tok(0x7d,0x0400000d),tok(0x7d,0x04000007))),
                     dict(default=STRUCT.replace(b'\x12\x00',b'\x12\x01'))]:
            kw=dict(default=STRUCT,custom_struct=True,custom_spec=b'\x0a\x01\x11\x2c');kw.update(opts)
            _,p=self.proof(raw(**kw))
            self.assertNotEqual('PROVEN_FIRST_CUSTOM_CALL_NORMAL_RETURN_SLICE',p['status'])

    def test_reference_constraint_cannot_accept_integer_or_struct(self):
        for opts in ({}, dict(default=STRUCT,custom_struct=True,custom_spec=b'\x0a\x01\x11\x2c')):
            result,p=self.proof(raw(custom_generic=(0,4,15),**opts))
            self.assertEqual('FIRST_CUSTOM_GENERIC_CONSTRAINT',p['status'])
            remaining=[r for r in result['residualEffects']['unmodeledCalls'] if r['targetToken']=='0x2b000001']
            self.assertEqual(2,remaining[0]['occurrences'])

    def test_budget_and_cancellation(self):
        result=extract_item_dispatch_sets(raw(),limits=replace(ItemDispatchSetLimits(),steps=1))
        self.assertFalse(result['complete']);self.assertNotIn('factory',result)
        with self.assertRaisesRegex(RuntimeError,'cancel'):
            extract_item_dispatch_sets(raw(),checkpoint=lambda: (_ for _ in ()).throw(RuntimeError('cancel')))
