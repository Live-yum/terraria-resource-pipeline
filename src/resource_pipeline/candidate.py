"""Private evidence seals. Integrity is not semantic completeness or publication approval."""
from __future__ import annotations

from pathlib import Path
import os
import re
import time

from .adapters import file_digest, tree_inventory
from .contracts import package_file
from .security import PipelineError, atomic_write, canonical_json, read_json, sha256

MANIFEST = 'sealed-candidate.json'


def _regular_root(root: Path):
    if any(path.is_symlink() for path in (root, *root.parents)) or not root.is_dir():
        raise PipelineError('Candidate root is not a regular directory')


def _tree(root: Path, checkpoint):
    _regular_root(root)
    rows = tree_inventory(root, checkpoint=checkpoint, reject_hardlinks=True)
    directories = []
    def reject_walk_error(_error):
        raise PipelineError('Candidate tree cannot be completely read')
    for directory, names, _ in os.walk(root, followlinks=False, onerror=reject_walk_error):
        checkpoint()
        directories.extend((Path(directory) / name).relative_to(root).as_posix() for name in names)
    # Empty directory additions must invalidate review too.
    descriptor = {'files': rows, 'directories': sorted(directories)}
    return descriptor, {'sha256': sha256(canonical_json(descriptor)),
                        'fileCount': len(rows), 'bytes': sum(row['bytes'] for row in rows)}


def _snapshot(directory: Path, job: dict, checkpoint):
    _regular_root(directory)
    if job.get('kind') != 'raw-input-preflight' or not job.get('producerEvidence'):
        raise PipelineError('No candidate evidence')
    archives, source_trees, textures, private = {}, {}, {}, {}
    roles = set(job['sources'])
    if not roles or not roles <= {'server', 'client'}:
        raise PipelineError('Invalid candidate roles')
    for role in sorted(roles):
        checkpoint()
        source = job['sources'][role]
        if source.get('inventoryStatus') != 'verified':
            raise PipelineError('Candidate source was not verified')
        archive = package_file(directory, role + '.zip')
        size = archive.stat().st_size
        digest = file_digest(archive, maximum=source['archiveBytes'], checkpoint=checkpoint)
        if size != source['archiveBytes'] or digest != source['archiveSha256']:
            raise PipelineError('Candidate source changed')
        archives[role] = {'sha256': digest, 'bytes': size}
        tree, summary = _tree(directory / (role + '-files'), checkpoint)
        if tree['files'] != sorted(source['inventory']['files'], key=lambda row: row['path']):
            raise PipelineError('Candidate source inventory changed')
        source_trees[role] = summary
        private[role + 'Source'] = tree
        binding = job['producerEvidence'].get('inputBinding', {}).get(role, {})
        if binding.get('treeSha256') != sha256(canonical_json(tree['files'])):
            raise PipelineError('Candidate evidence source binding changed')
        texture = job.get('textures', {}).get(role, {})
        output = directory / (role + '-texture-job') / 'output'
        if texture.get('status') in ('TEXTURES_DECODED', 'NO_SUPPORTED_TEXTURES'):
            tree, summary = _tree(output, checkpoint)
            if sha256(canonical_json(tree['files'])) != texture.get('outputSha256'):
                raise PipelineError('Candidate texture evidence changed')
            textures[role] = summary
            private[role + 'Textures'] = tree
        elif output.exists() or output.is_symlink():
            raise PipelineError('Unaccepted candidate textures')
    # Unexpected source/texture roots cannot be silently excluded from the seal.
    for role in {'server', 'client'} - roles:
        for name in (role + '.zip', role + '-files', role + '-texture-job'):
            path = directory / name
            if path.exists() or path.is_symlink():
                raise PipelineError('Unexpected candidate source')
    evidence, evidence_summary = _tree(directory / 'adapter-evidence', checkpoint)
    if read_json(package_file(directory / 'adapter-evidence', 'version-adapter-manifest.json')) != job['producerEvidence']:
        raise PipelineError('Candidate adapter receipt changed')
    private['adapterEvidence'] = evidence
    projection = {key: job.get(key) for key in ('id', 'kind', 'declaredVersion', 'sources', 'textures',
                  'producerEvidence', 'extractionComplete', 'executedInput', 'blockers')}
    return {'schemaVersion': 1, 'jobBinding': sha256(canonical_json(projection)),
            'archives': archives, 'sourceTrees': source_trees, 'textures': textures,
            'adapterEvidence': evidence_summary, 'inventory': private}


def _summary(snapshot, digest, valid=True):
    return {'schemaVersion': 1, 'candidateDigest': digest,
            'integrityStatus': 'SEALED' if valid else 'INVALID',
            'reviewable': False, 'publishable': False, 'extractionComplete': False,
            **{key: snapshot[key] for key in ('archives', 'sourceTrees', 'textures', 'adapterEvidence')},
            'diagnostics': [] if valid else ['CANDIDATE_INTEGRITY_INVALID']}


def seal_candidate(directory: Path, job: dict, *, checkpoint=lambda: None):
    if (directory / MANIFEST).exists() or (directory / MANIFEST).is_symlink():
        raise PipelineError('Candidate already sealed')
    snapshot = _snapshot(directory, job, checkpoint)
    content = canonical_json(snapshot)
    checkpoint()
    atomic_write(directory / MANIFEST, content)
    return _summary(snapshot, sha256(content))


def _valid_candidate(value):
    if type(value) is not dict or set(value) != {
            'schemaVersion', 'candidateDigest', 'integrityStatus', 'reviewable', 'publishable',
            'extractionComplete', 'archives', 'sourceTrees', 'textures', 'adapterEvidence', 'diagnostics'}:
        return False
    def digest(value):
        return type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None
    def summary(value, tree=True):
        fields = {'sha256', 'bytes', 'fileCount'} if tree else {'sha256', 'bytes'}
        return (type(value) is dict and set(value) == fields and digest(value['sha256'])
                and all(type(value[key]) is int and 0 <= value[key] <= 2**53 - 1
                        for key in fields - {'sha256'}))
    if (type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or not digest(value['candidateDigest']) or value['integrityStatus'] not in ('SEALED', 'INVALID')
            or any(value[key] is not False for key in ('reviewable', 'publishable', 'extractionComplete'))
            or value['diagnostics'] != ([] if value['integrityStatus'] == 'SEALED' else ['CANDIDATE_INTEGRITY_INVALID'])):
        return False
    for key in ('archives', 'sourceTrees', 'textures'):
        rows = value[key]
        if type(rows) is not dict or not set(rows) <= {'server', 'client'}:
            return False
        if not all(summary(row, key != 'archives') for row in rows.values()):
            return False
    return (bool(value['archives']) and set(value['archives']) == set(value['sourceTrees'])
            and set(value['textures']) <= set(value['archives']) and summary(value['adapterEvidence']))


def verify_candidate(directory: Path, job: dict):
    """Rehash real bytes on each review. Never reseal or promote partial evidence."""
    candidate = job.get('candidate')
    if candidate is None:
        return None
    if not _valid_candidate(candidate):
        return {'schemaVersion': 1, 'candidateDigest': None, 'integrityStatus': 'INVALID',
                'reviewable': False, 'publishable': False, 'extractionComplete': False,
                'archives': {}, 'sourceTrees': {}, 'textures': {}, 'adapterEvidence': None,
                'diagnostics': ['CANDIDATE_INTEGRITY_INVALID']}
    invalid = {**candidate, 'integrityStatus': 'INVALID', 'reviewable': False,
               'publishable': False, 'extractionComplete': False,
               'diagnostics': ['CANDIDATE_INTEGRITY_INVALID']}
    if candidate['integrityStatus'] != 'SEALED':
        return invalid
    deadline = time.monotonic() + 120
    def checkpoint():
        if time.monotonic() >= deadline:
            raise PipelineError('Candidate verification timed out')
    try:
        recorded = read_json(package_file(directory, MANIFEST))
        if sha256(canonical_json(recorded)) != candidate['candidateDigest']:
            return invalid
        current = _snapshot(directory, job, checkpoint)
        checkpoint()
        if current != recorded or _summary(recorded, candidate['candidateDigest']) != candidate:
            return invalid
        return _summary(recorded, candidate['candidateDigest'])
    except (OSError, PipelineError, ValueError, KeyError, TypeError, AttributeError):
        return invalid
