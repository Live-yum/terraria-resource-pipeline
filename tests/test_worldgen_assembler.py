import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from resource_pipeline.worldgen_assembler import assemble_worldgen_resources, NUMERIC, NAMED
from resource_pipeline.security import PipelineError, canonical_json, sha256


def original_worldgen_inputs():
    """Original small examples, no copied game fixture or private baseline."""
    root = {'settings': {'kind': 'object', 'ref': 'HookConfiguration'},
            'modules': {'kind': 'object', 'ref': 'Modules'}}
    types = {'HookConfiguration': {'size': {'kind': 'integer', 'min': 1, 'max': 10},
                                  'style': {'kind': 'string', 'choices': ['example']},
                                  'enabled': {'kind': 'boolean'},
                                  'values': {'kind': 'map', 'keyKind': 'integer', 'item': {'kind': 'number'}},
                                  'extra': {'kind': 'any'}},
             'Modules': {'steps': {'kind': 'array', 'item': {'kind': 'string'}}}}
    options = {'enabled': True, 'versions': ['001'], 'schema': {'revision': 'a' * 64, 'root': root, 'types': types}}
    authority = {kind: [[1, 'Original ' + kind]] for kind in NUMERIC}
    authority.update({kind: [['example', 'Original ' + kind]] for kind in NAMED})
    choices = {'gameChoices': {kind: {'ids': [1], 'names': []} for kind in NUMERIC},
               'catalogs': {kind: copy.deepcopy(authority[kind]) for kind in NAMED},
               'configOptions': {'biomes': {'example': {'kind': 'object', 'fields': {'amount': {'kind': 'number', 'min': 0}}}},
                                 'passes': {'example': {'kind': 'array', 'item': {'kind': 'boolean'}}}},
               'choiceLabels': {'example': 'Original choice'},
               'groupLabels': {'example': {'label': 'Original', 'help': 'Original help', 'icon': 'test'}}}
    inputs = {'gameVersion': '0.0.1', 'serverVersionKey': '001', 'serviceOptions': options,
              'defaults': {'settings': {'size': 3, 'style': 'example', 'enabled': True, 'values': {'1': 0.5},
                                        'extra': {'test': [False, 2, 'value']}}, 'modules': {'steps': ['one']}},
              'authoritativeCatalogs': authority, 'choices': choices, 'sourceHashes': {'service-schema': 'b' * 64, 'app-policy': 'c' * 64}}
    materials = {kind: [[1, 'Original ' + kind, '#123456']] for kind in NUMERIC[1:]}
    items = [[1], ['Original item'], ['ExampleId'], [0], [1], [-1], [0]]
    foundations = {kind: {'gameVersion': '0.0.1', 'releaseId': 'd' * 64, 'baseSha256': sha256(canonical_json(value)), 'bytes': canonical_json(value)}
                   for kind, value in (('items', items), ('materials', materials))}
    return inputs, foundations


class WorldgenAssemblerTests(unittest.TestCase):
    def test_deterministic_no_mutation_no_publication_trust(self):
        inputs, foundations = original_worldgen_inputs()
        before = copy.deepcopy((inputs, foundations))
        objects, receipt = assemble_worldgen_resources(inputs, foundations=foundations)
        self.assertEqual((inputs, foundations), before)
        self.assertEqual((objects, receipt), assemble_worldgen_resources(inputs, foundations=foundations))
        # Object order is canonical; arrays preserve explicit policy ordering.
        reordered = dict(reversed(list(inputs.items())))
        self.assertEqual((objects, receipt), assemble_worldgen_resources(reordered, foundations=foundations))
        self.assertEqual(set(objects), {'worldgen.choices', 'worldgen.options'})
        self.assertEqual(receipt['releaseRoles'], ['worldgen.choices'])
        self.assertEqual(receipt['serviceArtifacts'], ['worldgen.options'])
        self.assertEqual(receipt['status'], 'DERIVED_ONLY')
        self.assertFalse(receipt['sourceSemanticsVerified'])
        self.assertFalse(receipt['publicationApproved'])
        choices = json.loads(objects['worldgen.choices'])
        self.assertEqual(choices['schemaRevision'], inputs['serviceOptions']['schema']['revision'])
        self.assertEqual(choices['foundations']['items']['baseSha256'], foundations['items']['baseSha256'])
        self.assertEqual(json.loads(objects['worldgen.options']), inputs['serviceOptions'])

    def test_invalid_schema_defaults_catalogs_and_policy(self):
        edits = [
            lambda i: i.update(approved=True),
            lambda i: i.update(gameVersion='other'),
            lambda i: i.update(serverVersionKey='١٢٣'),
            lambda i: i.update(sourceHashes={}),
            lambda i: i['serviceOptions'].update(enabled=1),
            lambda i: i['serviceOptions'].update(versions=['001', '001']),
            lambda i: i['serviceOptions'].update(versions=['002']),
            lambda i: i['serviceOptions']['schema'].update(revision='bad'),
            lambda i: i['serviceOptions']['schema']['root']['settings'].update(ref='Missing'),
            lambda i: i['serviceOptions']['schema']['types']['HookConfiguration']['size'].update(min=11),
            lambda i: i['serviceOptions']['schema']['types']['HookConfiguration']['size'].update(min=True),
            lambda i: i['serviceOptions']['schema']['types']['HookConfiguration']['style'].update(choices=['x', 'x']),
            lambda i: i['serviceOptions']['schema']['types']['HookConfiguration']['size'].update(default=2),
            lambda i: i['defaults']['settings'].update(size=True),
            lambda i: i['defaults']['settings'].update(size=99),
            lambda i: i['defaults']['settings'].update(style='missing'),
            lambda i: i['defaults']['settings'].update(unknown=1),
            lambda i: i['defaults']['settings'].update(extra=None),
            lambda i: i['defaults']['settings'].update(values={'bad': 1}),
            lambda i: i['defaults']['modules'].update(steps=['x'] * 257),
            lambda i: i['serviceOptions']['schema']['types']['HookConfiguration']['size'].update(readOnly=True),
            lambda i: i['authoritativeCatalogs'].update(items=[['1', 'Wrong type']]),
            lambda i: i['authoritativeCatalogs'].update(seeds=[[1, 'Wrong type']]),
            lambda i: i['authoritativeCatalogs'].update(items=[[2, 'Unselected']]),
            lambda i: i['authoritativeCatalogs']['passes'].append(['missing', 'Missing pass']),
            lambda i: i['authoritativeCatalogs']['seeds'].append(['missing', 'Missing seed']),
            lambda i: i['choices']['gameChoices']['items'].update(ids=[2]),
            lambda i: i['choices']['gameChoices']['items'].update(ids=[True]),
            lambda i: i['choices']['gameChoices']['items'].update(ids=[1, 1]),
            lambda i: i['choices']['gameChoices']['items'].update(names=[[2, 'Unknown']]),
            lambda i: i['choices']['gameChoices']['items'].update(names=[[1, 'x'], [1, 'y']]),
            lambda i: i['choices']['catalogs'].update(seeds=[['missing', 'Unknown']]),
            lambda i: i['choices']['catalogs'].update(seeds=[['example', 'x'], ['example', 'y']]),
            lambda i: i['choices']['configOptions']['biomes'].update(missing={'kind': 'boolean'}),
            lambda i: i['choices']['configOptions']['biomes'].update(example={'kind': 'object', 'ref': 'HookConfiguration'}),
            lambda i: i['choices']['configOptions']['biomes'].update(example={'kind': 'map', 'keyKind': 'unknown', 'item': {'kind': 'boolean'}}),
            lambda i: i['choices'].update(choiceLabels={}),
            lambda i: i['choices']['choiceLabels'].update(example='😀' * 513),
            lambda i: i['choices']['choiceLabels'].update(example='\ud800'),
            lambda i: i['choices']['choiceLabels'].update(constructor='Bad'),
            lambda i: i['choices']['groupLabels']['example'].update(icon=''),
            lambda i: i['defaults']['settings'].update(extra=float('inf')),
            lambda i: i['defaults']['settings'].update(extra=2**53),
        ]
        for edit in edits:
            with self.subTest(edit=edits.index(edit)):
                inputs, foundations = original_worldgen_inputs()
                edit(inputs)
                with self.assertRaises(PipelineError): assemble_worldgen_resources(inputs, foundations=foundations)

    def test_every_fixture_node_rejects_malformed_types_cleanly(self):
        base, foundations = original_worldgen_inputs()
        def paths(value, prefix=()):
            yield prefix
            if type(value) is dict:
                for key, child in value.items(): yield from paths(child, prefix + (key,))
            elif type(value) is list:
                for index, child in enumerate(value): yield from paths(child, prefix + (index,))
        for path in paths(base):
            if not path: continue
            for replacement in (None, True, False, [], {}, 'oops', 1, 1.5):
                value = copy.deepcopy(base)
                at = value
                for key in path[:-1]: at = at[key]
                at[path[-1]] = replacement
                try:
                    assemble_worldgen_resources(value, foundations=foundations)
                except PipelineError:
                    pass  # Valid alternatives may succeed; malformed types must not crash.

    def test_foundation_rejections(self):
        for kind, field, value in [('items', 'gameVersion', '0.0.2'), ('materials', 'releaseId', 'bad'),
                                   ('items', 'baseSha256', 'f' * 64), ('materials', 'bytes', b'{}')]:
            with self.subTest(kind=kind, field=field):
                inputs, foundations = original_worldgen_inputs()
                foundations[kind][field] = value
                with self.assertRaises(PipelineError): assemble_worldgen_resources(inputs, foundations=foundations)
        for raw in [b'{"tiles":[],"tiles":[]}', b'{"tiles":NaN}', b'\xff', b'[]',
                    canonical_json({'tiles': [[1, 'x', 'bad']], 'walls': [[1, 'x', '#123456']], 'paints': [[1, 'x', '#123456']]})]:
            inputs, foundations = original_worldgen_inputs()
            foundations['materials'].update(bytes=raw, baseSha256=sha256(raw))
            with self.assertRaises(PipelineError): assemble_worldgen_resources(inputs, foundations=foundations)

    def test_budgets_and_cycles_reject_before_serialization(self):
        for mode in ('cycle', 'depth', 'nodes', 'string'):
            inputs, foundations = original_worldgen_inputs()
            if mode == 'cycle': inputs['defaults']['settings']['extra'] = inputs
            elif mode == 'depth':
                value = 0
                for _ in range(40): value = [value]
                inputs['defaults']['settings']['extra'] = value
            elif mode == 'nodes': inputs['defaults']['settings']['extra'] = [0] * 200001
            else: inputs['defaults']['settings']['extra'] = 'x' * 4097
            with self.assertRaises(PipelineError): assemble_worldgen_resources(inputs, foundations=foundations)

    def test_default_depth_matches_consumer_root_node_counting(self):
        for layers in (0, 14, 15):
            with self.subTest(layers=layers):
                inputs, foundations = original_worldgen_inputs()
                value = 1
                for _ in range(layers): value = [value]
                inputs['defaults']['settings']['extra'] = value
                if layers <= 14:
                    assemble_worldgen_resources(inputs, foundations=foundations)
                else:
                    with self.assertRaisesRegex(PipelineError, 'depth'):
                        assemble_worldgen_resources(inputs, foundations=foundations)

    @unittest.skipUnless(os.environ.get('TERRARIA_CONSUMER_ROOT'), 'optional actual app depth oracle')
    def test_actual_app_default_depth_boundary(self):
        root = Path(os.environ['TERRARIA_CONSUMER_ROOT']).resolve()
        code = '''import fs from 'node:fs'; import {pathToFileURL} from 'node:url';
const p=JSON.parse(fs.readFileSync(0,'utf8'));
const {validateGenerationConfig}=await import(pathToFileURL(process.argv[1]+'/features/world-generation/services/metadata-contract.mjs'));
try { validateGenerationConfig(p.defaults,p.schema); console.log('accepted'); }
catch { console.log('rejected'); }'''
        for layers, expected in ((14, 'accepted'), (15, 'rejected')):
            inputs, foundations = original_worldgen_inputs()
            value = 1
            for _ in range(layers): value = [value]
            inputs['defaults']['settings']['extra'] = value
            result = subprocess.run(['node', '--input-type=module', '-e', code, str(root)],
                                    input=json.dumps({'defaults': inputs['defaults'], 'schema': inputs['serviceOptions']['schema']}),
                                    text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout.strip(), expected)
            if expected == 'accepted':
                assemble_worldgen_resources(inputs, foundations=foundations)
            else:
                with self.assertRaises(PipelineError): assemble_worldgen_resources(inputs, foundations=foundations)

    def test_override_and_empty_optional_defaults_are_explicit(self):
        inputs, foundations = original_worldgen_inputs()
        inputs['defaults'] = {}
        inputs['choices']['gameChoices']['items']['names'] = [[1, 'App label']]
        objects, _ = assemble_worldgen_resources(inputs, foundations=foundations)
        self.assertEqual(json.loads(objects['worldgen.choices'])['gameChoices']['items']['names'], [[1, 'App label']])

    @unittest.skipUnless(os.environ.get('TERRARIA_BOOT_CONTRACT_JAVA') and os.environ.get('TERRARIA_BOOT_CLASSPATH'),
                         'optional current backend contract oracle')
    def test_current_backend_contract(self):
        inputs, foundations = original_worldgen_inputs()
        objects, _ = assemble_worldgen_resources(inputs, foundations=foundations)
        source = Path(os.environ['TERRARIA_BOOT_CONTRACT_JAVA']).resolve()
        classpath = os.environ['TERRARIA_BOOT_CLASSPATH']
        with tempfile.TemporaryDirectory(prefix='worldgen-oracle-') as directory:
            oracle = Path(directory) / 'WorldgenOracle.java'
            oracle.write_text('''package cn.iocoder.yudao.module.viewer.service.resourcepublication;
import com.fasterxml.jackson.databind.ObjectMapper;
public final class WorldgenOracle {
 public static void main(String[] args) throws Exception {
  ConsumerRoleContract.validate("worldgen.choices", new ObjectMapper().readTree(System.in));
  System.out.println("Current backend accepted original assembly");
 }
}''', encoding='utf-8')
            subprocess.run(['java', 'com.sun.tools.javac.Main', '-proc:none', '-cp', classpath, '-d', directory,
                            str(source), str(oracle)], capture_output=True, check=True)
            result = subprocess.run(['java', '-cp', directory + os.pathsep + classpath,
                                     'cn.iocoder.yudao.module.viewer.service.resourcepublication.WorldgenOracle'],
                                    input=objects['worldgen.choices'], capture_output=True, check=True)
            self.assertIn(b'accepted', result.stdout)

    @unittest.skipUnless(os.environ.get('TERRARIA_CONSUMER_ROOT'), 'optional actual app contract oracle')
    def test_actual_app_contract_and_defaults(self):
        inputs, foundations = original_worldgen_inputs()
        objects, _ = assemble_worldgen_resources(inputs, foundations=foundations)
        payload = {'choices': json.loads(objects['worldgen.choices']), 'options': json.loads(objects['worldgen.options']), 'defaults': inputs['defaults']}
        root = Path(os.environ['TERRARIA_CONSUMER_ROOT']).resolve()
        code = '''import fs from 'node:fs'; import {pathToFileURL} from 'node:url';
const p=JSON.parse(fs.readFileSync(0,'utf8'));
const c=await import(pathToFileURL(process.argv[1]+'/features/world-generation/services/metadata-contract.mjs'));
c.validateGenerationOptions(p.options);
c.validateGenerationConfig(p.defaults,p.options.schema);
c.validateWorldGenerationSnapshot({gameVersion:'0.0.1',readJson:()=>p.choices},{pin:{gameVersion:'0.0.1'},serverVersionKey:'001',schemaRevision:p.options.schema.revision});
console.log('Original worldgen assembly accepted');'''
        result = subprocess.run(['node', '--input-type=module', '-e', code, str(root)], input=json.dumps(payload), text=True, capture_output=True, check=True)
        self.assertIn('accepted', result.stdout)


if __name__ == '__main__': unittest.main()
