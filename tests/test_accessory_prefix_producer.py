import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from baseline_fixture import baseline_pe
from resource_pipeline import accessory_prefix_semantics as accessory
from resource_pipeline import prefix_pool_semantics as pools, prefix_coefficient_semantics as coefficients
from resource_pipeline.real_producer import RawEvidenceProducer
from resource_pipeline.security import PipelineError, sha256


class AccessoryPrefixProducerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.server = self.root / 'server'; self.server.mkdir()
        self.raw = baseline_pe(); self.digest = sha256(self.raw)
        (self.server / 'TerrariaServer.exe').write_bytes(self.raw)
        self.output = self.root / 'evidence'

    def model(self, path, checkpoint):
        checkpoint()
        return {'schemaVersion': 1, 'family': 'accessory-prefix-effects', 'status': 'PROVEN_FINITE_FIELD_TRANSFORM',
                'inputSha256': sha256(path.read_bytes()), 'sourceRole': 'server', 'methodModelComplete': True,
                'allBytePrefixInputsCovered': True, 'executedInput': False, 'complete': False, 'publishable': False,
                'runtimeSnapshotUsable': False, 'effects': [{'prefixId': 900001, 'PRIVATE_SOURCE_FIELD': 'PRIVATE_ROW'}],
                'fieldEvidence': ['PRIVATE_FIELDS'], 'methodEvidence': {'private': 'PRIVATE_IL'}, 'futurePrivateField': 'PRIVATE_FUTURE'}

    def install(self, side_effect=None):
        self.enterContext(patch.dict(accessory.PROFILES, {self.digest: {'role': 'server'}}))
        return self.enterContext(patch.object(accessory, 'extract_accessory_prefix_semantics', side_effect=side_effect or self.model))

    def test_private_proof_file_summary_only_and_gates_stay_closed(self):
        helper = self.install()
        manifest = RawEvidenceProducer().produce({'server': self.server}, {}, self.output)
        helper.assert_called_once()
        row = manifest['accessoryPrefixEvidence'][0]
        self.assertEqual(row['sourceSha256'], self.digest)
        self.assertEqual(row['path'], 'server-0-accessory-prefix.json')
        raw = (self.output / row['path']).read_bytes()
        self.assertEqual(sha256(raw), row['sha256'])
        self.assertIn('PRIVATE_ROW', raw.decode())
        self.assertTrue(row['summary']['methodModelComplete'])
        self.assertEqual(row['summary']['effectCount'], 1)
        self.assertFalse(row['summary']['fullItemGroupComplete'])
        self.assertNotIn('PRIVATE_', json.dumps(manifest))
        self.assertNotIn('900001', json.dumps(manifest))
        self.assertFalse(manifest['extractionComplete']); self.assertFalse(manifest['publishable'])
        self.assertFalse(manifest['familyCoverage']['items']['complete'])

    def test_unknown_profile_never_invokes_source_proof(self):
        with patch.object(accessory, 'extract_accessory_prefix_semantics') as helper:
            manifest = RawEvidenceProducer().produce({'server': self.server}, {}, self.output)
        helper.assert_not_called()
        row = manifest['accessoryPrefixEvidence'][0]
        self.assertEqual(row['summary']['status'], 'UNSUPPORTED_PROFILE')
        self.assertFalse(row['summary']['methodModelComplete'])
        self.assertFalse(row['summary']['consumerProjectionVerified'])

    def test_proof_error_does_not_emit_adapter_manifest(self):
        self.install(lambda *a, **k: (_ for _ in ()).throw(PipelineError('bounded proof failed')))
        with self.assertRaisesRegex(PipelineError, 'bounded proof failed'):
            RawEvidenceProducer().produce({'server': self.server}, {}, self.output)
        self.assertFalse((self.output / 'version-adapter-manifest.json').exists())

    def test_source_role_or_digest_mismatch_fails_before_approval(self):
        for key, value in (('inputSha256', 'f' * 64), ('sourceRole', 'client')):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                def wrong(path, checkpoint):
                    result = self.model(path, checkpoint); result[key] = value; return result
                with patch.dict(accessory.PROFILES, {self.digest: {'role': 'server'}}), patch.object(accessory, 'extract_accessory_prefix_semantics', side_effect=wrong):
                    with self.assertRaisesRegex(PipelineError, 'source binding'):
                        RawEvidenceProducer().produce({'server': self.server}, {}, Path(tmp) / 'output')

    def test_pool_and_coefficient_models_only_expose_summary(self):
        common = {'schemaVersion': 1, 'status': 'ORIGINAL_MODEL', 'inputSha256': self.digest, 'sourceRole': 'server',
                  'complete': False, 'executedInput': False, 'publishable': False, 'runtimeSnapshotUsable': False,
                  'futurePrivateField': 'PRIVATE_FUTURE'}
        pool_model = {**common, 'family': 'prefix-pools', 'wholeInitializerProven': True, 'independentDomainInitializerProven': True,
                      'pools': {'OriginalPool': [900001]}, 'poolEvidence': ['PRIVATE_POOLS']}
        coeff_model = {**common, 'family': 'prefix-coefficients', 'coefficientModelComplete': True,
                       'records': [{'prefixId': 900002, 'coefficients': {'PRIVATE_COEFF': 1.25}}]}
        with patch.dict(pools._PROFILES, {self.digest: {'role': 'server'}}), patch.dict(coefficients.PROFILES, {self.digest: ('server', 1)}), \
                patch.object(pools, 'extract_prefix_pool_semantics', return_value=pool_model), \
                patch.object(coefficients, 'extract_prefix_coefficient_semantics', return_value=coeff_model):
            manifest = RawEvidenceProducer().produce({'server': self.server}, {}, self.output)
        self.assertNotIn('PRIVATE_', json.dumps(manifest)); self.assertNotIn('90000', json.dumps(manifest))
        pool = manifest['prefixPoolEvidence'][0]; coeff = manifest['prefixCoefficientEvidence'][0]
        self.assertEqual(pool['summary']['poolCount'], 1); self.assertEqual(pool['summary']['entryCount'], 1)
        self.assertEqual(pool['summary']['factScope'], 'INITIALIZER_BOUNDARY')
        self.assertEqual(coeff['summary']['prefixCount'], 1); self.assertFalse(coeff['summary']['eligibilityProven'])
        for row in (pool, coeff):
            raw = (self.output / row['path']).read_bytes()
            self.assertEqual(sha256(raw), row['sha256']); self.assertIn('PRIVATE_', raw.decode())
        self.assertFalse(manifest['publishable']); self.assertFalse(manifest['extractionComplete'])

    def test_unknown_pool_and_coefficient_profiles_are_not_called(self):
        with patch.object(pools, 'extract_prefix_pool_semantics') as pool, patch.object(coefficients, 'extract_prefix_coefficient_semantics') as coeff:
            result = RawEvidenceProducer().produce({'server': self.server}, {}, self.output)
        pool.assert_not_called(); coeff.assert_not_called()
        for key in ('prefixPoolEvidence', 'prefixCoefficientEvidence'):
            self.assertEqual(result[key][0]['summary']['status'], 'UNSUPPORTED_PROFILE')
            self.assertFalse(result[key][0]['summary']['modelComplete'])

    def test_prefix_model_source_mismatch_is_terminal(self):
        with patch.dict(coefficients.PROFILES, {self.digest: ('server', 1)}), \
                patch.object(coefficients, 'extract_prefix_coefficient_semantics', return_value={'inputSha256': 'f' * 64, 'sourceRole': 'server'}):
            with self.assertRaisesRegex(PipelineError, 'binding mismatch'):
                RawEvidenceProducer().produce({'server': self.server}, {}, self.output)
        self.assertFalse((self.output / 'version-adapter-manifest.json').exists())


if __name__ == '__main__': unittest.main()
