"""Original generic IL fixtures for empty-pair path pruning; never game execution."""
from dataclasses import replace
import hashlib
import struct
import unittest
from unittest.mock import patch

from dispatch_fixture import dispatch_pe, tok, assemble
from resource_pipeline.item_texture_aliases import ItemTextureAliasLimits
from resource_pipeline.set_factory_empty_custom import extract_empty_custom_set


def fixture(*, mutate=lambda b:b, call_length=0, call_type=0x01000001, caller_tail=b'', dead=b'\x02\x28\x07\x00\x00\x06\x26', **opts):
    # The dead region deliberately contains an unknown call. An empty array
    # cannot enter it; nonempty arrays receive no certificate whatsoever.
    prefix = assemble([4,0x8e,0x69,0x18,0x5d,(0x2c,'@9'),(0x72,0x70000001),
        (0x73,0x0a000003),0x7a,2,(0x7b,0x04000007),(0x8d,0x1b000001),0x0a,0x16,0x0b,
        (0x2b,'@24'),6,7,3,(0xa4,0x1b000001),7,0x17,0x58,0x0b,
        7,6,0x8e,0x69,(0x32,'@16'),4])
    dead_start = len(prefix) + 12
    check = dead_start + len(dead)
    end = check + 9
    prefix += tok(0x39, end-len(prefix)-5) + b'\x16\x0c'
    prefix += tok(0x38, check-len(prefix)-5)
    code = prefix + dead + b'\x08\x04\x8e\x69' + struct.pack('<Bi',0x3f,dead_start-check-9) + b'\x06\x2a'
    tail = tok(0x7e,0x04000002) + b'\x16' + bytes((0x16 + call_length,)) + tok(0x8d,call_type) + tok(0x6f,0x2b000001) + b'\x26'
    return dispatch_pe(custom_code=mutate(code), sets_tail=tail + caller_tail, **opts)


class EmptyCustomSetTests(unittest.TestCase):
    def reject(self, raw, **kwargs):
        r=extract_empty_custom_set(raw, **kwargs)
        self.assertNotEqual('PROVEN_EMPTY_PAIR_NORMAL_RETURN_EFFECTS',r['status'])
        self.assertNotIn('proof',r)
        self.assertEqual([],r['metadataEvidence'])
        self.assertFalse(r['complete']); self.assertFalse(r['publishable'])

    def test_empty_path_and_callsite_are_separate_conditional_facts(self):
        raw=fixture(); r=extract_empty_custom_set(raw)
        self.assertEqual('PROVEN_EMPTY_PAIR_NORMAL_RETURN_EFFECTS',r['status'])
        self.assertEqual(hashlib.sha256(raw).hexdigest(),r['inputSha256'])
        self.assertEqual([],r['proof']['executedCallsOnEmptyPath'])
        self.assertFalse(r['proof']['factoryOrPoolWrites'])
        self.assertFalse(r['proof']['nonemptyPairsProven'])
        self.assertFalse(r['proof']['normalReturnGuaranteed'])
        self.assertFalse(r['proof']['cacheStateAtCallSiteProven'])
        self.assertEqual(1,len(r['emptyArgumentCalls']))
        self.assertFalse(r['emptyArgumentCalls'][0]['receiverAndSizeAtCallProven'])
        self.assertTrue(r['metadataEvidence'][0]['ilSha256'])
        self.assertFalse(r['wholeInitializerProven'])

    def test_dead_unknown_calls_and_writes_are_not_claimed_proven(self):
        r=extract_empty_custom_set(fixture(dead=b'\x02\x03' + tok(0x7d,0x04000007)))
        self.assertEqual('PROVEN_EMPTY_PAIR_NORMAL_RETURN_EFFECTS',r['status'])
        self.assertFalse(r['proof']['nonemptyPairsProven'])

    def test_reachable_extra_call_store_return_or_changed_counter_rejected(self):
        for change in (lambda b:b[:-1]+tok(0x28,0x06000007)+b'\x2a',
                       lambda b:b.replace(b'\x16\x0c\x38',b'\x17\x0c\x38'),
                       lambda b:b.replace(b'\x08\x04\x8e\x69',b'\x07\x04\x8e\x69'),
                       lambda b:b.replace(tok(0x7b,0x04000007),tok(0x7d,0x04000007)),
                       lambda b:b.replace(b'\x3f',b'\x3c')):
            self.reject(fixture(mutate=change))

    def test_default_copy_size_and_generic_binding_are_exact(self):
        self.reject(fixture(custom_element=b'\x08'))
        self.reject(fixture(custom_locals=b'\x07\x04\x1d\x08\x08\x08\x08'))
        self.reject(fixture(field_flags={7:0x11}))
        self.reject(fixture(mutate=lambda b:b.replace(b'\x06\x07\x03\xa4',b'\x06\x07\x04\xa4')))

    def test_nonempty_wrong_element_branch_and_exception_region_rejected(self):
        result = extract_empty_custom_set(fixture(call_length=1))
        self.assertEqual([], result['emptyArgumentCalls'])
        self.assertFalse(result['proof']['nonemptyPairsProven'])
        self.reject(fixture(call_type=0x01000002))
        self.reject(fixture(caller_tail=b'\x2b\x00'))
        self.reject(fixture(header_flags={7:0x301b}))
        self.reject(fixture(mutate=lambda b: b.replace(b'\x16\x0c\x38', b'\x16\x0c\x39')))

    def test_dispatch_keeps_residual_calls_and_separate_scope(self):
        from resource_pipeline.item_dispatch_sets import extract_item_dispatch_sets
        result = extract_item_dispatch_sets(fixture())
        custom = result['factory']['emptyCustomSet']
        self.assertEqual('PROVEN_EMPTY_PAIR_NORMAL_RETURN_EFFECTS', custom['status'])
        self.assertFalse(custom['wholeInitializerProven'])
        self.assertTrue(any(r['targetToken'] == '0x2b000001' for r in result['residualEffects']['unmodeledCalls']))
        self.assertEqual('UNPROVEN_AT_CALL_SITE', result['factory']['bufferLengthStatus'])
        self.assertFalse(result['cctorNormalReturnSnapshotUsable'])

    def test_generic_metadata_cannot_silently_establish_valid_instantiation(self):
        for param in ((1,0,15), (0,1,15), (0,0,13)):
            self.reject(fixture(custom_generic=param))
        for signature in (b'\x0a\x02\x08\x08', b'\x0a\x01\x01', b'\x0a\x01\x08\x08'):
            self.reject(fixture(custom_spec=signature))
        r = extract_empty_custom_set(fixture(custom_spec_method=8))
        self.assertEqual([], r['emptyArgumentCalls'])
        r = extract_empty_custom_set(fixture(custom_spec=b'\x0a\x01\x1e\x00'))
        self.assertFalse(r['emptyArgumentCalls'][0]['genericInstantiationValidityProven'])
        with self.assertRaises(ValueError): fixture(pooled=True)
        with self.assertRaises(ValueError): fixture(reject_zero=True)

    def test_budget_cancellation_and_malformed_input_fail_closed(self):
        for kw in ({'instructions':1},{'steps':1},{'method_bytes':1},{'total_method_bytes':1}):
            self.reject(fixture(),limits=replace(ItemTextureAliasLimits(),**kw))
        repeat = tok(0x7e,0x04000002) + b'\x16\x16' + tok(0x8d,0x01000001) + tok(0x6f,0x2b000001) + b'\x26'
        self.reject(fixture(caller_tail=repeat*10), limits=replace(ItemTextureAliasLimits(), evidence_bytes=4096))
        self.reject(b'not PE')
        with patch('resource_pipeline.item_texture_aliases.time.monotonic',side_effect=[0,60]):
            self.reject(fixture())
        error=RuntimeError('stop')
        def cancel():raise error
        with self.assertRaises(RuntimeError) as got:
            extract_empty_custom_set(fixture(),checkpoint=cancel)
        self.assertIs(error,got.exception)


if __name__=='__main__':unittest.main()
