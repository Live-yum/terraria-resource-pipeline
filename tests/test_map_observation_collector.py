"""Original source-contract checks only; C# compilation/observation is separate."""
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]/'tools/RuntimeObservationCollector'
class MapObservationTests(unittest.TestCase):
    def test_fixed_pins_and_no_render_entry(self):
        p=json.loads((ROOT/'fixed-profile.json').read_text())
        keys={r['key'] for r in p['methods']}
        self.assertIn('mapInitialize',keys)
        method=next(r for r in p['methods'] if r['key']=='mapInitialize')
        self.assertEqual((method['owner'],method['name']),('Terraria.Map.MapHelper','Initialize'))
        self.assertEqual(p['xnaColorField']['signatureHex'],'0609')
        s=(ROOT/'MapObservation.cs').read_text()
        for forbidden in ('GetMethod(', 'GetField(', 'BoringSetup','Assembly.Load','Activator.CreateInstance'):
            self.assertNotIn(forbidden,s)
        self.assertIn('field.DeclaringType==colorType',s)
        self.assertIn('CheckAssembly(assembly)',s)
    def test_full_options_and_missing_legends_are_preserved(self):
        s=(ROOT/'MapObservation.cs').read_text()
        self.assertIn('for (int id=0;id<count;id++)',s)
        self.assertIn('"optionCount",length',s)
        self.assertIn('"legendPresent",text!=null',s)
        self.assertIn('length==0 ||',s)
        self.assertIn('colors=(Array)colors.Clone(); legend=(Array)legend.Clone()',s)
        self.assertIn('"appOptionSelectionApplied",false',s)
        for key in ('complete','publishable','initializationVerified','sourceSemanticsVerified','isolationVerified'):
            self.assertIn('"'+key+'",false',s)
    def test_original_selftest_and_explicit_build_inputs(self):
        s=(ROOT/'SelfTest.cs').read_text()
        self.assertIn('MapColorBytes(0x04030201U)',s)
        self.assertIn('new ushort[]{65535,1}',s)
        self.assertIn('MaterialObservationSelfTest.Run()',s)
        for name in ('Build.ps1','RuntimeObservationCollector.csproj'):
            self.assertIn('MapObservation.cs',(ROOT/name).read_text())
    def test_versioned_integration_is_diagnostic(self):
        s=(ROOT/'FixedCollector.cs').read_text()
        self.assertIn('"schemaVersion",2,"kind","pinned-consumer-data-observation-fragment"',s)
        self.assertLess(s.index('CaptureMaterialObservation(samples'),s.index('CaptureMapObservation(culture'))
        self.assertIn('"mapObservation",mapObservation',s)
