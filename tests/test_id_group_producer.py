import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from baseline_fixture import baseline_pe
from resource_pipeline import id_count_semantics as counts, prefix_group_semantics as groups
from resource_pipeline.real_producer import RawEvidenceProducer
from resource_pipeline.security import PipelineError, sha256


class IdGroupProducerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.server = self.root / 'server'; self.server.mkdir()
        self.raw = baseline_pe(); self.digest = sha256(self.raw)
        (self.server / 'TerrariaServer.exe').write_bytes(self.raw)
        self.output = self.root / 'evidence'

    def models(self):
        common = {'inputSha256': self.digest, 'sourceRole': 'server', 'status': 'ORIGINAL_MODEL',
                  'wholeInitializerProven': True, 'complete': False, 'publishable': False,
                  'runtimeSnapshotUsable': False, 'normalReturnGuaranteed': False,
                  'futurePrivateField': 'PRIVATE_FUTURE'}
        return ({**common, 'family': 'id-counts', 'domains': {
                    n: {'count': i, 'private': 'PRIVATE_DOMAIN'} for n, i in [('ItemID', 13), ('TileID', 7), ('WallID', 4)]}},
                {**common, 'family': 'prefix-groups', 'independentDomainInitializerProven': True,
                 'groups': {'Original': [900001]}, 'groupEvidence': ['PRIVATE_GROUP']})

    def test_private_models_are_redacted_and_never_promote_complete(self):
        a, b = self.models()
        with patch.dict(counts._PROFILES, {self.digest: None}), patch.dict(groups._PROFILES, {self.digest: None}), \
                patch.object(counts, 'extract_id_count_semantics', return_value=a), \
                patch.object(groups, 'extract_prefix_group_semantics', return_value=b):
            result = RawEvidenceProducer().produce({'server': self.server}, {}, self.output)
        text = json.dumps(result)
        self.assertNotIn('PRIVATE_', text); self.assertNotIn('900001', text)
        for key in ('idCountEvidence', 'prefixGroupEvidence'):
            row = result[key][0]; raw = (self.output / row['path']).read_bytes()
            self.assertIn('PRIVATE_', raw.decode()); self.assertEqual(sha256(raw), row['sha256'])
            self.assertTrue(row['summary']['conditional']); self.assertFalse(row['summary']['complete'])
            self.assertFalse(row['summary']['normalReturnGuaranteed'])
            self.assertFalse(row['summary']['runtimeDependencyBindingVerified'])
        self.assertEqual({'ItemID': 13, 'TileID': 7, 'WallID': 4}, result['idCountEvidence'][0]['summary']['domainCounts'])
        self.assertEqual(1, result['prefixGroupEvidence'][0]['summary']['groupCount'])
        for family, count in [('items', 13), ('tiles', 7), ('walls', 4)]:
            cap = result['familyCoverage'][family]
            self.assertEqual(count, cap['idDomainSubcapabilities'][0]['count']); self.assertFalse(cap['complete'])
        self.assertFalse(result['extractionComplete']); self.assertFalse(result['publishable'])

    def test_unknown_profiles_never_call_helpers(self):
        with patch.object(counts, 'extract_id_count_semantics') as a, patch.object(groups, 'extract_prefix_group_semantics') as b:
            result = RawEvidenceProducer().produce({'server': self.server}, {}, self.output)
        a.assert_not_called(); b.assert_not_called()
        for key in ('idCountEvidence', 'prefixGroupEvidence'):
            self.assertEqual('UNSUPPORTED_PROFILE', result[key][0]['summary']['status'])
            self.assertFalse(result[key][0]['summary']['modelComplete'])
        self.assertIsNone(result['familyCoverage']['items']['idDomainSubcapabilities'][0]['count'])

    def test_each_supported_stage_failure_is_terminal(self):
        for module, helper in [(counts, 'extract_id_count_semantics'), (groups, 'extract_prefix_group_semantics')]:
            with self.subTest(stage=helper), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / 'evidence'
                with patch.dict(module._PROFILES, {self.digest: None}), patch.object(module, helper, side_effect=PipelineError('proof failed')):
                    with self.assertRaisesRegex(PipelineError, 'proof failed'):
                        RawEvidenceProducer().produce({'server': self.server}, {}, target)
                self.assertFalse((target / 'version-adapter-manifest.json').exists())

    def test_source_role_and_hash_are_bound_for_both_stages(self):
        for module, helper, base in zip((counts, groups), ('extract_id_count_semantics', 'extract_prefix_group_semantics'), self.models()):
            for key, value in [('inputSha256', '0' * 64), ('sourceRole', 'client')]:
                with self.subTest(stage=helper, key=key), tempfile.TemporaryDirectory() as tmp:
                    target = Path(tmp) / 'evidence'
                    with patch.dict(module._PROFILES, {self.digest: None}), patch.object(module, helper, return_value={**base, key: value}):
                        with self.assertRaisesRegex(PipelineError, 'binding mismatch'):
                            RawEvidenceProducer().produce({'server': self.server}, {}, target)
                    self.assertFalse((target / 'version-adapter-manifest.json').exists())


if __name__ == '__main__': unittest.main()
