"""Original orchestration tests; source/codec proofs have separate real oracles."""
import unittest
from unittest.mock import patch

from resource_pipeline.observed_player_assembly import assemble_observed_player_resources
from resource_pipeline.security import PipelineError


class ObservedPlayerAssemblyTests(unittest.TestCase):
    def run_join(self, changed=False, changed_items=False):
        policy_receipt = {'sourceTextures': {'original.png': 'a' * 64}, 'complete': False}
        final = dict(policy_receipt)
        if changed: final['sourceTextures'] = {'original.png': 'b' * 64}
        with patch('resource_pipeline.observed_player_assembly.build_player_choice_recipes',
                   return_value=({'rows': [{'key': 'clothes:0'}]}, {}, policy_receipt)) as choices, \
             patch('resource_pipeline.observed_player_assembly.adapt_observed_player_facts',
                   return_value=({'original': 'facts'}, {'complete': False, 'itemHashes': {'original': 'a'}})) as facts, \
             patch('resource_pipeline.observed_player_assembly.assemble_player_from_fresh_textures',
                   return_value=({'player.walk': b'original'}, {'texturePolicyReceipts': {'player-choice-policy': final}, 'itemHashes': {'original': 'b' if changed_items else 'a'}})) as assembly:
            result = assemble_observed_player_resources(observation={'original': 'observation'},
                pe_bytes=b'original source fixture', app_sources={}, textures={'original.png': b'png fixture'},
                item_objects={'original': b'foundation'})
            self.assertEqual(facts.call_args.kwargs['choices'], {'clothes:0': {}})
            self.assertEqual(assembly.call_args.kwargs['facts'], {'original': 'facts'})
            self.assertEqual(set(assembly.call_args.kwargs['source_hashes']), {'player-fact-adaptation'})
            self.assertEqual(choices.call_count, 1)
            return result

    def test_internal_choices_and_fact_receipts_bind_final_assembly(self):
        objects, receipt = self.run_join()
        self.assertEqual(objects['player.walk'], b'original')
        for field in ('complete', 'publishable', 'executedInput', 'observationAuthenticated',
                      'runtimeInitializationVerified', 'sourceSemanticsVerified'):
            self.assertIs(receipt[field], False)

    def test_changed_texture_provenance_rejects_before_output(self):
        with self.assertRaisesRegex(PipelineError, 'provenance changed'):
            self.run_join(changed=True)

    def test_changed_foundation_receipt_rejects_before_output(self):
        with self.assertRaisesRegex(PipelineError, 'foundation changed'):
            self.run_join(changed_items=True)
