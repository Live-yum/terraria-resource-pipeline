"""Producer/HTTP integration with original synthetic inputs, never game assets."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient

from baseline_fixture import baseline_pe
from resource_pipeline import research_semantics as research
from resource_pipeline.adapters import tree_inventory
from resource_pipeline.api import create_app
from resource_pipeline.preflight import _raw_checkpoint
from resource_pipeline.real_producer import RawEvidenceProducer
from resource_pipeline.security import PipelineError, canonical_json, sha256


def fixture_model(path, *, platform, culture, checkpoint):
    """A mock installed research helper, retaining the reviewed catalog parser."""
    checkpoint()
    data = path.read_bytes()
    catalog = research.parse_research_catalog(
        b'OriginalResearchAlpha\td\tx\nOriginalResearchBeta\tI\tx',
        {'OriginalResearchAlpha': 900001, 'OriginalResearchBeta': 900002},
        culture=culture, checkpoint=checkpoint)
    model = {
        'schemaVersion': 1, 'family': 'creative-research', 'status': 'EXTRACTED_CONDITIONAL_MODEL',
        'executedInput': False, 'publishable': False, 'complete': False,
        'researchModelComplete': True, 'culture': culture, 'profile': dict(research.PROFILE),
        'sourceReference': dict(research.REFERENCE),
        'input': {'sha256': sha256(data), 'bytes': len(data), 'platform': platform},
        'scope': 'original synthetic research integration fixture',
        'assumptions': ['invariant ASCII category model; runtime culture unverified'],
        'unsupportedScope': ['runtime state', 'item defaults', 'full family semantics'],
        'completenessBoundary': 'conditional research model only',
        'persistentIdOverrides': {'900003': 900002},
        'overrideCalls': [{'target': 900002, 'sources': [900003]}],
        'resource': {'bytes': 73, 'sha256': 'a' * 64, 'privateResource': 'PRIVATE_RESOURCE'},
        'methodEvidence': [{'privateMethod': 'PRIVATE_METHOD'}],
        'nameBindingEvidence': {'privateNames': 'PRIVATE_BINDINGS'},
        'futurePrivateField': 'PRIVATE_FUTURE',
        **catalog,
    }
    model['statistics'].update(persistentOverrideCount=1, overrideCallCount=1,
                               lookupAvailableCount=3, lookupDomainCount=3,
                               futurePrivateStatistic='PRIVATE_STATISTIC')
    model['tableDigests'] = {key: sha256(canonical_json(model[key]))
                            for key in ('baseCounts', 'persistentIdOverrides')}
    return model


class ResearchProducerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = self.root / 'server'
        self.server.mkdir()
        self.data = baseline_pe()
        self.source = self.server / 'TerrariaServer.exe'
        self.source.write_bytes(self.data)
        self.output = self.root / 'evidence'

    def install_fixture(self, helper=fixture_model):
        # This is test-only installation. Uploaded filenames/metadata never
        # select or modify the production profile or its installed helper.
        self.enterContext(patch.dict(research.PROFILE, inputSha256=sha256(self.data)))
        return self.enterContext(patch.object(research, 'extract_research_semantics', side_effect=helper))

    def produce(self, **options):
        return RawEvidenceProducer().produce({'server': self.server}, {}, self.output, **options)

    def assert_no_private_values(self, value):
        encoded = json.dumps(value)
        for sentinel in ('OriginalResearchAlpha', 'OriginalResearchBeta', '900001', '900002', '900003',
                         'baseOrigins', 'overrideCalls', 'privateResource', 'privateMethod',
                         'privateNames', 'futurePrivateField', 'PRIVATE_'):
            self.assertNotIn(sentinel, encoded)
        for item in value.get('researchEvidence', []):
            summary = item['summary']
            self.assertNotIn('baseCounts', summary)
            self.assertNotIn('persistentIdOverrides', summary)

    def assert_gates_closed(self, manifest):
        self.assertFalse(manifest['extractionComplete'])
        self.assertFalse(manifest['publishable'])
        self.assertFalse(manifest['executedInput'])
        self.assertEqual(['GAME_VERSION_AND_FULL_SEMANTICS_NOT_VERIFIED'], manifest['blockers'])
        for family in manifest['familyCoverage'].values():
            self.assertFalse(family['complete'])
        self.assertIn('defaults', manifest['familyCoverage']['items']['missingRules'])
        self.assertIn('dynamicTooltips', manifest['familyCoverage']['items']['missingRules'])

    def test_default_producer_binds_separate_private_file_and_conditional_capability(self):
        helper = self.install_fixture()
        manifest = self.produce()
        helper.assert_called_once()
        self.assertEqual('windows', helper.call_args.kwargs['platform'])
        self.assertEqual('invariant', helper.call_args.kwargs['culture'])
        item = manifest['researchEvidence'][0]
        self.assertEqual(sha256(self.data), item['sourceSha256'])
        self.assertEqual('server-0-research.json', item['path'])
        encoded = (self.output / item['path']).read_bytes()
        self.assertEqual(sha256(encoded), item['sha256'])
        private = json.loads(encoded)
        self.assertEqual({'900001': 1, '900002': 15}, private['baseCounts'])
        self.assertEqual({'900003': 900002}, private['persistentIdOverrides'])
        self.assertEqual('OriginalResearchAlpha', private['baseOrigins']['900001']['name'])
        self.assertEqual(2, item['summary']['statistics']['baseDefinitionCount'])
        self.assertFalse(item['summary']['runtimeCultureVerified'])
        self.assertEqual(private['tableDigests'], item['summary']['tableDigests'])
        capability = manifest['familyCoverage']['items']['researchSubcapabilities'][0]
        self.assertEqual('EXTRACTED_CONDITIONAL_MODEL', capability['status'])
        self.assertTrue(capability['conditional'])
        self.assertTrue(capability['researchModelComplete'])
        self.assertFalse(capability['complete'])
        self.assertFalse(capability['runtimeCultureVerified'])
        self.assertEqual(item['sha256'], capability['evidenceSha256'])
        self.assertEqual(item['path'], capability['evidencePath'])
        self.assertEqual(manifest, json.loads((self.output / 'version-adapter-manifest.json').read_bytes()))
        self.assert_no_private_values(manifest)
        self.assert_gates_closed(manifest)
        # The real default static inspector still runs and retains its private
        # baseline proof rather than being replaced by the research mock.
        static = json.loads((self.output / manifest['serverMetadata'][0]['path']).read_bytes())
        self.assertEqual('PROVEN_FRESH_PRIMITIVE_BASELINE', static['freshItemBaseline']['status'])
        self.assertNotIn('baseCounts', static)

    def test_unknown_profiles_are_unsupported_without_guessing_from_windows_path(self):
        nested = self.server / 'Windows'
        nested.mkdir()
        self.source.rename(nested / self.source.name)
        with patch.object(research, 'extract_research_semantics') as helper:
            manifest = self.produce()
        helper.assert_not_called()
        summary = manifest['researchEvidence'][0]['summary']
        self.assertEqual('UNSUPPORTED_PROFILE', summary['status'])
        self.assertEqual('unknown', summary['input']['platform'])
        self.assertFalse(summary['researchModelComplete'])
        self.assertFalse(summary['runtimeCultureVerified'])
        self.assertNotIn('statistics', summary)
        self.assert_gates_closed(manifest)

    def test_rejected_static_inputs_still_receive_explicit_unsupported_evidence(self):
        self.source.write_bytes(b'original inert non-PE fixture')
        manifest = self.produce()
        self.assertEqual(1, len(manifest['rejectedServerMetadata']))
        self.assertEqual([], manifest['serverMetadata'])
        self.assertEqual('UNSUPPORTED_PROFILE', manifest['researchEvidence'][0]['summary']['status'])
        self.assertTrue((self.output / 'server-0-research.json').is_file())
        self.assert_gates_closed(manifest)

    def test_multiple_inputs_are_bound_independently_and_paths_do_not_select_platform(self):
        helper = self.install_fixture()
        linux = self.server / 'Linux'
        windows = self.server / 'Windows'
        linux.mkdir(); windows.mkdir()
        self.source.rename(linux / self.source.name)
        other = b'original unsupported binary'
        (windows / self.source.name).write_bytes(other)
        manifest = self.produce()
        helper.assert_called_once()
        rows = manifest['researchEvidence']
        self.assertEqual(2, len(rows))
        self.assertEqual('windows', rows[0]['summary']['input']['platform'])
        self.assertEqual('unknown', rows[1]['summary']['input']['platform'])
        self.assertEqual([sha256(self.data), sha256(other)], [row['sourceSha256'] for row in rows])
        for row in rows:
            self.assertEqual(row['sha256'], sha256((self.output / row['path']).read_bytes()))

    def test_custom_inspector_keeps_original_keyword_contract(self):
        self.install_fixture()
        calls = []
        def custom(path, *, checkpoint):
            checkpoint(); calls.append(path)
            return {'input': {'sha256': sha256(path.read_bytes())}}
        manifest = RawEvidenceProducer(custom).produce({'server': self.server}, {}, self.output)
        self.assertEqual([self.source], calls)
        self.assertEqual('EXTRACTED_CONDITIONAL_MODEL', manifest['researchEvidence'][0]['summary']['status'])

    def test_no_server_input_adds_no_research_claims(self):
        manifest = RawEvidenceProducer().produce({}, {}, self.output)
        self.assertEqual([], manifest['researchEvidence'])
        self.assertEqual([], manifest['familyCoverage']['items']['researchSubcapabilities'])
        self.assertEqual([], list(self.output.glob('*-research.json')))

    def test_source_hash_and_size_mismatch_reject_before_research_write(self):
        for key, value in (('sha256', '0' * 64), ('bytes', len(self.data) + 1)):
            with self.subTest(field=key), tempfile.TemporaryDirectory() as folder:
                model = fixture_model(self.source, platform='windows', culture='invariant', checkpoint=lambda: None)
                model['input'][key] = value
                with patch.dict(research.PROFILE, inputSha256=sha256(self.data)), \
                     patch.object(research, 'extract_research_semantics', return_value=model), \
                     self.assertRaisesRegex(PipelineError, 'Research source differs'):
                    RawEvidenceProducer().produce({'server': self.server}, {}, Path(folder) / 'output')
                self.assertFalse((Path(folder) / 'output/server-0-research.json').exists())
                self.assertFalse((Path(folder) / 'output/version-adapter-manifest.json').exists())

    def test_changed_source_rejected_by_full_final_rehash(self):
        def mutate(path, **options):
            model = fixture_model(path, **options)
            path.write_bytes(b'changed source after research inspection')
            return model
        self.install_fixture(mutate)
        with self.assertRaisesRegex(PipelineError, 'Original source changed'):
            self.produce()
        self.assertFalse((self.output / 'version-adapter-manifest.json').exists())

    def test_verified_inventory_mismatch_prevents_research_call(self):
        helper = self.install_fixture()
        inventories = {'server': tree_inventory(self.server)}
        self.source.write_bytes(b'changed after verified ZIP inventory')
        with self.assertRaisesRegex(PipelineError, 'Static metadata source differs'):
            self.produce(expected_inventories=inventories)
        helper.assert_not_called()
        self.assertFalse((self.output / 'version-adapter-manifest.json').exists())

    def test_outer_cancellation_and_deadline_escape_research_stage(self):
        for code in ('RAW_JOB_CANCELED', 'RAW_JOB_TIMEOUT'):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as folder:
                now = [0.0]
                cancel = [False]
                def trigger(path, **options):
                    now[0] = 200.0 if code == 'RAW_JOB_TIMEOUT' else 0.006
                    cancel[0] = code == 'RAW_JOB_CANCELED'
                    return fixture_model(path, **options)
                check = _raw_checkpoint(lambda: cancel[0], 120, clock=lambda: now[0])
                with patch.dict(research.PROFILE, inputSha256=sha256(self.data)), \
                     patch.object(research, 'extract_research_semantics', side_effect=trigger), \
                     self.assertRaisesRegex(PipelineError, code):
                    RawEvidenceProducer().produce({'server': self.server}, {}, Path(folder) / 'output', checkpoint=check)
                self.assertFalse((Path(folder) / 'output/version-adapter-manifest.json').exists())

    def test_research_failure_does_not_become_a_successful_partial_manifest(self):
        self.install_fixture(lambda *args, **kwargs: (_ for _ in ()).throw(PipelineError('private proof failed')))
        with self.assertRaisesRegex(PipelineError, 'private proof failed'):
            self.produce()
        self.assertFalse((self.output / 'version-adapter-manifest.json').exists())

    def test_private_evidence_and_public_summary_have_separate_budgets(self):
        for mode in ('private', 'summary'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                model = fixture_model(self.source, platform='windows', culture='invariant', checkpoint=lambda: None)
                if mode == 'private':
                    model['futurePrivateField'] = 'x' * (8 * 1024 * 1024)
                else:
                    model['assumptions'] = ['x' * (64 * 1024)]
                with patch.dict(research.PROFILE, inputSha256=sha256(self.data)), \
                     patch.object(research, 'extract_research_semantics', return_value=model), self.assertRaises(PipelineError):
                    RawEvidenceProducer().produce({'server': self.server}, {}, Path(folder) / 'output')
                self.assertFalse((Path(folder) / 'output/server-0-research.json').exists())
                self.assertFalse((Path(folder) / 'output/version-adapter-manifest.json').exists())

    @staticmethod
    def upload(data):
        package = BytesIO()
        with zipfile.ZipFile(package, 'w') as archive:
            archive.writestr('TerrariaServer.exe', data)
        return package.getvalue()

    def test_http_upload_polling_list_review_and_saved_job_contain_only_summary(self):
        helper = self.install_fixture()
        app = create_app(self.root / 'http', synchronous=True)
        with TestClient(app) as client:
            response = client.post('/api/raw-jobs', files={
                'server_file': ('original-synthetic.zip', self.upload(self.data), 'application/zip')})
            self.assertEqual(202, response.status_code)
            job = response.json()
            self.assertEqual('BLOCKED', job['state'])
            identity = job['id']
            self.assertIn('VERSION_UNVERIFIED', {row['code'] for row in job['blockers']})
            for value in (job, client.get(f'/api/jobs/{identity}').json(),
                          client.get(f'/api/jobs/{identity}?compact=true').json(),
                          client.get('/api/jobs').json()[0], app.state.pipeline.get(identity)):
                self.assert_no_private_values(value)
                self.assert_no_private_values(value['producerEvidence'])
                self.assert_gates_closed(value['producerEvidence'])
            review = client.get(f'/api/raw-jobs/{identity}/review').json()
            self.assertFalse(review['reviewable'])
            self.assert_no_private_values(review)
            self.assertEqual(409, client.post(f'/api/raw-jobs/{identity}/publish', json={
                'confirmed': True, 'reviewDigest': '0' * 64}).status_code)
            self.assertIsNone(client.get('/api/current').json())
            evidence = job['producerEvidence']['researchEvidence'][0]
            path = app.state.pipeline.root / 'jobs' / identity / 'adapter-evidence' / evidence['path']
            self.assertEqual(evidence['sha256'], sha256(path.read_bytes()))
            self.assertIn('baseOrigins', json.loads(path.read_bytes()))
        helper.assert_called_once()

    def test_http_unknown_profile_is_explicit_and_cannot_be_promoted(self):
        app = create_app(self.root / 'http', synchronous=True)
        with TestClient(app) as client:
            response = client.post('/api/raw-jobs', files={
                'server_file': ('Windows-server.zip', self.upload(self.data), 'application/zip')})
            self.assertEqual(202, response.status_code)
            job = response.json()
            summary = job['producerEvidence']['researchEvidence'][0]['summary']
            self.assertEqual('UNSUPPORTED_PROFILE', summary['status'])
            self.assertEqual('unknown', summary['input']['platform'])
            self.assertEqual('BLOCKED', job['state'])
            self.assert_gates_closed(job['producerEvidence'])

    def test_http_cancellation_cleans_evidence_and_keeps_retry_available(self):
        def cancel(path, **options):
            (path.parent.parent / 'cancel.request').write_text('cancel')
            raise PipelineError('RAW_JOB_CANCELED')
        self.install_fixture(cancel)
        app = create_app(self.root / 'http', synchronous=True)
        with TestClient(app) as client:
            job = client.post('/api/raw-jobs', files={
                'server_file': ('original.zip', self.upload(self.data), 'application/zip')}).json()
            self.assertEqual('CANCELED', job['state'])
            self.assertNotIn('producerEvidence', job)
            self.assertFalse((app.state.pipeline.root / 'jobs' / job['id'] / 'adapter-evidence').exists())
            retried = client.post(f"/api/raw-jobs/{job['id']}/retry")
            self.assertEqual(202, retried.status_code)
            self.assertNotEqual(job['id'], retried.json()['id'])
            self.assertIsNone(client.get('/api/current').json())

    def test_http_outer_timeout_cleans_evidence_and_does_not_restart_clock(self):
        now = [0.0]
        def expire(path, **options):
            now[0] = 121.0
            return fixture_model(path, **options)
        self.install_fixture(expire)
        app = create_app(self.root / 'http', synchronous=True)
        with patch('resource_pipeline.preflight.time', SimpleNamespace(monotonic=lambda: now[0])), \
             TestClient(app) as client:
            job = client.post('/api/raw-jobs', files={
                'server_file': ('original.zip', self.upload(self.data), 'application/zip')}).json()
            self.assertEqual('BLOCKED', job['state'])
            self.assertEqual('RAW_JOB_TIMEOUT', job['error'])
            self.assertNotIn('producerEvidence', job)
            self.assertFalse((app.state.pipeline.root / 'jobs' / job['id'] / 'adapter-evidence').exists())
            self.assertIsNone(client.get('/api/current').json())

    def test_http_research_errors_are_redacted_and_never_leave_manifest(self):
        for exception in (PipelineError('PRIVATE_RESEARCH_ERROR'), OSError('PRIVATE_RESEARCH_PATH')):
            with self.subTest(error=type(exception).__name__):
                with patch.dict(research.PROFILE, inputSha256=sha256(self.data)), \
                     patch.object(research, 'extract_research_semantics', side_effect=exception):
                    app = create_app(self.root / type(exception).__name__, synchronous=True)
                    with TestClient(app) as client:
                        job = client.post('/api/raw-jobs', files={
                            'server_file': ('original.zip', self.upload(self.data), 'application/zip')}).json()
                        self.assertEqual('BLOCKED', job['state'])
                        self.assertNotIn('producerEvidence', job)
                        self.assertNotIn('PRIVATE_', json.dumps(job))
                        expected = 'SEMANTIC_PRODUCER_REJECTED' if isinstance(exception, PipelineError) else 'RAW_PROCESSING_FAILED'
                        self.assertIn(expected, {row['code'] for row in job['blockers']})
                        self.assertFalse((app.state.pipeline.root / 'jobs' / job['id'] / 'adapter-evidence' /
                                          'version-adapter-manifest.json').exists())
                        self.assertIsNone(client.get('/api/current').json())


if __name__ == '__main__':
    unittest.main()
