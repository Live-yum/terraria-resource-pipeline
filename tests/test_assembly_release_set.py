"""Thirteen original synthetic derived roles, exact cross-group transport binds.

This is NOT real Terraria source acceptance or a producer-proof certificate.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from resource_pipeline.consumer_release import GROUPS, build_consumer_release, verify_consumer_release
from resource_pipeline.item_assembler import assemble_item_resources
from resource_pipeline.materials_assembler import assemble_material_resources
from resource_pipeline.marker_assembler import assemble_marker_resources
from resource_pipeline.pixel_assembler import assemble_pixel_resources, ALGORITHM
from resource_pipeline.player_assembler import assemble_player_resources
from resource_pipeline.worldgen_assembler import assemble_worldgen_resources
from resource_pipeline.security import canonical_json, sha256
from test_item_assembler import original_inputs
from test_materials_assembler import original_material_inputs
from test_marker_assembler import original_marker_inputs
from test_player_assembler import original_player_inputs
from test_worldgen_assembler import original_worldgen_inputs


def original_assembled_set(root):
    binding = {'serverSha256': sha256(b'original source fixture'), 'clientTreeSha': 'b' * 40, 'consumerCommit': 'c' * 40}
    materials, mr = assemble_material_resources(**original_material_inputs())
    pixel = assemble_pixel_resources(materials['materials.base'], game_version='0.0.1',
              verified_base_sha256=sha256(materials['materials.base']),
              policy={'schemaVersion': 1, 'policyId': 'original-integration', 'gameVersion': '0.0.1',
                      'consumerCommit': 'c' * 40, 'algorithm': ALGORITHM, 'tileIds': [0, 1], 'wallIds': [1], 'paintIds': [1]})
    materials.update(pixel.objects)
    items, ir = assemble_item_resources(original_inputs())
    groups, manifests = {'materials': materials, 'items': items}, {}
    for group, objects in groups.items():
        manifests[group] = build_consumer_release(root / group, game_version='0.0.1', source_binding=binding, objects=objects, group=group)
    marker_inputs = original_marker_inputs()
    marker_inputs.update(material_base=materials['materials.base'], material_base_sha256=sha256(materials['materials.base']))
    markers, kr = assemble_marker_resources(**marker_inputs)
    player_inputs = original_player_inputs()
    player_inputs['item_objects'] = items
    player_inputs['policy']['itemHashes'] = {k: sha256(v) for k, v in items.items()}
    player, pr = assemble_player_resources(**player_inputs)
    worldgen_inputs, foundations = original_worldgen_inputs()
    for group, role in (('items', 'items.catalog'), ('materials', 'materials.base')):
        raw = groups[group][role]
        foundations[group] = {'gameVersion': '0.0.1', 'releaseId': manifests[group]['releaseId'],
                              'baseSha256': sha256(raw), 'bytes': raw}
    worldgen, wr = assemble_worldgen_resources(worldgen_inputs, foundations=foundations)
    service = worldgen.pop('worldgen.options')
    groups.update(markers=markers, player=player, worldgen=worldgen)
    for group in ('markers', 'player', 'worldgen'):
        manifests[group] = build_consumer_release(root / group, game_version='0.0.1', source_binding=binding, objects=groups[group], group=group)
    return groups, manifests, service, [mr, ir, kr, pr, wr, pixel.evidence]


class AssemblyReleaseSetTests(unittest.TestCase):
    def test_thirteen_original_roles_roundtrip_and_remain_unapproved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            groups, manifests, service, receipts = original_assembled_set(root)
            self.assertEqual(sum(len(objects) for objects in groups.values()), 13)
            self.assertNotIn('worldgen.options', {role for rows in groups.values() for role in rows})
            self.assertTrue(json.loads(service)['enabled'])
            for group, manifest in manifests.items():
                self.assertEqual(set(groups[group]), set(GROUPS[group][0]))
                actual = verify_consumer_release(root / group, manifest_sha256=sha256(canonical_json(manifest)), group=group)
                self.assertEqual(actual, groups[group])
            for receipt in receipts:
                self.assertFalse(receipt.get('sourceSemanticsVerified', receipt.get('sourceCompletenessEstablished')))
                self.assertIsNot(receipt.get('publicationApproved'), True)
            data = json.loads(groups['worldgen']['worldgen.choices'])
            for group in ('items', 'materials'):
                self.assertEqual(data['foundations'][group]['releaseId'], manifests[group]['releaseId'])

    @unittest.skipUnless(os.environ.get('TERRARIA_CONSUMER_ROOT'), 'optional actual whole consumer set oracle')
    def test_actual_app_accepts_all_derived_original_groups_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            groups, manifests, service, receipts = original_assembled_set(root)
            (root / 'service.json').write_bytes(service)
            code = '''import fs from 'node:fs';import {pathToFileURL} from 'node:url';
const [app,root]=process.argv.slice(1),imp=p=>import(pathToFileURL(app+p));
function snap(group){const m=JSON.parse(fs.readFileSync(root+'/'+group+'/manifest.json'));return {gameVersion:m.gameVersion,manifest:m,
readBytes:r=>fs.readFileSync(root+'/'+group+'/'+m.objects[r].path),readJson(r){return JSON.parse(this.readBytes(r))}}}
const m=snap('materials'),i=snap('items'),p=snap('player'),k=snap('markers'),w=snap('worldgen');
(await imp('/shared/game/material-resource-contract.mjs')).validateMaterialResourceSnapshot(m);
(await imp('/shared/game/item-resource-contract.mjs')).validateItemResourceSnapshot(i);
(await imp('/shared/game/player-resource-contract.mjs')).validatePlayerResourceSnapshot(p,i);
(await imp('/shared/game/marker-resource-contract.mjs')).validateMarkerResourceSnapshot(k,m);
const c=await imp('/features/world-generation/services/metadata-contract.mjs'), options=JSON.parse(fs.readFileSync(root+'/service.json'));
c.validateGenerationOptions(options);c.validateWorldGenerationSnapshot(w,{pin:{gameVersion:'0.0.1'},serverVersionKey:'001',schemaRevision:options.schema.revision});
console.log('All 13 original derived roles accepted together; not source production proof');'''
            result = subprocess.run(['node', '--input-type=module', '-e', code, os.environ['TERRARIA_CONSUMER_ROOT'], str(root)],
                                    capture_output=True, text=True, check=True, timeout=30)
            self.assertIn('All 13', result.stdout)


if __name__ == '__main__': unittest.main()
