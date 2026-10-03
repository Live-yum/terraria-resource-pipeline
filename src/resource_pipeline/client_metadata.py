"""Private client metadata evidence, with no server/runtime semantics implied.

Only bounded byte parsing is used. Loading assemblies, resolving dependencies,
executing methods and applying the server item/locale-loader models are excluded.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import struct

from .security import PipelineError, canonical_json
from .server_semantics import (SemanticLimits, _Metadata, _id_constants, _languages,
                               _types, read_assembly_bytes)
from .static_il import json_evidence_size


def extract_client_metadata(input_path: Path, limits: SemanticLimits = SemanticLimits(),
                            checkpoint=lambda: None) -> dict:
    checkpoint()
    data = read_assembly_bytes(input_path, limits, checkpoint)
    if data.startswith(b'version https://git-lfs.github.com/spec/v1'):
        raise PipelineError('Client input is a Git LFS pointer, not a PE payload')
    try:
        meta = _Metadata(data, limits, checkpoint=checkpoint)
        if meta.rows[32] != 1:
            raise PipelineError('Input must contain exactly one CLI assembly definition')
        assembly, offset = meta.row(32, 1)
        version = '.'.join(str(value) for value in assembly[1:5])
        assembly_name = meta.string(assembly[7])
        ids, proofs, unsupported, version_fields = _id_constants(meta, _types(meta))
        # Preserve per-resource raw documents. Flattened/duplicate projections
        # are diagnostic only, not proof of the client's runtime loader rules.
        languages, resources, locales, ambiguous, identical = _languages(meta)
    except PipelineError:
        raise
    except (UnicodeError, struct.error, ValueError, RecursionError) as exc:
        raise PipelineError('Malformed or unsupported client PE/CLI data') from exc
    result = {
        'schemaVersion': 1, 'extractor': 'static-client-pe-cli-python-v1',
        'sourceRole': 'client', 'status': 'PARTIAL', 'executedInput': False,
        'complete': False, 'publishable': False,
        'input': {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)},
        'assembly': {'name': assembly_name, 'version': version, 'metadataOffset': offset},
        'gameVersionEvidence': {'status': 'DECLARED_IN_ASSEMBLY', 'trusted': False,
            'method': 'CLI-Assembly-table', 'assemblyMetadataOffset': offset,
            'versionFileOffset': offset + 4, 'literalFields': version_fields},
        'metadata': {'cliHeaderOffset': meta.cli_offset, 'metadataOffset': meta.metadata_offset,
            'runtimeVersion': meta.runtime_version,
            'tableRows': {str(key): value for key, value in meta.rows.items() if value}},
        'ids': ids, 'idEvidence': proofs, 'unsupportedFields': unsupported,
        # Do not duplicate every value in strings and per-key hash projections.
        # Resource byte offsets/hashes plus raw category/property ordinals retain
        # the source proof while keeping real client output within the budget.
        'languages': {name: {key: value for key, value in language.items()
                            if key in ('language', 'document', 'evidence')}
                      for name, language in languages.items()}, 'resources': resources,
        'localeDiagnostics': {'keyCounts': {name: len(rows) for name, rows in locales.items()},
            'ambiguousKeys': ambiguous, 'identicalDuplicateKeys': identical,
            'interpretation': 'raw-resource-and-diagnostic-projection-only',
            'binaryLoaderEquivalenceVerified': False, 'fallbackEvaluated': False,
            'copyCommandsEvaluated': False, 'interpolationEvaluated': False},
        'unsupported': ['runtime-item-defaults', 'server-item-model-equivalence',
            'client-locale-loader-equivalence', 'resolved-dynamic-tooltips',
            'asset-installation-provenance', 'authoritative-game-version'],
        'sourcePolicy': 'Binary declarations and matching hashes are evidence, not operator identity/version pins',
    }
    json_evidence_size(result, limits.output_bytes, checkpoint)
    return result


def compare_metadata(server: dict, client: dict, checkpoint=lambda: None) -> dict:
    """Bounded informational comparison; equality never authenticates a source."""
    def domains(evidence):
        result = {}
        for group, fields in evidence.get('ids', {}).items():
            checkpoint()
            values = set()
            for name, value in fields.items():
                checkpoint()
                if name.casefold() != 'count' and type(value) is int:
                    values.add(value)
            result[group] = sorted(values)
        return result

    server_ids, client_ids = domains(server), domains(client)
    groups = sorted(set(server_ids) | set(client_ids))
    def locale_hashes(evidence):
        result = {}
        for row in evidence.get('resources', []):
            checkpoint()
            if 'language' in row and 'sha256' in row:
                result[row['name']] = row['sha256']
        return result
    server_locales, client_locales = locale_hashes(server), locale_hashes(client)
    return {'status': 'INFORMATIONAL_ONLY', 'trusted': False, 'complete': False,
        'serverSha256': server['input']['sha256'], 'clientSha256': client['input']['sha256'],
        'declaredVersions': {'server': server.get('assembly', {}).get('version'),
                            'client': client.get('assembly', {}).get('version')},
        'declaredVersionsMatch': (server.get('assembly', {}).get('version') is not None
            and server.get('assembly', {}).get('version') == client.get('assembly', {}).get('version')),
        'idDomains': {group: {'serverCount': len(server_ids.get(group, [])),
            'clientCount': len(client_ids.get(group, [])),
            'serverSha256': hashlib.sha256(canonical_json(server_ids.get(group))).hexdigest(),
            'clientSha256': hashlib.sha256(canonical_json(client_ids.get(group))).hexdigest(),
            'match': group in server_ids and group in client_ids and server_ids[group] == client_ids[group]}
            for group in groups},
        'localeResourceHashes': {name: {'serverSha256': server_locales.get(name),
            'clientSha256': client_locales.get(name),
            'match': name in server_locales and name in client_locales and server_locales[name] == client_locales[name]}
            for name in sorted(set(server_locales) | set(client_locales))},
        'installationProvenanceVerified': False,
        'interpretation': 'Metadata agreement cannot establish provenance or full semantic equivalence'}
