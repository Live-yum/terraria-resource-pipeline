"""Original synthetic instruction bytes; no game code, CLR or original assets."""
from dataclasses import replace
import hashlib
import struct
import unittest

from resource_pipeline.static_il import (AbstractEvaluator, ILUnsupported, Instruction, Method,
    MetadataProgram, StaticILLimits, Value, bounded_evidence_json, decode_il,
    extract_item_default_stages, json_evidence_size, method_signature, select_stage_sample)
from resource_pipeline.security import PipelineError


FIELD=0x04000001
ENTRY=0x06000001
HELPER=0x06000002


def token(op,value):return bytes((op,))+struct.pack('<I',value)
def ldc(value):return b'\x20'+struct.pack('<i',value)


def method(code, token_=ENTRY, name='SetDefaults1', instance=True, returned='void', args=('i4',), locals_=(), initialized=False):
    return Method(token_,name,instance,returned,args,decode_il(code),
                  {'methodToken':f'0x{token_:08x}','methodName':name,'codeOffset':1000,
                   'codeBytes':len(code),'ilSha256':hashlib.sha256(code).hexdigest()},tuple(locals_),initialized,256)


class Program:
    def __init__(self,*methods,kind='i4',extra_fields=None):
        self.methods={row.token:row for row in methods}
        self.fields={FIELD:{'name':'OriginalField','kind':kind,'metadataOffset':500}}
        self.fields.update(extra_fields or {})
    def get(self,token_):
        if token_ not in self.methods:raise ILUnsupported('UNSUPPORTED_EXTERNAL_OR_GENERIC_CALL',token=token_)
        return self.methods[token_]


def stage(code,*,kind='i4',item_id=1,helpers=(),limits=StaticILLimits(),checkpoint=None,locals_=(),initialized=False,extra_fields=None):
    program=Program(method(code,locals_=locals_,initialized=initialized),*helpers,kind=kind,extra_fields=extra_fields)
    return AbstractEvaluator(program,limits,checkpoint).stage(ENTRY,item_id)


class StaticILTests(unittest.TestCase):
    def test_literal_numeric_boolean_float_and_offsets(self):
        for kind,code,value in [('i4',ldc(123),123),('bool',b'\x17',True),('r4',b'\x22'+struct.pack('<f',1.25),1.25)]:
            payload=b'\x02'+code+token(0x7d,FIELD)+b'\x2a'
            result=stage(payload,kind=kind)
            self.assertEqual('PROVEN_NUMERIC_STAGE_WRITES',result['status'])
            self.assertEqual(value,result['fields'][0]['value'])
            self.assertEqual(1000+1+len(code),result['fields'][0]['evidence']['instructionFileOffset'])
            self.assertFalse(result['executedInput'])
            self.assertFalse(result['finalItemDefaults'])

    def test_known_switch_and_default_paths(self):
        # ldarg.1; ldc.1; sub; switch(case1,case2); ret; case1; case2
        prefix=b'\x03\x17\x59\x45'+struct.pack('<I',2)
        first=b'\x02'+ldc(10)+token(0x7d,FIELD)+b'\x2a'
        second=b'\x02'+ldc(20)+token(0x7d,FIELD)+b'\x2a'
        base=len(prefix)+8
        code=prefix+struct.pack('<ii',1,1+len(first))+b'\x2a'+first+second
        for item_id,value in [(1,10),(2,20)]:self.assertEqual(value,stage(code,item_id=item_id)['fields'][0]['value'])
        self.assertEqual('NO_PROVEN_NUMERIC_WRITES',stage(code,item_id=3)['status'])
        self.assertIn(base+1,decode_il(code))

    def test_signed_and_unsigned_comparisons(self):
        # Numeric compare selects 1 or 2.
        for branch,expected in [(0x30,2),(0x35,1)]:
            code=ldc(-1)+ldc(0)+bytes((branch,))+struct.pack('<b',12)
            code+=b'\x02'+ldc(2)+token(0x7d,FIELD)+b'\x2a'
            code+=b'\x02'+ldc(1)+token(0x7d,FIELD)+b'\x2a'
            self.assertEqual(expected,stage(code)['fields'][0]['value'])

    def test_pure_numeric_helper_uses_its_actual_il_and_call_chain(self):
        # Original fixture: a*100+b, independent of any game helper implementation.
        helper=method(b'\x02'+ldc(100)+b'\x5a\x03\x58\x2a',HELPER,'FixturePrice',False,'i4',('i4','i4'))
        code=b'\x02'+ldc(3)+ldc(7)+token(0x28,HELPER)+token(0x7d,FIELD)+b'\x2a'
        result=stage(code,helpers=(helper,))
        self.assertEqual(307,result['fields'][0]['value'])
        self.assertEqual([f'0x{ENTRY:08x}'],result['fields'][0]['evidence']['callChain'])
        helper2=method(b'\x02\x03'+token(0x7d,FIELD)+b'\x2a',HELPER,'FixtureAssign',True,'void',('i4',))
        result=stage(b'\x02'+ldc(9)+token(0x28,HELPER)+b'\x2a',helpers=(helper2,))
        self.assertEqual([f'0x{ENTRY:08x}',f'0x{HELPER:08x}'],result['fields'][0]['evidence']['callChain'])

    def test_unknown_static_numeric_write_does_not_fabricate_a_value(self):
        code=b'\x02'+ldc(4)+token(0x7d,FIELD)+b'\x02'+token(0x7e,0x040000aa)+token(0x7d,FIELD)+b'\x2a'
        result=stage(code)
        self.assertEqual([],result['fields'])
        self.assertEqual('UNKNOWN_VALUE',result['excludedFields'][0]['reason'])

    def test_unknown_branch_and_call_only_return_prefix_evidence(self):
        prefix=b'\x02'+ldc(4)+token(0x7d,FIELD)
        code=prefix+token(0x7e,0x040000aa)+b'\x2c\x01\x00\x2a'
        result=stage(code)
        self.assertEqual('UNSUPPORTED_UNKNOWN_BRANCH',result['status'])
        self.assertEqual([],result['fields'])
        self.assertEqual(4,result['prefixFields'][0]['value'])
        result=stage(prefix+token(0x28,0x0a000001)+b'\x2a')
        self.assertEqual('UNSUPPORTED_EXTERNAL_OR_GENERIC_CALL',result['status'])
        self.assertEqual([],result['fields'])

    def test_loops_recursion_and_budgets_stop(self):
        self.assertEqual('UNSUPPORTED_LOOP',stage(b'\x2b\xfe')['status'])
        code=b'\x02\x03'+token(0x28,ENTRY)+b'\x2a'
        self.assertEqual('UNSUPPORTED_RECURSIVE_CALL',stage(code)['status'])
        code=b'\x00'*8+b'\x2a'
        self.assertEqual('METHOD_STEP_LIMIT',stage(code,limits=replace(StaticILLimits(),method_steps=3))['status'])
        self.assertEqual('TOTAL_STEP_LIMIT',stage(code,limits=replace(StaticILLimits(),total_steps=3))['status'])

    def test_integer_wrap_truncation_and_zero_extend(self):
        code=b'\x02'+ldc(0x7fffffff)+b'\x17\x58'+token(0x7d,FIELD)+b'\x2a'
        self.assertEqual(-2147483648,stage(code)['fields'][0]['value'])
        code=b'\x02'+ldc(0x1234abcd)+b'\xd2'+token(0x7d,FIELD)+b'\x2a'
        self.assertEqual(205,stage(code)['fields'][0]['value'])
        code=b'\x02'+ldc(-1)+b'\x6e'+token(0x7d,FIELD)+b'\x2a'
        self.assertEqual(4294967295,stage(code,kind='u8')['fields'][0]['value'])

    def test_unsigned_field_reload_preserves_stack_bit_pattern(self):
        code=b'\x02'+ldc(-1)+token(0x7d,FIELD)+b'\x02\x02'+token(0x7b,FIELD)+b'\x17\x58'+token(0x7d,FIELD)+b'\x2a'
        self.assertEqual(0,stage(code,kind='u4')['fields'][0]['value'])

    def test_stack_local_and_return_types_fail_closed(self):
        self.assertEqual('STACK_UNDERFLOW',stage(b'\x26\x2a')['status'])
        self.assertEqual('INVALID_VARIABLE_INDEX',stage(b'\x06\x26\x2a')['status'])
        code=b'\x22'+struct.pack('<f',1)+b'\x0a\x2a'
        self.assertEqual('INVALID_NUMERIC_STACK_TYPE',stage(code,locals_=('i4',))['status'])
        code=b'\x02'+ldc(1)+token(0x7d,FIELD)+b'\x2a'
        self.assertEqual('INVALID_NUMERIC_STACK_TYPE',stage(code,kind='i8')['status'])
        self.assertEqual('NONEMPTY_RETURN_STACK',stage(b'\x17\x2a')['status'])

    def test_numeric_locals_and_unknown_initial_fields(self):
        code=ldc(7)+b'\x0a\x02\x06'+token(0x7d,FIELD)+b'\x2a'
        self.assertEqual(7,stage(code,locals_=('i4',))['fields'][0]['value'])
        code=b'\x02\x02'+token(0x7b,FIELD)+b'\x17\x58'+token(0x7d,FIELD)+b'\x2a'
        self.assertEqual('UNKNOWN_VALUE',stage(code)['excludedFields'][0]['reason'])

    def test_non_numeric_assignment_excluded_without_touching_numeric_facts(self):
        code=b'\x02'+token(0x7e,0x040000aa)+token(0x7d,FIELD+1)+b'\x02'+ldc(3)+token(0x7d,FIELD)+b'\x2a'
        result=stage(code,extra_fields={FIELD+1:{'name':'ReferenceField','kind':'other'}})
        self.assertEqual(3,result['fields'][0]['value'])
        self.assertEqual('NON_NUMERIC_FIELD',result['excludedFields'][0]['reason'])

    def test_float_arithmetic_indirect_virtual_constructor_paths_are_explicit(self):
        float_=b'\x22'+struct.pack('<f',1.0)
        self.assertEqual('UNSUPPORTED_FLOAT_ARITHMETIC',stage(float_+float_+b'\x58\x26\x2a')['status'])
        for opcode,name in [(0x29,'UNSUPPORTED_INDIRECT_CALL'),(0x6f,'UNSUPPORTED_VIRTUAL_CALL'),(0x73,'UNSUPPORTED_CONSTRUCTOR_CALL')]:
            self.assertEqual(name,stage(token(opcode,0x0a000001)+b'\x2a')['status'])

    def test_cancellation_propagates_without_partial_success(self):
        calls=[0]
        def checkpoint():
            calls[0]+=1
            if calls[0]==3:raise PipelineError('cancelled')
        with self.assertRaisesRegex(PipelineError,'cancelled'):stage(b'\x00'*8+b'\x2a',checkpoint=checkpoint)

    def test_decoder_rejects_truncation_invalid_targets_and_unknown_opcodes(self):
        for code in (b'\x20\x01',b'\xfe',b'\x24',b'\x2b\x7f',b'\x45\xff\xff\xff\xff',b'\x2b\x01'+ldc(1)+b'\x2a'):
            with self.subTest(code=code),self.assertRaises(ILUnsupported):decode_il(code)
        with self.assertRaises(ILUnsupported):decode_il(b'\x00'*5,replace(StaticILLimits(),method_instructions=2))

    def test_call_depth_and_decode_byte_limits(self):
        helper=method(b'\x2a',HELPER,'FixtureVoid',False,'void',())
        self.assertEqual('CALL_DEPTH_LIMIT',stage(token(0x28,HELPER)+b'\x2a',helpers=(helper,),limits=replace(StaticILLimits(),call_depth=1))['status'])
        with self.assertRaises(ILUnsupported):decode_il(b'\x00'*8,replace(StaticILLimits(),method_bytes=4))

    def test_signature_and_sampling_bounds(self):
        self.assertEqual((True,'void',('i4',)),method_signature(b'\x20\x01\x01\x08'))
        for blob in (b'',b'\x60\x00\x01',b'\x10\x00\x01',b'\x00\x81'):
            with self.assertRaises(ILUnsupported):method_signature(blob)
        selected=select_stage_sample(range(1,7000))
        self.assertEqual(32,len(selected))
        self.assertEqual([1,2,3],selected[:3])
        self.assertEqual(6999,selected[-1])
        self.assertEqual([1,2],select_stage_sample([1,2]))



class StaticILPEIntegrationTests(unittest.TestCase):
    def test_explicit_item_and_base_layout_are_rejected_before_numeric_claims(self):
        import tempfile
        from pathlib import Path
        from il_fixture import item_stage_pe
        from resource_pipeline.server_semantics import extract_server_semantics
        for option in ('explicit_layout','explicit_base'):
            with self.subTest(option=option),tempfile.TemporaryDirectory() as root:
                path=Path(root)/'fixture.exe';path.write_bytes(item_stage_pe(**{option:True}))
                result=extract_server_semantics(path)['itemDefaultStages']
                self.assertEqual('PARTIAL',result['status'])
                self.assertEqual('UNSUPPORTED_EXPLICIT_FIELD_LAYOUT',result['diagnostic']['code'])
                self.assertEqual([],result['records'])
                self.assertEqual([1,2],result['unattemptedSelectedIds'])
                self.assertEqual(0,result['provenFieldAssignments'])

    def test_real_metadata_route_uses_synthetic_method_bodies_and_preserves_input(self):
        import tempfile
        from pathlib import Path
        from il_fixture import item_stage_pe
        from resource_pipeline.server_semantics import extract_server_semantics
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'fixture.exe';data=item_stage_pe();path.write_bytes(data)
            result=extract_server_semantics(path)
            proof=result['itemDefaultStages']
            self.assertEqual(data,path.read_bytes())
            self.assertEqual([1,2],proof['selection']['sampledIds'])
            self.assertEqual(0,proof['selection']['remainingCount'])
            self.assertEqual(10,len(proof['records']))
            first=next(row for row in proof['records'] if row['id']==1 and row['method']=='SetDefaults1')
            self.assertEqual(17,first['fields'][0]['value'])
            helper=next(row for row in proof['records'] if row['id']==2 and row['method']=='SetDefaults3')
            self.assertEqual(200,helper['fields'][0]['value'])
            self.assertEqual(['0x06000003','0x06000006'],helper['fields'][0]['evidence']['dependencyMethodTokens'])
            self.assertTrue(any(row['status']=='UNSUPPORTED_LOOP' for row in proof['records']))
            for method_ in proof['methods']:
                code=data[method_['codeOffset']:method_['codeOffset']+method_['codeBytes']]
                self.assertEqual(hashlib.sha256(code).hexdigest(),method_['ilSha256'])
            selected=extract_server_semantics(path,item_stage_ids=[2])['itemDefaultStages']
            self.assertEqual([2],selected['selection']['sampledIds'])
            self.assertEqual([1],selected['selection']['remainingIds'])
            with self.assertRaises(PipelineError):extract_server_semantics(path,item_stage_ids=[999])

    def test_private_receipt_redaction_and_wall_clock_bound(self):
        import tempfile
        import importlib.util
        import json
        from pathlib import Path
        from unittest.mock import patch
        from il_fixture import item_stage_pe
        from resource_pipeline.server_semantics import extract_server_semantics
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'fixture.exe';path.write_bytes(item_stage_pe())
            result=extract_server_semantics(path)
            script=Path(__file__).parents[1]/'scripts/ci_server_semantics.py'
            spec=importlib.util.spec_from_file_location('ci_stage',script);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            redacted=module.redact(result)
            self.assertNotIn('FixtureValue',json.dumps(redacted))
            self.assertNotIn('FixtureHelper',json.dumps(redacted))
            self.assertGreater(redacted['itemDefaultStages']['provenFieldAssignments'],0)
            with patch('resource_pipeline.static_il.time.monotonic',side_effect=[0,100]):
                timed=extract_server_semantics(path)['itemDefaultStages']
            self.assertEqual('TOTAL_TIME_LIMIT',timed['diagnostic']['code'])
            self.assertEqual([1,2],timed['unattemptedSelectedIds'])
            self.assertEqual([],timed['records'])


class StaticILResourceBoundTests(unittest.TestCase):
    def test_long_names_across_all_stage_pairs_stop_with_bounded_prefix_evidence(self):
        from unittest.mock import patch
        from resource_pipeline.security import canonical_json
        # The reviewed growth shape: 1024 fields * 4096-byte names, five stages
        # for each of 32 IDs. Without a shared evidence budget this exceeds
        # 700 MB, although it stays within the instruction-step allowance.
        fields={FIELD+n:{'name':str(n).zfill(4)+'x'*4092,'kind':'i4'} for n in range(1024)}
        code=b''.join(b'\x02\x17'+token(0x7d,field) for field in fields)+b'\x2a'
        methods=[method(code,ENTRY+n,'SetDefaults'+str(n+1)) for n in range(5)]
        program=Program(*methods,extra_fields=fields)
        program.by_name={row.name:[row.token] for row in methods};program.used={}
        old_get=program.get
        def get(token_):
            row=old_get(token_)
            if token_ not in program.used:
                program.evidence_budget.charge(row.evidence);program.used[token_]=row.evidence
            return row
        program.get=get
        def build(meta,types,limits,checkpoint,budget):
            program.evidence_budget=budget
            return program
        with patch('resource_pipeline.static_il.MetadataProgram',side_effect=build):
            result=extract_item_default_stages(None,None,range(1,33))
        self.assertEqual('TOTAL_EVIDENCE_BYTE_LIMIT',result['diagnostic']['code'])
        self.assertEqual('PARTIAL',result['status'])
        self.assertLessEqual(len(canonical_json(result)),StaticILLimits().evidence_bytes)
        self.assertLessEqual(result['evidenceBudget']['accountedConstructionBytes'],StaticILLimits().evidence_bytes)
        self.assertLess(result['analyzedInstructions'],32*5*3073)
        self.assertEqual(160,result['attemptedStagePairs']+result['unattemptedStagePairs'])
        self.assertGreater(result['unattemptedStagePairs'],0)
        self.assertTrue(result['unattemptedSelectedIds'])
        self.assertEqual('TOTAL_EVIDENCE_BYTE_LIMIT',result['records'][-1]['status'])
        self.assertEqual([],result['records'][-1]['fields'])
        self.assertGreater(result['prefixFieldAssignments'],0)

    def test_excluded_names_and_replaced_facts_consume_the_shared_budget(self):
        limits=replace(StaticILLimits(),evidence_bytes=32*1024)
        field={FIELD:{'name':'x'*4096,'kind':'other'}}
        code=(b'\x02\x14'+token(0x7d,FIELD))*100+b'\x2a'
        result=stage(code,extra_fields=field,limits=limits)
        self.assertEqual('TOTAL_EVIDENCE_BYTE_LIMIT',result['status'])
        self.assertEqual(1,len(result['excludedFields']))
        self.assertLess(result['analyzedInstructions'],100*3)
        field[FIELD]['kind']='i4'
        result=stage((b'\x02\x17'+token(0x7d,FIELD))*100+b'\x2a',extra_fields=field,limits=limits)
        self.assertEqual('TOTAL_EVIDENCE_BYTE_LIMIT',result['status'])
        self.assertEqual([],result['fields'])
        self.assertEqual(1,len(result['prefixFields']))

    def test_method_names_dependencies_and_static_reads_consume_evidence_budget(self):
        from resource_pipeline.static_il import EvidenceBudget
        budget=EvidenceBudget(20*1024)
        helper=method(b'\x2a',HELPER,'x'*4096,False,'void',())
        # Shared budget is also the metadata method-list budget.
        budget.charge(helper.evidence)
        budget.charge(helper.evidence)
        with self.assertRaisesRegex(ILUnsupported,'TOTAL_EVIDENCE_BYTE_LIMIT'):
            budget.charge(helper.evidence)
        code=b''.join(token(0x7e,0x04001000+n)+b'\x26' for n in range(1000))+b'\x2a'
        result=stage(code,limits=replace(StaticILLimits(),evidence_bytes=16*1024))
        self.assertEqual('TOTAL_EVIDENCE_BYTE_LIMIT',result['status'])
        self.assertGreater(len(result['unresolvedStaticReads']),0)
        self.assertLess(len(result['unresolvedStaticReads']),1000)
        # Increasing dependency/call-chain evidence is counted as serialized
        # data too, rather than charging only the numeric field's own name.
        evidence={'fieldName':'a','evidence':{'dependencyMethodTokens':['0x06000001']*256,'callChain':['0x06000001']*8}}
        budget=EvidenceBudget(16*1024)
        budget.charge(evidence);budget.charge(evidence)
        with self.assertRaisesRegex(ILUnsupported,'TOTAL_EVIDENCE_BYTE_LIMIT'):budget.charge(evidence)

    def test_field_and_method_indices_are_bounded_before_insertion(self):
        class Meta:
            checkpoint=staticmethod(lambda:None)
            def __init__(self,name='Field'):
                self.name=name;self.reads={};self.strings=0;self.blobs=0
            def row(self,table,rid):
                self.reads[table]=self.reads.get(table,0)+1
                if table==2:return (0,0,0,0,1,1),0
                if table==4:return (6,rid,1),0
                if table==6:return (0,0,6,rid,1,1),0
                raise AssertionError(table)
            def string(self,index):self.strings+=1;return self.name
            def blob(self,index):self.blobs+=1;return b'\x06\x08',0
        def types(fields,methods):
            return {1:{'fullName':'Terraria.Item','firstField':1,'lastField':1+fields,'firstMethod':1,'lastMethod':1+methods}}
        meta=Meta();program=MetadataProgram.__new__(MetadataProgram)
        with self.assertRaisesRegex(ILUnsupported,'FIELD_COUNT_LIMIT'):
            program.__init__(meta,types(1_000_000,0),replace(StaticILLimits(),fields=4))
        self.assertEqual(4,len(program.fields));self.assertEqual(4,meta.strings);self.assertEqual(4,meta.blobs)
        self.assertEqual(5,meta.reads[4])
        meta=Meta();program=MetadataProgram.__new__(MetadataProgram)
        with self.assertRaisesRegex(ILUnsupported,'METHOD_INDEX_COUNT_LIMIT'):
            program.__init__(meta,types(0,1_000_000),replace(StaticILLimits(),method_index=4))
        self.assertEqual({},program.method_rows);self.assertEqual(0,meta.reads.get(6,0));self.assertEqual(0,meta.strings)
        for fields,methods in [(100,0),(0,100)]:
            meta=Meta('x'*4096);program=MetadataProgram.__new__(MetadataProgram)
            with self.assertRaisesRegex(ILUnsupported,'METADATA_NAME_BYTE_LIMIT'):
                program.__init__(meta,types(fields,methods),replace(StaticILLimits(),metadata_name_bytes=8192))
            self.assertEqual(8192,program.name_bytes)
            self.assertEqual(2,len(program.fields) if fields else len(program.method_rows))
            self.assertEqual(3,meta.strings)

    def test_utf8_preflight_matches_canonical_and_bounds_single_string_chunks(self):
        from unittest.mock import patch
        from resource_pipeline.security import canonical_json
        value={'quotes':'"\\\n\u0000','unicode':'🦖汉é'*5000,'numbers':[None,False,True,-2,1.25]}
        expected=canonical_json(value)
        self.assertEqual(len(expected),json_evidence_size(value,len(expected)))
        self.assertEqual(expected,bounded_evidence_json(value,len(expected)))
        with patch('resource_pipeline.static_il.canonical_json',side_effect=AssertionError('must preflight')):
            with self.assertRaisesRegex(PipelineError,'EVIDENCE_JSON_BYTE_LIMIT'):
                bounded_evidence_json(value,len(expected)-1)
        huge='🦖'*1_000_000
        calls=[]
        import json
        encode=json.encoder.encode_basestring
        def bounded_encode(item):
            calls.append(len(item))
            return encode(item)
        with patch('resource_pipeline.static_il.json.encoder.encode_basestring',side_effect=bounded_encode):
            with self.assertRaisesRegex(PipelineError,'EVIDENCE_JSON_BYTE_LIMIT'):json_evidence_size(huge,32)
        self.assertLessEqual(max(calls),4096)
        with self.assertRaises(PipelineError):json_evidence_size(float('nan'),100)
        loop=[];loop.append(loop)
        with self.assertRaises(PipelineError):json_evidence_size(loop,100)

    def test_api_and_cli_reject_oversized_final_output_before_canonical_allocation(self):
        import contextlib
        import io
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from il_fixture import item_stage_pe
        from resource_pipeline.server_semantics import SemanticLimits,extract_server_semantics,main
        with tempfile.TemporaryDirectory() as root:
            source=Path(root)/'fixture.exe';source.write_bytes(item_stage_pe())
            output=Path(root)/'result.json';output.write_bytes(b'prior evidence')
            with patch('resource_pipeline.static_il.canonical_json',side_effect=AssertionError('must preflight')):
                with self.assertRaisesRegex(PipelineError,'SEMANTIC_OUTPUT_REJECTED: EVIDENCE_JSON_BYTE_LIMIT'):
                    extract_server_semantics(source,limits=replace(SemanticLimits(),output_bytes=1000))
                # Cover CLI's independent write-boundary guard, even if an
                # injected producer accidentally bypasses its API preflight.
                with patch('resource_pipeline.server_semantics.extract_server_semantics',return_value={'huge':'x'*2000}),\
                     patch('resource_pipeline.server_semantics.SemanticLimits',return_value=replace(SemanticLimits(),output_bytes=1000)),\
                     contextlib.redirect_stdout(io.StringIO()) as stdout:
                    self.assertEqual(1,main([str(source),str(output)]))
            self.assertIn('EVIDENCE_JSON_BYTE_LIMIT',stdout.getvalue())
            self.assertEqual(b'prior evidence',output.read_bytes())
            self.assertEqual([],list(Path(root).glob('.server-semantics-*')))


if __name__=='__main__':unittest.main()
