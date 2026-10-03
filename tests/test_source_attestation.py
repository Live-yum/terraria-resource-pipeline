"""Synthetic bytes only; no game inputs or policy registration through uploads."""
from dataclasses import asdict, replace
from io import BytesIO
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from resource_pipeline.pipeline import Pipeline
from resource_pipeline.preflight import RawInputPreflight, TrustedSource
from resource_pipeline.security import ArchiveLimits, PipelineError
from resource_pipeline.source_attestation import (ClientInstallationAttestation, LFS_HEADER,
                                                  _git_hash, _snapshot, build_attested_client)


class SourceAttestationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = b'MZ' + b'synthetic-inert-client' * 20
        self.exe = self.root / 'client.exe'
        self.exe.write_bytes(self.payload)
        self.content = self.root / 'fixture' / 'Content'
        for directory in ('Images', 'Fonts'):
            (self.content / directory).mkdir(parents=True)
            (self.content / directory / 'fixture.xnb').write_bytes(b'synthetic-' + directory.encode())
        self.digest = hashlib.sha256(self.payload).hexdigest()
        pointer = LFS_HEADER + f'oid sha256:{self.digest}\nsize {len(self.payload)}\n'.encode()
        (self.content / 'Terraria.exe').write_bytes(pointer)
        (self.content / 'PixelShader.xnb').write_bytes(b'excluded synthetic shader')
        tree, files, trees = _snapshot(self.content, ArchiveLimits(), lambda: None)
        self.policy = ClientInstallationAttestation('fixture/source', 'a' * 40, tree,
            trees['Images'], trees['Fonts'], _git_hash('blob', pointer), self.digest,
            len(self.payload), '1.4.5.8', 'Operator confirms exact synthetic pair')
        self.archive = self.root / 'source.zip'
        self.pack()

    def pack(self, wrapper=None):
        wrapper = wrapper or 'source-' + self.policy.commit
        with zipfile.ZipFile(self.archive, 'w') as z:
            for path in sorted(self.content.rglob('*')):
                if path.is_file():
                    z.write(path, wrapper + '/Content/' + path.relative_to(self.content).as_posix())

    def run_binding(self, policy=None):
        return build_attested_client(self.archive, self.exe, self.root / 'result', policy or self.policy)

    def test_git_tree_order_matches_independent_git_write_tree_golden(self):
        root = self.root / 'golden'
        (root / 'a').mkdir(parents=True)
        (root / 'a/nested').write_bytes(b'nested')
        (root / 'a.x').write_bytes(b'dot')
        (root / 'a0').write_bytes(b'zero')
        # Independently generated with Git write-tree, including the tricky
        # a.x < a/ < a0 directory-name ordering boundary.
        digest, _, _ = _snapshot(root, ArchiveLimits(), lambda: None)
        self.assertEqual('54b3caffa9c1e3bcbb989231ccd24a7074d80a2d', digest)

    def test_checkpoint_failure_removes_partial_output(self):
        def canceled(): raise PipelineError('Canceled')
        with self.assertRaisesRegex(PipelineError, 'Canceled'):
            build_attested_client(self.archive, self.exe, self.root / 'result', self.policy, checkpoint=canceled)
        self.assertFalse((self.root / 'result').exists())

    def test_payload_size_limit_applies_to_materialized_executable(self):
        with self.assertRaises(PipelineError):
            build_attested_client(self.archive, self.exe, self.root / 'result', self.policy,
                                  limits=ArchiveLimits(file_bytes=len(self.payload) - 1))
        self.assertFalse((self.root / 'result').exists())

    def test_input_symlink_is_rejected(self):
        link = self.root / 'link.exe'
        link.symlink_to(self.exe)
        with self.assertRaises(PipelineError):
            build_attested_client(self.archive, link, self.root / 'result', self.policy)

    def test_source_recompression_does_not_change_output_pin(self):
        first = self.run_binding()
        other = self.root / 'repacked.zip'
        with zipfile.ZipFile(self.archive) as original, zipfile.ZipFile(other, 'w', compression=zipfile.ZIP_DEFLATED) as out:
            for name in reversed(original.namelist()): out.writestr(name, original.read(name))
        second = build_attested_client(other, self.exe, self.root / 'second', self.policy)
        self.assertNotEqual(first['source']['archiveSha256'], second['source']['archiveSha256'])
        self.assertEqual(first['trustedSource'], second['trustedSource'])

    def test_measured_scoped_pin_and_truthful_exclusions(self):
        receipt = self.run_binding()
        with zipfile.ZipFile(self.root / 'result/client-source.zip') as z:
            self.assertEqual({'Content/Terraria.exe', 'Content/Images/fixture.xnb',
                              'Content/Fonts/fixture.xnb'}, set(z.namelist()))
            self.assertEqual(self.payload, z.read('Content/Terraria.exe'))
        self.assertEqual(self.digest, receipt['source']['clientSha256'])
        self.assertEqual(self.policy.executable_pointer_blob, receipt['source']['clientLfsPointerBlobSha1'])
        self.assertFalse(receipt['soundsPresentInSource'])
        self.assertIn('Sounds', receipt['excluded'])
        self.assertTrue(all(receipt[key] is False for key in
                            ('fullAssetCoverage', 'complete', 'publishable', 'vendorAuthenticityVerified', 'executedInput')))
        self.assertEqual(hashlib.sha256((self.root / 'result/client-source.zip').read_bytes()).hexdigest(),
                         receipt['trustedSource']['archive_sha256'])

    def test_attestation_tree_or_pointer_mismatch_rejected(self):
        for field in ('content_tree', 'images_tree', 'fonts_tree', 'executable_pointer_blob'):
            with self.subTest(field=field), self.assertRaises(PipelineError):
                self.run_binding(replace(self.policy, **{field: 'b' * 40}))
            self.assertFalse((self.root / 'result').exists())

    def test_tree_changes_rejected_even_when_payload_unchanged(self):
        for change in ('modify', 'add', 'remove'):
            path = self.content / 'Images/fixture.xnb'
            path.write_bytes(b'synthetic-Images')
            extra = self.content / 'Images/new.xnb'
            extra.unlink(missing_ok=True)
            if change == 'modify': path.write_bytes(b'changed')
            elif change == 'add': extra.write_bytes(b'new')
            else: path.unlink()
            self.pack()
            with self.subTest(change=change), self.assertRaises(PipelineError): self.run_binding()

    def test_payload_mismatch_or_pointer_instead_of_payload_rejected(self):
        for data in (self.payload[:-1] + b'!', (self.content / 'Terraria.exe').read_bytes()):
            self.exe.write_bytes(data)
            with self.assertRaises(PipelineError): self.run_binding()

    def test_archive_wrapper_commit_mismatch_rejected(self):
        self.pack(wrapper='source-' + 'b' * 40)
        with self.assertRaises(PipelineError): self.run_binding()

    def test_policy_must_be_separate_configuration(self):
        with self.assertRaises(PipelineError): self.run_binding(asdict(self.policy))
        with self.assertRaises(ValueError): replace(self.policy, method='assembly-declared')
        with self.assertRaises(ValueError): replace(self.policy, scope='all-assets')

    def test_asset_pointer_never_counts_as_materialized_asset(self):
        (self.content / 'Images/fixture.xnb').write_bytes(LFS_HEADER + b'oid sha256:fake\nsize 12\n')
        self.pack()
        with self.assertRaisesRegex(PipelineError, 'Asset LFS pointer'): self.run_binding()

    def test_empty_directories_not_silently_ignored(self):
        (self.content / 'Images/empty').mkdir()
        with self.assertRaisesRegex(PipelineError, 'Empty directory'):
            _snapshot(self.content, ArchiveLimits(), lambda: None)

    def test_pinning_does_not_relax_combined_source_or_publication(self):
        receipt = self.run_binding()
        pipeline = Pipeline(self.root / 'pipeline')
        # Deliberately supply the client package in the server slot. A valid
        # client pin cannot authenticate its use as server/combined input.
        service = RawInputPreflight(pipeline, trusted_sources=(TrustedSource(**receipt['trustedSource']),))
        data = (self.root / 'result/client-source.zip').read_bytes()
        result = service.process(service.submit(server_file=(BytesIO(data), 'combined.zip'))['id'])
        codes = {row['code'] for row in result['blockers']}
        self.assertIn('VERSION_UNVERIFIED', codes)
        self.assertIn('COMBINED_TEXTURE_SOURCE_UNVERIFIED', codes)
        self.assertFalse(result['extractionComplete'])
        with self.assertRaises(PipelineError): pipeline.publish(result['id'], '', True)

    def test_recompressing_valid_repetitive_input_stays_within_ratio_limit(self):
        (self.content / 'Images/fixture.xnb').write_bytes(b'\0' * (1024 * 1024))
        tree, _, trees = _snapshot(self.content, ArchiveLimits(), lambda: None)
        self.policy = replace(self.policy, content_tree=tree, images_tree=trees['Images'])
        self.pack()  # Stored input is valid under the unchanged ratio guard.
        receipt = self.run_binding()
        data = (self.root / 'result/client-source.zip').read_bytes()
        pipeline = Pipeline(self.root / 'pipeline')
        service = RawInputPreflight(pipeline, trusted_sources=(TrustedSource(**receipt['trustedSource']),))
        job = service.process(service.submit(client_file=(BytesIO(data), 'client.zip'))['id'])
        self.assertEqual('verified', job['sources']['client']['inventoryStatus'])
        with zipfile.ZipFile(BytesIO(data)) as z:
            self.assertEqual(zipfile.ZIP_STORED, z.getinfo('Content/Images/fixture.xnb').compress_type)

    def test_pin_is_exact_zip_not_repacked_content_or_uploaded_claim(self):
        receipt = self.run_binding()
        data = (self.root / 'result/client-source.zip').read_bytes()
        pipeline = Pipeline(self.root / 'pipeline')
        service = RawInputPreflight(pipeline, trusted_sources=(TrustedSource(**receipt['trustedSource']),))
        matching = service.process(service.submit(client_file=(BytesIO(data), 'client.zip'))['id'])
        self.assertEqual('verified', matching['sources']['client']['versionEvidence']['status'])
        changed = BytesIO()
        with zipfile.ZipFile(BytesIO(data)) as z, zipfile.ZipFile(changed, 'w') as out:
            for name in z.namelist(): out.writestr(name, z.read(name))
            out.writestr('operator-attestation.json', json.dumps(asdict(self.policy)))
        mismatch = service.process(service.submit(client_file=(BytesIO(changed.getvalue()), 'client.zip'))['id'])
        self.assertEqual('unverified', mismatch['sources']['client']['versionEvidence']['status'])
        self.assertEqual('BLOCKED', matching['state'])
        self.assertFalse(matching['extractionComplete'])

if __name__ == '__main__': unittest.main()
