"""Original localization behavior fixtures; no game text or executable input."""
import unittest

from resource_pipeline.locale_semantics import embedded_baselines
from resource_pipeline.security import PipelineError


def baseline(entries, **kwargs):
    resources, languages = [], {}
    for n,(locale,strings) in enumerate(entries):
        name = f'fixture{n}'
        row = {'name':name,'language':locale,'status':'EXTRACTED','sha256':str(n)*64,'dataOffset':100+n}
        resources.append(row)
        languages[name] = {'strings':strings,'keyEvidence':{key:{'jsonPointer':'/Fixture/Value'} for key in strings}}
    return embedded_baselines(languages,resources,**kwargs)


class LocaleSemanticsTests(unittest.TestCase):
    def test_default_fallback_then_target_override(self):
        result = baseline([('en-US',{'C.A':'english','C.OnlyEnglish':'fallback'}),('zh-Hans',{'C.A':'target'})])['zh-Hans']
        self.assertEqual({'C.A':'target','C.OnlyEnglish':'fallback'},result['strings'])
        self.assertEqual(['C.OnlyEnglish'],result['fallbackKeys'])
        self.assertEqual('zh-Hans',result['keyEvidence']['C.A']['sourceLocale'])
        self.assertFalse(result['binaryLoaderEquivalenceVerified'])
        self.assertFalse(result['complete'])

    def test_fallback_copy_commands_resolve_before_target_load(self):
        result = baseline([('en-US',{'C.A':'english','C.Inherited':'{$C.A}'}),('zh-Hans',{'C.A':'target','C.New':'{$C.A}'})])['zh-Hans']
        self.assertEqual('english',result['strings']['C.Inherited'])
        self.assertEqual('target',result['strings']['C.New'])
        self.assertEqual(2,len(result['copyPasses']))

    def test_copy_chains_missing_references_and_two_part_regex(self):
        result = baseline([('en-US',{'C.A':'{$C.B}','C.B':'{$C.C}','C.C':'final','C.Missing':'{$No.Key}','C.NotMatch':'{$One.Two.Three}'})])['en-US']
        self.assertEqual('final',result['strings']['C.A'])
        self.assertEqual('No.Key',result['strings']['C.Missing'])
        self.assertEqual('{$One.Two.Three}',result['strings']['C.NotMatch'])
        self.assertEqual('MISSING_REFERENCE',result['copyPasses'][0]['missingReferences'][0]['code'])

    def test_cycles_are_explicit_even_when_text_stabilizes(self):
        result = baseline([('en-US',{'C.A':'{$C.B}','C.B':'{$C.A}','C.Self':'{$C.Self}'})])['en-US']
        self.assertTrue(any(row['code']=='REFERENCE_CYCLE' for row in result['copyPasses'][0]['errors']))
        self.assertEqual('REFERENCE_MODEL_WITH_ERRORS',result['status'])

    def test_explosive_and_iteration_limits_are_bounded_errors(self):
        result = baseline([('en-US',{'C.A':'{$C.A}x'})],string_limit=32)['en-US']
        self.assertTrue(any(row['code']=='REFERENCE_STRING_LIMIT' for row in result['copyPasses'][0]['errors']))
        self.assertLessEqual(len(result['strings']['C.A']),32)
        result = baseline([('en-US',{'C.A':'{$C.B}{$C.B}','C.B':'text'})],operation_limit=1)['en-US']
        self.assertTrue(any(row['code']=='REFERENCE_OPERATION_LIMIT' for row in result['copyPasses'][0]['errors']))

    def test_variants_are_separate_and_target_load_clears_fallback_variants(self):
        result = baseline([('en-US',{'C.A':'normal','C.A$Other':'alternate'}),('zh-Hans',{'C.A':'target','C.A$Target$Ignored':'variant'})])
        self.assertEqual({'C.A':'normal'},result['en-US']['strings'])
        self.assertEqual('alternate',result['en-US']['variants']['C.A$Other']['value'])
        self.assertNotIn('C.A$Other',result['zh-Hans']['variants'])
        self.assertEqual('variant',result['zh-Hans']['variants']['C.A$Target']['value'])

    def test_baseline_count_is_bounded(self):
        with self.assertRaises(PipelineError):
            baseline([(f'fixture-{n}',{'C.A':'value'}) for n in range(33)])
        result = baseline([('en-US',{'C.A':'{$C.B}','C.B':'abcdefgh'})],total_limit=12)['en-US']
        self.assertTrue(any(row['code']=='EFFECTIVE_LOCALE_TOTAL_LIMIT' for row in result['copyPasses'][0]['errors']))

    def test_bad_resource_stops_only_its_culture_reference_load(self):
        resources = [{'name':'bad','language':'en-US','status':'REJECTED_RESOURCE'},
                     {'name':'later','language':'en-US','status':'EXTRACTED','sha256':'a'*64,'dataOffset':1},
                     {'name':'good','language':'zh-Hans','status':'EXTRACTED','sha256':'b'*64,'dataOffset':2}]
        languages = {'later':{'strings':{'C.A':'skip'}},'good':{'strings':{'C.B':'keep'}}}
        result = embedded_baselines(languages,resources)['zh-Hans']
        self.assertEqual({'C.B':'keep'},result['strings'])
        self.assertEqual('CULTURE_LOAD_STOPPED_AT_BAD_RESOURCE',result['loadErrors'][0]['code'])


if __name__ == '__main__':
    unittest.main()
