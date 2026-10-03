"""Original synthetic research inputs and IL; no game bytes, names, or ID tables."""
from dataclasses import replace
import struct
import tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import unittest

from resource_pipeline.research_semantics import (
    ResearchLimits, extract_override_initializer, extract_research_model, extract_research_semantics,
    lookup_research_count, parse_research_catalog, research_summary,
)
from resource_pipeline.security import PipelineError
from resource_pipeline.static_il import decode_il


_ARRAY = 0x01000001
_HELPER = 0x06000001
_INIT = 0x0a000001
_FIELD = 0x04000001


def i4(value):
    return b'\x20' + struct.pack('<i',value)


def token(op, value):
    return bytes((op,)) + struct.pack('<I',value)


def group(target, sources):
    result = i4(target) + i4(len(sources)) + token(0x8d,_ARRAY)
    for index, source in enumerate(sources):
        result += b'\x25' + i4(index) + i4(source) + b'\x9e'
    return result + token(0x28,_HELPER)


def extract(code, *, read_rva=None, limits=ResearchLimits()):
    return extract_override_initializer(list(decode_il(code).values()),_HELPER,
        int_type=_ARRAY,initialize_array=_INIT,
        read_rva=read_rva or (lambda *_: (_ for _ in ()).throw(AssertionError('Unexpected RVA'))),
        limits=limits)


class ResearchCatalogTests(unittest.TestCase):
    def test_line_splits_comments_columns_and_exact_names(self):
        payload = (b'//Fixture\td\tx\r\n'
                   b'First\tD\tx\rSecond\tb\tx\n'
                   b'first\tz\tx\n First\td\tx\n'
                   b'//only comment\nFirst\tc\n'
                   b'Third\t\textra\tignored')
        result = parse_research_catalog(payload,{'First':11,'Second':12,'Third':-3})
        self.assertEqual({'11':1,'12':25,'-3':50},result['baseCounts'])
        self.assertEqual(2,result['statistics']['comments'])
        self.assertEqual(2,result['statistics']['unknownNames'])
        self.assertEqual(1,result['statistics']['shortRows'])
        self.assertEqual(7,result['baseOrigins']['-3']['row'])

    def test_alias_overwrite_and_exclusion_preserves_previous_assignment(self):
        result = parse_research_catalog(
            b'Primary\tc\tx\nAlias\td\tx\nPrimary\te\tx\nExcluded\te\tx',
            {'Primary':7,'Alias':7,'Excluded':8})
        self.assertEqual({'7':1},result['baseCounts'])
        self.assertEqual('Alias',result['baseOrigins']['7']['name'])
        self.assertEqual(2,result['statistics']['duplicateRecognizedRows'])
        self.assertEqual(2,result['statistics']['excludedRows'])
        self.assertEqual(2,result['statistics']['assignments'])

    def test_all_rule_categories_and_explicit_invariant_uppercase_i(self):
        categories = ['',*'ABCDEFGHIJKLMNO']
        expected = [50,50,25,5,1,None,2,3,10,15,30,99,100,200,20,400]
        names = {'Fixture'+str(i):i for i in range(len(categories))}
        text = '\n'.join(f'Fixture{i}\t{category}\tx' for i,category in enumerate(categories))
        result = parse_research_catalog(text.encode(),names,culture='invariant')
        self.assertEqual({str(i):value for i,value in enumerate(expected) if value is not None},result['baseCounts'])
        for culture in ('tr-TR','az-Latn-AZ','en-US',None):
            with self.subTest(culture=culture),self.assertRaises(PipelineError):
                parse_research_catalog(b'Fixture\tI\tx',{'Fixture':3},culture=culture)

    def test_unknown_recognized_category_throws_and_is_not_default(self):
        for category in (b'z',b' a',b'a ',b'1',b'AA'):
            with self.subTest(category=category),self.assertRaises(PipelineError):
                parse_research_catalog(b'Fixture\t'+category+b'\tx',{'Fixture':1})
        # The game's name test comes before category processing.
        self.assertEqual({},parse_research_catalog(b'Unknown\tINVALID\tx',{})['baseCounts'])

    def test_empty_field_name_is_exact_not_trimmed_or_numeric(self):
        result = parse_research_catalog(b'\td\tx\n2\td\tx\nName \td\tx',{'':-1,'Name':2})
        self.assertEqual({'-1':1},result['baseCounts'])

    def test_optional_utf8_bom_and_non_ascii_are_bounded(self):
        self.assertEqual({'1':1},parse_research_catalog(b'\xef\xbb\xbfF\td\tx',{'F':1})['baseCounts'])
        for payload in (b'\xff\xfeF\x00',b'F\td\t\xff','F\td\t中文'.encode()):
            with self.subTest(payload=payload),self.assertRaises(PipelineError):
                parse_research_catalog(payload,{'F':1})

    def test_resource_row_name_and_evidence_limits(self):
        cases = [({'resource_bytes':3},b'F\td\tx',{'F':1}),
                 ({'rows':1},b'F\td\tx\nF\td\tx',{'F':1}),
                 ({'row_bytes':3},b'F\td\tx',{'F':1}),
                 ({'names':1},b'',{'F':1,'G':2}),
                 ({'name_bytes':1},b'',{'FF':1}),
                 ({'evidence_bytes':1},b'F\td\tx',{'F':1})]
        for options,payload,names in cases:
            with self.subTest(options=options),self.assertRaises(PipelineError):
                parse_research_catalog(payload,names,limits=replace(ResearchLimits(),**options))

    def test_non_integer_identifiers_are_not_accepted(self):
        for value in (True,1.5,'1',1<<31,-(1<<31)-1):
            with self.subTest(value=value),self.assertRaises(PipelineError):
                parse_research_catalog(b'F\td\tx',{'F':value})

    def test_checkpoint_interrupts_work_without_partial_result(self):
        def cancelled():
            raise RuntimeError('synthetic cancellation')
        with self.assertRaisesRegex(RuntimeError,'synthetic cancellation'):
            parse_research_catalog(b'F\td\tx',{'F':1},checkpoint=cancelled)


class ResearchOverrideTests(unittest.TestCase):
    def test_inline_arrays_direction_repeated_writes_and_negative_ids(self):
        result = extract(group(101,[-8,4])+group(202,[4,5])+b'\x2a')
        self.assertEqual({'-8':101,'4':202,'5':202},result['persistentIdOverrides'])
        self.assertEqual([101,202],[row['target'] for row in result['overrideCalls']])

    def test_rva_array_callback_has_exact_requested_size(self):
        requests = []
        def read(field,count):
            requests.append((field,count))
            return [-11,12,13],{'sha256':'synthetic-digest','bytes':12}
        code = i4(200)+i4(3)+token(0x8d,_ARRAY)+b'\x25'+token(0xd0,_FIELD)+token(0x28,_INIT)+token(0x28,_HELPER)+b'\x2a'
        result = extract(code,read_rva=read)
        self.assertEqual([(_FIELD,3)],requests)
        self.assertEqual({'-11':200,'12':200,'13':200},result['persistentIdOverrides'])
        self.assertEqual('synthetic-digest',result['rvaArrays'][0]['sha256'])

    def test_truncated_wrong_type_or_call_rejects_entire_initializer(self):
        good = group(10,[2])+b'\x2a'
        cases = [good[:-1],good+b'\x00',good.replace(token(0x8d,_ARRAY),token(0x8d,_ARRAY+1)),
                 good.replace(token(0x28,_HELPER),token(0x28,_HELPER+1)),
                 good.replace(b'\x9e',b'\x9d'),b'\x00'+good]
        for code in cases:
            with self.subTest(code=code),self.assertRaises(PipelineError):
                extract(code)

    def test_array_zero_negative_large_or_missing_store_rejects(self):
        for length in (0,-1,1025):
            with self.subTest(length=length),self.assertRaises(PipelineError):
                extract(i4(10)+i4(length)+token(0x8d,_ARRAY)+b'\x2a')
        code = i4(10)+i4(2)+token(0x8d,_ARRAY)+b'\x25'+i4(1)+i4(20)+b'\x9e'+token(0x28,_HELPER)+b'\x2a'
        with self.assertRaises(PipelineError):
            extract(code)

    def test_incomplete_or_invalid_rva_values_reject(self):
        code = i4(10)+i4(2)+token(0x8d,_ARRAY)+b'\x25'+token(0xd0,_FIELD)+token(0x28,_INIT)+token(0x28,_HELPER)+b'\x2a'
        for values in ([1],[1,True],[1,1<<31],[1,'2']):
            with self.subTest(values=values),self.assertRaises(PipelineError):
                extract(code,read_rva=lambda *_:(values,{}))

    def test_limits_and_cancellation(self):
        code = group(10,[2])+group(20,[3])+b'\x2a'
        for limits in (replace(ResearchLimits(),override_calls=1),replace(ResearchLimits(),instructions=2)):
            with self.assertRaises(PipelineError):
                extract(code,limits=limits)

    def test_one_hop_does_not_follow_chain_or_cycle(self):
        model = {'baseCounts':{'1':99,'2':25,'3':5},'persistentIdOverrides':{'1':2,'2':3,'3':1,'4':9}}
        self.assertEqual(25,lookup_research_count(model,1)['count'])
        self.assertEqual(5,lookup_research_count(model,2)['count'])
        self.assertEqual(99,lookup_research_count(model,3)['count'])
        result = lookup_research_count(model,4)
        self.assertFalse(result['available'])
        self.assertEqual(9,result['definitionId'])
        self.assertFalse(result['hasOwnDefinition'])
        self.assertFalse(lookup_research_count(model,99)['overridden'])

    def test_definition_id_and_consumer_availability_are_distinct(self):
        model = {'baseCounts':{'20':3},'persistentIdOverrides':{'10':20}}
        result = lookup_research_count(model,10)
        self.assertEqual({'itemId':10,'definitionId':20,'count':3,'available':True,
                          'overridden':True,'hasOwnDefinition':False},result)
        self.assertEqual(['20'],list(model['baseCounts']))


class ResearchProfileTests(unittest.TestCase):
    def test_unrecognized_binary_or_platform_has_no_game_tables(self):
        meta = SimpleNamespace(reader=SimpleNamespace(data=b'original synthetic fixture'))
        for platform in ('windows','linux','macos','Windows'):
            result = extract_research_model(meta,{},platform=platform)
            self.assertEqual('UNSUPPORTED_PROFILE',result['status'])
            self.assertFalse(result['researchModelComplete'])
            self.assertNotIn('baseCounts',result)
            self.assertNotIn('persistentIdOverrides',result)

    def test_redacted_summary_uses_allowlist(self):
        meta = SimpleNamespace(reader=SimpleNamespace(data=b'original synthetic fixture'))
        model = extract_research_model(meta,{})
        model.update(baseCounts={'41':123},persistentIdOverrides={'42':41},
                     baseOrigins={'41':{'name':'SensitiveSyntheticName'}},
                     overrideCalls=[{'target':41}],nameBindingEvidence={'private':'SensitiveSyntheticName'})
        summary = research_summary(model)
        self.assertNotIn('baseCounts',summary)
        self.assertNotIn('nameBindingEvidence',summary)
        self.assertNotIn('SensitiveSyntheticName',str(summary))
        self.assertFalse(summary['complete'])
        self.assertFalse(summary['publishable'])

    def test_limit_validation(self):
        for name,value in (('rows',0),('instructions',True),('wall_seconds',float('inf')),('wall_seconds',0)):
            with self.subTest(name=name),self.assertRaises(ValueError):
                replace(ResearchLimits(),**{name:value})


class ResearchBoundaryTests(unittest.TestCase):
    def test_unsupported_profile_and_initializer_respect_tiny_output_budget(self):
        tiny=replace(ResearchLimits(),evidence_bytes=1)
        meta=SimpleNamespace(reader=SimpleNamespace(data=b'synthetic'))
        with self.assertRaises(PipelineError):extract_research_model(meta,{},limits=tiny)
        with self.assertRaises(PipelineError):extract(group(10,[2])+b'\x2a',limits=tiny)

    def test_file_deadline_covers_read_metadata_and_type_enumeration(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.bin';source.write_bytes(b'synthetic')
            for phase in ('read','metadata','types'):
                with self.subTest(phase=phase):
                    now=[0.0];meta=SimpleNamespace(reader=SimpleNamespace(data=b'synthetic'))
                    def read(*_):
                        if phase=='read':now[0]=100.0
                        return b'synthetic'
                    def metadata(*_):
                        if phase=='metadata':now[0]=100.0
                        return meta
                    def types(*_):
                        if phase=='types':now[0]=100.0
                        return {}
                    with patch('resource_pipeline.research_semantics.time.monotonic',side_effect=lambda:now[0]), \
                         patch('resource_pipeline.locale_mapping._read_input',side_effect=read), \
                         patch('resource_pipeline.server_semantics._Metadata',side_effect=metadata), \
                         patch('resource_pipeline.server_semantics._types',side_effect=types), \
                         self.assertRaisesRegex(PipelineError,'wall time'):
                        extract_research_semantics(source,limits=replace(ResearchLimits(),wall_seconds=.001))

    def test_cancel_is_checked_before_reading_any_file(self):
        def canceled():raise PipelineError('original cancellation')
        with patch('resource_pipeline.locale_mapping._read_input',side_effect=AssertionError('must not read')), \
             self.assertRaisesRegex(PipelineError,'original cancellation'):
            extract_research_semantics(Path('never-open'),checkpoint=canceled)

    def test_direct_helpers_enforce_their_wall_deadline(self):
        now=[0.0]
        def slow():now[0]=1.0
        tiny=replace(ResearchLimits(),wall_seconds=.001)
        with patch('resource_pipeline.research_semantics.time.monotonic',side_effect=lambda:now[0]), \
             self.assertRaisesRegex(PipelineError,'wall time'):
            parse_research_catalog(b'Original\td\tx',{'Original':1},limits=tiny,checkpoint=slow)
        now[0]=0.0
        with patch('resource_pipeline.research_semantics.time.monotonic',side_effect=lambda:now[0]), \
             self.assertRaisesRegex(PipelineError,'wall time'):
            extract_override_initializer(list(decode_il(group(10,[2])+b'\x2a').values()),_HELPER,
                int_type=_ARRAY,initialize_array=_INIT,read_rva=lambda *_:None,limits=tiny,checkpoint=slow)

    def test_file_reader_rejects_links_and_input_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.bin';source.write_bytes(b'synthetic')
            link=Path(directory)/'link.bin';link.symlink_to(source)
            with self.assertRaisesRegex(PipelineError,'symlink'):extract_research_semantics(link)
            with self.assertRaises(PipelineError):extract_research_semantics(source,limits=replace(ResearchLimits(),input_bytes=1))

    def test_summary_drops_nested_private_fields_and_rejects_noncount_statistics(self):
        model=extract_research_model(SimpleNamespace(reader=SimpleNamespace(data=b'synthetic')), {})
        model['statistics']={'rows':3,'privateNames':['SensitiveSyntheticName']}
        model['profile']['privateNames']=['SensitiveSyntheticName']
        model['sourceReference']['privateNames']=['SensitiveSyntheticName']
        model['input']['privateNames']=['SensitiveSyntheticName']
        model['tableDigests']={'baseCounts':'a'*64,'persistentIdOverrides':'b'*64,'privateNames':['SensitiveSyntheticName']}
        summary=research_summary(model)
        self.assertNotIn('SensitiveSyntheticName',str(summary));self.assertEqual({'rows':3},summary['statistics'])
        model['statistics']['rows']='SensitiveSyntheticName'
        with self.assertRaisesRegex(PipelineError,'statistics'):research_summary(model)


if __name__ == '__main__':
    unittest.main()
