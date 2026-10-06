#!/usr/bin/env python3
"""Run two independent adapters on identical fixtures, retaining real evidence.

No handwritten expected values qualify. The game adapter must invoke the pinned
assembly; the native adapter must invoke the pinned built engine (not port the
expected arithmetic). Outputs are normalized JSON with no timing/path fields.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import re

CATEGORIES = {'map-header', 'map-color-paint', 'missing-tile', 'background-rle', 'player-conversion'}

def sha(data): return hashlib.sha256(data).hexdigest()
def normalized(data): return json.dumps(json.loads(data), ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode() + b'\n'

def run(config, root, output):
    if output.exists(): raise ValueError('output must be a new private directory')
    cases = config['cases']
    if not cases or not CATEGORIES <= {c['category'] for c in cases}: raise ValueError('all five categories are required')
    ids = [c['id'] for c in cases]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r'[-a-zA-Z0-9_]{1,80}', i) for i in ids): raise ValueError('invalid case IDs')
    assembly = (root / config['gameAssembly']).resolve()
    if not assembly.is_relative_to(root.resolve()) or sha(assembly.read_bytes()) != config['gameAssemblySha256']:
        raise ValueError('game assembly identity mismatch')
    for side in ('game', 'native'):
        adapter = config[side + 'Adapter']
        if not isinstance(adapter, list) or not adapter or any(not isinstance(a, str) for a in adapter):
            raise ValueError('adapter must be an explicit executable argv, never a shell string')
    output.mkdir(mode=0o700, parents=True)
    report = {key: config[key] for key in ('appCommit', 'engineSourceCommit', 'extractorCommit', 'gameAssemblySha256',
        'resourceManifestSha256', 'builtinDescriptorSha256', 'authorityId', 'sequence')}
    report.update(schema=1, producer='executed-game-assembly-vs-native', cases=[])
    for case in cases:
        source = (root / case['input']).resolve()
        if not source.is_relative_to(root.resolve()): raise ValueError('fixture path escapes evidence root')
        data = source.read_bytes()
        request = json.loads(data)
        if request.get('resourceManifestSha256') != config['resourceManifestSha256']:
            raise ValueError('fixture resource release mismatch')
        dependencies = case.get('dependencies', {})
        if not dependencies:
            raise ValueError('real TMRT/player dependency digests are required')
        for relative, expected in dependencies.items():
            path = (source.parent / relative).resolve()
            if not path.is_relative_to(root.resolve()) or sha(path.read_bytes()) != expected:
                raise ValueError('fixture dependency mismatch')
        if sha(data) != case['inputSha256']: raise ValueError('input fixture digest mismatch')
        prefix = case['id']
        (output / (prefix + '.input')).write_bytes(data)
        entry = {'id': prefix, 'category': case['category'], 'inputPath': prefix+'.input', 'inputSha256': sha(data),
                 'gameMethod': case['gameMethod'], 'nativeEntryPoint': case['nativeEntryPoint']}
        for side in ('game', 'native'):
            # Adapter receives the same file bytes, plus explicit provenance. No
            # expected output is visible to the other adapter during execution.
            process = subprocess.run(config[side+'Adapter'] + ['--category', case['category'], '--input', str(source),
                '--assembly', str(assembly), '--source-commit', config['engineSourceCommit'], '--scratch', str((output/(prefix+'.'+side+'-scratch')).resolve())],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=config.get('timeoutSeconds', 120))
            (output/(prefix+'.'+side+'.stderr')).write_bytes(process.stderr)
            if process.returncode:
                entry['status']='execution-failed';entry['failedSide']=side;entry['returnCode']=process.returncode
                report['cases'].append(entry)
                (output/'differential-report.json').write_text(json.dumps(report,indent=2)+'\n')
                raise ValueError(side+' adapter failed; stderr and partial report retained')
            for relative, expected in dependencies.items():
                if sha((source.parent / relative).read_bytes()) != expected:
                    raise ValueError('fixture dependency changed during execution')
            response = json.loads(process.stdout)
            if response.get('entryPoint') != case['gameMethod' if side=='game' else 'nativeEntryPoint']:
                raise ValueError('adapter executed another entry point')
            if response.get('side') != side or response.get('inputSha256') != sha(data): raise ValueError('adapter did not bind input')
            if side == 'game' and response.get('gameAssemblySha256') != config['gameAssemblySha256']: raise ValueError('wrong game loaded')
            if side == 'native' and response.get('engineSourceCommit') != config['engineSourceCommit']: raise ValueError('wrong native engine loaded')
            result = normalized(json.dumps(response['result']).encode())
            name = prefix + '.' + side + '.json'
            (output/name).write_bytes(result)
            entry[side+'Path'], entry[side+'Sha256'] = name, sha(result)
        entry['status'] = 'passed' if entry['gameSha256'] == entry['nativeSha256'] else 'failed'
        report['cases'].append(entry)
        (output/'differential-report.json').write_text(json.dumps(report, indent=2)+'\n')
    if any(c['status'] != 'passed' for c in report['cases']): raise ValueError('real game/native outputs differ; evidence retained')
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--evidence-root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a=p.parse_args()
    print(json.dumps(run(json.loads(a.config.read_bytes()), a.evidence_root, a.output), indent=2))
if __name__=='__main__': main()
