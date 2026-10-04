import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

from resource_pipeline.security import canonical_json, sha256
from test_item_assembler import original_inputs
from test_marker_assembler import original_marker_inputs
from test_pixel_assembler import fixture as pixel_fixture, policy as pixel_policy
from test_materials_assembler import original_material_inputs
from test_player_assembler import original_player_inputs
from test_worldgen_assembler import original_worldgen_inputs

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/assemble_consumer_draft.py'


class AssemblyCliTests(unittest.TestCase):
    def call(self, group, source, output, *extra):
        return subprocess.run([sys.executable, str(SCRIPT), '--group', group,
                               '--input-root', str(source), '--output', str(output), *extra],
                              capture_output=True, text=True)

    def test_item_cli_generates_private_draft_never_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'item-facts.json').write_bytes(canonical_json(original_inputs()))
            output = root / 'draft'
            response = self.call('items', root, output)
            self.assertEqual(response.returncode, 0, response.stderr)
            self.assertNotIn('Original', response.stdout)
            self.assertFalse(json.loads(response.stdout)['consumerReleaseReady'])
            manifest = json.loads((output / 'assembly.json').read_bytes())
            self.assertEqual(manifest['kind'], 'private-derived-draft')
            self.assertFalse(manifest['sourceProductionComplete'])
            self.assertFalse((output / 'manifest.json').exists())
            for row in manifest['files'].values():
                raw = (output / row['file']).read_bytes()
                self.assertEqual(sha256(raw), row['sha256'])
            self.assertNotEqual(self.call('items', root, output).returncode, 0)

    def test_marker_cli_provenance_and_hash_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = original_marker_inputs()
            (root / 'materials.base.json').write_bytes(inputs['material_base'])
            (root / 'policy.json').write_bytes(canonical_json(inputs['policy']))
            for group in ('textures', 'source_files'):
                folder = 'sources' if group == 'source_files' else group
                for name, raw in inputs[group].items():
                    path = root / folder / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
            output = root / 'draft'
            self.assertNotEqual(self.call('markers', root, output).returncode, 0)
            self.assertFalse(output.exists())
            response = self.call('markers', root, output, '--base-sha256', inputs['material_base_sha256'])
            self.assertEqual(response.returncode, 0, response.stderr)
            self.assertEqual(len(json.loads((output / 'assembly.json').read_bytes())['files']), 2)

    def test_pixel_cli_derives_index_not_material_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = canonical_json(pixel_fixture())
            (root / 'materials.base.json').write_bytes(base)
            (root / 'policy.json').write_bytes(canonical_json(pixel_policy()))
            output = root / 'draft'
            response = self.call('pixel', root, output, '--base-sha256', sha256(base))
            self.assertEqual(response.returncode, 0, response.stderr)
            manifest = json.loads((output / 'assembly.json').read_bytes())
            self.assertEqual(set(manifest['files']), {'pixel.catalog', 'pixel.rgb'})
            self.assertFalse(manifest['consumerReleaseReady'])
            self.assertEqual((output / 'pixel.rgb.bin').read_bytes()[:4], b'SRGB')

    def test_materials_cli_writes_only_partial_atomic_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inputs = original_material_inputs()
            (root / 'material-records.json').write_bytes(canonical_json(inputs['records']))
            (root / 'policy.json').write_bytes(canonical_json(inputs['policy']))
            response = self.call('materials', root, root / 'draft')
            self.assertEqual(response.returncode, 0, response.stderr)
            manifest = json.loads((root / 'draft/assembly.json').read_bytes())
            self.assertEqual(set(manifest['releaseRoles']), {'materials.base', 'materials.rules'})
            self.assertFalse(manifest['consumerReleaseReady'])

    def test_player_cli_binds_items_and_renders_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inputs = original_player_inputs()
            (root / 'policy.json').write_bytes(canonical_json(inputs['policy']))
            for role, raw in inputs['item_objects'].items(): (root / (role + '.json')).write_bytes(raw)
            (root / 'textures').mkdir()
            for name, raw in inputs['textures'].items(): (root / 'textures' / name).write_bytes(raw)
            response = self.call('player', root, root / 'draft')
            self.assertEqual(response.returncode, 0, response.stderr)
            manifest = json.loads((root / 'draft/assembly.json').read_bytes())
            self.assertEqual(set(manifest['releaseRoles']), {'player.presentation', 'player.walk', 'player.atlas'})
            self.assertEqual((root / 'draft/player.atlas.png').read_bytes()[:8], b'\x89PNG\r\n\x1a\n')

    def test_worldgen_service_options_never_invent_a_release_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inputs, foundations = original_worldgen_inputs()
            (root / 'worldgen-input.json').write_bytes(canonical_json(inputs))
            (root / 'foundations.json').write_bytes(canonical_json({k: {f: v for f, v in row.items() if f != 'bytes'} for k, row in foundations.items()}))
            (root / 'items.catalog.json').write_bytes(foundations['items']['bytes'])
            (root / 'materials.base.json').write_bytes(foundations['materials']['bytes'])
            response = self.call('worldgen', root, root / 'draft')
            self.assertEqual(response.returncode, 0, response.stderr)
            manifest = json.loads((root / 'draft/assembly.json').read_bytes())
            self.assertEqual(manifest['releaseRoles'], ['worldgen.choices'])
            self.assertEqual(manifest['serviceArtifacts'], ['worldgen.options'])

    def test_missing_final_fact_and_linked_root_do_not_emit_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = original_inputs()
            del inputs['records'][0]['gameplay']['mana']
            (root / 'item-facts.json').write_bytes(canonical_json(inputs))
            output = root / 'draft'
            self.assertNotEqual(self.call('items', root, output).returncode, 0)
            self.assertFalse(output.exists())
            (root / 'link').symlink_to(root, target_is_directory=True)
            self.assertNotEqual(self.call('items', root / 'link', output).returncode, 0)
            self.assertFalse(output.exists())

    def test_dot_dot_output_cannot_bypass_private_output_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'item-facts.json').write_bytes(canonical_json(original_inputs()))
            checkout = SCRIPT.parents[1]
            output = Path('/tmp/..') / checkout.relative_to('/') / ('must-not-write-' + uuid.uuid4().hex)
            response = self.call('items', root, output)
            self.assertNotEqual(response.returncode, 0)
            self.assertIn('outside the pipeline checkout', response.stderr)
            self.assertFalse(output.exists())


if __name__ == '__main__': unittest.main()
