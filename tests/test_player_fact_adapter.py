import copy
import json
import unittest
from unittest.mock import patch

from resource_pipeline.item_assembler import assemble_item_resources
from resource_pipeline.item_texture_aliases import _Program, _Budget, ItemTextureAliasLimits
from resource_pipeline.player_assembler import UNLOCKS
from resource_pipeline.player_fact_adapter import (FACE_SETS, DYE_CLASSES, _join_player_facts,
                                                   adapt_observed_player_facts)
from resource_pipeline.player_fact_app_policy import (_Literal, _declaration, _extract_policy_sources,
                                                     extract_player_app_policy, APP_SOURCE_PINS)
from resource_pipeline.player_fact_source_shapes import (SOURCE_SHA256, _hair_head_sets, _back_hair,
    _clothes, _current_constants, _count_prefix, extract_player_static_facts)
from resource_pipeline.security import PipelineError, canonical_json
from resource_pipeline.static_il import decode_il, Instruction
from player_fact_source_fixture import fixture, assemble
from test_item_assembler import original_inputs


def program(raw):
    return _Program(raw, _Budget(ItemTextureAliasLimits(), None))


def original_policy_sources():
    wing = {'frameCount': 3, 'offsetX': 0, 'offsetY': 1, 'cropRight': 0, 'cropBottom': 0,
            'anchorMode': 'common-wing', 'special': None, 'requires': [], 'animateWhenIdle': False, 'fidelity': 'source-port'}
    return {'services/selection.mjs': 'export const UNLOCKS = ' + json.dumps([[key, 'Original unlock', 1, 38] for key in UNLOCKS]) + '\n'
                'const negative=new Set([3])\nconst BUFFS=()=>({known:id<=1||[2].includes(id)})\n',
            'services/versions.mjs': 'export const VERSION_LABELS = {"38":"0.0.1"}\n',
            'services/render/terraria-player-draw-rules.mjs': 'const DEFAULT_WING_RULE = Object.freeze(' + json.dumps(wing) + ');\n'
                'const WING_RULES = Object.freeze({2: {offsetY: -3, animateWhenIdle: true}});\n'}


def original_join_inputs():
    item = original_inputs()
    item['records'][0]['gameplay'].update(dye=2, hairDye=2)
    item['records'][1]['gameplay']['hairDye'] = 0
    item['records'][2]['gameplay']['hairDye'] = 1
    objects, _ = assemble_item_resources(item)
    static = {'currentVersion': {'saveVersion': 38, 'gameVersion': '0.0.1'}, 'buffCount': 4, 'faceCount': 3,
        'clothes': [2, 0, 1], 'hairRules': {'fullHairHeads': [2], 'hatHairHeads': [3], 'drawsBackWithoutHeadgear': [],
        'backHairStyle': {'lowerExclusive': 0, 'upperExclusive': 4, 'excludedRanges': [], 'excludedIds': [2], 'includedIds': [6]}}}
    app, _ = _extract_policy_sources(original_policy_sources())
    observation = {'schemaVersion': 1, 'gameVersion': '0.0.1', 'sourceSha256': SOURCE_SHA256, 'culture': 'zh-Hans',
        'dedServ': True, 'buffCount': 4, 'buffs': [{'id': n, 'name': 'Original buff ' + str(n), 'description': ''} for n in range(1, 4)],
        'dyes': [{'itemId': 1, 'shaderId': 2, 'class': 'Terraria.Graphics.Shaders.ArmorShaderData', 'pass': 'OriginalPass',
                  'color': [0.25, 0.5, 0.75], 'secondaryColor': [1, 0.25, 0], 'saturation': 0.5}],
        'faceCount': 3, 'faceSets': {key: [False, True, False] for key in FACE_SETS},
        'hairShaderCount': 2, 'hairDyeBindings': [{'itemId': 1, 'shaderId': 2}, {'itemId': 3, 'shaderId': 1}],
        'mainDebuff': [False, True, False, False], 'omittedFields': ['dyes.image'],
        'initializationVerified': False, 'complete': False, 'publishable': False}
    return observation, static, app, objects, {'clothes:0': {}, 'clothes:1': {}, 'clothes:2': {}}


class PlayerFactSnapshotTests(unittest.TestCase):
    def test_mapping_budgets_precede_copy_or_parsing(self):
        observation, static, policy, objects, choices = original_join_inputs()
        with self.assertRaises(PipelineError):
            _join_player_facts(observation, static, policy, objects, {str(n): None for n in range(8193)})
        with self.assertRaises(PipelineError):
            _join_player_facts(observation, static, policy, {**objects, 'extra': b'x'}, choices)

    def test_callback_cannot_rewrite_observation_or_foundation_receipt(self):
        observation, static, policy, objects, choices = original_join_inputs()
        name = observation['buffs'][0]['name']
        hashes = {role: __import__('hashlib').sha256(raw).hexdigest() for role, raw in objects.items()}
        def mutate():
            observation['buffs'][0]['name'] = 'Changed caller string'
            objects['items.rules'] = b'not JSON anymore'
            choices.clear()
        facts, evidence = _join_player_facts(observation, static, policy, objects, choices, mutate)
        self.assertEqual(facts['buffs']['1'][0], name)
        self.assertEqual(evidence['itemHashes'], hashes)


class PlayerFactSourceShapeTests(unittest.TestCase):
    def test_original_pe_constructor_rva_and_version_constants(self):
        raw, tokens = fixture(); p = program(raw)
        clothes, evidence = _clothes(p, p.body(tokens['clothesMethod']), tokens['clothes'])
        self.assertEqual(clothes, [2, 0, 1]); self.assertEqual(evidence['bytes'], 12)
        current, proof = _current_constants(p, tokens['release'], tokens['version'], tokens['display'])
        self.assertEqual(current, {'saveVersion': 38, 'gameVersion': '0.0.1'}); self.assertEqual(len(proof), 3)

    def test_clothes_are_actually_read_from_pe_bytes(self):
        for order in ((1, 0, 2), (2, 1, 0)):
            raw, tokens = fixture(clothes=order); p = program(raw)
            self.assertEqual(_clothes(p, p.body(tokens['clothesMethod']), tokens['clothes'])[0], list(order))
        for options in ({'malformed_array_size': True}, {'clothes': (2, 2, 0)}, {'clothes': (2, 256, 0)}):
            raw, tokens = fixture(**options); p = program(raw)
            with self.assertRaises(PipelineError): _clothes(p, p.body(tokens['clothesMethod']), tokens['clothes'])

    def test_duplicate_constant_is_rejected(self):
        raw, tokens = fixture(duplicate_constant=True); p = program(raw)
        with self.assertRaises(PipelineError): _current_constants(p, tokens['release'], tokens['version'], tokens['display'])

    def test_original_call_free_head_tree(self):
        raw, t = fixture(); p = program(raw)
        result = _hair_head_sets(p.body(t['headMethod'])['instructions'], t['headEnd'], t['head'])
        self.assertEqual(result, {'fullHairHeads': [2], 'hatHairHeads': [3], 'drawsBackWithoutHeadgear': []})

    def test_head_unreachable_call_and_backward_branch_rejected(self):
        raw, t = fixture(); original = program(raw).body(t['headMethod'])['instructions']
        for altered in ([Instruction(0, 0x28, 0x06000001, 5), *original[1:]],
                        [Instruction(0, 0x2b, 0, 1), *original[1:]],
                        [Instruction(0, 0x7b, 0x04000999, 5), *original[1:]]):
            with self.assertRaises(PipelineError): _hair_head_sets(altered, t['headEnd'], t['head'])

    def test_original_back_hair_grammar_extracts_values(self):
        raw, t = fixture(); result = _back_hair(program(raw).body(t['backMethod'])['instructions'], 0, t['hair'])
        self.assertEqual(result, {'lowerExclusive': 10, 'upperExclusive': 70, 'excludedRanges': [[20, 22], [30, 32], [40, 42]],
                                'excludedIds': [50, 52, 54, 56], 'includedIds': [4, 7, 8, 72, 90]})

    def test_back_hair_grammar_rejects_wrong_targets_and_calls(self):
        raw, t = fixture(); ops = program(raw).body(t['backMethod'])['instructions']
        for index, opcode, operand in ((1, 0x7b, 123), (6, 0x31, 0), (10, 0x28, 0x06000001), (len(ops)-1, 0x00, None)):
            changed = list(ops); old = changed[index]; changed[index] = Instruction(old.offset, opcode, operand, old.next_offset)
            with self.assertRaises(PipelineError): _back_hair(changed, 0, t['hair'])

    def test_count_prefix_uses_literal_and_exact_store(self):
        for count in (4, 27):
            ops = list(decode_il(assemble([(0x1f, count), (0x80, 0x04000002), 0x2a])[0]).values())
            self.assertEqual(_count_prefix(ops, 0x04000002), count)
            with self.assertRaises(PipelineError): _count_prefix(ops, 0x04000003)

    def test_public_source_reader_rejects_nonpinned_pe(self):
        with self.assertRaisesRegex(PipelineError, 'source pin'): extract_player_static_facts(fixture()[0])
        with self.assertRaises(PipelineError): extract_player_static_facts(b'')


class PlayerAppPolicyTests(unittest.TestCase):
    def test_original_literal_policy_only(self):
        result, versions = _extract_policy_sources(original_policy_sources())
        self.assertEqual(result['selection']['negativeBuffs'], [3])
        self.assertEqual(result['selection']['commonBuffs'], [2])
        self.assertEqual(result['wingRules']['slots']['2']['offsetY'], -3)
        self.assertEqual(result['wingRules']['slots']['2']['frameCount'], 3)
        self.assertEqual(set(versions), set(UNLOCKS))
        self.assertNotIn('clothes', result['selection']); self.assertNotIn('hairDyeItems', result['selection'])
        self.assertNotIn('maxBuffId', result['selection'])

    def test_literal_parser_comments_escapes_and_trailing_comma(self):
        source = 'const A = Object.freeze({ /* original */ alpha: [1, -2,], "label": "v\\u2013w", ok:true, absent:null});'
        self.assertEqual(_declaration(source, 'A', 'freeze'), {'alpha': [1, -2], 'label': 'v–w', 'ok': True, 'absent': None})

    def test_literal_parser_never_evaluates_expressions(self):
        for literal in ('call()', '[...something]', '{get x(){return 1}}', '{["x"]:1}', '{x:1,x:2}',
                        '{__proto__:1}', '`template`', 'NaN', 'Infinity', '{x:process.exit()}', '{x:1 + 2}'):
            with self.subTest(literal=literal), self.assertRaises(PipelineError): _declaration('const A = ' + literal + ';', 'A')
        for suffix in ('.map(call)', ' + 1', '(call)', ' && call()', '\n + 1', '\n [0]'):
            with self.assertRaises(PipelineError): _declaration('const A = [1]' + suffix + ';', 'A')

    def test_duplicate_declarations_and_budgets(self):
        with self.assertRaises(PipelineError): _declaration('const A = 1\nconst A = 2\n', 'A')
        with self.assertRaises(PipelineError): _Literal('[' * 14 + '0' + ']' * 14).value()
        with self.assertRaises(PipelineError): _Literal('"' + 'a' * 4097 + '"').value()

    def test_public_app_reader_cannot_accept_synthetic_or_override_pins(self):
        values = {key: value.encode() for key, value in original_policy_sources().items()}
        self.assertEqual(set(values), set(APP_SOURCE_PINS))
        with self.assertRaisesRegex(PipelineError, 'immutable app source'): extract_player_app_policy(values)
        with self.assertRaises(PipelineError): extract_player_app_policy({})


class PlayerFactAdapterTests(unittest.TestCase):
    def test_complete_original_join_preserves_source_kinds_and_order(self):
        inputs = original_join_inputs(); before = copy.deepcopy(inputs)
        facts, receipt = _join_player_facts(*inputs)
        self.assertEqual(inputs, before)
        self.assertEqual(facts['selection']['clothes'], [2, 0, 1])
        self.assertEqual(facts['selection']['hairDyeItems'], [0, 3, 1])
        self.assertEqual(facts['selection']['maxBuffId'], 3)
        self.assertEqual(facts['selection']['negativeBuffs'], [3])
        self.assertFalse(receipt['negativeBuffPolicyEqualsMainDebuff'])
        self.assertEqual(facts['dyes'][0]['class'], 'ArmorShaderData')
        self.assertNotIn('image', facts['dyes'][0]); self.assertNotIn('shaderId', facts['dyes'][0])
        self.assertEqual(facts['dyes'][0]['secondaryColor'], [1, 0.25, 0])
        self.assertEqual(_join_player_facts(*inputs), (facts, receipt))

    def test_all_exact_dye_classes(self):
        for name in DYE_CLASSES:
            inputs = original_join_inputs(); inputs[0]['dyes'][0]['class'] = name
            self.assertEqual(_join_player_facts(*inputs)[0]['dyes'][0]['class'], name.rsplit('.', 1)[1])

    def test_observation_adversarial_mutations_fail_closed(self):
        mutations = (
            lambda x: x.update(schemaVersion=True), lambda x: x.update(sourceSha256='0' * 64),
            lambda x: x.update(gameVersion='0.0.2'), lambda x: x.update(culture='en-US'),
            lambda x: x.update(dedServ=False), lambda x: x.update(complete=True),
            lambda x: x.update(initializationVerified=True), lambda x: x.update(publishable=True),
            lambda x: x.update(omittedFields=[]), lambda x: x.update(buffCount=5),
            lambda x: x['buffs'].pop(), lambda x: x['buffs'][0].update(id=2),
            lambda x: x['buffs'][0].update(name=''), lambda x: x['buffs'][0].pop('description'),
            lambda x: x['dyes'][0].update(shaderId=1), lambda x: x['dyes'][0].pop('color'),
            lambda x: x['dyes'][0].pop('saturation'), lambda x: x['dyes'][0].update(image=''),
            lambda x: x['dyes'][0].update(color=[0, float('nan'), 1]),
            lambda x: x['dyes'][0].update(saturation=True), lambda x: x['dyes'][0].update(color=[1, 0, 5]),
            lambda x: x['dyes'][0].update(**{'class': 'ArmorShaderData'}),
            lambda x: x['dyes'][0].update(**{'class': 'Terraria.Graphics.Shaders.TeamArmorShaderData'}),
            lambda x: x['dyes'][0].update(**{'pass': 'not-a-pass'}), lambda x: x['dyes'].append(copy.deepcopy(x['dyes'][0])),
            lambda x: x.update(faceCount=4), lambda x: x['faceSets'].pop('PreventHairDraw'),
            lambda x: x['faceSets']['PreventHairDraw'].pop(), lambda x: x['faceSets']['PreventHairDraw'].__setitem__(0, 1),
            lambda x: x.update(hairShaderCount=3), lambda x: x['hairDyeBindings'].pop(),
            lambda x: x['hairDyeBindings'][0].update(shaderId=1), lambda x: x['hairDyeBindings'][0].update(itemId=2),
            lambda x: x['mainDebuff'].pop(), lambda x: x.update(extra=True),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                inputs = original_join_inputs(); mutate(inputs[0])
                with self.assertRaises(PipelineError): _join_player_facts(*inputs)

    def test_bad_item_foundation_and_choices_are_rejected(self):
        inputs = original_join_inputs(); inputs[4].pop('clothes:2')
        with self.assertRaises(PipelineError): _join_player_facts(*inputs)
        inputs = original_join_inputs(); rules = json.loads(inputs[3]['items.rules']); rules['items'].pop('2')
        inputs[3]['items.rules'] = canonical_json(rules)
        with self.assertRaises(PipelineError): _join_player_facts(*inputs)
        inputs = original_join_inputs(); rules = json.loads(inputs[3]['items.rules']); rules['version'] = '0.0.2'
        inputs[3]['items.rules'] = canonical_json(rules)
        with self.assertRaises(PipelineError): _join_player_facts(*inputs)

    def test_current_version_conflict_is_a_blocker(self):
        inputs = original_join_inputs(); inputs[2]['versionLabels']['38'] = '0.0.2'
        with self.assertRaises(PipelineError): _join_player_facts(*inputs)

    def test_bounded_cyclic_observation_is_rejected(self):
        inputs = original_join_inputs(); inputs[0]['cycle'] = inputs[0]
        with self.assertRaises(PipelineError): _join_player_facts(*inputs)

    def test_public_receipt_does_not_claim_runtime_or_publication_acceptance(self):
        observation, static, policy, objects, choices = original_join_inputs()
        with patch('resource_pipeline.player_fact_adapter.extract_player_static_facts', return_value=(static, {'complete': False})), \
             patch('resource_pipeline.player_fact_adapter.extract_player_app_policy', return_value=(policy, {'freshGameObservation': False})):
            facts, receipt = adapt_observed_player_facts(observation, pe_bytes=b'original mock', app_sources={}, item_objects=objects, choices=choices)
        for field in ('executedInput', 'observationAuthenticated', 'runtimeInitializationVerified', 'sourceSemanticsVerified', 'complete', 'publishable'):
            self.assertIs(receipt[field], False)
        self.assertEqual(receipt['omittedFields'][0]['field'], 'dyes.image')
        self.assertEqual(facts['versionLabels'], {'38': '0.0.1'})


if __name__ == '__main__': unittest.main()
