#!/usr/bin/env python3
"""One-shot private CI proof; no proprietary strings or binaries in artifacts.

The full extraction remains in memory. Only counts, digests, numeric samples and
byte offsets are written. This does not execute, load or modify the input PE.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from resource_pipeline.server_semantics import extract_server_semantics
from resource_pipeline.security import PipelineError, canonical_json

SOURCE_REPOSITORY = 'Live-yum/TerrariaServerHook'
SOURCE_COMMIT = 'd6c7944d1f190884dcdb3d77c04c19a246d2eada'
SOURCE_PATH = 'server/1458/Windows/TerrariaServer.exe'
SOURCE_BLOB = 'ea00e07394ba51c64d1d36e27f4c176c81898581'
SOURCE_BYTES = 26_028_032


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def redact(result: dict) -> dict:
    """Closed output schema; no copying user/game strings into CI artifacts."""
    version = result['assembly']['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', version):
        raise PipelineError('Invalid numeric assembly version')
    families = {}
    for family, value in result['idFamilies'].items():
        records = value['records']
        families[family] = {
            'literalRecordCount': len(records),
            'distinctNumericValueCount': len({row['id'] for row in records}),
            'countConstants': [{'value': row['id'], 'fieldNameSha256': digest_text(row['name']),
                                'valueBlobOffset': row['offset'], 'metadataToken': row['metadataToken']}
                               for row in value['countConstants']],
            'unsupportedFieldCount': len(value['unsupportedFields']),
            'numericSamples': [{'value': row['id'], 'fieldNameSha256': digest_text(row['name']),
                                'metadataToken': row['metadataToken'], 'valueBlobOffset': row['offset'],
                                'constantMetadataOffset': row['evidence']['constantMetadataOffset'],
                                'valueBytes': row['evidence']['valueBytes']}
                               for row in records[:3]],
            'complete': False,
        }
    locales = {}
    for locale, value in result['localization'].items():
        if not re.fullmatch(r'[a-z]{2}-[A-Za-z]{2,4}', locale):
            raise PipelineError('Unexpected locale identifier')
        strings = value['strings']
        locales[locale] = {
            'keyCount': len(strings), 'rawKeyCount': len(value['rawStrings']),
            'fallbackKeyCount': len(value['baseline']['fallbackKeys']),
            'variantCount': len(value['baseline']['variants']),
            'sourceModelStatus': value['baseline']['status'],
            'modelErrorCount': len(value['baseline']['loadErrors']) + sum(len(row['errors']) for row in value['baseline']['copyPasses']),
            'missingReferenceCount': sum(len(row['missingReferences']) for row in value['baseline']['copyPasses']),
            'copyExpandedKeyCount': sum(len(row['copyEvidence']) for row in value['baseline']['copyPasses']),
            'contentSha256': hashlib.sha256(canonical_json(strings)).hexdigest(),
            'keyValueSamples': [{'keySha256': digest_text(key), 'valueSha256': digest_text(text),
                                 'valueUtf8Bytes': len(text.encode('utf-8'))}
                                for key, text in list(strings.items())[:3]],
            'resources': [{'resourceNameSha256': digest_text(row['name']),
                           'dataOffset': row['dataOffset'], 'bytes': row['bytes'],
                           'sha256': row['sha256'], 'decodedBytes': row['decodedBytes'],
                           'decodedSha256': row['decodedSha256'],
                           'compression': row['compression'], 'keyCount': row['keyCount'],
                           'ambiguousKeyCount': row['ambiguousKeyCount'],
                           'identicalDuplicateKeyCount': row['identicalDuplicateKeyCount'],
                           'manifestResourceToken': row['manifestResourceToken'],
                           'metadataOffset': row['metadataOffset']} for row in value['resources']
                          if row['status'] in ('EXTRACTED', 'EXTRACTED_WITH_GAPS')],
            'complete': False,
        }
    statuses = {}
    for resource in result['resources']:
        statuses[resource['status']] = statuses.get(resource['status'], 0) + 1
    return {
        'schemaVersion': 1, 'status': 'PARTIAL', 'executedInput': False,
        'complete': False, 'publishable': False, 'rawStringsIncluded': False, 'rawBinaryIncluded': False,
        'extractor': 'static-pe-cli-python-v1',
        'source': {'repository': SOURCE_REPOSITORY, 'commit': SOURCE_COMMIT, 'path': SOURCE_PATH,
                   'gitBlobSha1': SOURCE_BLOB, **result['input']},
        'assemblyVersion': version,
        'localeRuleEvidence': result['localeRuleEvidence'],
        'loaderMethodEvidence': [{'methodNameSha256': digest_text(row.get('type','') + '.' + row.get('method','')),
                                  **{key: value for key,value in row.items() if key not in ('type','method')}}
                                 for row in result['loaderMethodEvidence']],
        'duplicateSelectionEvidence': {locale: [{'pathSha256': digest_text(row.get('jsonPointer',row['key'])),
                                                  'resourceNameSha256': digest_text(row['resource']),
                                                  **{key: row[key] for key in ('occurrences','occurrenceOrdinals','selectedOrdinal','valueSha256s','rule') if key in row}}
                                               for row in rows]
                                       for locale,rows in result['localeDiagnostics']['duplicateSelectionEvidence'].items()},
        'versionEvidence': {'status': 'DECLARED_IN_ASSEMBLY', 'trusted': False,
                            'method': 'CLI-Assembly-table',
                            'versionFileOffset': result['gameVersionEvidence']['versionFileOffset'],
                            'assemblyMetadataOffset': result['assembly']['metadataOffset']},
        'metadata': {'cliHeaderOffset': result['metadata']['cliHeaderOffset'],
                     'metadataOffset': result['metadata']['metadataOffset'],
                     'runtimeVersionSha256': digest_text(result['metadata']['runtimeVersion']),
                     'tableRows': result['metadata']['tableRows']},
        'idGroupCount': len(result['ids']),
        'literalConstantCount': sum(len(rows) for rows in result['ids'].values()),
        'unsupportedFieldCount': len(result['unsupportedFields']),
        'families': families, 'locales': locales,
        'resourceStatusCounts': statuses,
        'resourceDiagnostics': [{'nameSha256': digest_text(row['name']), 'status': row['status'],
                                 'metadataOffset': row['metadataOffset'],
                                 **{key: row[key] for key in ('dataOffset', 'bytes', 'sha256', 'errorCode') if key in row}}
                                for row in result['resources'] if row['status'] not in ('EXTRACTED', 'EXTRACTED_WITH_GAPS')],
        'localeDiagnostics': {'missingChineseCount': len(result['localeDiagnostics']['missingChineseKeys']),
                              'missingEnglishCount': len(result['localeDiagnostics']['missingEnglishKeys']),
                              'identicalDuplicateKeyCounts': {name: len(rows) for name, rows in result['localeDiagnostics']['identicalDuplicateKeys'].items()},
                              'ambiguousKeyCounts': {name: len(rows) for name, rows in result['localeDiagnostics']['ambiguousKeys'].items()},
                              'unresolvedReferenceCounts': {name: len(rows) for name, rows in result['localeDiagnostics']['unresolvedReferences'].items()},
                              'runtimeFallbackEvaluated': False, 'runtimeInterpolationEvaluated': False},
        'gaps': {family: row['gaps'] for family, row in result['coverage'].items()},
    }


def run(source_root: Path, output: Path) -> dict:
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('GITHUB_REPOSITORY') != SOURCE_REPOSITORY:
        raise PipelineError('This proof is restricted to the authorized source repository CI')
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    if event.get('repository', {}).get('private') is not True:
        raise PipelineError('Source proof requires a private repository')
    if os.environ.get('PINNED_SOURCE_COMMIT') != SOURCE_COMMIT:
        raise PipelineError('Source checkout commit differs from approved pin')
    path = source_root / SOURCE_PATH
    if path.is_symlink() or not path.is_file() or path.stat().st_size != SOURCE_BYTES:
        raise PipelineError('Source binary file/size differs from approved pin')
    git_digest = hashlib.sha1(f'blob {SOURCE_BYTES}\0'.encode())
    with path.open('rb') as source:
        while block := source.read(1024 * 1024):
            git_digest.update(block)
    if git_digest.hexdigest() != SOURCE_BLOB:
        raise PipelineError('Source binary differs from approved Git blob pin')
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = extract_server_semantics(path)
    if before != result['input']['sha256'] or before != hashlib.sha256(path.read_bytes()).hexdigest():
        raise PipelineError('Source binary changed during static inspection')
    proof = redact(result)
    proof['inputUnchanged'] = True
    proof['parserSha256'] = hashlib.sha256((Path(__file__).resolve().parents[1] / 'src/resource_pipeline/server_semantics.py').read_bytes()).hexdigest()
    proof['localeModelSha256'] = hashlib.sha256((Path(__file__).resolve().parents[1] / 'src/resource_pipeline/locale_semantics.py').read_bytes()).hexdigest()
    proof['pipelineCommit'] = os.environ.get('PINNED_PIPELINE_COMMIT', '')
    if not re.fullmatch(r'[a-f0-9]{40}', proof['pipelineCommit']):
        raise PipelineError('Missing pinned pipeline checkout commit')
    if any(part.is_symlink() for part in (output, *output.parents)):
        raise PipelineError('Evidence output cannot traverse symlinks')
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with output.open('xb') as destination:
        destination.write(canonical_json(proof))
    return proof


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        proof = run(args.source_root, args.output)
        print(json.dumps({'status': 'PARTIAL', 'executedInput': False, 'inputUnchanged': True,
                          'assemblyVersion': proof['assemblyVersion'],
                          'inputSha256': proof['source']['sha256'],
                          'literalConstantCount': proof['literalConstantCount'],
                          'localizationFileCount': sum(len(row['resources']) for row in proof['locales'].values()),
                          'locales': {locale: row['keyCount'] for locale, row in proof['locales'].items()},
                          'rawStringsIncluded': False, 'publishable': False}))
        return 0
    except (PipelineError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({'status':'REJECTED', 'executedInput':False, 'error':str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
