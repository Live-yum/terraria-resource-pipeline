from dataclasses import replace
from fractions import Fraction
from pathlib import Path
import random
import struct
import tempfile
import unittest

from xna_color_fixture import fixture,assemble
from resource_pipeline.item_texture_aliases import _Program,_Budget,ItemTextureAliasLimits
from resource_pipeline.static_il import ILUnsupported
from resource_pipeline.xna_color_semantics import prove_color_intrinsics,extract_color_intrinsics,rgba_from_ints,multiply_rgba


def program(**opts):return _Program(fixture(**opts),_Budget(ItemTextureAliasLimits(),None))
def f32(value):return struct.unpack('<f',struct.pack('<f',value))[0]


class ColorSemanticsTests(unittest.TestCase):
    def test_whole_original_value_bodies_and_named_literals(self):
        r=prove_color_intrinsics(program())
        self.assertEqual(15,len(r.evidence['methodEvidence']))
        self.assertEqual(0x87654321,r.named['Black'])
        self.assertTrue(r.evidence['wholeBodiesProven'])
        for k in ['moduleInitializationProven','runtimeDependencyBindingVerified','executedInput','complete','publishable']:
            self.assertFalse(r.evidence[k])

    def test_all_methods_reject_unknown_statements_and_undersized_stacks(self):
        keys=['packed','clamp','rgb','R','G','B','A','alpha','equal','eq','multiply','Transparent','Black','Gray','LightGray']
        for key in keys:
            for opts in [{'method_code':{key:b'\x00\x2a'}},{'maxstack':{key:0}}]:
                with self.subTest(key=key,opts=opts),self.assertRaises(ILUnsupported):prove_color_intrinsics(program(**opts))

    def test_type_field_core_signature_layout_and_call_targets_fail_closed(self):
        opts=[{'type_flags':{2:0x100101}},{'type_flags':{2:0x100111}},{'type_base':{2:9}},
              {'field_flags':{'packed':0x11}},{'field_signatures':{'packed':b'\x06\x08'}},
              {'field_names':{'packed':'other'}},{'extra_field':True},{'cctor':True},
              {'assembly_keys':{'mscorlib':b'unknown!'}},{'ref_names':{'System.ValueType':'Object'}},
              {'member_names':{'uint-equals':'Mutate'}},{'member_signatures':{'uint-equals':b'\x20\x01\x02\x1c'}},
              {'method_flags':{'equal':0x86}},{'method_flags':{'multiply':0x886}},
              {'method_signatures':{'rgb':b'\x20\x03\x01\x09\x09\x09'}},
              {'locals':{'multiply':b'\x07\x00'}},{'impl_flags':{'R':1}},
              {'header_flags':{'equal':0x301b}},{'local_tokens':{'equal':0x11000001}}]
        for opt in opts:
            with self.subTest(opt=opt),self.assertRaises(ILUnsupported):prove_color_intrinsics(program(**opt))

    def test_unknown_public_vendor_and_cancellation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'original.dll';p.write_bytes(fixture())
            with self.assertRaisesRegex(ILUnsupported,'UNSUPPORTED_VENDOR'):extract_color_intrinsics(p)
            def cancel():raise RuntimeError('stop')
            with self.assertRaisesRegex(RuntimeError,'stop'):extract_color_intrinsics(p,cancel)

    def test_all_byte_values_scales_and_rgba_lanes_match_independent_fraction_reference(self):
        scales=[-2.,-0.,0.,2**-149,0.5,1.,1.25,2.,256.,f32(0.7),f32(0.8),f32(3.4028234663852886e38)]
        for scale in scales:
            fraction=Fraction(scale)*65536
            factor=max(0,min(16777215,int(fraction)))
            for n in range(256):
                packed=sum(n<<(8*i) for i in range(4))
                channel=min(255,int(Fraction(n*factor,65536)))
                self.assertEqual(sum(channel<<(8*i) for i in range(4)),multiply_rgba(packed,scale))
        rng=random.Random(317)
        for _ in range(300):
            packed=rng.randrange(2**32);scale=f32(rng.uniform(-1,3));factor=max(0,min(16777215,int(Fraction(scale)*65536)))
            expected=sum(min(255,((packed>>(8*i)&255)*factor)//65536)<<(8*i) for i in range(4))
            self.assertEqual(expected,multiply_rgba(packed,scale))

    def test_constructor_clamps_signed_inputs_and_scalar_domain_is_strict(self):
        for r in (-2**31,-1,0,127,255,256,2**31-1):
            self.assertEqual((255<<24)|(max(0,min(255,r)))|(19<<8)|(203<<16),rgba_from_ints(r,19,203))
        for args in [(True,0,0),(2**31,0,0),(-2**31-1,0,0),(0.5,0,0)]:
            with self.assertRaises(ILUnsupported):rgba_from_ints(*args)
        for packed,scale in [(True,0.5),(-1,0.5),(2**32,0.5),(0,0.7),(0,float('nan')),(0,float('inf')),(0,1)]:
            with self.assertRaises(ILUnsupported):multiply_rgba(packed,scale)


if __name__=='__main__':unittest.main()
