"""Build private Item/player drafts from a pinned collector observation and fresh PNGs.

No execution, source-completeness certificate, approval or publication is performed.
"""
import argparse
from pathlib import Path

from resource_pipeline.contracts import package_file
from resource_pipeline.observed_item_join import APP_SOURCE_PINS as ITEM_PINS, assemble_observed_item_resources
from resource_pipeline.observed_player_assembly import assemble_observed_player_resources
from resource_pipeline.player_fact_app_policy import APP_SOURCE_PINS as PLAYER_PINS
from resource_pipeline.security import PipelineError, atomic_write, canonical_json, read_json, sha256


def safe_path(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise PipelineError('Linked input/output paths are forbidden')
    return path.resolve(strict=False)


def bounded_bytes(path, maximum):
    safe_path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise PipelineError('Input file violates bounded byte contract')
    raw = path.read_bytes()
    if not 0 < len(raw) <= maximum:
        raise PipelineError('Input file changed outside byte bound')
    return raw


def source_files(root, pins):
    safe_path(root)
    return {name: bounded_bytes(package_file(root, name), 256 * 1024) for name in pins}


def fresh_textures(root, inventory):
    safe_path(root); safe_path(inventory)
    names = read_json(inventory, 1024 * 1024)
    if (type(names) is not list or not 0 < len(names) <= 8192
            or any(type(name) is not str for name in names) or len(set(names)) != len(names)):
        raise PipelineError('Texture inventory must list bounded unique relative paths')
    result, size = {}, 0
    for name in names:
        raw = bounded_bytes(package_file(root, name), 32 * 1024 * 1024)
        size += len(raw)
        if size > 128 * 1024 * 1024:
            raise PipelineError('Texture inventory exceeds aggregate byte bound')
        result[name] = raw
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observation', type=Path, required=True)
    parser.add_argument('--client', type=Path, required=True)
    parser.add_argument('--item-app-source-root', type=Path, required=True)
    parser.add_argument('--player-app-source-root', type=Path)
    parser.add_argument('--textures-root', type=Path)
    parser.add_argument('--texture-inventory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    player_args = (args.player_app_source_root, args.textures_root, args.texture_inventory)
    if any(player_args) and not all(player_args):
        parser.error('Player assembly requires all three player source/texture options')
    output = safe_path(args.output)
    roots = [Path(__file__).resolve().parents[1], safe_path(args.item_app_source_root),
             safe_path(args.observation).parent, safe_path(args.client).parent]
    roots.extend(safe_path(p) for p in (args.player_app_source_root, args.textures_root) if p)
    if args.texture_inventory: roots.append(safe_path(args.texture_inventory).parent)
    if any(output == root or root in output.parents for root in roots):
        parser.error('Private output must be outside the checkout and all input roots')
    if output.exists(): parser.error('Private output must be new')
    observation = read_json(args.observation, 64 * 1024 * 1024)
    pe = bounded_bytes(args.client, 128 * 1024 * 1024)
    objects, item_receipt = assemble_observed_item_resources(observation, pe_bytes=pe,
        app_sources=source_files(args.item_app_source_root, ITEM_PINS))
    receipts = {'items': item_receipt}
    if all(player_args):
        player, receipts['player'] = assemble_observed_player_resources(
            observation=observation['playerObservation'], pe_bytes=pe,
            app_sources=source_files(args.player_app_source_root, PLAYER_PINS),
            textures=fresh_textures(args.textures_root, args.texture_inventory), item_objects=objects)
        objects = {**objects, **player}
    output.mkdir(parents=True, mode=0o700)
    files = {}
    for role, raw in objects.items():
        suffix = '.bin' if role == 'player.walk' else '.png' if role == 'player.atlas' else '.json'
        name = role + suffix
        atomic_write(output / name, raw)
        files[role] = {'file': name, 'bytes': len(raw), 'sha256': sha256(raw)}
    marker = {'schemaVersion': 1, 'kind': 'private-observed-derived-draft', 'files': files,
              'receipts': receipts, 'sourceProductionComplete': False, 'consumerReleaseReady': False,
              'observationAuthenticated': False, 'publicationApproved': False}
    atomic_write(output / 'assembly.json', canonical_json(marker))
    print(canonical_json({'kind': marker['kind'], 'roles': sorted(objects),
        'sourceProductionComplete': False, 'consumerReleaseReady': False, 'publicationApproved': False}).decode())


if __name__ == '__main__': main()
