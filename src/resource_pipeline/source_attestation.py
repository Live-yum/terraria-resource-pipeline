"""Offline operator attestation binding. Never an uploaded manifest trust path.

The operator supplies a reviewed policy, separately from the source ZIP. Matching
Git identities authenticate the chosen bytes, not the publisher or game semantics.
Only the confirmed client executable, Images and Fonts enter the output package.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import zipfile
import zlib

from .preflight import TrustedSource
from .security import ArchiveLimits, PipelineError, canonical_json, extract_zip, relative_path

LFS_HEADER = b'version https://git-lfs.github.com/spec/v1\n'


@dataclass(frozen=True)
class ClientInstallationAttestation:
    repository: str
    commit: str
    content_tree: str
    images_tree: str
    fonts_tree: str
    executable_pointer_blob: str
    executable_sha256: str
    executable_bytes: int
    game_version: str
    statement: str
    scope: str = 'client-executable-images-fonts'
    method: str = 'operator-confirmed-same-installation'

    def __post_init__(self):
        if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', self.repository)
                or any(not re.fullmatch(r'[0-9a-f]{40}', x) for x in
                       (self.commit, self.content_tree, self.images_tree, self.fonts_tree,
                        self.executable_pointer_blob))
                or not re.fullmatch(r'[0-9a-f]{64}', self.executable_sha256)
                or type(self.executable_bytes) is not int or self.executable_bytes < 1
                or not re.fullmatch(r'[0-9]+(?:\.[0-9]+){2,3}', self.game_version)
                or not isinstance(self.statement, str) or not self.statement.strip()
                or len(self.statement) > 2000
                or self.scope != 'client-executable-images-fonts'
                or self.method != 'operator-confirmed-same-installation'):
            raise ValueError('Invalid separately reviewed operator attestation')


def _git_hash(kind: str, data: bytes) -> str:
    return hashlib.sha1(f'{kind} {len(data)}\0'.encode() + data).hexdigest()


def _read_regular(path: Path, maximum: int, checkpoint) -> bytes:
    checkpoint()
    if path.is_symlink():
        raise PipelineError('Source links are forbidden')
    with path.open('rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise PipelineError('Source file exceeds bounds')
        data = bytearray()
        while chunk := stream.read(min(1024 * 1024, maximum - len(data) + 1)):
            checkpoint()
            data.extend(chunk)
            if len(data) > maximum:
                raise PipelineError('Source actual bytes exceed bounds')
        if len(data) != info.st_size:
            raise PipelineError('Source changed while reading')
    return bytes(data)


def _snapshot(root: Path, limits: ArchiveLimits, checkpoint) -> tuple[str, dict, dict]:
    """Reconstruct Git's regular-100644 tree from actual extracted bytes.

    Empty directories are rejected: Git does not bind their identity. Source
    modes are intentionally limited to ordinary files, never executable code.
    """
    files, trees = {}, {}
    total = 0
    def walk(directory):
        nonlocal total
        checkpoint()
        if directory.is_symlink() or not directory.is_dir():
            raise PipelineError('Source tree must contain ordinary directories')
        entries = []
        for child in directory.iterdir():
            checkpoint()
            relative = child.relative_to(root).as_posix()
            relative_path(relative)
            mode = child.lstat().st_mode
            if stat.S_ISDIR(mode):
                digest = walk(child)
                entries.append((child.name.encode() + b'/', b'40000', child.name.encode(), digest))
            elif stat.S_ISREG(mode):
                size = child.stat().st_size
                if size > limits.file_bytes or len(files) >= limits.files:
                    raise PipelineError('Attested source exceeds file bounds')
                data = _read_regular(child, limits.file_bytes, checkpoint)
                total += len(data)
                if len(data) != size or total > limits.expanded_bytes:
                    raise PipelineError('Attested source exceeds byte bounds')
                if data.startswith(LFS_HEADER) and relative != 'Terraria.exe':
                    raise PipelineError('Asset LFS pointer is not materialized data')
                digest = _git_hash('blob', data)
                files[relative] = {'bytes': size, 'sha256': hashlib.sha256(data).hexdigest(),
                                   'gitBlobSha1': digest}
                entries.append((child.name.encode(), b'100644', child.name.encode(), digest))
            else:
                raise PipelineError('Links and special files are not attested inputs')
        if not entries:
            raise PipelineError('Empty directory has no attested Git identity')
        raw = b''.join(mode + b' ' + name + b'\0' + bytes.fromhex(digest)
                       for _, mode, name, digest in sorted(entries))
        digest = _git_hash('tree', raw)
        trees[directory.relative_to(root).as_posix()] = digest
        return digest
    return walk(root), files, trees


def build_attested_client(source_archive: Path, executable: Path, output_directory: Path,
                          policy: ClientInstallationAttestation,
                          limits: ArchiveLimits = ArchiveLimits(), checkpoint=lambda: None) -> dict:
    """Produce a measured ZIP pin only after checking independently reviewed pins.

    No service API calls this function and no policy is read from an upload.
    The destination must be new. Failures remove incomplete output files.
    """
    from dataclasses import asdict
    if type(policy) is not ClientInstallationAttestation:
        raise PipelineError('A separately configured operator policy is required')
    for path in (source_archive, executable):
        if path.is_symlink() or not path.is_file():
            raise PipelineError('Attested input must be an ordinary file')
    if source_archive.stat().st_size > limits.archive_bytes:
        raise PipelineError('Archive exceeds source bounds')
    if executable.stat().st_size != policy.executable_bytes or policy.executable_bytes > limits.file_bytes:
        raise PipelineError('Client payload size differs from attestation')
    output_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    archive_path = output_directory / 'client-source.zip'
    try:
        with tempfile.TemporaryDirectory(prefix='attested-', dir=output_directory) as temporary:
            private = Path(temporary)
            archive_bytes = _read_regular(source_archive, limits.archive_bytes, checkpoint)
            archive_digest = hashlib.sha256(archive_bytes).hexdigest()
            snapshot_zip = private / 'input.zip'
            snapshot_zip.write_bytes(archive_bytes)
            del archive_bytes
            extract_zip(snapshot_zip, private / 'source', limits, checkpoint=checkpoint)
            # GitHub's archive wrapper is exact and commit-specific. Full source
            # tree identity is checked below; a filename is not version proof.
            wrapper = policy.repository.split('/')[1] + '-' + policy.commit
            roots = list((private / 'source').iterdir())
            if len(roots) != 1 or roots[0].name != wrapper:
                raise PipelineError('Pinned source archive wrapper mismatch')
            content = roots[0] / 'Content'
            content_tree, files, trees = _snapshot(content, limits, checkpoint)
            if (content_tree != policy.content_tree or trees.get('Images') != policy.images_tree
                    or trees.get('Fonts') != policy.fonts_tree):
                raise PipelineError('Attested Content/Images/Fonts Git tree mismatch')
            pointer = content / 'Terraria.exe'
            expected_pointer = (LFS_HEADER + f'oid sha256:{policy.executable_sha256}\n'
                                f'size {policy.executable_bytes}\n'.encode())
            if (_read_regular(pointer, 1024, checkpoint) != expected_pointer
                    or files['Terraria.exe']['gitBlobSha1'] != policy.executable_pointer_blob):
                raise PipelineError('Attested executable LFS pointer mismatch')
            payload = _read_regular(executable, policy.executable_bytes, checkpoint)
            if (len(payload) != policy.executable_bytes or payload.startswith(LFS_HEADER)
                    or not payload.startswith(b'MZ')
                    or hashlib.sha256(payload).hexdigest() != policy.executable_sha256):
                raise PipelineError('Materialized client payload differs from attestation')
            chosen = {name: row for name, row in files.items()
                      if name.startswith(('Images/', 'Fonts/'))}
            chosen['Terraria.exe'] = {'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
            if (sum(row['bytes'] for row in chosen.values()) > limits.expanded_bytes
                    or len(chosen) > limits.files):
                raise PipelineError('Scoped client package exceeds source bounds')
            with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED) as output:
                for name in sorted(chosen):
                    checkpoint()
                    data = payload if name == 'Terraria.exe' else _read_regular(content / name, limits.file_bytes, checkpoint)
                    if (len(data) != chosen[name]['bytes']
                            or hashlib.sha256(data).hexdigest() != chosen[name]['sha256']):
                        raise PipelineError('Source changed while packaging')
                    entry = zipfile.ZipInfo('Content/' + name, date_time=(1980, 1, 1, 0, 0, 0))
                    entry.external_attr = 0o100644 << 16
                    # Deflating highly repetitive but valid input must not
                    # generate a package rejected by the unchanged ZIP ratio
                    # guard. Measure the same raw DEFLATE representation first.
                    compressor = zlib.compressobj(wbits=-15)
                    compressed_size = 0
                    for offset in range(0, len(data), 1024 * 1024):
                        checkpoint()
                        compressed_size += len(compressor.compress(data[offset:offset + 1024 * 1024]))
                    compressed_size += len(compressor.flush())
                    entry.compress_type = (zipfile.ZIP_STORED if len(data) > max(1, compressed_size) * limits.ratio
                                           else zipfile.ZIP_DEFLATED)
                    output.writestr(entry, data)
            if archive_path.stat().st_size > limits.archive_bytes:
                raise PipelineError('Scoped client archive exceeds source bounds')
            archive_sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            pin = TrustedSource('client', archive_sha256, policy.game_version)
            # The output has a stable identity independent of source archive
            # compression, but a new ZIP always receives its own measured pin.
            receipt = {'schemaVersion': 1, 'status': 'OPERATOR_ATTESTED_SOURCE_BOUND',
                'method': policy.method, 'scope': policy.scope,
                'attestationSha256': hashlib.sha256(canonical_json(asdict(policy))).hexdigest(),
                'source': {'repository': policy.repository, 'commit': policy.commit,
                           'archiveSha256': archive_digest, 'contentTreeSha1': content_tree,
                           'imagesTreeSha1': trees['Images'], 'fontsTreeSha1': trees['Fonts'],
                           'clientLfsPointerBlobSha1': policy.executable_pointer_blob,
                           'clientSha256': policy.executable_sha256, 'clientBytes': len(payload)},
                'package': {'archiveSha256': archive_sha256, 'archiveBytes': archive_path.stat().st_size,
                            'fileCount': len(chosen), 'expandedBytes': sum(row['bytes'] for row in chosen.values()),
                            'inventorySha256': hashlib.sha256(canonical_json(chosen)).hexdigest()},
                'trustedSource': asdict(pin), 'operatorConfirmedGameVersion': policy.game_version,
                'excluded': ['Sounds', 'music-wave-banks', 'shader-and-audio-bank-files'],
                'soundsPresentInSource': any(name.startswith('Sounds/') for name in files),
                'vendorAuthenticityVerified': False, 'fullAssetCoverage': False,
                'complete': False, 'publishable': False, 'executedInput': False}
            (output_directory / 'source-binding.json').write_bytes(canonical_json(receipt))
            return receipt
    except BaseException:
        import shutil
        shutil.rmtree(output_directory)
        raise
