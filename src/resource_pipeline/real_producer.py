"""Real, data-only producer stages. Partial evidence never authorizes publication.

The same uploaded source trees feed static CLI metadata and the sandbox's raw-XNB
receipts. No old viewer tables, uploaded approval flags or game execution is used.
"""
from __future__ import annotations
from pathlib import Path
import re

from .adapters import AdapterLimits, file_digest, tree_inventory
from .contracts import package_file
from .client_metadata import extract_client_metadata, compare_metadata
from .security import PipelineError, atomic_write, canonical_json, relative_path, sha256
from .static_il import bounded_evidence_json, json_evidence_size
from .locale_mapping import extract_locale_mapping
from . import research_semantics

FAMILIES = {
    'items': r'Item_(\d+)', 'tiles': r'Tiles_(\d+)', 'walls': r'Wall_(\d+)',
    'npcs': r'NPC_(\d+)', 'buffs': r'Buff_(\d+)',
    'player': r'Player_(\d+)_(\d+)', 'hair': r'Player_Hair(?:Alt)?_(\d+)',
}
REQUIRED_SEMANTICS = {
    'items': ['defaults', 'localizedNames', 'dynamicTooltips', 'prefixCompatibility'],
    'tiles': ['localizedNames', 'frameVariants', 'paintBehavior', 'pixelCandidates'],
    'walls': ['localizedNames', 'paintBehavior', 'pixelCandidates'],
    'buffs': ['localizedNames', 'localizedDescriptions', 'behavior'],
    'prefixes': ['localizedNames', 'effects', 'compatibility'],
    'paints': ['localizedNames', 'colorTransform'],
    'npcs': ['localizedNames', 'frameLayout'],
    'player': ['equipmentMapping', 'frameLayout', 'layerRules'],
    'hair': ['frameLayout', 'styleMapping'],
    'pixel': ['candidatePalette', 'rgbIndex', 'baseHashBinding'],
}


def _research_evidence(path: Path, source_row: dict, output: Path, index: int, checkpoint) -> dict:
    """Keep research tables private and select the model by inspected bytes only.

    An unknown hash is unsupported even if its path says Windows. The model's
    invariant ASCII case mapping is explicit; no runtime culture is established.
    Failures, including the caller's cancellation/deadline, must escape this
    stage so that no adapter manifest can endorse incomplete evidence.
    """
    checkpoint()
    profile = research_semantics.PROFILE
    digest = source_row['sha256']
    if digest == profile['inputSha256']:
        model = research_semantics.extract_research_semantics(
            path, platform=profile['platform'], culture='invariant', checkpoint=checkpoint)
    else:
        # Reject unknown profiles before parsing them; unsupported metadata or a
        # custom static inspector must not make their platform appear verified.
        model = {
            'schemaVersion': 1, 'family': 'creative-research', 'status': 'UNSUPPORTED_PROFILE',
            'executedInput': False, 'publishable': False, 'complete': False,
            'researchModelComplete': False, 'culture': 'invariant',
            'profile': dict(profile), 'sourceReference': dict(research_semantics.REFERENCE),
            'input': {'sha256': digest, 'bytes': source_row['bytes'], 'platform': 'unknown'},
            'scope': 'research base definitions and one-hop persistent ID overrides only',
            'assumptions': ['Invariant-equivalent ASCII category model only; runtime culture not verified'],
            'unsupportedScope': ['unrecognized binary/platform', 'runtime culture and state',
                                 'Item.SetDefaults', 'full item semantics and publication'],
            'reason': 'Input SHA-256 does not match the fixed audited Windows research profile',
        }
    checkpoint()
    if (model.get('input', {}).get('sha256') != digest
            or model.get('input', {}).get('bytes') != source_row['bytes']):
        raise PipelineError('Research source differs from verified inventory')
    encoded = bounded_evidence_json(model, research_semantics.ResearchLimits().evidence_bytes, checkpoint)
    # The reviewed allowlist excludes tables, per-row origins and override pairs
    # from ALL receipts, including uncompressed job responses and polling.
    summary = research_semantics.research_summary(model)
    summary['runtimeCultureVerified'] = False
    bounded_evidence_json(summary, 64 * 1024, checkpoint)
    evidence_path = f'server-{index}-research.json'
    checkpoint()
    atomic_write(output / evidence_path, encoded)
    return {'sourceSha256': digest, 'path': evidence_path, 'sha256': sha256(encoded),
            'summary': summary}


def _validated_expected_inventories(sources: dict[str, Path], expected: dict, checkpoint) -> dict[str, list[dict]]:
    """Copy only locally computed ZIP inventories; this is not an upload field.

    Structural checks finish for every role before any source-file read. The
    producer must still rehash every complete source tree before its manifest.
    """
    limits = AdapterLimits()
    if (type(expected) is not dict or set(expected) != set(sources)
            or any(type(role) is not str or role not in ('server', 'client') for role in expected)):
        raise PipelineError('Verified source inventory roles do not match source roots')
    result = {}
    total_bytes = total_paths = 0
    for role, rows in expected.items():
        checkpoint()
        if type(rows) is not list or len(rows) > limits.files:
            raise PipelineError('Verified source inventory exceeds file-count policy')
        copied, files, directories = [], {}, {}
        for row in rows:
            checkpoint()
            if type(row) is not dict or set(row) != {'path', 'bytes', 'sha256'}:
                raise PipelineError('Invalid verified source inventory row')
            name, size, digest = row['path'], row['bytes'], row['sha256']
            if (type(name) is not str or len(name) > 600 or type(size) is not int or not 0 <= size <= limits.file_bytes
                    or type(digest) is not str or len(digest) != 64 or re.fullmatch('[a-f0-9]{64}', digest) is None):
                raise PipelineError('Invalid verified source inventory fields')
            relative_path(name)
            if name.casefold() in files:
                raise PipelineError('Verified source inventory path collision')
            files[name.casefold()] = name
            total_bytes += size
            if total_bytes > limits.total_bytes:
                raise PipelineError('Verified source inventory exceeds byte budget')
            copied.append({'path': name, 'bytes': size, 'sha256': digest})
        for row in copied:
            checkpoint()
            for parent in Path(row['path']).parents:
                if parent == Path('.'):
                    continue
                name = parent.as_posix()
                key = name.casefold()
                if key in files or (key in directories and directories[key] != name):
                    raise PipelineError('Verified source inventory file/directory collision')
                directories[key] = name
                if total_paths + len(files) + len(directories) > limits.files:
                    raise PipelineError('Verified source inventory exceeds path-count policy')
        total_paths += len(files) + len(directories)
        if total_paths > limits.files:
            raise PipelineError('Verified source inventory exceeds path-count policy')
        result[role] = sorted(copied, key=lambda row: row['path'])
    # Defer filesystem checks until ALL metadata is structurally safe. In
    # particular, malformed client metadata cannot cause an earlier server read.
    for root in sources.values():
        checkpoint()
        if (not isinstance(root, Path) or any(path.is_symlink() for path in (root, *root.parents))
                or not root.is_dir()):
            raise PipelineError('Verified source root must be a regular directory without links')
    checkpoint()
    return result


class RawEvidenceProducer:
    """Installed original parser, never selected by uploaded command/metadata."""
    adapter_id = 'terraria-static-metadata-and-raw-textures-v1'

    def __init__(self, inspect_server=None):
        self._builtin_inspector = inspect_server is None
        if inspect_server is None:
            from .server_semantics import extract_server_semantics
            inspect_server = extract_server_semantics
        self.inspect_server = inspect_server

    def produce(self, sources: dict[str, Path], textures: dict, output: Path, *, checkpoint=lambda: None,
                expected_inventories: dict[str, list[dict]] | None = None) -> dict:
        checkpoint()
        if output.exists() or any(p.is_symlink() for p in [output, *output.parents]):
            raise PipelineError('Evidence output must be a new private directory')
        inventories = (_validated_expected_inventories(sources, expected_inventories, checkpoint)
                       if expected_inventories is not None else
                       {role: tree_inventory(root, checkpoint=checkpoint) for role, root in sources.items()})
        output.mkdir(parents=True)
        bindings = {role: {'treeSha256': sha256(canonical_json(rows))} for role, rows in inventories.items()}
        server = sources.get('server')
        executables = [row for row in inventories.get('server', []) if Path(row['path']).name == 'TerrariaServer.exe']
        if len(executables) > 4:
            raise PipelineError('Ambiguous server executable inventory')
        assemblies, rejected, mappings, research = [], [], [], []
        for index, source_row in enumerate(executables):
            checkpoint()
            # Inventory reuse cannot bypass link checks on paths actually read.
            path = package_file(server, source_row['path'])
            digest = file_digest(path, checkpoint=checkpoint)
            if digest != source_row['sha256']:
                raise PipelineError('Static metadata source differs from verified inventory')
            research.append(_research_evidence(path, source_row, output, index, checkpoint))
            try:
                options={'checkpoint':checkpoint}
                if self._builtin_inspector:options['requested_locales']=('en-US','zh-Hans')
                evidence = self.inspect_server(path, **options)
                if evidence.get("input", {}).get("sha256") != digest:
                    raise PipelineError("Static metadata source changed")
            except PipelineError:
                rejected.append({'sha256': digest, 'code': 'STATIC_METADATA_REJECTED'})
                continue
            encoded=bounded_evidence_json(evidence,64*1024*1024,checkpoint)
            evidence_digest=sha256(encoded)
            atomic_write(output / f'server-{index}.json',encoded)
            mapping=extract_locale_mapping(path,evidence,server_evidence_sha256=evidence_digest,checkpoint=checkpoint)
            mapping_encoded=bounded_evidence_json(mapping,16*1024*1024,checkpoint)
            mapping_path=f'server-{index}-locale-mapping.json'
            atomic_write(output/mapping_path,mapping_encoded)
            mappings.append({'sourceSha256':digest,'path':mapping_path,'sha256':sha256(mapping_encoded),
                'serverEvidenceSha256':evidence_digest,'ruleBindingStatus':mapping.get('ruleBinding',{}).get('status'),
                'diagnostic':mapping.get('diagnostic'),
                'channels':{name:{key:value for key,value in channel.items() if key not in ('records','unattemptedIds')}
                            for name,channel in mapping['channels'].items()}})
            assemblies.append({'sha256': digest, 'evidence': evidence, 'path': f'server-{index}.json'})
        clients, rejected_clients = [], []
        client_executables = [row for row in inventories.get('client', [])
                              if Path(row['path']).name == 'Terraria.exe']
        if len(client_executables) > 4:
            raise PipelineError('Ambiguous client executable inventory')
        for index, source_row in enumerate(client_executables):
            checkpoint()
            path = package_file(sources['client'], source_row['path'])
            digest = file_digest(path, checkpoint=checkpoint)
            if digest != source_row['sha256'] or path.stat().st_size != source_row['bytes']:
                raise PipelineError('Client static metadata source differs from verified inventory')
            try:
                evidence = extract_client_metadata(path, checkpoint=checkpoint)
            except PipelineError:
                checkpoint()
                rejected_clients.append({'sourceRole': 'client', 'sha256': digest,
                    'inputPath': source_row['path'], 'code': 'STATIC_METADATA_REJECTED'})
                continue
            if evidence.get('input') != {'sha256': digest, 'bytes': source_row['bytes']}:
                raise PipelineError('Client static metadata source changed')
            encoded = bounded_evidence_json(evidence, 64 * 1024 * 1024, checkpoint)
            evidence_path = f'client-{index}.json'
            atomic_write(output / evidence_path, encoded)
            clients.append({'sourceRole': 'client', 'sha256': digest, 'bytes': source_row['bytes'],
                'inputPath': source_row['path'], 'path': evidence_path,
                'evidenceSha256': sha256(encoded), 'evidence': evidence})
        comparisons = [compare_metadata(server['evidence'], client['evidence'], checkpoint)
                       for server in assemblies for client in clients]
        json_evidence_size(comparisons, 16 * 1024 * 1024, checkpoint)
        id_sets = {}
        for assembly in assemblies:
            for family, value in assembly['evidence'].get('idFamilies', {}).items():
                id_sets.setdefault(family, set()).update(row['id'] for row in value.get('records', [])
                    if type(row.get('id')) is int and row['id'] >= 0)
        images = {family: [] for family in FAMILIES}
        unclassified = 0
        for role, receipt in textures.items():
            if receipt.get('status') != 'TEXTURES_DECODED':
                continue
            for row in receipt.get('images', []):
                checkpoint()
                stem = Path(row['input']).stem
                for family, pattern in FAMILIES.items():
                    match = re.fullmatch(pattern, stem)
                    if match:
                        parents = Path(row['input']).parent.parts
                        texture_role = ('BASE_TEXTURE_LAYOUT' if parents and parents[-1] == 'Images'
                                        else 'AUXILIARY_TEXTURE_LAYOUT' if 'Images' in parents
                                        else 'UNLOCATED_NAME_MATCH')
                        images[family].append({'sourceRole': role, 'ids': [int(x) for x in match.groups()],
                            'textureRole': texture_role,
                            'sourceSha256': row['sourceSha256'], 'pngSha256': row['sha256'],
                            'width': row['width'], 'height': row['height'], 'surfaceFormat': row.get('surfaceFormat'),
                            'inputPath': row['input'], 'outputPath': row['output']})
                        break
                else:
                    unclassified += 1
        coverage = {}
        for family, missing_rules in REQUIRED_SEMANTICS.items():
            checkpoint()
            rows = images.get(family, [])
            base_rows = [row for row in rows if row['textureRole'] == 'BASE_TEXTURE_LAYOUT']
            picture_ids = {row['ids'][0] for row in base_rows}
            ids = id_sets.get(family, set())
            coverage[family] = {'status': 'PARTIAL' if ids or rows else 'UNSUPPORTED',
                'constantIds': len(ids), 'decodedImages': len(rows),
                'decodedBaseImages': len(base_rows),
                'decodedAuxiliaryImages': sum(row['textureRole'] == 'AUXILIARY_TEXTURE_LAYOUT' for row in rows),
                'unlocatedNameMatches': sum(row['textureRole'] == 'UNLOCATED_NAME_MATCH' for row in rows),
                'baseTextureLocationPolicy': 'direct child of an exact Images directory; layout evidence only',
                'idsWithoutDirectImage': sorted(ids - picture_ids),
                'missingRules': missing_rules, 'complete': False}
            coverage[family]['localizedSubcapabilities']=[{'sourceSha256':mapping['sourceSha256'],
                'evidencePath':mapping['path'],'evidenceSha256':mapping['sha256'],
                'channels':{name:channel for name,channel in mapping['channels'].items() if channel['family']==family}}
                for mapping in mappings if any(channel['family']==family for channel in mapping['channels'].values())]
            if rows:
                atomic_write(output / f'{family}-textures.json', canonical_json(rows))
        coverage['items']['researchSubcapabilities'] = [
            {'capability': 'creativeResearchConditionalModel',
             'status': item['summary']['status'], 'conditional': True, 'complete': False,
             'researchModelComplete': item['summary']['researchModelComplete'],
             'culture': 'invariant', 'runtimeCultureVerified': False,
             'sourceSha256': item['sourceSha256'], 'evidencePath': item['path'],
             'evidenceSha256': item['sha256']}
            for item in research]
        manifest = {'schemaVersion': 1, 'adapterId': self.adapter_id,
            'status': 'PARTIAL', 'executedInput': False, 'extractionComplete': False,
            'publishable': False, 'inputBinding': bindings,
            'serverMetadata': [{k: v for k, v in item.items() if k != 'evidence'} for item in assemblies],
            'rejectedServerMetadata': rejected, 'familyCoverage': coverage,
            'clientMetadata': [{k: v for k, v in item.items() if k != 'evidence'} for item in clients],
            'rejectedClientMetadata': rejected_clients, 'serverClientComparisons': comparisons,
            'localeMappingEvidence':mappings,
            'researchEvidence': research,
            'unclassifiedDecodedImages': unclassified,
            'versionEvidence': [item['evidence'].get('gameVersionEvidence') for item in assemblies],
            'clientVersionEvidence': [item['evidence']['gameVersionEvidence'] for item in clients],
            'blockers': ['GAME_VERSION_AND_FULL_SEMANTICS_NOT_VERIFIED']}
        if any(not row['declaredVersionsMatch'] for row in comparisons):
            manifest['blockers'].append('SERVER_CLIENT_DECLARED_VERSION_MISMATCH')
        if rejected_clients:
            manifest['blockers'].append('CLIENT_STATIC_METADATA_REJECTED')
        for role, root in sources.items():
            if tree_inventory(root, checkpoint=checkpoint) != inventories[role]:
                raise PipelineError('Original source changed during semantic extraction')
        checkpoint()
        atomic_write(output / 'version-adapter-manifest.json', canonical_json(manifest))
        return manifest
