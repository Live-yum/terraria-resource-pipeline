"""Synthetic candidate seals; no game assets or publication authority."""
import copy
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient
from resource_pipeline.api import create_app
from resource_pipeline.candidate import seal_candidate, verify_candidate
from resource_pipeline.preflight import RawInputPreflight
from resource_pipeline.real_producer import RawEvidenceProducer
from resource_pipeline.security import canonical_json, sha256
from test_raw_stage_metrics import MemoryJobStore


def archive():
    stream = BytesIO()
    with zipfile.ZipFile(stream, 'w') as out:
        out.writestr('Content/original.txt', 'Original synthetic data only')
    stream.seek(0)
    return stream


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = MemoryJobStore(Path(self.temp.name))
        self.service = RawInputPreflight(self.store, semantic_producer=RawEvidenceProducer())
        self.job = self.service.submit((archive(), 'private-server.zip'), (archive(), 'private-client.zip'))
        self.job = self.service.process(self.job['id'])
        self.root = self.store.root / 'jobs' / self.job['id']

    def test_sealed_partial_candidate_is_traceable_but_never_reviewable(self):
        candidate = self.job['candidate']
        self.assertEqual(candidate, verify_candidate(self.root, self.job))
        self.assertEqual(candidate['integrityStatus'], 'SEALED')
        self.assertEqual(self.job['state'], 'BLOCKED')
        for flag in ('reviewable', 'publishable', 'extractionComplete'):
            self.assertIs(candidate[flag], False)
        self.assertEqual(candidate['archives']['server']['sha256'], self.job['sources']['server']['archiveSha256'])
        self.assertEqual(candidate['sourceTrees']['server']['fileCount'], 1)
        self.assertNotIn('private-server', json.dumps(candidate))
        self.assertNotIn('Content/original', json.dumps(candidate))
        self.assertNotIn('inventory', candidate)

    def assert_invalid(self):
        value = verify_candidate(self.root, self.job)
        self.assertEqual(value['integrityStatus'], 'INVALID')
        self.assertEqual(value['diagnostics'], ['CANDIDATE_INTEGRITY_INVALID'])
        self.assertIs(value['reviewable'], False)

    def test_zip_byte_change_invalidates(self):
        path = self.root / 'server.zip'
        data = bytearray(path.read_bytes()); data[-1] ^= 1; path.write_bytes(data)
        self.assert_invalid()

    def test_source_change_missing_and_addition_invalidate(self):
        path = self.root / 'server-files/Content/original.txt'
        original = path.read_bytes()
        path.write_bytes(b'changed'); self.assert_invalid()
        path.unlink(); self.assert_invalid()
        path.write_bytes(original)
        added = path.with_name('extra.txt'); added.write_bytes(b'extra'); self.assert_invalid(); added.unlink()
        added.mkdir(); self.assert_invalid()

    def test_evidence_change_and_addition_invalidate(self):
        path = self.root / 'adapter-evidence/version-adapter-manifest.json'
        original = path.read_bytes()
        path.write_bytes(b'{}'); self.assert_invalid()
        path.write_bytes(original)
        (path.parent / 'unaccepted.json').write_bytes(b'{}'); self.assert_invalid()

    def test_job_binding_and_seal_changes_invalidate(self):
        self.job['declaredVersion'] = '1.4.5.0'; self.assert_invalid()
        self.job['declaredVersion'] = None
        (self.root / 'sealed-candidate.json').write_bytes(b'{}'); self.assert_invalid()

    @unittest.skipIf(os.name == 'nt' or (hasattr(os, 'geteuid') and os.geteuid() == 0),
                     'Requires POSIX non-root directory permissions')
    def test_unreadable_directory_cannot_hide_an_added_file(self):
        empty = self.root / 'server-files/empty'
        empty.mkdir()
        (self.root / 'sealed-candidate.json').unlink()
        self.job['candidate'] = seal_candidate(self.root, self.job)
        (empty / 'hidden.txt').write_bytes(b'changed')
        try:
            empty.chmod(0)
            self.assert_invalid()
        finally:
            empty.chmod(0o700)

    def test_source_link_and_parent_link_are_rejected(self):
        path = self.root / 'server-files/Content/original.txt'
        target = self.root / 'outside.txt'; target.write_bytes(path.read_bytes())
        path.unlink(); path.symlink_to(target); self.assert_invalid()

    def test_resealing_is_rejected(self):
        with self.assertRaises(Exception):
            seal_candidate(self.root, self.job)

    def test_seal_io_failure_is_terminal_and_sanitized(self):
        job = self.service.submit((archive(), 'original.zip'))
        with patch('resource_pipeline.candidate.atomic_write', side_effect=OSError('/private/secret')):
            result = self.service.process(job['id'])
        self.assertEqual(result['state'], 'BLOCKED')
        self.assertEqual(result['error'], 'RAW_PROCESSING_FAILED')
        self.assertNotIn('candidate', result)
        self.assertNotIn('producerEvidence', result)
        self.assertNotIn('secret', json.dumps(result))

    def test_seal_interrupt_remains_interrupted_and_cleans_evidence(self):
        job = self.service.submit((archive(), 'original.zip'))
        with patch('resource_pipeline.candidate.atomic_write', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.service.process(job['id'])
        result = self.store.get(job['id'])
        self.assertEqual(result['state'], 'INTERRUPTED')
        self.assertNotIn('candidate', result)
        self.assertNotIn('producerEvidence', result)
        self.assertFalse((self.store.root / 'jobs' / job['id'] / 'adapter-evidence').exists())

    def test_cancel_during_seal_cleans_candidate_and_evidence(self):
        from resource_pipeline.candidate import file_digest
        job = self.service.submit((archive(), 'original.zip'))
        requested = False
        def cancel_then_hash(*args, **kwargs):
            nonlocal requested
            if not requested:
                requested = True
                self.service.request_cancel(job['id'])
            return file_digest(*args, **kwargs)
        with patch('resource_pipeline.candidate.file_digest', side_effect=cancel_then_hash):
            result = self.service.process(job['id'])
        self.assertEqual(result['state'], 'CANCELED')
        self.assertNotIn('candidate', result)
        self.assertNotIn('producerEvidence', result)
        root = self.store.root / 'jobs' / job['id']
        self.assertFalse((root / 'sealed-candidate.json').exists())
        self.assertFalse((root / 'adapter-evidence').exists())

    def test_archive_entry_order_is_not_tree_identity(self):
        stream = BytesIO()
        with zipfile.ZipFile(stream, 'w') as out:
            out.writestr('z.txt', 'last')
            out.writestr('a.txt', 'first')
        stream.seek(0)
        job = self.service.submit((stream, 'original.zip'))
        job = self.service.process(job['id'])
        self.assertEqual(job['candidate']['integrityStatus'], 'SEALED')
        self.assertEqual(verify_candidate(self.store.root / 'jobs' / job['id'], job), job['candidate'])

    def test_legacy_job_has_no_candidate(self):
        self.job.pop('candidate')
        self.assertIsNone(verify_candidate(self.root, self.job))

    def test_malformed_persisted_receipts_are_sanitized_not_500(self):
        original = copy.deepcopy(self.job)
        for key, malformed in [('candidate', 'private-secret'), ('candidate', {'secret': 'private-secret'}),
                               ('sources', {'server': 'private-secret'}), ('producerEvidence', ['private-secret'])]:
            self.job = copy.deepcopy(original)
            self.job[key] = malformed
            self.assert_invalid()
            self.assertNotIn('private-secret', json.dumps(verify_candidate(self.root, self.job)))

    def test_diagnostic_exception_text_never_leaks(self):
        with patch('resource_pipeline.candidate._snapshot', side_effect=OSError('/private/secret')):
            self.assert_invalid()
            self.assertNotIn('secret', json.dumps(verify_candidate(self.root, self.job)))

    def test_texture_output_tampering_invalidates(self):
        # Trusted test decoder receipt, fully bound by the private seal.
        from resource_pipeline.adapters import tree_inventory
        output = self.root / 'client-texture-job/output'; output.mkdir(parents=True)
        (output / 'texture-report.json').write_bytes(b'{}')
        (output / 'original.png').write_bytes(b'original synthetic byte fixture')
        self.job['textures']['client'] = {'status': 'TEXTURES_DECODED',
            'outputSha256': sha256(canonical_json(tree_inventory(output)))}
        (self.root / 'sealed-candidate.json').unlink()
        self.job['candidate'] = seal_candidate(self.root, self.job)
        self.assertEqual(verify_candidate(self.root, self.job)['integrityStatus'], 'SEALED')
        (output / 'original.png').write_bytes(b'changed'); self.assert_invalid()


class CandidateApiTests(unittest.TestCase):
    def test_review_revalidates_and_non_raw_type_check_precedes_verification(self):
        with tempfile.TemporaryDirectory() as root:
            app = create_app(Path(root), synchronous=True)
            with TestClient(app) as client:
                response = client.post('/api/raw-jobs', files={
                    'server_file': ('private.zip', archive().read(), 'application/zip')})
                self.assertEqual(response.status_code, 202)
                job = response.json(); identity = job['id']
                review = client.get(f'/api/raw-jobs/{identity}/review').json()
                self.assertEqual(review['candidate']['integrityStatus'], 'SEALED')
                self.assertFalse(review['reviewable'])
                (Path(root) / 'jobs' / identity / 'server.zip').write_bytes(b'changed')
                review = client.get(f'/api/raw-jobs/{identity}/review').json()
                self.assertEqual(review['candidate']['integrityStatus'], 'INVALID')
                self.assertFalse(review['reviewable'])
                self.assertEqual(client.post(f'/api/raw-jobs/{identity}/publish', json={
                    'confirmed': True, 'reviewDigest': review['candidate']['candidateDigest']}).status_code, 409)
                original_job = app.state.pipeline.get(identity)
                for field, malformed in [('sources', {'server': 'private-secret'}),
                                         ('producerEvidence', ['private-secret']), ('candidate', 'private-secret')]:
                    corrupt = copy.deepcopy(original_job)
                    corrupt[field] = malformed
                    app.state.pipeline.save(corrupt)
                    response = client.get(f'/api/raw-jobs/{identity}/review')
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()['candidate']['integrityStatus'], 'INVALID')
                    self.assertNotIn('private-secret', response.text)
                ordinary = app.state.pipeline.submit(archive().read(), 'ordinary.zip')
                with patch('resource_pipeline.candidate.verify_candidate', side_effect=AssertionError('wrong task')):
                    self.assertEqual(client.get(f'/api/raw-jobs/{ordinary["id"]}/review').status_code, 404)
