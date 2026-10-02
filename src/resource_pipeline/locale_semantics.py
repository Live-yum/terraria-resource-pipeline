"""Original, bounded model of a pinned LanguageManager embedded-only baseline.

This module never executes game or bundled dependency code. The reference source
is evidence for the model, not proof of equivalence to an arbitrary binary.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from .security import PipelineError

REFERENCE = {
    'repository': 'Live-yum/TerrariaDecompiledSource',
    'commit': '8255d34616c780af12079425ac92a0a7aed87d71',
    'languageManagerPath': 'Terraria.Localization/LanguageManager.cs',
    'languageManagerGitBlob': '94181f9660eeb7cdc2f37edc66d0d6231cd9d56f',
    'gameCultureGitBlob': 'a2190f8d9e33a7cfe97fc6eddaf207b4994353c4',
    'localizedTextGitBlob': 'aaf1380ff232ce5bf8e50ad2a1c0a41ca8a1e65f',
    'jsonNetReferenceAssemblyVersion': '10.0.0.0',
    'jsonNetReferenceSha256': '588155340ef9b7aa609b71724679b5356ba2ecdaa31ce1de010ae6bb3436fd07',
    'jsonNetRuleSource': 'https://github.com/JamesNK/Newtonsoft.Json/blob/10.0.3/Src/Newtonsoft.Json/Serialization/JsonSerializerInternalReader.cs',
    'jsonNetDictionaryRule': 'PopulateDictionary assigns dictionary[keyValue] = itemValue',
}
_PATTERN = re.compile(r'\{\$([^{}]+)\}')
_WORD_CATEGORIES = frozenset(('Ll', 'Lu', 'Lt', 'Lo', 'Lm', 'Mn', 'Nd', 'Pc'))


def _matches(value):
    for match in _PATTERN.finditer(value):
        parts = match.group(1).split('.')
        if len(parts) == 2 and all(part and all(unicodedata.category(char) in _WORD_CATEGORIES for char in part) for part in parts):
            yield match


def _digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _cycles(strings, checkpoint):
    """Bounded iterative DFS; records back edges, including stable self cycles."""
    graph = {key: list(dict.fromkeys(match.group(1) for match in _matches(value) if match.group(1) in strings))
             for key, value in strings.items()}
    state, result = {}, []
    for root in graph:
        checkpoint()
        if state.get(root):
            continue
        state[root] = 1
        stack = [(root, iter(graph[root]))]
        while stack:
            checkpoint()
            key, children = stack[-1]
            child = next(children, None)
            if child is None:
                state[key] = 2
                stack.pop()
            elif state.get(child) == 1:
                result.append({'key': key, 'reference': child, 'code': 'REFERENCE_CYCLE'})
            elif not state.get(child):
                state[child] = 1
                stack.append((child, iter(graph[child])))
    return result


def _copy_commands(strings, provenance, *, string_limit, total_limit, operation_limit, checkpoint):
    """In-place, insertion-order, 100-pass replacement from the pinned source."""
    errors = _cycles(strings, checkpoint)
    missing, evidence = [], {}
    operations = 0
    current_total = sum(len(value.encode('utf-8')) for value in strings.values())
    for key in list(strings):
        checkpoint()
        initial = strings[key]
        refs_used = []
        stabilized = False
        for attempt in range(100):
            checkpoint()
            before = strings[key]
            pieces, offset, length = [], 0, 0
            exhausted = False
            for match in _matches(before):
                operations += 1
                if operations > operation_limit:
                    errors.append({'key': key, 'code': 'REFERENCE_OPERATION_LIMIT'})
                    exhausted = True
                    break
                reference = match.group(1)
                replacement = strings.get(reference, reference)
                prefix = before[offset:match.start()]
                length += len(prefix.encode('utf-8')) + len(replacement.encode('utf-8'))
                if length > string_limit:
                    errors.append({'key': key, 'code': 'REFERENCE_STRING_LIMIT'})
                    exhausted = True
                    break
                pieces.extend((prefix, replacement))
                offset = match.end()
                refs_used.append(reference)
                if reference not in strings:
                    missing.append({'key': key, 'reference': reference, 'replacement': 'reference-key-text',
                                    'code': 'MISSING_REFERENCE'})
            if exhausted:
                break
            pieces.append(before[offset:])
            after = ''.join(pieces)
            if len(after.encode('utf-8')) > string_limit:
                errors.append({'key': key, 'code': 'REFERENCE_STRING_LIMIT'})
                break
            if before == after:
                stabilized = True
                break
            next_total = current_total - len(before.encode('utf-8')) + len(after.encode('utf-8'))
            if next_total > total_limit:
                errors.append({'key': key, 'code': 'EFFECTIVE_LOCALE_TOTAL_LIMIT'})
                break
            current_total = next_total
            strings[key] = after
        if not stabilized:
            errors.append({'key': key, 'code': 'REFERENCE_DID_NOT_STABILIZE_WITHIN_100_PASSES'})
        if refs_used:
            evidence[key] = {'initialSha256': _digest(initial), 'effectiveSha256': _digest(strings[key]),
                             'references': list(dict.fromkeys(refs_used)), 'passes': attempt + 1,
                             'stabilized': stabilized}
        if operations > operation_limit:
            break
    total = sum(len(value.encode('utf-8')) for value in strings.values())
    if total > total_limit:
        errors.append({'code': 'EFFECTIVE_LOCALE_TOTAL_LIMIT'})
    return {'errors': errors, 'missingReferences': missing, 'copyEvidence': evidence,
            'replacementOperations': operations, 'effectiveUtf8Bytes': total}


def embedded_baselines(languages, resources, *, string_limit=1024*1024,
                       total_limit=64*1024*1024, operation_limit=1_000_000, checkpoint=None):
    """Model a fresh manager: default en-US then one target, no content packs.

    Resource table order is retained explicitly. Runtime enumeration order,
    source/binary method equivalence and VariableText evaluation are not assumed
    to have been independently verified.
    """
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    locale_names = list(dict.fromkeys(row['language'] for row in resources if 'language' in row))
    if len(locale_names) > 32 or len(resources) > 4096:
        raise PipelineError('Embedded locale/resource model count exceeds limit')
    result = {}
    for target in locale_names:
        checkpoint()
        strings, provenance = {'': ''}, {'': {'origin': 'LocalizedText.Empty'}}
        variants, decisions, load_errors = {}, [], []
        passes = []
        phases = ['en-US'] if target == 'en-US' else ['en-US', target]
        for phase in phases:
            variants.clear()  # LoadLanguage clears variations, including fallback variations.
            for resource_index, row in enumerate(resources):
                checkpoint()
                if row.get('language') != phase:
                    continue
                if row['status'] not in ('EXTRACTED', 'EXTRACTED_WITH_GAPS'):
                    load_errors.append({'resource': row['name'], 'code': 'CULTURE_LOAD_STOPPED_AT_BAD_RESOURCE'})
                    break  # The pinned LoadFilesForCulture catch breaks this culture's loop.
                document = languages[row['name']]
                for key, value in document['strings'].items():
                    checkpoint()
                    proof = {'resource': row['name'], 'resourceOrder': resource_index,
                             'sourceLocale': phase, 'resourceSha256': row['sha256'],
                             'dataOffset': row['dataOffset'],
                             **document.get('keyEvidence', {}).get(key, {})}
                    if '$' in key:
                        parts = key.split('$')
                        variant_key = parts[0] + '$' + parts[1]
                        variants[variant_key] = {'baseKey': parts[0], 'variant': parts[1],
                                                 'value': value, 'evidence': proof}
                        continue
                    if key in strings:
                        decisions.append({'key': key, 'previous': provenance[key], 'selected': proof,
                                          'rule': 'LanguageManager.UpdateTextValue-in-resource-order'})
                    strings[key] = value
                    provenance[key] = proof
            copy = _copy_commands(strings, provenance, string_limit=string_limit,
                                  total_limit=total_limit, operation_limit=operation_limit, checkpoint=checkpoint)
            passes.append({'culture': phase, **copy})
        fallback_keys = [key for key, proof in provenance.items()
                         if key and target != 'en-US' and proof.get('sourceLocale') == 'en-US']
        strings.pop('', None)
        provenance.pop('', None)
        result[target] = {'strings': strings, 'keyEvidence': provenance,
                          'variants': variants, 'fallbackKeys': fallback_keys,
                          'resourceOverrides': decisions, 'copyPasses': passes, 'loadErrors': load_errors,
                          'status': 'REFERENCE_MODEL_WITH_ERRORS' if load_errors or any(row['errors'] or row['missingReferences'] for row in passes)
                          else 'REFERENCE_MODEL_EVALUATED',
                          'sourceModelEvaluated': True, 'binaryLoaderEquivalenceVerified': False,
                          'runtimeResourceOrderVerified': False, 'externalContentSourcesApplied': False,
                          'dynamicVariableTextEvaluated': False, 'complete': False}
    return result
