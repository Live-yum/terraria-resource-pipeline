"""Real, data-only producer stages. Partial evidence never authorizes publication.

The same uploaded source trees feed static CLI metadata and the sandbox's raw-XNB
receipts. No old viewer tables, uploaded approval flags or game execution is used.
"""
from __future__ import annotations
from pathlib import Path
import re

from .adapters import file_digest, tree_inventory
from .security import PipelineError, atomic_write, canonical_json, sha256
from .static_il import bounded_evidence_json
from .locale_mapping import extract_locale_mapping

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

class RawEvidenceProducer:
    """Installed original parser, never selected by uploaded command/metadata."""
    adapter_id = 'terraria-static-metadata-and-raw-textures-v1'

    def __init__(self, inspect_server=None):
        self._builtin_inspector = inspect_server is None
        if inspect_server is None:
            from .server_semantics import extract_server_semantics
            inspect_server = extract_server_semantics
        self.inspect_server = inspect_server

    def produce(self, sources: dict[str, Path], textures: dict, output: Path, *, checkpoint=lambda: None) -> dict:
        checkpoint()
        if output.exists() or any(p.is_symlink() for p in [output, *output.parents]):
            raise PipelineError('Evidence output must be a new private directory')
        output.mkdir(parents=True)
        inventories = {role: tree_inventory(root, checkpoint=checkpoint) for role, root in sources.items()}
        bindings = {role: {'treeSha256': sha256(canonical_json(rows))} for role, rows in inventories.items()}
        server = sources.get('server')
        executables = [server / row['path'] for row in inventories.get('server', []) if Path(row['path']).name == 'TerrariaServer.exe']
        if len(executables) > 4:
            raise PipelineError('Ambiguous server executable inventory')
        assemblies, rejected, mappings = [], [], []
        for index, path in enumerate(executables):
            checkpoint()
            digest = file_digest(path, checkpoint=checkpoint)
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
                        images[family].append({'sourceRole': role, 'ids': [int(x) for x in match.groups()],
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
            picture_ids = {row['ids'][0] for row in rows}
            ids = id_sets.get(family, set())
            coverage[family] = {'status': 'PARTIAL' if ids or rows else 'UNSUPPORTED',
                'constantIds': len(ids), 'decodedImages': len(rows),
                'idsWithoutDirectImage': sorted(ids - picture_ids),
                'missingRules': missing_rules, 'complete': False}
            coverage[family]['localizedSubcapabilities']=[{'sourceSha256':mapping['sourceSha256'],
                'evidencePath':mapping['path'],'evidenceSha256':mapping['sha256'],
                'channels':{name:channel for name,channel in mapping['channels'].items() if channel['family']==family}}
                for mapping in mappings if any(channel['family']==family for channel in mapping['channels'].values())]
            if rows:
                atomic_write(output / f'{family}-textures.json', canonical_json(rows))
        manifest = {'schemaVersion': 1, 'adapterId': self.adapter_id,
            'status': 'PARTIAL', 'executedInput': False, 'extractionComplete': False,
            'publishable': False, 'inputBinding': bindings,
            'serverMetadata': [{k: v for k, v in item.items() if k != 'evidence'} for item in assemblies],
            'rejectedServerMetadata': rejected, 'familyCoverage': coverage,
            'localeMappingEvidence':mappings,
            'unclassifiedDecodedImages': unclassified,
            'versionEvidence': [item['evidence'].get('gameVersionEvidence') for item in assemblies],
            'blockers': ['GAME_VERSION_AND_FULL_SEMANTICS_NOT_VERIFIED']}
        for role, root in sources.items():
            if tree_inventory(root, checkpoint=checkpoint) != inventories[role]:
                raise PipelineError('Original source changed during semantic extraction')
        checkpoint()
        atomic_write(output / 'version-adapter-manifest.json', canonical_json(manifest))
        return manifest
