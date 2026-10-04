from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import struct
import tempfile
import unittest

from map_palette_fixture import fixture
from id_count_fixture import dependency_fixture
from xna_color_fixture import fixture as color_fixture
from resource_pipeline.item_texture_aliases import _Program,_Budget,ItemTextureAliasLimits
from resource_pipeline.id_count_semantics import prove_id_dictionary_dependency,prove_id_count_initializer
from resource_pipeline.xna_color_semantics import prove_color_intrinsics
from resource_pipeline.map_palette_semantics import prove_map_palette,extract_map_palette_semantics
from resource_pipeline.static_il import ILUnsupported


def program(raw):return _Program(raw,_Budget(ItemTextureAliasLimits(instructions=1000000,steps=2000000),None))


class MapPaletteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dependency=prove_id_dictionary_dependency(program(dependency_fixture()))
        cls.colors=prove_color_intrinsics(program(color_fixture()))

    def model(self,**options):
        p=program(fixture(**options))
        domains={n:prove_id_count_initializer(p,'Terraria.ID.'+n,self.dependency) for n in ('TileID','WallID')}
        return prove_map_palette(p,domains,self.colors)

    def test_original_model_covers_both_complete_count_intervals(self):
        v=self.model();self.assertEqual([2,1],[len(v['rows'][k]) for k in ('tiles','walls')])
        self.assertEqual([0xff1e140a],v['rows']['tiles'][0]['packedRgba'])
        self.assertEqual([0xff5a5046],v['rows']['walls'][0]['packedRgba'])
        self.assertTrue(v['targetPaletteModelComplete'])
        for key in ('complete','publishable','runtimeSnapshotUsable','runtimeDependencyBindingVerified',
                    'wholeInitializerProven','legendTailNonmutationProven','materialBaseReady','executedInput'):
            self.assertFalse(v[key])

    def test_unknown_gradient_is_not_fabricated_and_alias_to_target_is_rejected(self):
        v=self.model(unknown=True);self.assertEqual(1,v['unprovenPaletteSlots'])
        self.assertEqual(self.model()['rows'],v['rows'])
        for index in (1,2,3):
            with self.subTest(index=index),self.assertRaises(ILUnsupported):self.model(unknown=True,unknown_index=index)

    def test_unknown_indices_control_and_reference_type_cannot_be_ignored(self):
        for option in ('unknown_index_use','unknown_control','wrong_local_type','huge_array','invalid_bounds','unknown_effect','opaque_pointer','opaque_arithmetic'):
            with self.subTest(option=option),self.assertRaises(ILUnsupported):self.model(**{option:True})

    def test_named_color_comes_from_model_and_q16_scale_preserves_alpha(self):
        self.assertEqual([0x87654321],self.model(named=True)['rows']['tiles'][0]['packedRgba'])
        self.assertEqual([0xff0f0a05],self.model(scale=0.5)['rows']['tiles'][0]['packedRgba'])
        scale=struct.unpack('<f',struct.pack('<f',0.8))[0]
        factor=int(scale*65536)
        expected=0xff000000|sum((v*factor>>16)<<(8*i) for i,v in enumerate((10,20,30)))
        self.assertEqual([expected],self.model(scale=scale)['rows']['tiles'][0]['packedRgba'])

    def test_identity_signature_fields_headers_and_declared_stack(self):
        mutations=[{'assembly_keys':{'Microsoft.Xna.Framework':b'badbytes'}},
          {'ref_names':{'Microsoft.Xna.Framework.Color':'OtherColor'}},
          {'field_flags':{'colorLookup':0x16}},{'field_signatures':{'tileLookup':b'\x06\x1d\x08'}},
          {'member_names':{'.ctor':'Other'}},{'member_signatures':{'.ctor':b'\x20\x03\x01\x0c\x0c\x0c'}},
          {'maxstack':{'initialize':2}},{'maxstack':{'multiply':1}},
          {'header_flags':{'initialize':0x3003}},{'method_flags':{'initialize':0x91}},
          {'method_flags':{'legend':0x91}},{'locals':{'initialize':b'\x07\x00'},'unknown':True},
          {'counts':{'TileID':3}},{'counts':{'WallID':2}}]
        for option in mutations:
            with self.subTest(option=option),self.assertRaises(ILUnsupported):self.model(**option)

    def test_forged_count_values_and_evidence_do_not_bind(self):
        p=program(fixture());d={n:prove_id_count_initializer(p,'Terraria.ID.'+n,self.dependency) for n in ('TileID','WallID')}
        for value in (1,3,True):
            wrong=deepcopy(d);wrong['TileID']['count']=value
            with self.assertRaises(ILUnsupported):prove_map_palette(p,wrong,self.colors)
        d['TileID']['countMethodEvidence']['ilSha256']='0'*64
        with self.assertRaises(ILUnsupported):prove_map_palette(p,d,self.colors)

    def test_review_regressions_distinguish_value_and_managed_address(self):
        for opt in ('ref_multiply','ref_equality','value_getter','value_ctor'):
            with self.subTest(opt=opt),self.assertRaisesRegex(ILUnsupported,'COLOR_'):
                self.model(**{opt:True})
        self.assertEqual(self.model()['rows'],self.model(address_getter=True)['rows'])

    def test_missing_outputs_and_definite_integer_exception_fail_cleanly(self):
        with self.assertRaisesRegex(ILUnsupported,'REQUIRED_OUTPUT_FIELD_MISSING'):
            self.model(empty_outputs=True)
        with self.assertRaisesRegex(ILUnsupported,'INTEGER_DIVIDE_BY_ZERO'):
            self.model(opaque_zero_division=True)

    def test_public_source_profile_is_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.dll';xna=Path(tmp)/'color.dll';source.write_bytes(fixture());xna.write_bytes(color_fixture())
            with self.assertRaisesRegex(ILUnsupported,'UNSUPPORTED_SOURCE_PROFILE'):
                extract_map_palette_semantics(source,xna_path=xna)
            def stop():raise RuntimeError('stop')
            with self.assertRaisesRegex(RuntimeError,'stop'):extract_map_palette_semantics(source,xna_path=xna,checkpoint=stop)


if __name__=='__main__':unittest.main()
