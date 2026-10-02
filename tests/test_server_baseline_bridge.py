"""Synthetic server/API bridge tests: no game data, assets, source or execution."""
from dataclasses import replace
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient

from baseline_fixture import baseline_pe, put_field
from il_fixture import item_stage_pe
from resource_pipeline.api import create_app
from resource_pipeline.item_baseline import extract_fresh_item_baseline
from resource_pipeline.real_producer import RawEvidenceProducer
from resource_pipeline.security import PipelineError, canonical_json
from resource_pipeline.server_semantics import (
    SemanticLimits, _Metadata, _item_evidence, _types, extract_server_semantics,
)
from resource_pipeline.static_il import StaticILLimits, extract_item_default_stages


class ServerBaselineBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input = self.root / 'TerrariaServer.exe'

    def tearDown(self):
        self.temp.cleanup()

    def inspect(self, data=None, **options):
        data = baseline_pe() if data is None else data
        self.input.write_bytes(data)
        result = extract_server_semantics(self.input, **options)
        self.assertEqual(data, self.input.read_bytes())
        return result

    def combined(self, limits=StaticILLimits(), checkpoint=lambda: None):
        # The existing original fixture includes an unused typed constructor.
        # Here its original body/signature become one ordinary synthetic stage.
        data = baseline_pe(method_names={6: 'SetDefaults1'}, method_flags={6: 0x86},
                           typed_ctor=put_field(4, b'\x03') + b'\x2a')
        meta = _Metadata(data, SemanticLimits())
        return _item_evidence(meta, _types(meta), [7], {'kind': 'original-synthetic-test'},
                              limits, checkpoint, hashlib.sha256(data).hexdigest())

    def test_positive_inspector_exposes_one_fresh_reset_zero_with_source_binding(self):
        result = self.inspect()
        baseline = result['freshItemBaseline']
        self.assertEqual('PROVEN_FRESH_PRIMITIVE_BASELINE', baseline['status'])
        self.assertEqual([0], baseline['resetArguments'])
        self.assertEqual(15, baseline['provenPrimitiveFields'])
        self.assertEqual(result['input']['sha256'], baseline['inputSha256'])
        self.assertEqual(hashlib.sha256(self.input.read_bytes()).hexdigest(), baseline['inputSha256'])
        fields = {row['fieldName']: row for row in baseline['fields']}
        self.assertEqual(0, fields['OriginalCounter']['value'])
        self.assertEqual('reset', fields['OriginalCounter']['evidence']['phase'])
        self.assertEqual('fresh-zero', fields['width']['evidence']['phase'])
        self.assertNotIn('records', baseline)
        for evidence in (result, baseline, result['itemDefaultStages']):
            self.assertFalse(evidence['complete'])
            self.assertFalse(evidence['executedInput'])
        self.assertFalse(baseline['finalItemDefaults'])
        self.assertFalse(result['publishable'])
        self.assertTrue(all(not row['complete'] for row in result['coverage'].values()))
        self.assertIn('runtime-item-defaults', result['unsupported'])

    def test_stage_receivers_stay_unknown_and_the_baseline_is_not_an_override(self):
        stages, baseline, accounting = self.combined()
        self.assertEqual(1, len(stages['records']))
        stage = stages['records'][0]
        self.assertEqual('unknown', stage['initialInstanceFields'])
        self.assertEqual(7, stage['fields'][0]['value'])
        self.assertEqual(['OriginalCounter'], [row['fieldName'] for row in stage['fields']])
        self.assertEqual(0, next(row['value'] for row in baseline['fields']
                                if row['fieldName'] == 'OriginalCounter'))
        self.assertFalse(stage['finalItemDefaults'])
        self.assertFalse(baseline['finalItemDefaults'])
        self.assertEqual(stages['analyzedInstructions'] + baseline['analyzedInstructions'],
                         accounting['analyzedInstructions'])

    def test_existing_stage_records_and_coverage_do_not_change(self):
        data = item_stage_pe()
        meta = _Metadata(data, SemanticLimits())
        direct = extract_item_default_stages(meta, _types(meta), [1, 2])
        result = self.inspect(data)
        self.assertEqual(direct['records'], result['itemDefaultStages']['records'])
        self.assertEqual(direct['analyzedInstructions'], result['itemDefaultStages']['analyzedInstructions'])
        self.assertEqual('BASELINE_REQUIRES_CORE_OBJECT_BASE', result['freshItemBaseline']['status'])
        self.assertEqual([], result['freshItemBaseline']['fields'])

    def test_unsupported_item_shapes_cannot_be_promoted(self):
        for options, code in (({'item_flags': 0x21}, 'UNSUPPORTED_BASELINE_ITEM_LAYOUT'),
                              ({'assembly_name': 'OriginalFakeCore'}, 'BASELINE_REQUIRES_CORE_OBJECT_BASE')):
            with self.subTest(options=options):
                result = self.inspect(baseline_pe(**options))
                baseline = result['freshItemBaseline']
                self.assertEqual(code, baseline['status'])
                self.assertFalse(baseline['baselineComplete'])
                self.assertEqual([], baseline['fields'])
                self.assertEqual(result['inputSha256'], baseline['inputSha256'])
                self.assertFalse(result['publishable'])

    def test_remaining_step_capacity_is_subtracted_and_exhaustion_skips_baseline(self):
        limits = replace(StaticILLimits(), total_steps=3)
        with patch('resource_pipeline.server_semantics.extract_fresh_item_baseline') as baseline:
            stages, proof, accounting = self.combined(limits)
        baseline.assert_not_called()
        self.assertEqual('TOTAL_STEP_LIMIT', stages['diagnostic']['code'])
        self.assertEqual('NOT_ANALYZED', proof['status'])
        self.assertEqual('NO_REMAINING_TOTAL_STEPS', proof['reason'])
        self.assertEqual([], proof['fields'])
        self.assertLessEqual(accounting['analyzedInstructions'], limits.total_steps + 1)
        limits = replace(StaticILLimits(), total_steps=12)
        with patch('resource_pipeline.server_semantics.extract_fresh_item_baseline',
                   wraps=extract_fresh_item_baseline) as baseline:
            stages, proof, accounting = self.combined(limits)
        self.assertEqual(limits.total_steps - stages['analyzedInstructions'],
                         baseline.call_args.kwargs['limits'].total_steps)
        self.assertEqual('TOTAL_STEP_LIMIT', proof['status'])
        self.assertEqual([], proof['fields'])
        self.assertLessEqual(accounting['analyzedInstructions'], limits.total_steps + 1)

    def test_cumulative_evidence_is_subtracted_and_low_remainder_skips(self):
        limits = replace(StaticILLimits(), evidence_bytes=24 * 1024)
        with patch('resource_pipeline.server_semantics.extract_fresh_item_baseline') as baseline:
            result = self.inspect(il_limits=limits)
        baseline.assert_not_called()
        proof = result['freshItemBaseline']
        self.assertEqual('NOT_ANALYZED', proof['status'])
        self.assertEqual('INSUFFICIENT_REMAINING_EVIDENCE_BYTES', proof['reason'])
        budget = result['itemAnalysisBudget']['evidenceBudget']
        self.assertLessEqual(budget['accountedConstructionBytes'], limits.evidence_bytes)
        with patch('resource_pipeline.server_semantics.extract_fresh_item_baseline',
                   wraps=extract_fresh_item_baseline) as baseline:
            stages, proof, accounting = self.combined()
        budget = accounting['evidenceBudget']
        self.assertEqual(StaticILLimits().evidence_bytes - budget['bridgeReserveBytes'] -
                         stages['evidenceBudget']['accountedConstructionBytes'],
                         baseline.call_args.kwargs['limits'].evidence_bytes)
        self.assertEqual(budget['bridgeReserveBytes'] + budget['stageConstructionBytes'] +
                         budget['baselineConstructionBytes'], budget['accountedConstructionBytes'])
        self.assertLessEqual(budget['accountedConstructionBytes'], budget['limitBytes'])
        self.assertLessEqual(len(canonical_json({'itemDefaultStages': stages, 'freshItemBaseline': proof,
                                                'itemAnalysisBudget': accounting})), budget['limitBytes'])

    def test_decode_capacity_reservations_sum_to_original_limits(self):
        stages, proof, accounting = self.combined()
        reservations = accounting['decodeReservations']
        self.assertEqual(StaticILLimits().total_decoded_instructions,
                         reservations['stages']['totalInstructions'] + reservations['freshBaseline']['totalInstructions'])
        self.assertEqual(StaticILLimits().total_method_bytes,
                         reservations['stages']['totalMethodBytes'] + reservations['freshBaseline']['totalMethodBytes'])
        with patch('resource_pipeline.server_semantics.extract_fresh_item_baseline') as baseline:
            result = self.inspect(il_limits=replace(StaticILLimits(), total_decoded_instructions=1))
        baseline.assert_not_called()
        self.assertEqual('NOT_ANALYZED', result['freshItemBaseline']['status'])
        self.assertEqual('NO_RESERVED_DECODE_CAPACITY', result['freshItemBaseline']['reason'])

    def test_cancel_and_deadline_propagate_instead_of_becoming_skips(self):
        in_baseline = False
        def start_baseline(*args, **kwargs):
            nonlocal in_baseline
            in_baseline = True
            return extract_fresh_item_baseline(*args, **kwargs)
        def cancel():
            if in_baseline:
                raise PipelineError('original bridge cancellation')
        with patch('resource_pipeline.server_semantics.extract_fresh_item_baseline', side_effect=start_baseline):
            with self.assertRaisesRegex(PipelineError, 'original bridge cancellation'):
                self.inspect(checkpoint=cancel)
        in_baseline = False
        # One external deadline spans both analyzers; the second receives only
        # the remaining seconds and cannot restart the original 30-second clock.
        def clock():
            return 31.0 if in_baseline else 0.0
        with patch('resource_pipeline.server_semantics.monotonic', side_effect=clock), \
             patch('resource_pipeline.server_semantics.extract_fresh_item_baseline', side_effect=start_baseline):
            with self.assertRaisesRegex(PipelineError, 'STATIC_ITEM_ANALYSIS_TOTAL_TIME_LIMIT'):
                self.inspect()

    def test_malformed_metadata_and_root_output_guard_remain_fail_closed(self):
        with self.assertRaises(PipelineError):
            self.inspect(baseline_pe(base_coded=0xfffd))
        with self.assertRaisesRegex(PipelineError, 'SEMANTIC_OUTPUT_REJECTED'):
            self.inspect(limits=replace(SemanticLimits(), output_bytes=1024))
        self.assertEqual(64 * 1024 * 1024, SemanticLimits().output_bytes)
        with self.assertRaisesRegex(PipelineError, 'STATIC_ITEM_ANALYSIS_EVIDENCE_ENVELOPE_LIMIT'):
            self.inspect(il_limits=replace(StaticILLimits(), evidence_bytes=16 * 1024))

    def test_default_raw_producer_receives_baseline_and_keeps_defaults_missing(self):
        server = self.root / 'server'
        server.mkdir()
        data = baseline_pe()
        (server / 'TerrariaServer.exe').write_bytes(data)
        output = self.root / 'evidence'
        manifest = RawEvidenceProducer().produce({'server': server}, {}, output)
        evidence = json.loads((output / manifest['serverMetadata'][0]['path']).read_text())
        proof = evidence['freshItemBaseline']
        self.assertEqual('PROVEN_FRESH_PRIMITIVE_BASELINE', proof['status'])
        self.assertEqual(hashlib.sha256(data).hexdigest(), proof['inputSha256'])
        self.assertIn('defaults', manifest['familyCoverage']['items']['missingRules'])
        self.assertFalse(manifest['extractionComplete'])
        self.assertFalse(manifest['publishable'])
        self.assertFalse(manifest['familyCoverage']['items']['complete'])

    def test_synthetic_http_intake_retains_private_proof_without_publish_promotion(self):
        package = io.BytesIO()
        with zipfile.ZipFile(package, 'w') as archive:
            archive.writestr('TerrariaServer.exe', baseline_pe())
        app = create_app(self.root / 'http', synchronous=True)
        with TestClient(app) as client:
            response = client.post('/api/raw-jobs', files={
                'server_file': ('server.zip', package.getvalue(), 'application/zip')})
            self.assertEqual(202, response.status_code)
            job = response.json()
            self.assertFalse(job['extractionComplete'])
            self.assertFalse(job['producerEvidence']['publishable'])
            self.assertIn('defaults', job['producerEvidence']['familyCoverage']['items']['missingRules'])
            saved = app.state.pipeline.root / 'jobs' / job['id'] / 'adapter-evidence' / 'server-0.json'
            self.assertEqual('PROVEN_FRESH_PRIMITIVE_BASELINE', json.loads(saved.read_text())['freshItemBaseline']['status'])
            review = client.get(f"/api/raw-jobs/{job['id']}/review").json()
            self.assertFalse(review['reviewable'])
            self.assertIsNone(review['reviewDigest'])
            publish = client.post(f"/api/raw-jobs/{job['id']}/publish", json={'reviewDigest': '0' * 64, 'confirmed': True})
            self.assertEqual(409, publish.status_code)
            self.assertIsNone(client.get('/api/current').json())

    def test_stats_redactor_contains_only_baseline_counts_status_and_hash_binding(self):
        script = Path(__file__).parents[1] / 'scripts' / 'ci_server_semantics.py'
        spec = importlib.util.spec_from_file_location('bridge_redaction', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = self.inspect()
        proof = result['freshItemBaseline']
        summary = module.redact(result)['freshItemBaseline']
        self.assertEqual(hashlib.sha256(canonical_json(proof)).hexdigest(), summary['proofSha256'])
        self.assertEqual(proof['inputSha256'], summary['inputSha256'])
        self.assertEqual(15, summary['provenPrimitiveFieldCount'])
        self.assertEqual([0], summary['resetArguments'])
        self.assertFalse(summary['complete'])
        self.assertFalse(summary['finalItemDefaults'])
        self.assertEqual({'status', 'inputSha256', 'proofSha256', 'scope', 'resetArguments',
                          'executedInput', 'complete', 'finalItemDefaults', 'provenPrimitiveFieldCount',
                          'unknownPrimitiveFieldCount', 'excludedFieldCount', 'prefixFieldCount', 'methodCount',
                          'staticInitializerCount', 'analyzedInstructions', 'notAnalyzedReason'}, set(summary))
        for name in ('OriginalCounter', 'OriginalMaximum', 'OriginalPrice', 'OriginalStaticMaximum'):
            self.assertNotIn(name, json.dumps(summary))
        stopped = self.inspect(il_limits=replace(StaticILLimits(), evidence_bytes=24 * 1024))
        summary = module.redact(stopped)['freshItemBaseline']
        self.assertEqual('NOT_ANALYZED', summary['status'])
        self.assertEqual('INSUFFICIENT_REMAINING_EVIDENCE_BYTES', summary['notAnalyzedReason'])
        self.assertEqual(0, summary['provenPrimitiveFieldCount'])


if __name__ == '__main__':
    unittest.main()
