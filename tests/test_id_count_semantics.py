"""Original PE regressions: no private game image is required by this suite."""
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import inspect
import struct
import tempfile
import unittest

from id_count_fixture import dependency_fixture, game_fixture, token, ldc
from resource_pipeline.id_count_semantics import (
    extract_id_count_semantics, prove_id_count_program, prove_id_count_initializer,
    prove_id_dictionary_dependency,
)
from resource_pipeline.item_texture_aliases import _Program, _Budget, ItemTextureAliasLimits, _CheckpointCancelled
from resource_pipeline.static_il import ILUnsupported
from resource_pipeline.security import PipelineError


def program(raw, limits=None, checkpoint=None):
    return _Program(raw,_Budget(limits or ItemTextureAliasLimits(),checkpoint))


def dependency(**options):
    return prove_id_dictionary_dependency(program(dependency_fixture(**options)))


def proof(name='ItemID', **options):
    return prove_id_count_initializer(program(game_fixture(**options)),'Terraria.ID.'+name,dependency())


def body(key):
    keys=['ctor','create','generic','closure','predicate','action','init','singleton','named','value','key']
    p=program(dependency_fixture());m=p.body(0x06000001+keys.index(key))
    return p.meta.reader.take(m['evidence']['codeOffset'],m['size'])


class IdCountSemanticsTests(unittest.TestCase):
    def test_original_three_domains_have_independent_primitive_boundaries(self):
        d=dependency();p=program(game_fixture())
        for name,n,primitive in [('ItemID',13,'Int16'),('TileID',14,'UInt16'),('WallID',15,'UInt16')]:
            with self.subTest(name=name):
                r=prove_id_count_initializer(p,'Terraria.ID.'+name,d)
                self.assertEqual(n,r['count']);self.assertEqual(n,r['maxExclusive']);self.assertEqual(0,r['minInclusive'])
                self.assertEqual(primitive,r['primitiveType']);self.assertEqual('INITIALIZER_BOUNDARY',r['factScope'])
                self.assertTrue(r['wholeInitializerProven']);self.assertTrue(r['externalTailCountNonmutationProven'])
                self.assertTrue(r['independentDomainInitializerProven']);self.assertFalse(r['normalReturnGuaranteed'])
                self.assertFalse(r['runtimeSnapshotUsable']);self.assertEqual(1,r['reflectionEvidence']['publicPrimitiveLiteralFields'])
                self.assertEqual(1,r['countMethodEvidence']['requiredMaxStack'])

    def test_entire_dependency_and_cache_scan_are_evidence(self):
        e=dependency().evidence
        self.assertEqual(11,len(e['methodEvidence']))
        self.assertTrue(e['allElevenMethodsWholeBodyProven']);self.assertTrue(e['cachedDelegatesHaveOnlyFixedTargets'])
        self.assertFalse(e['gameFieldWrites']);self.assertTrue(e['countNonmutationRequiresConcretePrimitiveFieldProof'])
        self.assertEqual(11,e['cacheWriterScan']['methodBodiesDecoded'])
        self.assertEqual(4,e['cacheWriterScan']['directWriterCount'])
        self.assertTrue(e['cacheWriterScan']['completeScopedOperandScan'])
        self.assertFalse(e['cacheWriterScan']['fieldAddressTaking'])
        self.assertEqual(5,e['methodEvidence'][1]['requiredMaxStack'])

    def test_unknown_full_method_statements_cannot_hide_behind_supported_calls(self):
        for key in ('ctor','create','generic','closure','predicate','action','init','singleton','named','value','key'):
            code=body(key)
            for changed in (b'\x00'+code,code+b'\x00',code[:-1]+b'\x14\x26\x2a',code[:-1]):
                with self.subTest(key=key,changed=changed.hex()),self.assertRaises((ILUnsupported,PipelineError)):
                    dependency(method_code={key:changed})

    def test_callback_targets_branches_types_and_comparisons_fail_closed(self):
        cases=[('create',token(0xfe06,0x06000009),token(0xfe06,0x06000005)),
               ('create',token(0xfe06,0x06000006),token(0xfe06,0x06000009)),
               ('create',b'\x1f\x18',b'\x1f\x1c'),
               ('action',b'\x2f\x17',b'\x30\x17'),
               ('predicate',token(0x7b,0x04000004),token(0x7b,0x04000005)),
               ('generic',token(0x28,0x06000002),token(0x28,0x06000001))]
        for key,old,new in cases:
            code=body(key);self.assertIn(old,code)
            with self.subTest(key=key),self.assertRaises(ILUnsupported):dependency(method_code={key:code.replace(old,new,1)})
        code=bytearray(body('create'));code[32]=0 # cached nonnull branch now reaches pop with inconsistent grammar
        with self.assertRaises(ILUnsupported):dependency(method_code={'create':bytes(code)})

    def test_core_intrinsic_and_generic_signature_identity_is_exact(self):
        cases=[{'member_names':{'read':'SetValue'}},{'member_names':{'convert':'Invoke'}},
          {'member_signatures':{'read':b'\x20\x01\x08\x1c'}},
          {'member_signatures':{'dict':b'\x20\x01\x01\x1c'}},
          {'member_signatures':{'predicateCtor':b'\x20\x02\x01\x1c\x08'}},
          {'member_signatures':{'each':b'\x20\x01\x01\x1c'}},
          {'spec_signatures':{'first':b'\x0a\x01\x1c'}},
          {'spec_signatures':{'reverse':b'\x0a\x03\x08\x08\x0e'}},
          {'assembly_names':{'mscorlib':'FakeCore'}},{'assembly_keys':{'mscorlib':b'bad-key!'}},
          {'assembly_flags':{'System.Core':1}},{'assembly_names':{'System.Core':'mscorlib'}},
          {'ref_names':{'System.Collections.Generic.Dictionary`2':'OriginalDictionary`2'}},
          {'ref_names':{'System.Reflection.FieldInfo':'OriginalFieldInfo'}},
          {'spoof_core_type':True}]
        for options in cases:
            with self.subTest(options=options),self.assertRaises((ILUnsupported,PipelineError)):dependency(**options)

    def test_exact_field_storage_and_readonly_caches(self):
        for key in ('names','reverse','count','type','dictionary','singleton','predicate','value','key'):
            for flags in (0,0x10,0x116,0x36 if key!='singleton' else 0x16):
                with self.subTest(key=key,flags=flags),self.assertRaises(ILUnsupported):dependency(field_flags={key:flags})
        for flags in (0x100003,0x100113,0x100123):
            with self.subTest(flags=flags),self.assertRaises(ILUnsupported):dependency(type_flags={3:flags})

    def test_scan_proves_complete_direct_writer_closure_and_no_address_taking(self):
        for code in (b'\x14'+token(0x80,0x04000007)+b'\x2a',
                     token(0x7f,0x04000007)+b'\x26\x2a',
                     token(0x7c,0x04000007)+b'\x26\x2a',
                     token(0xd0,0x04000007)+b'\x26\x2a'):
            with self.subTest(code=code.hex()),self.assertRaisesRegex(ILUnsupported,'CACHE_'):
                dependency(extra_code=code)
        with self.assertRaisesRegex(ILUnsupported,'UNEXPECTED_WRITER'):dependency(alias_cache=True)
        # Unrelated well-formed IL is scanned, not semantically certified.
        self.assertEqual(12,dependency(extra_code=b'\x2a').evidence['cacheWriterScan']['methodBodiesDecoded'])

    def test_scanner_does_not_skip_malformed_or_unsupported_bodies(self):
        for code in (b'',b'\xff',b'\x7e\x01',b'\x2b\xff'):
            with self.subTest(code=code.hex()),self.assertRaises((ILUnsupported,PipelineError)):
                dependency(extra_code=code)
        for options in ({'header_flags':{'extra':0x301b}}, {'header_flags':{'extra':0x3023}},
                        {'impl_flags':{'extra':1}}, {'local_tokens':{'extra':0x01000001}}):
            with self.subTest(options=options),self.assertRaises((ILUnsupported,PipelineError)):
                dependency(extra_code=b'\x2a',**options)

    def test_maxstack_locals_and_method_flags_are_not_just_hash_checks(self):
        requirements={'ctor':2,'create':5,'generic':2,'closure':1,'predicate':2,'action':3,
                      'init':1,'singleton':1,'named':2,'value':1,'key':1}
        for key,n in requirements.items():
            with self.subTest(key=key),self.assertRaisesRegex(ILUnsupported,'MAXSTACK'):
                dependency(maxstack={key:n-1})
        for opts in ({'locals':{'create':b'\x07\x03\x1c\x08\x1c'}},
                     {'locals':{'action':b'\x07\x01\x07'}},
                     {'method_flags':{'generic':0x86}}, {'method_flags':{'init':0x91}}):
            with self.subTest(opts=opts),self.assertRaises((ILUnsupported,PipelineError)):dependency(**opts)

    def test_count_has_whole_body_and_exact_primitive_domain(self):
        base=ldc(13)+token(0x80,0x04000001)+token(0x28,0x2b000001)+token(0x80,0x04000002)+b'\x2a'
        for code in (base+b'\x00',b'\x00'+base,base[:-1]+token(0x80,0x04000001)+b'\x2a',
                     base.replace(token(0x80,0x04000002),token(0x80,0x04000001)),
                     base.replace(token(0x28,0x2b000001),token(0x6f,0x2b000001))):
            with self.subTest(code=code.hex()),self.assertRaises(ILUnsupported):proof(method_code={'ItemID.cctor':code})
        for name in ('ItemID','TileID','WallID'):
            limit=32767 if name=='ItemID' else 65535
            self.assertEqual(limit,proof(name,counts={name:limit})['count'])
            for n in (0,-1,limit+1):
                with self.subTest(name=name,n=n),self.assertRaisesRegex(ILUnsupported,'PRIMITIVE_DOMAIN'):
                    proof(name,counts={name:n})
        with self.assertRaisesRegex(ILUnsupported,'MAXSTACK'):proof(maxstack={'ItemID.cctor':0})

    def test_count_search_and_reflected_literal_metadata_are_exact(self):
        cases=[{'field_flags':{'ItemID.Count':0x16}},{'field_signatures':{'ItemID.Count':b'\x06\x08'}},
          {'field_flags':{'ItemID.Literal':0x16}},
          {'field_signatures':{'ItemID.Literal':b'\x06\x1c'}},
          {'field_names':{'ItemID.Literal':'Count'}},{'field_signatures':{'ItemID.Search':b'\x06\x1c'}},
          {'constant_type':8},{'constant_blob':b'\x01\x00\x00\x00'},
          {'spec_signatures':{'ItemID.Create':b'\x0a\x02\x12\x08\x07'}},
          {'member_names':{'ItemID.Create':'OriginalCreate'}},
          {'assembly_names':{'ReLogic':'NotReLogic'}},{'assembly_keys':{'ReLogic':b'anything'}},
          {'method_flags':{'ItemID.cctor':0x91}},{'type_base':{2:9}}]
        # Private literal is not selected by reflection and is deliberately okay.
        for options in cases:
            with self.subTest(options=options),self.assertRaises((ILUnsupported,PipelineError)):proof(**options)
        self.assertEqual(0,proof(field_flags={'ItemID.Literal':0x8051})['reflectionEvidence']['publicPrimitiveLiteralFields'])

    def test_no_dictionary_boolean_can_supply_a_dependency_completion(self):
        with self.assertRaisesRegex(ILUnsupported,'CERTIFICATE_TYPE'):
            prove_id_count_initializer(program(game_fixture()),'Terraria.ID.ItemID',{'status':'PROVEN','complete':True})

    def test_budgets_and_cancellation_abort_without_certificate(self):
        for key,n in (('method_bytes',10),('total_method_bytes',100),('instructions',10),('steps',100)):
            limits=replace(ItemTextureAliasLimits(),**{key:n})
            with self.subTest(key=key),self.assertRaises(ILUnsupported):
                prove_id_dictionary_dependency(program(dependency_fixture(),limits))
        class Cancelled(Exception):pass
        calls=0
        def checkpoint():
            nonlocal calls
            calls+=1
            if calls>50:raise Cancelled('original cancellation')
        with self.assertRaises(_CheckpointCancelled) as ctx:program(dependency_fixture(),checkpoint=checkpoint)
        self.assertIsInstance(ctx.exception.original,Cancelled)

    def test_coded_rid_overflow_cannot_alias_an_existing_type_or_member(self):
        # Coded-index storage can be wide in a PE. These mocked raw rows model
        # that width over our small original image without a multi-million-row
        # fixture. The resolver must validate the RID before combining tokens.
        from resource_pipeline.id_count_semantics import _member, _spec, DICT, M, FIELD, ENUM, FUNC
        p=program(dependency_fixture());original=p.row
        def bad_member(table,rid):
            row,offset=original(table,rid)
            if table==10 and rid==2:
                row=list(row);row[0]=((0x1000001)<<3)|4
            return row,offset
        with patch.object(p,'row',bad_member),self.assertRaisesRegex(ILUnsupported,'RID_RANGE'):
            _member(p,0x0a000002,DICT,'.ctor',M('void',this=True))
        def bad_spec(table,rid):
            row,offset=original(table,rid)
            if table==43 and rid==1:
                row=list(row);row[0]=((0x1000001)<<1)|1
            return row,offset
        with patch.object(p,'row',bad_spec),self.assertRaisesRegex(ILUnsupported,'RID_RANGE'):
            _spec(p,0x2b000001,'FirstOrDefault',M(('mvar',0),ENUM(('mvar',0)),FUNC(('mvar',0),'bool'),generic=1),(FIELD,))
        g=program(game_fixture());grow=g.row;dep=dependency()
        def bad_game_spec(table,rid):
            row,offset=grow(table,rid)
            if table==43 and rid==1:
                row=list(row);row[0]=((0x1000001)<<1)|1
            return row,offset
        with patch.object(g,'row',bad_game_spec),self.assertRaisesRegex(ILUnsupported,'RID_RANGE'):
            prove_id_count_initializer(g,'Terraria.ID.ItemID',dep)

    def test_public_entry_has_no_profile_override_and_rejects_synthetic_image(self):
        self.assertEqual(['input_path','checkpoint'],list(inspect.signature(extract_id_count_semantics).parameters))
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'original.dll';p.write_bytes(game_fixture())
            with self.assertRaisesRegex(ILUnsupported,'UNSUPPORTED_INPUT_PROFILE'):extract_id_count_semantics(p)
        with self.assertRaisesRegex(ILUnsupported,'UNSUPPORTED_INPUT_PROFILE'):prove_id_count_program(program(game_fixture()))
        self.assertEqual(['p'],list(inspect.signature(prove_id_count_program).parameters))
        error=RuntimeError('cancel before reading')
        def cancel():raise error
        with self.assertRaises(RuntimeError) as ctx:extract_id_count_semantics(Path('/missing'),checkpoint=cancel)
        self.assertIs(error,ctx.exception)


if __name__=='__main__':unittest.main()
