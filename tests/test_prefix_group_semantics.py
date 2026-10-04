"""Original synthetic whole-group composition, lifecycle and refusal tests."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import inspect
import tempfile
import unittest
from unittest.mock import patch

from prefix_group_fixture import VALUES, fixture, group_body, ldc, tok
from resource_pipeline.item_assembler import GROUPS
from resource_pipeline.item_dispatch_sets import ItemDispatchSetLimits
from resource_pipeline.item_texture_aliases import _Program, _Budget, _CheckpointCancelled
from resource_pipeline.prefix_group_semantics import _prove, extract_prefix_group_semantics
from resource_pipeline.static_il import EvidenceSizeLimit, ILUnsupported


def program(raw=None, limits=None, checkpoint=None, **options):
    return _Program(fixture(**options) if raw is None else raw,
                    _Budget(limits or ItemDispatchSetLimits(), checkpoint))


class PrefixGroupSemanticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # This is the real independent dependency grammar proof over ORIGINAL
        # synthetic bytes, not a success flag or a literal-assignment mock.
        from id_count_fixture import dependency_fixture
        from resource_pipeline.id_count_semantics import prove_id_dictionary_dependency
        cls.dependency = prove_id_dictionary_dependency(program(raw=dependency_fixture()))

    def count(self, p):
        from resource_pipeline.id_count_semantics import prove_id_count_initializer
        return prove_id_count_initializer(p, 'Terraria.ID.ItemID', self.dependency)

    def proof(self, **options):
        p = program(**options)
        return _prove(p, self.count(p))

    def test_whole_independent_domain_seven_fresh_arrays_and_empty_queue(self):
        result = self.proof()
        self.assertEqual({name:list(ids) for name,ids in zip(GROUPS,VALUES)}, result['groups'])
        self.assertEqual(list(GROUPS),list(result['groups']))
        self.assertEqual({'groupCount':7,'groupEntryCount':12,'uniqueReferencedItemCount':10,
                          'itemDomainCount':13},result['stats'])
        self.assertTrue(result['wholeInitializerProven'])
        self.assertTrue(result['independentDomainInitializerProven'])
        self.assertTrue(result['declaredDomain']['externalTailCountNonmutationProven'])
        self.assertEqual('INITIALIZER_BOUNDARY',result['factScope'])
        self.assertEqual(13,result['declaredDomain']['maxExclusive'])
        self.assertEqual(7,len({r['allocationIdentity'] for r in result['groupEvidence']}))
        for row in result['groupEvidence']:
            self.assertEqual(13,row['length'])
            self.assertEqual('empty',row['queueBefore'])
            self.assertEqual('empty',row['queueAfter'])
            self.assertEqual((0,0),(row['enqueues'],row['dequeues']))
            self.assertFalse(row['defaultValue']);self.assertTrue(row['overrideValue'])
        self.assertEqual(5,result['methodEvidence']['requiredMaxStack'])
        self.assertTrue(result['methodEvidence']['allInstructionsCovered'])
        self.assertEqual([2,3,4,2],[m['requiredMaxStack'] for m in result['helperMethodEvidence']])
        for key in ('complete','publishable','runtimeSnapshotUsable','normalReturnGuaranteed','executedInput',
                    'runtimeDependencyBindingVerified'):
            self.assertFalse(result[key])

    def test_inline_rva_and_final_inline_overwrite_semantics(self):
        self.assertEqual(self.proof()['groups'],self.proof(inline=True)['groups'])
        code=group_body(inline=True)
        old=b'\x25'+ldc(0)+ldc(9)+b'\x9e'
        code=code.replace(old,old+b'\x25'+ldc(0)+ldc(12)+b'\x9e',1)
        self.assertEqual([12,1],self.proof(group_code=code)['groups'][GROUPS[0]])
        code=group_body().replace(tok(0x6f,0x06000004),b'\x25'+ldc(0)+ldc(12)+b'\x9e'+tok(0x6f,0x06000004),1)
        self.assertEqual([12,1],self.proof(group_code=code)['groups'][GROUPS[0]])

    def test_empty_source_arrays_still_allocate_seven_distinct_outputs(self):
        result=self.proof(values=((),)*7,maxstack={2:2})
        self.assertEqual({name:[] for name in GROUPS},result['groups'])
        self.assertEqual(2,result['methodEvidence']['requiredMaxStack'])
        self.assertEqual(7,result['factory']['freshDistinctOutputCount'])

    def test_independent_count_proof_required_and_bound_to_actual_body(self):
        for mutation in ({'owner':'Terraria.ID.TileID'},{'fieldToken':'0x04000002'},
                         {'wholeInitializerProven':False},{'independentDomainInitializerProven':False},
                         {'externalTailCountNonmutationProven':False},{'factScope':'FINAL'},
                         {'countMethodEvidence':{}},{'count':True},{'count':0},{'count':32768},{'count':14},{'count':12}):
            p=program();domain={**self.count(p),**mutation}
            with self.subTest(mutation=mutation),self.assertRaises(ILUnsupported):_prove(p,domain)
        p=program();domain=deepcopy(self.count(p));domain['countMethodEvidence']['ilSha256']='0'*64
        with self.assertRaisesRegex(ILUnsupported,'COUNT_PROOF_BINDING'):_prove(p,domain)

    def test_count_external_tail_cannot_be_sliced_off_or_ignored(self):
        base=ldc(13)+tok(0x80,0x04000001)
        for code in (base+b'\x2a',base+tok(0x28,0x0a000001)+b'\x2a',
                     base+tok(0x28,0x2b000001)+tok(0x80,0x04000002)+b'\x00\x2a'):
            with self.subTest(code=code.hex()),self.assertRaises(ILUnsupported):self.proof(count_code=code)

    def test_invalid_indexes_and_duplicate_input_values_fail_closed(self):
        for ids in ((-1,),(13,),(2**31-1,),(1,1)):
            for inline in (False,True):
                with self.subTest(ids=ids,inline=inline),self.assertRaises(ILUnsupported):
                    self.proof(values=(ids,*VALUES[1:]),inline=inline)
        code=group_body(inline=True).replace(b'\x25'+ldc(0)+ldc(9)+b'\x9e',b'\x25'+ldc(2)+ldc(9)+b'\x9e',1)
        with self.assertRaisesRegex(ILUnsupported,'ARRAY_INDEX'):self.proof(group_code=code)

    def test_all_caller_and_helper_stack_budgets_are_checked(self):
        for method,required in ((2,5),(3,2),(4,3),(5,4),(6,2)):
            with self.subTest(method=method),self.assertRaisesRegex(ILUnsupported,'MAXSTACK'):
                self.proof(maxstack={method:required-1})
            self.assertEqual(7,self.proof(maxstack={method:required})['stats']['groupCount'])

    def test_missing_duplicate_foreign_and_extra_group_fields_rejected(self):
        code=group_body()
        for changed in (group_body(VALUES[:-1]),
                        code.replace(tok(0x80,0x04000005),tok(0x80,0x04000004)),
                        code.replace(tok(0x80,0x04000004),tok(0x80,0x04000001)),
                        code.replace(tok(0x80,0x04000004),tok(0x80,0x0400000e)),
                        code[:-1]+group_body(VALUES[:1])[15:]):
            with self.subTest(code=changed.hex()),self.assertRaises(ILUnsupported):self.proof(group_code=changed)
        for opts in ({'field_names':{4:'AlmostGroup'}},{'field_names':{5:GROUPS[0]}},
                     {'type_first_fields':{5:10}},{'type_first_fields':{5:12}}):
            with self.subTest(opts=opts),self.assertRaises(ILUnsupported):self.proof(**opts)

    def test_exact_types_signatures_flags_and_intrinsics(self):
        for opts in ({'field_flags':{3:0x36}},{'field_flags':{4:0x6}},{'field_flags':{4:0x116}},
                     {'field_flags':{4:0x2016}},{'field_signatures':{3:b'\x06\x1c'}},
                     {'field_signatures':{4:b'\x06\x1d\x08'}},{'ref_names':{2:'UInt32'}},
                     {'ref_names':{3:'Byte'}},{'initialize_name':'Untrusted'},
                     {'initialize_owner':9},{'key':b'notcore!'},{'dequeue_name':'Peek'},
                     {'queue_signature':b'\x15\x12\x21\x01\x1d\x08'},
                     {'layout_delta':{0:4}},{'layout_pack':{0:4}},
                     {'field_flags':{14:0x11}},{'type_flags':{7:0x130}},
                     {'type_flags':{4:0x12}},{'type_flags':{4:0x82}},{'type_flags':{4:1}}):
            with self.subTest(opts=opts),self.assertRaises(ILUnsupported):self.proof(**opts)

    def test_group_initializer_locals_eh_signature_and_flags(self):
        for opts in ({'group_locals':True},{'header_flags':{2:0x301b}},
                     {'signatures':{2:b'\x00\x00\x08'}},{'method_flags':{2:0x91}},
                     {'method_flags':{2:0x1881}}):
            with self.subTest(opts=opts),self.assertRaises(ILUnsupported):self.proof(**opts)

    def test_unknown_effect_stack_residue_branch_and_wrong_targets(self):
        code=group_body()
        for changed in (b'\x00'+code,b'\x16'+code,b'\x2b\x00'+code,b'\x2a'+code,
                        code+b'\x00',code[:-1],code[:-1]+tok(0x28,0x0a000001)+b'\x2a',
                        code.replace(tok(0x7e,0x04000003),tok(0x7e,0x04000004),1),
                        code.replace(tok(0x73,0x06000003),tok(0x73,0x06000004),1),
                        code.replace(tok(0x6f,0x06000004),tok(0x28,0x06000004),1),
                        code.replace(tok(0x6f,0x06000004),tok(0x6f,0x06000005),1),
                        code.replace(tok(0x28,0x0a000002),tok(0x28,0x0a000001),1),
                        code[:-1]+tok(0x7f,0x04000004)+b'\x26\x2a'):
            with self.subTest(code=changed.hex()),self.assertRaises(ILUnsupported):self.proof(group_code=changed)

    def test_unfresh_or_escaping_factory_and_unknown_helper_effects(self):
        for mutate in (lambda c:c[11:],lambda c:c[:11]+c[22:],
                       lambda c:c[:-1]+b'\x02'+tok(0x28,0x06000004)+b'\x2a',
                       lambda c:c.replace(b'\x02\x03\x7d',b'\x02\x17\x7d')):
            with self.subTest(mutate=mutate),self.assertRaises(ILUnsupported):self.proof(constructor=mutate)
        for opts in ({'wrapper':b'\x02\x17\x03'+tok(0x28,0x06000005)+b'\x2a'},
                     {'getter':b'\x2a'},{'fill':b'\x2a'},{'fill_locals':b'\x07\x00'},
                     {'getter_eh':lambda e:e[:4]+bytes(len(e)-4)}):
            with self.subTest(opts=opts),self.assertRaises(ILUnsupported):self.proof(**opts)

    def test_no_module_or_factory_initializer_effect_is_assumed(self):
        # Reassigning method ownership exposes an unexpected .cctor to the
        # module, or moves the group .cctor onto SetFactory. Both must fail.
        for opts in ({'type_first_methods':{2:2}},{'type_first_methods':{5:2}}):
            with self.subTest(opts=opts),self.assertRaises(ILUnsupported):self.proof(**opts)

    def test_shared_limits_and_cancellation(self):
        for changes in ({'steps':1},{'instructions':1},{'method_bytes':1},{'total_method_bytes':1},
                        {'literal_ids':1},{'literal_bytes':1}):
            with self.subTest(changes=changes),self.assertRaises(ILUnsupported):
                self.proof(limits=replace(ItemDispatchSetLimits(),**changes))
        p=program();domain=self.count(p)
        error=ValueError('original cancellation')
        def cancel():raise error
        p.budget.external=cancel
        with self.assertRaises(_CheckpointCancelled) as caught:_prove(p,domain)
        self.assertIs(error,caught.exception.original)
        with patch('resource_pipeline.item_texture_aliases.time.monotonic',side_effect=[0,120]):
            with self.assertRaisesRegex(ILUnsupported,'TIME_LIMIT'):program()
        p=program();domain=self.count(p)
        p.budget.limits=replace(p.budget.limits,evidence_bytes=16*1024)
        with self.assertRaises(EvidenceSizeLimit):_prove(p,domain)

    def test_public_api_has_fixed_profiles_no_proof_override_and_early_cancel(self):
        self.assertEqual(['input_path','checkpoint'],list(inspect.signature(extract_prefix_group_semantics).parameters))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'original.dll';path.write_bytes(fixture())
            with self.assertRaisesRegex(ILUnsupported,'UNSUPPORTED_INPUT_PROFILE'):
                extract_prefix_group_semantics(path)
            error=RuntimeError('cancel')
            def cancel():raise error
            with self.assertRaises(RuntimeError) as caught:extract_prefix_group_semantics(path,cancel)
            self.assertIs(error,caught.exception)


if __name__ == '__main__':unittest.main()
