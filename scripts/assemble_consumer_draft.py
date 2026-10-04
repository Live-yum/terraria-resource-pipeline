"""Assemble private derived drafts, not verified Terraria consumer releases.

Inputs are complete normalized facts/policies, NOT raw Terraria archives. This
command intentionally has no publication, schema-2 proof, or approval options.
"""
import argparse
from pathlib import Path

from resource_pipeline.contracts import package_file
from resource_pipeline.item_assembler import assemble_item_resources
from resource_pipeline.marker_assembler import assemble_marker_resources
from resource_pipeline.security import PipelineError, atomic_write, canonical_json, read_json, sha256


def safe_root(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise PipelineError('Assembly cannot traverse linked roots')


def read_bytes(root, name, maximum):
    path = package_file(root, name)
    if not 0 < path.stat().st_size <= maximum:
        raise PipelineError('Assembly input exceeds bounded file contract')
    return path.read_bytes()


def assemble(group, root, base_sha256=None):
    safe_root(root)
    if group == 'items':
        return assemble_item_resources(read_json(package_file(root, 'item-facts.json'), 32 * 1024 * 1024))
    base = read_bytes(root, 'materials.base.json', 32 * 1024 * 1024)
    if not base_sha256 or sha256(base) != base_sha256:
        raise PipelineError('An independent exact --base-sha256 is required')
    policy = read_json(package_file(root, 'policy.json'), 1024 * 1024)
    if type(policy) is not dict:
        raise PipelineError('Assembly policy must be an object')
    if group == 'pixel':
        from resource_pipeline.pixel_assembler import assemble_pixel_resources
        result = assemble_pixel_resources(base, game_version=policy.get('gameVersion'),
                                          verified_base_sha256=base_sha256, policy=policy)
        return result.objects, result.evidence
    if type(policy) is not dict or type(policy.get('sourceFiles')) is not dict or not 0 < len(policy['sourceFiles']) <= 32:
        raise PipelineError('Bounded marker source policy required')
    rows = policy.get('rows')
    if type(rows) is not list or not 0 < len(rows) <= 256:
        raise PipelineError('Bounded marker row policy required')
    names = set()
    for row in rows:
        n = row.get('selector', {}).get('tile_type') if type(row) is dict and type(row.get('selector')) is dict else None
        if type(n) is not int or not 0 <= n <= 65535: raise PipelineError('Invalid marker tile ID')
        names.add(f'Tiles_{n}.png')
    # Preflight aggregate sizes before reading all files into memory.
    source_paths = [(name, package_file(root, 'sources/' + name)) for name in policy['sourceFiles'] if isinstance(name, str)]
    texture_paths = [(name, package_file(root, 'textures/' + name)) for name in names]
    if len(source_paths) != len(policy['sourceFiles']): raise PipelineError('Invalid marker source name')
    if (any(not 0 < p.stat().st_size <= 16 * 1024 * 1024 for _, p in source_paths)
            or sum(p.stat().st_size for _, p in source_paths) > 64 * 1024 * 1024
            or any(not 0 < p.stat().st_size <= 32 * 1024 * 1024 for _, p in texture_paths)
            or sum(p.stat().st_size for _, p in texture_paths) > 128 * 1024 * 1024):
        raise PipelineError('Marker input aggregate budget exceeded')
    return assemble_marker_resources(material_base=base, material_base_sha256=base_sha256, policy=policy,
                                     textures={name: path.read_bytes() for name, path in texture_paths},
                                     source_files={name: path.read_bytes() for name, path in source_paths})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', required=True, choices=('items', 'markers', 'pixel'))
    parser.add_argument('--input-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--base-sha256')
    args = parser.parse_args()
    safe_root(args.output)
    checkout = Path(__file__).resolve().parents[1]
    output = args.output.resolve(strict=False)
    if output == checkout or checkout in output.parents:
        parser.error('Private assembly output must be outside the pipeline checkout')
    if output.exists(): parser.error('Assembly output must be new')
    objects, receipt = assemble(args.group, args.input_root, args.base_sha256)
    output.mkdir(parents=True, mode=0o700)
    files = {}
    for role, raw in objects.items():
        suffix = '.bin' if role in ('markers.images', 'pixel.rgb') else '.json'
        name = role + suffix
        atomic_write(output / name, raw)
        files[role] = {'file': name, 'bytes': len(raw), 'sha256': sha256(raw)}
    # Final file is the completion marker, not a release manifest or certificate.
    atomic_write(output / 'assembly.json', canonical_json({'kind': 'private-derived-draft',
        'group': args.group, 'files': files, 'receipt': receipt, 'sourceProductionComplete': False,
        'consumerReleaseReady': False, 'publicationApproved': False}))
    print(canonical_json({'kind': 'private-derived-draft', 'group': args.group, 'objects': len(objects),
                          'rawBytes': sum(map(len, objects.values())), 'sourceProductionComplete': False,
                          'consumerReleaseReady': False, 'publicationApproved': False}).decode())


if __name__ == '__main__': main()
