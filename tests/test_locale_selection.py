import hashlib
import tempfile
import unittest
from pathlib import Path
from resource_pipeline.security import PipelineError
from resource_pipeline.server_semantics import extract_server_semantics
from test_server_semantics import synthetic_pe

class LocaleSelectionTests(unittest.TestCase):
    def extract(self, resources, **options):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'server.exe';source.write_bytes(synthetic_pe(resources=resources))
            return extract_server_semantics(source,**options)
    def test_selected_locale_includes_english_fallback_and_preserves_all_hashes(self):
        resources={
            'Terraria.Localization.Content.en-US.Main.json':b'{"C":{"A":"base","B":"fallback"}}',
            'Terraria.Localization.Content.zh-Hans.Main.json':'{"C":{"A":"中文"}}'.encode(),
            'Terraria.Localization.Content.fr-FR.Main.json':b'{"C":{"A":"fr"}}',
        }
        full=self.extract(resources)
        selected=self.extract(resources,requested_locales=('zh-Hans',))
        self.assertEqual({'en-US','zh-Hans'},set(selected['localization']))
        for locale in ('en-US','zh-Hans'):
            def expanded(result):
                row=result['localization'][locale]
                baseline=dict(row['baseline'])
                baseline['copyPasses']=[result['localeEvidence']['copyPasses'][index]
                                       for index in baseline.pop('copyPassRefs')]
                return {**row,'baseline':baseline}
            self.assertEqual(expanded(full),expanded(selected))
        self.assertEqual('fallback',selected['localization']['zh-Hans']['strings']['C.B'])
        self.assertEqual(['fr-FR'],selected['localeSelection']['notRequestedCultures'])
        skipped=selected['resources'][selected['localeSelection']['notRequestedResourceRefs'][0]]
        self.assertEqual('NOT_REQUESTED',skipped['status'])
        for row in selected['resources']:
            self.assertEqual(hashlib.sha256(resources[row['name']]).hexdigest(),row['sha256'])
        self.assertEqual(3,len(selected['resources']))
        self.assertFalse(selected['complete']);self.assertFalse(selected['publishable'])
    def test_unrequested_malformed_culture_is_explicitly_unprocessed(self):
        resources={'Terraria.Localization.Content.en-US.Main.json':b'{"C":{"A":"base"}}',
                   'Terraria.Localization.Content.fr-FR.Main.json':b'not-json'}
        selected=self.extract(resources,requested_locales=['zh-Hans'])
        self.assertEqual(['zh-Hans'],selected['localeSelection']['missingRequested'])
        self.assertEqual('NOT_REQUESTED',selected['resources'][1]['status'])
        self.assertEqual('REJECTED_RESOURCE',self.extract(resources)['resources'][1]['status'])
    def test_selection_is_bounded_configuration_and_all_cultures_remain_available(self):
        for value in [[],(),['bad'],['en-US']*33,'en-US',[1]]:
            with self.assertRaises(PipelineError):self.extract({},requested_locales=value)
        resources={'Terraria.Localization.Content.fr-FR.Main.json':b'{"C":{"A":"fr"}}'}
        full=self.extract(resources)
        self.assertIn('fr-FR',full['localization'])
        self.assertNotIn('localeSelection',full)
        selected=self.extract(resources,requested_locales=('fr-FR',))
        self.assertEqual(['en-US','fr-FR'],selected['localeSelection']['effective'])
