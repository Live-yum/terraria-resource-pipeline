"""Original bounded CLI tests; no game execution or extracted payloads."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from resource_pipeline.security import PipelineError

spec = importlib.util.spec_from_file_location('observed_cli', Path(__file__).resolve().parents[1] / 'scripts/assemble_observed_draft.py')
cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)


class ObservedAssemblyCliTests(unittest.TestCase):
    def test_texture_inventory_rejects_traversal_duplicates_and_bad_types(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); inventory = root / 'inventory.json'
            for names in [['../outside.png'], ['same.png', 'same.png'], [1], [], {'x': 1}]:
                inventory.write_text(json.dumps(names))
                with self.assertRaises(PipelineError): cli.fresh_textures(root, inventory)

    def test_texture_symlinks_and_byte_limits_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); raw = root / 'original.png'; raw.write_bytes(b'fixture')
            link = root / 'alias.png'; link.symlink_to(raw)
            with self.assertRaises(PipelineError): cli.bounded_bytes(link, 20)
            with self.assertRaises(PipelineError): cli.bounded_bytes(raw, 2)
            self.assertEqual(cli.bounded_bytes(raw, 20), b'fixture')

    def test_item_only_writes_false_gated_marker_last(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); inputs = root / 'inputs'; inputs.mkdir()
            observation = inputs / 'observation.json'; observation.write_text('{}')
            pe = inputs / 'client.exe'; pe.write_bytes(b'original-not-executable-fixture')
            output = root / 'out'
            argv = ['script', '--observation', str(observation), '--client', str(pe),
                    '--item-app-source-root', str(inputs), '--output', str(output)]
            with patch('sys.argv', argv), patch.object(cli, 'source_files', return_value={}), \
                 patch.object(cli, 'assemble_observed_item_resources',
                    return_value=({'items.catalog': b'{}'}, {'complete': False})), patch('builtins.print'):
                cli.main()
            marker = json.loads((output / 'assembly.json').read_bytes())
            self.assertEqual((output / 'items.catalog.json').read_bytes(), b'{}')
            for key in ('sourceProductionComplete', 'consumerReleaseReady', 'publicationApproved', 'observationAuthenticated'):
                self.assertIs(marker[key], False)
            with patch('sys.argv', argv), self.assertRaises(SystemExit): cli.main()

    def test_partial_player_options_and_output_traversal_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); inputs = root / 'inputs'; inputs.mkdir()
            argv = ['script', '--observation', str(inputs / 'o'), '--client', str(inputs / 'c'),
                    '--item-app-source-root', str(inputs), '--output', str(root / 'inputs/../inputs/out')]
            with patch('sys.argv', argv), self.assertRaises(SystemExit): cli.main()
            with patch('sys.argv', argv + ['--textures-root', str(root)]), self.assertRaises(SystemExit): cli.main()

    def test_output_cannot_be_inside_texture_inventory_parent(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            argv = ['script', '--observation', str(root / 'observations/o'), '--client', str(root / 'source/c'),
                    '--item-app-source-root', str(root / 'item-app'), '--player-app-source-root', str(root / 'player-app'),
                    '--textures-root', str(root / 'textures'), '--texture-inventory', str(root / 'inventories/list.json'),
                    '--output', str(root / 'inventories/out')]
            with patch('sys.argv', argv), self.assertRaises(SystemExit): cli.main()
