"""Original, bounded model of a pinned LanguageManager embedded-only baseline.

This module never executes game or bundled dependency code. The reference source
is evidence for the model, not proof of equivalence to an arbitrary binary.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from .security import PipelineError
from .static_il import bounded_evidence_json

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
                       total_limit=64*1024*1024, operation_limit=1_000_000, checkpoint=None, compact=False):
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
        strings, provenance = {'': ''}, {'': None if compact else {'origin': 'LocalizedText.Empty'}}
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
                    if compact:
                        entry = document.get('keyEvidence', {}).get(key, {}).get('rawEntryIndex')
                        if type(entry) is not int or not 0 <= entry < len(document.get('document', {}).get('entries', [])):
                            raise PipelineError('Compact locale provenance requires an ordered raw entry')
                        proof = [resource_index, entry]
                    else:
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
                        decisions.append([provenance[key], proof] if compact else
                                         {'key': key, 'previous': provenance[key], 'selected': proof,
                                          'rule': 'LanguageManager.UpdateTextValue-in-resource-order'})
                    strings[key] = value
                    provenance[key] = proof
            copy = _copy_commands(strings, provenance, string_limit=string_limit,
                                  total_limit=total_limit, operation_limit=operation_limit, checkpoint=checkpoint)
            passes.append({'culture': phase, **copy})
        fallback_keys = [key for key, proof in provenance.items()
                         if key and target != 'en-US' and
                         (resources[proof[0]]['language'] if compact else proof.get('sourceLocale')) == 'en-US']
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


def compact_localization(languages, resources, locales, baselines, *, checkpoint=None):
    """Schema v2: retain ordered raw text once and use explicit source references.

    Every category occurrence (including empty/replaced categories), property
    occurrence, variant value and duplicate order is retained in document arrays.
    Effective strings remain directly accessible on each localization object.
    """
    checkpoint = checkpoint or (lambda: None)
    resource_indices = {row['name']: index for index, row in enumerate(resources)}
    compact_languages = {}
    for name, document in languages.items():
        checkpoint()
        compact_languages[name] = {'language': document['language'], 'resourceRef': resource_indices[name],
                                   'document': document['document'], 'duplicateKeys': document['duplicateKeys']}
    localization, copy_passes = {}, []
    for locale, raw in sorted(locales.items()):
        checkpoint()
        baseline = baselines[locale]
        refs = []
        for evidence in baseline['copyPasses']:
            checkpoint()
            existing = next((index for index, prior in enumerate(copy_passes) if prior == evidence), None)
            if existing is None:
                existing = len(copy_passes);copy_passes.append(evidence)
            refs.append(existing)
        selected_resources = [row for row in resources if row.get('language') == locale]
        localization[locale] = {
            'strings': baseline['strings'],
            'rawSource': {'resourceRefs': [resource_indices[row['name']] for row in selected_resources if row['name'] in languages],
                          'keyCount': len(raw), 'canonicalStringsSha256': hashlib.sha256(bounded_evidence_json(raw,64*1024*1024,checkpoint)).hexdigest()},
            'baseline': {**{key:value for key,value in baseline.items() if key not in ('strings','copyPasses')},
                         'stringsRef': '/localization/' + locale.replace('~','~0').replace('/','~1') + '/strings',
                         'copyPassRefs': refs},
            'resources': selected_resources,
        }
    evidence = {'schemaVersion': 2, 'sourceReference': '[resources index, languages[resource.name].document.entries index]',
                'documentEntryFields': ['categoryOrdinal','propertyOrdinal','name','value'],
                'categoryOrder': 'document.categories preserves every original category occurrence, including empty categories',
                'entryOrder': 'entries preserves original category/property order, including overwritten duplicates',
                'keyEvidenceRule': 'Resolve source reference; form key as category + dot + name; hash original UTF-8 value',
                'resourceOverrideFields': ['previousSourceRef','selectedSourceRef'],
                'resourceOverrideRule': 'LanguageManager.UpdateTextValue-in-resource-order; key comes from selected entry',
                'rawMergeRule': 'Json.NET last dictionary assignment, then ordered category/property flattening, then resource order',
                'copyPasses': copy_passes, 'binaryLoaderEquivalenceVerified': False}
    return compact_languages, localization, evidence


def resolve_locale_source(languages, resources, reference):
    """Reconstruct the full old per-key provenance without repeated metadata."""
    if reference is None:return {'origin':'LocalizedText.Empty'}
    if not isinstance(reference,(list,tuple)) or len(reference)!=2 or any(type(n) is not int for n in reference):
        raise PipelineError('Invalid locale source reference')
    resource_index,entry_index=reference
    if not 0<=resource_index<len(resources):raise PipelineError('Invalid locale resource reference')
    resource=resources[resource_index]
    document=languages[resource['name']]['document']
    if not 0<=entry_index<len(document['entries']):raise PipelineError('Invalid locale entry reference')
    category_ordinal,property_ordinal,name,value=document['entries'][entry_index]
    category=document['categories'][category_ordinal]
    pointer='/'+'/'.join(part.replace('~','~0').replace('/','~1') for part in (category,name))
    return {'resource':resource['name'],'resourceOrder':resource_index,'sourceLocale':resource['language'],
            'resourceSha256':resource['sha256'],'dataOffset':resource['dataOffset'],
            'jsonPointer':pointer,'categoryOrdinal':category_ordinal,'propertyOrdinal':property_ordinal,
            'rawEntryIndex':entry_index,'valueSha256':_digest(value)}


def reconstruct_raw_locale(languages, resources, resource_refs, *, checkpoint=None):
    """Reconstruct rawStrings, including whole-category replacement semantics."""
    checkpoint=checkpoint or (lambda:None)
    merged={}
    for index in resource_refs:
        checkpoint()
        if type(index) is not int or not 0<=index<len(resources):raise PipelineError('Invalid raw locale resource reference')
        document=languages[resources[index]['name']]['document']
        categories=document['categories'];occurrences=[{} for _ in categories]
        for category_ordinal,property_ordinal,name,value in document['entries']:
            checkpoint()
            occurrences[category_ordinal][name]=value
        selected={}
        for category,entries in zip(categories,occurrences):selected[category]=entries
        for category,entries in selected.items():
            for name,value in entries.items():merged[category+'.'+name]=value
    return merged


def locale_copy_passes(result, locale):
    baseline=result['localization'][locale]['baseline']
    return [result['localeEvidence']['copyPasses'][index] for index in baseline['copyPassRefs']]
