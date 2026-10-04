"""Original material fragment source contracts, not a C# compile/runtime claim."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'tools' / 'RuntimeObservationCollector'
GETTERS = {
    'SubTiles', 'Alternates', 'LinkedAlternates', 'Width', 'Height',
    'CoordinateWidth', 'CoordinateHeights', 'CoordinatePadding', 'CoordinatePaddingFix',
    'Style', 'StyleMultiplier', 'StyleHorizontal', 'StyleWrapLimit', 'StyleLineSkip',
    'RandomStyleRange', 'SpecificRandomStyles', 'GetStyleOverride',
}


class MaterialObservationCollectorSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'MaterialObservation.cs').read_text()
        cls.self_test = (ROOT / 'MaterialObservationSelfTest.cs').read_text()

    def test_closed_pinned_getter_set_and_no_unsafe_calls(self):
        calls = set(re.findall(r'owner.Call\("(material[A-Za-z]+)"', self.source))
        self.assertEqual(calls, {'material' + name for name in GETTERS})
        for banned in ('CoordinateFullWidth', 'CoordinateFullHeight', 'CalculatePlacementStyle',
                       'GetTileData', 'CanPlace', 'DynamicInvoke', 'GetProperties(',
                       'GetProperty(', 'GetMethod(', 'Assembly.Load', 'Activator.CreateInstance'):
            self.assertNotIn(banned, self.source)
        self.assertIn('s.HasGetStyleOverride=owner.Call("materialGetStyleOverride",node)!=null;', self.source)

    def test_source_recursion_guard_precedes_getters(self):
        self.assertIn('baseObject=owner.Read("material.baseObject"); ExactNode(baseObject);', self.source)
        self.assertIn('foreach (string key in Modules) Module(key,baseObject,true);', self.source)
        self.assertIn('Module(key,node,key=="material._tileObjectStyle")', self.source)
        capture = self.source.index('internal override MaterialNodeSnapshot Capture')
        self.assertLess(self.source.index('Validate(node);', capture), self.source.index('owner.Call(', capture))
        self.assertIn('Object.ReferenceEquals(baseObject,owner.Read("material.baseObject"))', self.source)
        self.assertIn('value.GetType()==owner.fields[key].FieldType', self.source)
        self.assertIn('listType.GetGenericTypeDefinition()==typeof(List<>)', self.source)

    def test_iterative_identity_graph_and_cumulative_bounds(self):
        self.assertIn('RuntimeHelpers.GetHashCode(value)', self.source)
        self.assertIn('Object.ReferenceEquals(left,right)', self.source)
        self.assertIn('for (int index=0;index<graph.queue.Count;index++)', self.source)
        self.assertNotIn('foreach (object node in', self.source)
        for reason in ('MATERIAL_NODE_LIMIT', 'MATERIAL_CHILD_ARRAY_LIMIT', 'MATERIAL_GRAPH_EDGE_LIMIT',
                       'MATERIAL_COORDINATE_VALUE_LIMIT', 'MATERIAL_RANDOM_VALUE_LIMIT',
                       'MATERIAL_INTEGER_ARRAY_LIMIT', 'MATERIAL_OUTPUT_BYTE_LIMIT'):
            self.assertIn(reason, self.source)
        self.assertIn('count<=maximum-used', self.source)
        self.assertIn('this(65536,65536,1000000,1000000,1000000)', self.source)
        self.assertIn('PrimitiveJson.Encode(result).Length<=32*1024*1024', self.source)
        self.assertIn('return (int[])values.Clone();', self.source)

    def test_separate_placements_and_explicit_absence(self):
        self.assertIn('Integer("material.placeStyle",item)', self.source)
        self.assertNotIn('"item.placeStyle"', self.source)
        self.assertNotIn('FixedProfile.ItemFields', self.source)
        for key in ('samplePresent', 'requestedId', 'resolvedType', 'createTile', 'placeStyle'):
            self.assertIn('"' + key + '"', self.source)
        self.assertIn('present?(object)Integer("identity.type",item):null', self.source)
        self.assertIn('for (int id=1;id<itemCount;id++)', self.source)
        for unsupported in ('Math.Abs(', 'Math.Max(', 'Math.Min(', '(ushort)', '(uint)'):
            self.assertNotIn(unsupported, self.source)

    def test_item_english_value_is_observed_without_culture_switch_or_fallback(self):
        self.assertIn('object text=Call("materialItemName",null,id);', self.source)
        self.assertIn('text.GetType()==textType && Object.ReferenceEquals(text.GetType().Assembly,game)', self.source)
        self.assertIn('fields["localizedText.EnglishValue"].DeclaringType==textType', self.source)
        self.assertIn('fields["localizedText.Key"].DeclaringType==textType', self.source)
        self.assertIn('"englishValue",ObservedText(Read("localizedText.EnglishValue",text))', self.source)
        self.assertIn('"localizationKey",ObservedText(Read("localizedText.Key",text))', self.source)
        for prohibited in ('SetLanguage', 'get_EnglishValue', '??', 'String.IsNullOrEmpty', '.Trim('):
            self.assertNotIn(prohibited, self.source)
        # ObservedText retains null and empty strings, with exact string typing.
        observed_text = (ROOT / 'PlayerObservation.cs').read_text()
        self.assertIn('value==null || value.GetType()==typeof(string)', observed_text)
        self.assertIn('return value; // No replacement string', observed_text)

    def test_registry_count_is_independent_of_tile_domain(self):
        self.assertIn('tileCount==754', self.source)
        self.assertIn('ArrayField("material.frameImportant",typeof(bool),tileCount).Clone()', self.source)
        self.assertIn('registry.Length<=tileCount', self.source)
        self.assertNotIn('registry.Length==tileCount', self.source)
        self.assertIn('"registrationPresent",id<registry.Length', self.source)
        self.assertIn('"tileObjectDataCount",registry.Length', self.source)
        self.assertIn('if (children==null) return null;', self.source)

    def test_pixel_flags_are_full_cloned_source_registries(self):
        self.assertIn('int tileCount=Integer("tileId.Count");', self.source)
        self.assertNotIn('"material.tileCount"', self.source)
        for name in ('tileSolid', 'tileSolidTop', 'tileSand'):
            self.assertIn(f'"{name}",(bool[])ArrayField("material.{name}",typeof(bool),tileCount).Clone()', self.source)
        self.assertIn('"pixelFlags",pixelFlags', self.source)
        self.assertNotIn('pixelClassification', self.source)

    def test_partial_identity_and_no_completion_promotion(self):
        for key in ('isolationVerified', 'initializationVerified', 'sourceSemanticsVerified', 'complete', 'publishable'):
            self.assertIn(f'"{key}",false', self.source)
        for text in ('"evidenceLevel","DERIVED_ONLY"', '"status","PARTIAL"',
                     '"sourceSha256",FixedProfile.GameSha256', '"culture",culture', '"context",context',
                     'final-map-options-and-localized-names', 'ordered-shape-and-alias-policy'):
            self.assertIn(text, self.source)

    def test_original_self_test_covers_identity_cycles_signed_values_and_bounds(self):
        self.assertIn('new object[]{null,b,a,b}', self.self_test)
        self.assertIn('new object[]{a,a,null},4', self.self_test)
        self.assertIn('new int[]{-7,4,4}', self.self_test)
        self.assertIn('blockedReader.Captures==0', self.self_test)
        self.assertIn('i<2048', self.self_test)
        self.assertIn('Vendor equality must not run', self.self_test)
        self.assertIn('MATERIAL_COORDINATE_VALUE_LIMIT', self.self_test)
        self.assertIn('MATERIAL_RANDOM_VALUE_LIMIT', self.self_test)
        self.assertNotIn('FixedCollector(', self.self_test)
        self.assertNotIn('Assembly.Load', self.self_test)


if __name__ == '__main__':
    unittest.main()
