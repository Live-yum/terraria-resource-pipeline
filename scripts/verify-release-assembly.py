#!/usr/bin/env python3
"""Read-only cross-repository assembly gate. Never publishes or grants authority.

Inputs are exact release bytes and externally produced build/fixture evidence.
The ordinary ABI/artifact checker and current authority fence remain mandatory.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

CATEGORIES = {'map-header', 'map-color-paint', 'missing-tile', 'background-rle', 'player-conversion'}

def require(ok, message):
    if not ok:
        raise ValueError(message)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()

def read(root, relative):
    require(isinstance(relative, str) and relative and '\\' not in relative, 'invalid evidence path')
    path = (root / relative).resolve()
    require(path.is_relative_to(root.resolve()) and path.is_file(), 'evidence path missing or escapes root')
    return path.read_bytes()

def digest(value, size, name):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{%d}' % size, value), 'invalid ' + name)

def verify(identity, root):
    """All expected identity values are independently bound to evidence bytes."""
    require(identity.get('schema') == 1, 'unsupported release assembly schema')
    for field in ('appCommit', 'engineSourceCommit', 'extractorCommit'):
        digest(identity.get(field), 40, field)
    for field in ('gameAssemblySha256', 'resourceManifestSha256', 'builtinDescriptorSha256'):
        digest(identity.get(field), 64, field)
    require(isinstance(identity.get('authorityId'), str) and re.fullmatch(r'[-a-zA-Z0-9_.:]{1,160}', identity['authorityId']), 'invalid authority ID')
    require(isinstance(identity.get('sequence'), str) and re.fullmatch(r'0|[1-9][0-9]{0,79}', identity['sequence']), 'sequence must be a canonical decimal string')
    paths = identity['evidence']
    resource_bytes = read(root, paths['resourceManifest'])
    builtin_bytes = read(root, paths['builtinDescriptor'])
    require(sha(resource_bytes) == identity['resourceManifestSha256'], 'resource manifest bytes mismatch')
    require(sha(builtin_bytes) == identity['builtinDescriptorSha256'], 'builtin descriptor bytes mismatch')
    resource, builtin = json.loads(resource_bytes), json.loads(builtin_bytes)
    require(resource['sources']['serverSha256'] == identity['gameAssemblySha256'], 'resource game assembly mismatch')
    require(sha(read(root, paths['gameAssembly'])) == identity['gameAssemblySha256'], 'game assembly bytes mismatch')
    require(resource['gameVersion'] == builtin['gameVersion'], 'builtin game version mismatch')
    # Builtin assets may intentionally be older than remote resources. Record both
    # independently; do not pretend equal versions imply equal rendering logic.
    extraction = json.loads(read(root, paths['extraction']))
    require(extraction['extractorCommit'] == identity['extractorCommit'] and extraction.get('cleanBuild') is True, 'extractor commit/clean build mismatch')
    require(extraction['gameAssemblySha256'] == identity['gameAssemblySha256'], 'extraction assembly mismatch')
    require(extraction['resourceManifestSha256'] == identity['resourceManifestSha256'], 'extraction manifest mismatch')
    app = json.loads(read(root, paths['appBuild']))
    require(app['sourceCommit'] == identity['appCommit'] and app.get('dirty') is False, 'app build identity mismatch')
    require(app['builtinDescriptorSha256'] == identity['builtinDescriptorSha256'], 'app builtin identity mismatch')
    # Validate a captured approval snapshot only. This is not a replacement for
    # backend revocation checks immediately before/after any resource access.
    authority = json.loads(read(root, paths['authority']))
    require(authority['schema'] == 1 and authority['channel'] == 'stable', 'authority schema/channel mismatch')
    require(authority['authorityId'] == identity['authorityId'] and authority['sequence'] == identity['sequence'], 'authority fence mismatch')
    active = authority['active']
    require(active and active['manifestSha256'] == identity['resourceManifestSha256'] and active['gameVersion'] == resource['gameVersion'], 'authority active release mismatch')
    revoked = authority['revokedManifestSha256']
    require(isinstance(revoked, list) and revoked == sorted(set(revoked)), 'revocations not sorted and unique')
    for value in revoked:
        digest(value, 64, 'revocation')
    require(identity['resourceManifestSha256'] not in revoked, 'resource release revoked')
    # This order is the application's canonicalApproval order, not sort_keys.
    approval = {key: authority[key] for key in ('active', 'authorityId', 'channel', 'revokedManifestSha256', 'schema', 'sequence')}
    approval['active'] = {key: active[key] for key in ('gameVersion', 'manifestSha256')}
    require(sha(json.dumps(approval, ensure_ascii=False, separators=(',', ':')).encode()) == authority['stateSha256'], 'authority snapshot digest mismatch')
    engines = identity['engines']
    require(isinstance(engines, list) and {v['featureSet'] for v in engines} == {'wld', 'plr'} and len(engines) == 2, 'both unique wld and plr artifacts required')
    for expected in engines:
        feature = expected['featureSet']
        manifest = json.loads(read(root, expected['manifest']))
        require(manifest['sourceCommit'] == identity['engineSourceCommit'] and manifest['dirty'] is False, 'engine source mismatch')
        require(manifest['build']['featureSet'] == feature and manifest['abi'] == expected['abi'], 'engine ABI mismatch')
        require(app['engineManifestSha256'][feature] == sha(read(root, expected['manifest'])), 'app engine manifest mismatch')
        exports = manifest['abi']['requiredExports']
        require(isinstance(exports, list) and exports and len(set(exports)) == len(exports), 'engine exports invalid')
        require(sha(('\n'.join(exports) + '\n').encode()) == manifest['abi']['exportHash'], 'engine export digest mismatch')
        for artifact in manifest['artifacts']:
            data = read(root, expected['artifactRoot'] + '/' + artifact['path'])
            require(len(data) == artifact['bytes'] and sha(data) == artifact['sha256'], 'engine artifact bytes mismatch')
        require({a['role'] for a in manifest['artifacts']} == {'wrapper', 'wasm'} and len(manifest['artifacts']) == 2, 'engine artifact roles incomplete')
        runtime = json.loads(read(root, expected['runtimeIdentity']))
        require(runtime['sourceCommit'] == identity['engineSourceCommit'] and runtime['dirty'] is False and runtime['featureSet'] == feature, 'loaded engine identity mismatch')
        require(runtime['abiVersion'] == expected['abi']['version'], 'loaded engine ABI version mismatch')
        for kind in ('pixel', 'player', 'world'):
            claim = expected['abi'].get(kind + 'Workspace')
            if claim:
                require(runtime.get(kind + 'WorkspaceAbiVersion') == claim['version'], 'loaded workspace ABI mismatch: ' + kind)
    report = json.loads(read(root, paths['differentialReport']))
    report_base = Path(paths['differentialReport']).parent
    def case_bytes(relative):
        return read(root, (report_base / relative).as_posix())
    bound = ('appCommit', 'engineSourceCommit', 'extractorCommit', 'gameAssemblySha256', 'resourceManifestSha256', 'builtinDescriptorSha256', 'authorityId', 'sequence')
    require(all(report.get(key) == identity[key] for key in bound), 'differential provenance mismatch')
    require(report.get('producer') == 'executed-game-assembly-vs-native', 'differential report is not an executed game comparison')
    cases = report.get('cases', [])
    require(cases and CATEGORIES <= {v['category'] for v in cases}, 'differential categories incomplete')
    require(len({v['id'] for v in cases}) == len(cases), 'duplicate differential cases')
    for case in cases:
        require(case['status'] == 'passed' and case['gameMethod'] and case['nativeEntryPoint'], 'differential case not passed/executed')
        dependencies=case.get('inputDependencies')
        require(isinstance(dependencies,list) and dependencies, 'differential fixture dependencies missing')
        for dependency in dependencies:
            require(sha(case_bytes(dependency['path'])) == dependency['sha256'], 'differential fixture dependency changed')
        for kind in ('input', 'game', 'native'):
            require(sha(case_bytes(case[kind + 'Path'])) == case[kind + 'Sha256'], 'differential evidence bytes mismatch')
        require(case_bytes(case['gamePath']) == case_bytes(case['nativePath']), 'differential output differs')
    return {'schema': 1, 'status': 'assembly-verified', 'identitySha256': sha(canonical(identity)),
            'differentialCases': len(cases), 'authoritySnapshotOnly': True}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('identity', type=Path)
    parser.add_argument('--evidence-root', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(json.loads(args.identity.read_bytes()), args.evidence_root), indent=2))

if __name__ == '__main__':
    main()
