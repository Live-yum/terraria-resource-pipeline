"""Original, non-executable synthetic PE/CLI fixtures; no game binaries/strings."""
import gzip
from dataclasses import replace
import hashlib
import json
import os
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from resource_pipeline.security import PipelineError
from resource_pipeline.server_semantics import SemanticLimits, _Metadata, _languages, _types, extract_server_semantics, main
from resource_pipeline.locale_semantics import embedded_baselines, locale_copy_passes, reconstruct_raw_locale, resolve_locale_source


def synthetic_pe(*, resources=None, pe64=False, bad_signature=False, repeated_types=0, repeated_name='RepeatedFixture', version_fields=None):
    strings = bytearray(b'\0')
    blobs = bytearray(b'\0')
    def string(value):
        offset = len(strings)
        strings.extend(value.encode() + b'\0')
        return offset
    def blob(value):
        offset = len(blobs)
        size = len(value)
        if size < 128:
            blobs.append(size)
        elif size < 16384:
            blobs.extend(struct.pack('>H', size | 0x8000))
        else:
            blobs.extend(struct.pack('>I', size | 0xc0000000))
        blobs.extend(value)
        return offset
    fields, constants, typedefs = [], [], []
    def typedef(namespace, name, entries):
        typedefs.append(struct.pack('<IHHHHH', 0, string(name), string(namespace), 0, len(fields) + 1, 1))
        for name, kind, value in entries:
            flags = 0x8056 if kind else 0x16
            fields.append(struct.pack('<HHH', flags, string(name), blob(bytes((6, kind or 8))) if not bad_signature else blob(b'\x06\x05')))
            if kind:
                payload = value.encode('utf-16-le') if kind == 14 else struct.pack({4:'<b',5:'<B',6:'<h',7:'<H',8:'<i',9:'<I',10:'<q',11:'<Q'}[kind], value)
                constants.append(struct.pack('<HHH', kind, len(fields) << 2, blob(payload)))
    typedef('', '<Module>', [])
    typedef('Terraria', 'Main', version_fields if version_fields is not None else
            [('versionNumber', 14, '2.3.4.5'), ('curRelease', 8, 999)])
    typedef('Terraria.ID', 'ItemID', [('Empty',8,0),('Example',8,1),('Alias',8,1),('Count',8,2),('Dynamic',None,None),('Label',14,'synthetic')])
    typedef('Terraria.ID', 'ArmorIDs', [])
    typedef('', 'Head', [('ExampleHat',5,15)])
    typedef('Terraria.ID', 'NumericID', [('SByte',4,-5),('Byte',5,250),('Int16',6,-200),('UInt16',7,65500),('Int32',8,-100000),('UInt32',9,4000000000),('Int64',10,-9000000000),('UInt64',11,18000000000000000000)])
    if repeated_types:
        # Many TypeDefs reference the same heap offsets, without duplicating
        # their long names in the input. This is an original growth fixture.
        shared_name,shared_namespace=string(repeated_name),string('Fixture')
        typedefs.extend(struct.pack('<IHHHHH',0,shared_name,shared_namespace,0,len(fields)+1,1)
                        for _ in range(repeated_types))
    if resources is None:
        resources = {
            'Terraria.Localization.Content.en-US.Main.json': b'{// fixture\n "ItemName": {"Example": "Synthetic Example",},"Misc":{"Literal":"http://fixture/"},}',
            'Terraria.Localization.Content.zh-Hans.Main.json': '{"ItemName":{"Example":"测试条目"},"Misc":{"OnlyChinese":"值","Ref":"{$Reference.Absent}"}}'.encode(),
            'Other.fixture.dat': b'original fixture bytes',
        }
    resource_data = bytearray()
    manifests = []
    for name, payload in resources.items():
        manifests.append(struct.pack('<IIHH', len(resource_data), 1, string(name), 0))
        resource_data.extend(struct.pack('<I', len(payload)) + payload)
    rows = {
        0: [struct.pack('<HHHHH', 0, string('Synthetic.dll'), 1, 0, 0)],
        2: typedefs, 4: fields, 11: constants,
        32: [struct.pack('<IHHHHIHHH', 0x8004, 2,3,4,5,0,0,string('SyntheticServer'),0)],
        40: manifests, 41: [struct.pack('<HH',5,4)],
    }
    rows = {key:value for key,value in rows.items() if value}
    tables = bytearray(struct.pack('<IBBBBQQ', 0,2,0,0,1,sum(1<<table for table in rows),0))
    for table in sorted(rows):
        tables.extend(struct.pack('<I', len(rows[table])))
    for table in sorted(rows):
        tables.extend(b''.join(rows[table]))
    streams = {'#~':bytes(tables), '#Strings':bytes(strings), '#Blob':bytes(blobs), '#GUID':bytes(range(16))}
    metadata = bytearray(struct.pack('<IHHII', 0x424a5342,1,1,0,12) + b'v4.0.30319\0\0' + struct.pack('<HH',0,len(streams)))
    header_size = len(metadata) + sum(8 + ((len(name) + 1 + 3) & ~3) for name in streams)
    cursor = header_size
    for name, payload in streams.items():
        metadata.extend(struct.pack('<II', cursor, len(payload)))
        encoded = name.encode() + b'\0'
        metadata.extend(encoded + b'\0' * ((-len(encoded)) % 4))
        cursor += len(payload)
    for payload in streams.values():
        metadata.extend(payload)
    metadata_offset, raw_offset, virtual = 0x300, 0x200, 0x2000
    resource_offset = (metadata_offset + len(metadata) + 3) & ~3
    raw_size = ((resource_offset + len(resource_data) - raw_offset + 0x1ff) // 0x200) * 0x200
    output = bytearray(raw_offset + raw_size)
    def put(offset, value):
        output[offset:offset + len(value)] = value
    put(0,b'MZ'); put(0x3c,struct.pack('<I',0x80)); put(0x80,b'PE\0\0')
    optional_size = 240 if pe64 else 224
    put(0x84, struct.pack('<HHIIIHH',0x8664 if pe64 else 0x14c,1,0,0,0,optional_size,0x102))
    opt = 0x98
    put(opt,struct.pack('<H',0x20b if pe64 else 0x10b))
    directory = opt + (112 if pe64 else 96)
    put(directory - 4,struct.pack('<I',16))
    put(directory + 14*8,struct.pack('<II',virtual,72))
    section = opt + optional_size
    put(section,b'.text\0\0\0' + struct.pack('<IIII',raw_size,virtual,raw_size,raw_offset))
    put(raw_offset,struct.pack('<IHHIIIIII',72,2,5,virtual + metadata_offset - raw_offset,len(metadata),1,0,virtual + resource_offset - raw_offset,len(resource_data)))
    put(metadata_offset,metadata)
    put(resource_offset,resource_data)
    return bytes(output)


class ServerSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input = self.root / 'synthetic.exe'

    def tearDown(self):
        self.temp.cleanup()

    def extract(self, payload=None, limits=SemanticLimits()):
        self.input.write_bytes(payload if payload is not None else synthetic_pe())
        before = self.input.read_bytes()
        result = extract_server_semantics(self.input, limits)
        self.assertEqual(before, self.input.read_bytes())
        return result

    def test_constants_localizations_and_version_are_derived_without_execution(self):
        data = synthetic_pe()
        result = self.extract(data)
        self.assertEqual('PARTIAL',result['status'])
        self.assertFalse(result['executedInput'])
        self.assertFalse(result['complete'])
        self.assertFalse(result['publishable'])
        self.assertFalse(result['gameVersionEvidence']['trusted'])
        self.assertEqual('2.3.4.5',result['assembly']['version'])
        self.assertEqual(hashlib.sha256(data).hexdigest(),result['input']['sha256'])
        self.assertEqual(len(data),result['input']['bytes'])
        self.assertEqual(1,result['ids']['ItemID']['Example'])
        self.assertEqual(['Example','Alias'],result['aliases']['ItemID']['1'])
        self.assertEqual(2,result['literalCounts']['ItemID'])
        self.assertEqual(15,result['ids']['ArmorIDs+Head']['ExampleHat'])
        self.assertEqual('Synthetic Example',result['localization']['en-US']['strings']['ItemName.Example'])
        self.assertEqual('http://fixture/',result['localization']['en-US']['strings']['Misc.Literal'])
        self.assertEqual('测试条目',result['localization']['zh-Hans']['strings']['ItemName.Example'])
        self.assertEqual(3,len(result['resources']))
        self.assertEqual({'Dynamic','Label'}, {row['field'] for row in result['unsupportedFields']})
        self.assertEqual(3,len(result['idFamilies']['items']['records']))
        self.assertEqual(1,len(result['idFamilies']['items']['countConstants']))
        self.assertEqual(['Misc.Literal'],result['localeDiagnostics']['missingChineseKeys'])
        self.assertTrue(result['localeDiagnostics']['fallbackEvaluated'])
        self.assertFalse(result['localeDiagnostics']['binaryLoaderEquivalenceVerified'])
        self.assertEqual([{'key':'Misc.Ref','reference':'Reference.Absent'}],result['localeDiagnostics']['unresolvedReferences']['zh-Hans'])
        self.assertTrue(all(not row['complete'] for row in result['coverage'].values()))
        proof = result['idEvidence']['ItemID']['Example']
        self.assertEqual(1,struct.unpack_from('<i',data,proof['valueBlobOffset'])[0])
        self.assertEqual((2,3,4,5),struct.unpack_from('<HHHH',data,result['gameVersionEvidence']['versionFileOffset']))
        resource = result['localization']['en-US']['resources'][0]
        self.assertEqual(resource['sha256'],hashlib.sha256(data[resource['dataOffset']:resource['dataOffset'] + resource['bytes']]).hexdigest())

    def test_all_integral_widths_and_pe32plus(self):
        result = self.extract(synthetic_pe(pe64=True))
        self.assertEqual({'SByte':-5,'Byte':250,'Int16':-200,'UInt16':65500,'Int32':-100000,'UInt32':4000000000,'Int64':-9000000000,'UInt64':18000000000000000000},result['ids']['NumericID'])

    def test_gzip_zlib_and_utf8_bom_resources(self):
        raw = b'\xef\xbb\xbf{"Fixture":{"Key":"original value"}}'
        for suffix, payload, compression in [('.json.gz',gzip.compress(raw),'gzip'),('.json.zlib',zlib.compress(raw),'zlib'),('.json',raw,'none')]:
            with self.subTest(compression=compression):
                result = self.extract(synthetic_pe(resources={'Terraria.Localization.Content.en-US.Main' + suffix:payload}))
                self.assertEqual('original value',result['localization']['en-US']['strings']['Fixture.Key'])
                self.assertEqual(compression,result['resources'][0]['compression'])

    def test_resource_decoder_refuses_bombs_truncation_and_concatenation(self):
        name = 'Terraria.Localization.Content.en-US.Main.json.gz'
        for payload in [gzip.compress(b'{"a":"' + b'x'*20000 + b'"}'),gzip.compress(b'{}')[:-3],gzip.compress(b'{}') + gzip.compress(b'{}')]:
            with self.subTest(length=len(payload)):
                result = self.extract(synthetic_pe(resources={name:payload}),SemanticLimits(resource_file_bytes=1024))
                self.assertEqual('REJECTED_RESOURCE',result['resources'][0]['status'])
                self.assertEqual(1,result['ids']['ItemID']['Example'])

    def test_strict_localization_validation(self):
        name = 'Terraria.Localization.Content.en-US.Main.json'
        for payload in [b'{"a":1}',b'[]',b'/*bad',b'{"a":NaN}',b'\xff',b'['*70 + b']'*70]:
            with self.subTest(payload=payload[:20]):
                result = self.extract(synthetic_pe(resources={name:payload}))
                self.assertEqual('REJECTED_RESOURCE',result['resources'][0]['status'])
                self.assertFalse(result['complete'])

    def test_duplicate_keys_follow_typed_dictionary_last_assignment_with_trace(self):
        name = 'Terraria.Localization.Content.en-US.Main.json'
        result = self.extract(synthetic_pe(resources={name:b'{"C":{"A":"first","A":"last","Safe":"unique"}}'}))
        self.assertEqual({'C.A':'last','C.Safe':'unique'},result['localization']['en-US']['strings'])
        proof = result['localeDiagnostics']['duplicateSelectionEvidence']['en-US'][0]
        self.assertEqual('/C/A',proof['jsonPointer'])
        self.assertEqual([0,1],proof['occurrenceOrdinals'])
        self.assertEqual(1,proof['selectedOrdinal'])
        self.assertEqual(2,len(proof['valueSha256s']))
        self.assertFalse(result['complete'])
        result = self.extract(synthetic_pe(resources={name:b'{"C":{"A":"old"},"C":{"B":"new"}}'}))
        self.assertEqual({'C.B':'new'},result['localization']['en-US']['strings'])
        source=resolve_locale_source(result['languages'],result['resources'],result['localization']['en-US']['baseline']['keyEvidence']['C.B'])
        self.assertEqual(1,source['categoryOrdinal'])

    def test_equal_duplicate_values_are_proven_and_conflicts_select_last(self):
        name = 'Terraria.Localization.Content.en-US.Main.json'
        result = self.extract(synthetic_pe(resources={name:b'{"C":{"A":"x","A":"x"}}'}))
        self.assertEqual({'C.A':'x'},result['localization']['en-US']['strings'])
        self.assertEqual(1,len(result['localeDiagnostics']['identicalDuplicateKeys']['en-US']))
        result = self.extract(synthetic_pe(resources={name:b'{"C":{"A":"x","A":"x","A":"y"}}'}))
        self.assertEqual({'C.A':'y'},result['localization']['en-US']['strings'])
        self.assertEqual(3,result['localeDiagnostics']['duplicateSelectionEvidence']['en-US'][0]['occurrences'])

    def test_rejected_locale_preserves_other_resources_and_id_evidence(self):
        result = self.extract(synthetic_pe(resources={
            'Terraria.Localization.Content.en-US.Bad.json': b'{"bad":4}',
            'Terraria.Localization.Content.zh-Hans.Good.json': b'{"C":{"Safe":"unique"}}',
        }))
        self.assertEqual('REJECTED_RESOURCE',result['resources'][0]['status'])
        self.assertIn('dataOffset',result['resources'][0])
        self.assertIn('sha256',result['resources'][0])
        self.assertEqual({'C.Safe':'unique'},result['localization']['zh-Hans']['strings'])
        self.assertEqual(1,result['ids']['ItemID']['Example'])
        for limits in (SemanticLimits(locale_keys=1),SemanticLimits(decoded_total_bytes=10)):
            result = self.extract(limits=limits)
            self.assertEqual('RESOURCE_BUDGET_EXHAUSTED',result['resources'][0]['errorCode'])
            self.assertEqual('SKIPPED_RESOURCE_BUDGET',result['resources'][1]['status'])
            self.assertEqual(1,result['ids']['ItemID']['Example'])

    def test_duplicate_keys_across_resources_follow_metadata_order(self):
        result = self.extract(synthetic_pe(resources={
            'Terraria.Localization.Content.en-US.Z.json': b'{"C":{"A":"first"}}',
            'Terraria.Localization.Content.en-US.A.json': b'{"C":{"A":"last"}}',
        }))
        self.assertEqual({'C.A':'last'},result['localization']['en-US']['strings'])
        self.assertEqual(1,len(result['localeDiagnostics']['duplicateSelectionEvidence']['en-US']))
        choice = result['localization']['en-US']['baseline']['resourceOverrides'][0]
        self.assertEqual(0,resolve_locale_source(result['languages'],result['resources'],choice[0])['resourceOrder'])
        self.assertEqual(1,resolve_locale_source(result['languages'],result['resources'],choice[1])['resourceOrder'])

    def test_compact_locale_evidence_reconstructs_raw_order_and_full_model(self):
        resources={
            'Terraria.Localization.Content.en-US.Z.json': b'{"C":{"A":"first","A":"last","Base":"base","Copy":"{$C.Base}","Missing":"{$No.Key}","V$One":"v1"},"Gone":{"Old":"discarded"},"Gone":{},"Dot.C":{"K":"collides"},"Dot":{"C.K":"selected"}}',
            'Terraria.Localization.Content.en-US.A.json': b'{"C":{"A":"second-resource","Keep":"fallback","V$One":"v2"}}',
            'Terraria.Localization.Content.zh-Hans.Main.json': b'{"C":{"A":"target","Base":"target-base","New":"{$C.Base}","V$Two$Ignored":"target-variant","Loop":"{$C.Loop}"}}',
            'Terraria.Localization.Content.fr-FR.Main.json': b'{"C":{"A":"bonjour"}}',
        }
        data=synthetic_pe(resources=resources)
        languages,inventory,raw,_,_=_languages(_Metadata(data,SemanticLimits()))
        expanded=embedded_baselines(languages,inventory)
        result=self.extract(data)
        self.assertEqual(2,result['schemaVersion'])
        self.assertEqual(2,result['localeEvidence']['schemaVersion'])
        self.assertFalse(result['localeEvidence']['binaryLoaderEquivalenceVerified'])
        # Source values omitted by duplicate/category selection still survive.
        first=result['languages'][next(iter(resources))]['document']
        self.assertIn('discarded',[row[3] for row in first['entries']])
        self.assertIn('first',[row[3] for row in first['entries']])
        self.assertEqual(2,first['categories'].count('Gone'))
        for locale,value in result['localization'].items():
            reconstructed=reconstruct_raw_locale(result['languages'],result['resources'],value['rawSource']['resourceRefs'])
            self.assertEqual(raw[locale],reconstructed)
            from resource_pipeline.security import canonical_json
            self.assertEqual(hashlib.sha256(canonical_json(reconstructed)).hexdigest(),value['rawSource']['canonicalStringsSha256'])
            self.assertEqual(len(reconstructed),value['rawSource']['keyCount'])
            self.assertNotIn('rawStrings',value)
            self.assertNotIn('strings',value['baseline'])
            self.assertEqual('/localization/'+locale+'/strings',value['baseline']['stringsRef'])
            old=expanded[locale];new=value['baseline']
            self.assertEqual(old['strings'],value['strings'])
            self.assertEqual(old['copyPasses'],locale_copy_passes(result,locale))
            for key in ('fallbackKeys','loadErrors','status','binaryLoaderEquivalenceVerified','runtimeResourceOrderVerified','dynamicVariableTextEvaluated','complete'):
                self.assertEqual(old[key],new[key])
            self.assertEqual(old['keyEvidence'],{key:resolve_locale_source(result['languages'],result['resources'],ref) for key,ref in new['keyEvidence'].items()})
            self.assertEqual(len(old['resourceOverrides']),len(new['resourceOverrides']))
            for before,after in zip(old['resourceOverrides'],new['resourceOverrides']):
                self.assertEqual(before['previous'],resolve_locale_source(result['languages'],result['resources'],after[0]))
                self.assertEqual(before['selected'],resolve_locale_source(result['languages'],result['resources'],after[1]))
            self.assertEqual(old['variants'],{key:{**variant,'evidence':resolve_locale_source(result['languages'],result['resources'],variant['evidence'])} for key,variant in new['variants'].items()})
        # The identical fallback copy pass is represented once across cultures.
        refs=[value['baseline']['copyPassRefs'][0] for value in result['localization'].values()]
        self.assertEqual(1,len(set(refs)))

    def test_compact_order_survives_canonical_json_key_sorting_and_bad_resource(self):
        data=synthetic_pe(resources={
            'Terraria.Localization.Content.en-US.Z.json': b'{"C":{"Z":"{$C.A}","A":"{$C.B}","B":"value"}}',
            'Terraria.Localization.Content.en-US.Bad.json': b'{"bad":7}',
            'Terraria.Localization.Content.en-US.A.json': b'{"C":{"A":"raw-only-after-bad-resource"}}',
            'Terraria.Localization.Content.zh-Hans.Main.json': b'{"C":{"B":"target"}}',
        })
        result=self.extract(data)
        from resource_pipeline.security import canonical_json
        reloaded=json.loads(canonical_json(result))
        for locale,value in reloaded['localization'].items():
            raw=reconstruct_raw_locale(reloaded['languages'],reloaded['resources'],value['rawSource']['resourceRefs'])
            self.assertEqual(value['rawSource']['canonicalStringsSha256'],hashlib.sha256(canonical_json(raw)).hexdigest())
        self.assertEqual('value',reloaded['localization']['en-US']['strings']['C.A'])
        self.assertEqual('raw-only-after-bad-resource',reconstruct_raw_locale(reloaded['languages'],reloaded['resources'],reloaded['localization']['en-US']['rawSource']['resourceRefs'])['C.A'])
        self.assertEqual('CULTURE_LOAD_STOPPED_AT_BAD_RESOURCE',reloaded['localization']['en-US']['baseline']['loadErrors'][0]['code'])

    def test_wrong_signature_is_classified_not_invented(self):
        result = self.extract(synthetic_pe(bad_signature=True))
        self.assertNotIn('ItemID',result['ids'])
        self.assertTrue(any(row.get('reason') == 'unsupported-constant-type-or-signature' for row in result['unsupportedFields']))

    def test_checkpoint_cancellation_stops_read_metadata_and_language_without_output(self):
        self.input.write_bytes(synthetic_pe())
        original = self.input.read_bytes()
        calls = []
        extract_server_semantics(self.input, checkpoint=lambda: calls.append(None))
        self.assertGreater(len(calls), 100)
        for limit in (2, 50, len(calls) - 1):
            counter = [0]
            def cancel():
                counter[0] += 1
                if counter[0] == limit:
                    raise PipelineError('cancelled fixture extraction')
            with self.subTest(limit=limit), self.assertRaisesRegex(PipelineError, 'cancelled fixture'):
                extract_server_semantics(self.input, checkpoint=cancel)
            self.assertEqual(original,self.input.read_bytes())
            self.assertEqual([self.input],list(self.root.iterdir()))

    def test_size_rows_and_key_budgets(self):
        for limits in [SemanticLimits(input_bytes=10), SemanticLimits(table_rows=1),SemanticLimits(metadata_bytes=10)]:
            with self.subTest(limits=limits),self.assertRaises(PipelineError):
                self.extract(limits=limits)

    def test_global_type_count_is_checked_before_reading_or_allocating_types(self):
        data=synthetic_pe(resources={},repeated_types=100,repeated_name='X'*256)
        meta=_Metadata(data,replace(SemanticLimits(),type_count=8))
        with patch.object(meta,'row',side_effect=AssertionError('must reject before row traversal')):
            with self.assertRaisesRegex(PipelineError,'TypeDef count limit'):_types(meta)
        self.assertEqual({},meta._string_cache)
        with self.assertRaisesRegex(PipelineError,'TypeDef count limit'):
            self.extract(data,replace(SemanticLimits(),type_count=8))

    def test_metadata_string_cache_charges_heap_offsets_once_and_bounds_names(self):
        data=synthetic_pe(resources={},repeated_types=100,repeated_name='🦖'*64)
        meta=_Metadata(data,SemanticLimits())
        rows=_types(meta)
        self.assertEqual(1,len({id(rows[n]['name']) for n in range(7,107)}))
        name_index=meta.row(2,7)[0][1]
        before=(meta._decoded_name_bytes,len(meta._string_cache))
        with patch.object(meta.streams['#Strings'],'take',side_effect=AssertionError('cache hit must not decode')):
            self.assertIs(rows[7]['name'],meta.string(name_index))
        self.assertEqual(before,(meta._decoded_name_bytes,len(meta._string_cache)))
        limited=_Metadata(data,replace(SemanticLimits(),metadata_names_bytes=255))
        with self.assertRaisesRegex(PipelineError,'decoded metadata name byte limit'):
            limited.string(name_index)
        self.assertEqual({},limited._string_cache)
        self.assertEqual(0,limited._decoded_name_bytes)
        exact=_Metadata(data,replace(SemanticLimits(),metadata_names_bytes=256))
        self.assertEqual('🦖'*64,exact.string(name_index))
        self.assertEqual(256,exact._decoded_name_bytes)
        namespace_index=meta.row(2,7)[0][2]
        with self.assertRaisesRegex(PipelineError,'decoded metadata name byte limit'):exact.string(namespace_index)
        self.assertEqual(1,len(exact._string_cache))

    def test_metadata_cache_entry_cap_handles_empty_and_tiny_names(self):
        data=synthetic_pe(resources={})
        meta=_Metadata(data,replace(SemanticLimits(),metadata_string_entries=2))
        # Index zero and the module's empty namespace are distinct valid heap
        # offsets containing empty strings, so bytes alone cannot bound entries.
        namespace=meta.row(2,1)[0][2]
        self.assertNotEqual(0,namespace)
        self.assertEqual('',meta.string(0));self.assertEqual('',meta.string(namespace))
        with self.assertRaisesRegex(PipelineError,'metadata string entry limit'):
            meta.string(meta.row(2,1)[0][1])
        self.assertEqual(2,len(meta._string_cache))
        self.assertEqual(0,meta._decoded_name_bytes)

    def test_shared_type_names_still_pay_full_expanded_name_budget(self):
        data=synthetic_pe(resources={},repeated_types=100,repeated_name='X'*256)
        meta=_Metadata(data,replace(SemanticLimits(),type_fullname_bytes=4096))
        with self.assertRaisesRegex(PipelineError,'type full-name byte limit'):_types(meta)
        self.assertLess(meta._decoded_name_bytes,1024)
        # Exact-size acceptance, including multibyte names and nested '+' names.
        unicode_data=synthetic_pe(resources={},repeated_types=3,repeated_name='汉字')
        types=_types(_Metadata(unicode_data,SemanticLimits()))
        total=sum(len(row['fullName'].encode('utf-8')) for row in types.values())
        self.assertEqual(types,_types(_Metadata(unicode_data,replace(SemanticLimits(),type_fullname_bytes=total))))
        with self.assertRaisesRegex(PipelineError,'type full-name byte limit'):
            _types(_Metadata(unicode_data,replace(SemanticLimits(),type_fullname_bytes=total-1)))
        with self.assertRaisesRegex(PipelineError,'type full-name byte limit'):
            self.extract(data,replace(SemanticLimits(),type_fullname_bytes=4096))

    def test_cancellation_checks_cached_reads_and_nested_name_resolution(self):
        meta=_Metadata(synthetic_pe(resources={}),SemanticLimits())
        meta.string(0)
        def cancel():raise PipelineError('cancelled cached-name inspection')
        meta.checkpoint=cancel
        with self.assertRaisesRegex(PipelineError,'cancelled cached-name'):meta.string(0)
        meta=_Metadata(synthetic_pe(resources={}),SemanticLimits())
        original_row=meta.row
        def row(table,rid):
            value=original_row(table,rid)
            if table==2 and rid==meta.rows[2]:
                # Keep this row's string decoding live, then cancel at resolve.
                original_string=meta.string
                reads=[0]
                def string(index):
                    result=original_string(index);reads[0]+=1
                    if reads[0]==2:meta.checkpoint=cancel
                    return result
                meta.string=string
            return value
        meta.row=row
        with self.assertRaisesRegex(PipelineError,'cancelled cached-name'):_types(meta)

    def test_truncated_and_non_managed_inputs_fail_closed(self):
        data = synthetic_pe()
        for length in (0,1,2,32,64,128,256,len(data)-1):
            with self.subTest(length=length),self.assertRaises(PipelineError):
                self.extract(data[:length])
        for payload in (b'not executable',b'\x7fELF' + bytes(100)):
            with self.assertRaises(PipelineError):
                self.extract(payload)

    def test_corrupt_resource_length_is_rejected(self):
        data = bytearray(synthetic_pe())
        meta = _Metadata(bytes(data),SemanticLimits())
        struct.pack_into('<I',data,meta.resources.start,0xffffffff)
        with self.assertRaises(PipelineError):
            self.extract(bytes(data))

    def test_corrupt_metadata_tables_stream_overlap_and_cycles(self):
        original = synthetic_pe()
        meta = _Metadata(original,SemanticLimits())
        patches = [
            (meta.streams['#~'].start + 8, '<Q', 1<<63),
            (meta.streams['#~'].start + 24, '<I', 0xffffffff),
            (meta.layout[41][0], '<H', 4),
            (meta.layout[2][0] + 10, '<H', 65535),
        ]
        for offset, fmt, value in patches:
            data = bytearray(original)
            struct.pack_into(fmt,data,offset,value)
            with self.subTest(offset=offset),self.assertRaises(PipelineError):
                self.extract(bytes(data))
        data = bytearray(original)
        # First stream begins inside metadata headers.
        struct.pack_into('<I',data,meta.metadata_offset + 32,0)
        with self.assertRaisesRegex(PipelineError,'Overlapping'):
            self.extract(bytes(data))

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO test')
    def test_fifo_input_is_rejected_without_waiting_for_a_writer(self):
        fifo = self.root / 'pipe.exe'
        os.mkfifo(fifo)
        with self.assertRaisesRegex(PipelineError, 'regular file'):
            extract_server_semantics(fifo)

    def test_symlink_input_and_parent_refused(self):
        self.input.write_bytes(synthetic_pe())
        link = self.root / 'linked.exe'
        link.symlink_to(self.input)
        with self.assertRaisesRegex(PipelineError,'symlinks'):
            extract_server_semantics(link)
        parent = self.root / 'linked-directory'
        parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(PipelineError,'symlinks'):
            extract_server_semantics(parent / self.input.name)

    def test_invalid_assembly_name_is_a_controlled_parse_error(self):
        data = bytearray(synthetic_pe())
        meta = _Metadata(bytes(data),SemanticLimits())
        assembly, _ = meta.row(32,1)
        data[meta.streams['#Strings'].start + assembly[7]] = 0xff
        with self.assertRaises(PipelineError):
            self.extract(bytes(data))

    def test_index_widths_follow_heap_and_coded_table_thresholds(self):
        meta = _Metadata(synthetic_pe(),SemanticLimits())
        for heap in ('s','g','b'):
            self.assertEqual(2,meta.width(heap))
        meta.heap_sizes = 7
        for heap in ('s','g','b'):
            self.assertEqual(4,meta.width(heap))
        meta.rows[4] = 65535
        self.assertEqual(2,meta.width('t4'))
        meta.rows[4] = 65536
        self.assertEqual(4,meta.width('t4'))
        meta.rows[4] = 16383
        self.assertEqual(2,meta.width('cHasConstant'))
        meta.rows[4] = 16384
        self.assertEqual(4,meta.width('cHasConstant'))
        meta.rows[4] = 2048
        self.assertEqual(4,meta.width('cHasCustomAttribute'))

    def test_redaction_has_no_original_strings_or_field_names(self):
        script = Path(__file__).parents[1] / 'scripts/ci_server_semantics.py'
        spec = importlib.util.spec_from_file_location('ci_server_semantics',script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        evidence = module.redact(self.extract())
        text = json.dumps(evidence,ensure_ascii=False)
        for raw in ('Synthetic Example','测试条目','ItemName.Example','ExampleHat','OnlyChinese','v4.0.30319'):
            self.assertNotIn(raw,text)
        self.assertFalse(evidence['rawStringsIncluded'])
        self.assertFalse(evidence['rawBinaryIncluded'])
        self.assertFalse(evidence['publishable'])
        self.assertEqual(3,evidence['families']['items']['literalRecordCount'])
        self.assertEqual(2,evidence['locales']['en-US']['keyCount'])
        self.assertEqual(1,evidence['families']['items']['numericSamples'][1]['value'])
        mixed = self.extract(synthetic_pe(resources={
            'Terraria.Localization.Content.en-US.Bad.json': b'{"bad":4}',
            'Terraria.Localization.Content.en-US.Good.json': b'{"C":{"A":"valid"}}',
        }))
        redacted_mixed = module.redact(mixed)
        self.assertEqual(1,len(redacted_mixed['locales']['en-US']['resources']))
        self.assertTrue(any(row['status']=='REJECTED_RESOURCE' for row in redacted_mixed['resourceDiagnostics']))
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}):
            with self.assertRaisesRegex(PipelineError,'restricted'):
                module.run(self.root,self.root / 'redacted.json')

    def test_cli_rejects_output_symlink(self):
        self.input.write_bytes(synthetic_pe())
        target = self.root / 'target.txt'
        target.write_text('untouched')
        link = self.root / 'out.json'
        link.symlink_to(target)
        self.assertEqual(1,main([str(self.input),str(link)]))
        self.assertEqual('untouched',target.read_text())

    def test_cli_writes_private_evidence_without_overwriting_source(self):
        self.input.write_bytes(synthetic_pe())
        output = self.root / 'evidence.json'
        self.assertEqual(0,main([str(self.input),str(output)]))
        self.assertFalse(json.loads(output.read_text())['publishable'])
        self.assertEqual(1,main([str(self.input),str(self.input)]))
        self.assertEqual(b'MZ',self.input.read_bytes()[:2])


if __name__ == '__main__':
    unittest.main()
