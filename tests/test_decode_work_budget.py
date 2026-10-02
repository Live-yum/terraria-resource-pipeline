"""Original synthetic PE/IL work-accounting regressions; no game execution."""
from dataclasses import replace
import hashlib
import unittest
from unittest.mock import patch

from baseline_fixture import baseline_pe, put_field
from resource_pipeline.server_semantics import _Metadata, _types, _item_evidence, SemanticLimits
from resource_pipeline.static_il import StaticILLimits, MetadataProgram, ILUnsupported, Instruction, decode_il


def program(code, *, reset=b'\x2a', **limits):
    data=baseline_pe(method_names={6:'SetDefaults1'},method_flags={6:0x86},typed_ctor=code,reset_code=reset)
    meta=_Metadata(data,SemanticLimits())
    return MetadataProgram(meta,_types(meta),replace(StaticILLimits(),**limits))


class DecodeWorkBudgetTests(unittest.TestCase):
    def test_failed_method_keeps_decode_charges_and_cache_never_retries_work(self):
        p=program(b'\x00'*8+b'\x2a',total_decoded_instructions=4)
        built=[]
        def count(*args):
            built.append(args);return Instruction(*args)
        with patch('resource_pipeline.static_il.Instruction',side_effect=count):
            for token in (0x06000006,0x06000006,0x06000002):
                with self.assertRaisesRegex(ILUnsupported,'TOTAL_DECODED_INSTRUCTION_LIMIT'):
                    p.get(token)
        self.assertEqual(4,len(built));self.assertEqual(4,p.decoded_instructions)
        self.assertEqual(10,p.decoded_bytes)  # Nine rejected bytes and one attempted ret.

    def test_malformed_opcode_and_branch_keep_prior_work_charged(self):
        for code,charged,reason in [(b'\x00\x00\xff',3,'UNKNOWN_IL_OPCODE'),
                                    (b'\x2b\x01\x2a',2,'BRANCH_TARGET_NOT_INSTRUCTION')]:
            with self.subTest(reason=reason):
                p=program(code,total_decoded_instructions=4)
                with self.assertRaisesRegex(ILUnsupported,reason):p.get(0x06000006)
                self.assertEqual(charged,p.decoded_instructions);self.assertEqual(len(code),p.decoded_bytes)
                p.get(0x06000002)
                self.assertEqual(charged+1,p.decoded_instructions);self.assertEqual(len(code)+1,p.decoded_bytes)
                with self.assertRaisesRegex(ILUnsupported,reason):p.get(0x06000006)
                self.assertEqual(charged+1,p.decoded_instructions)

    def test_method_byte_cap_rejects_before_any_decode(self):
        p=program(b'\x00'*5+b'\x2a',total_method_bytes=5)
        with patch('resource_pipeline.static_il.decode_il',side_effect=AssertionError('must not decode')):
            with self.assertRaisesRegex(ILUnsupported,'TOTAL_METHOD_BYTE_LIMIT'):p.get(0x06000006)
        self.assertEqual(0,p.decoded_bytes);self.assertEqual(0,p.decoded_instructions)
        p.get(0x06000002);self.assertEqual(1,p.decoded_bytes)

    def test_per_method_instruction_rejection_does_not_refund_global_work(self):
        p=program(b'\x00'*8+b'\x2a',method_instructions=3,total_decoded_instructions=10)
        with self.assertRaisesRegex(ILUnsupported,'INSTRUCTION_LIMIT'):p.get(0x06000006)
        self.assertEqual(3,p.decoded_instructions)
        p.get(0x06000002);self.assertEqual(4,p.decoded_instructions)

    def test_callback_is_charged_before_reading_and_can_stop_next_attempt(self):
        remaining=[2]
        def charge():
            if remaining[0]==0:raise ILUnsupported('ORIGINAL_BUDGET_EXHAUSTED')
            remaining[0]-=1
        with self.assertRaisesRegex(ILUnsupported,'ORIGINAL_BUDGET_EXHAUSTED'):
            decode_il(b'\x00\x00\xff',instruction_budget=charge)
        self.assertEqual(0,remaining[0])

    def test_combined_passes_never_decode_beyond_shared_reserved_capacity(self):
        data=baseline_pe(method_names={6:'SetDefaults1'},method_flags={6:0x86},
                         typed_ctor=put_field(4,b'\x03')+b'\x2a')
        for capacity in (1,4,16,40,100):
            with self.subTest(capacity=capacity):
                meta=_Metadata(data,SemanticLimits());built=[]
                def count(*args):
                    built.append(args);return Instruction(*args)
                with patch('resource_pipeline.static_il.Instruction',side_effect=count):
                    stages,baseline,budget=_item_evidence(meta,_types(meta),[7],{'kind':'synthetic-capacity-test'},
                        replace(StaticILLimits(),total_decoded_instructions=capacity),lambda:None,hashlib.sha256(data).hexdigest())
                self.assertLessEqual(len(built),capacity)
                self.assertFalse(stages['finalItemDefaults']);self.assertFalse(baseline['finalItemDefaults'])
