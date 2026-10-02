"""Original synthetic metadata, IL shapes and text; no game source/assets."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from resource_pipeline import locale_mapping as mapping
from resource_pipeline.locale_mapping import MappingBound, MappingLimits, _eligible_fields, _inspect_rules, _map_fields, extract_locale_mapping
from resource_pipeline.real_producer import RawEvidenceProducer
from resource_pipeline.security import PipelineError
from resource_pipeline.static_il import Instruction


SPEC=('items','localizedNames','ItemName','Terraria.ID.ItemID',6,'_itemNameCache')


def fixture_server(strings=None):
    return {'schemaVersion':2,'localization':{'en-US':{'strings':strings or {},'baseline':{'copyPassRefs':[0],'loadErrors':[]}}},
            'localeEvidence':{'schemaVersion':2,'copyPasses':[{'errors':[],'missingReferences':[],'copyEvidence':{}}]}}


def fixture_fields(entries):
    return {'items':[{'id':number,'name':name,'fieldToken':f'0x{0x04000001+n:08x}'} for n,(number,name) in enumerate(entries)]}


def map_fixture(entries,strings=None,*,unresolved=None,server=None,count=8,limits=MappingLimits(),checkpoint=lambda:None):
    fields=fixture_fields(entries)
    with patch.object(mapping,'SPECS',(SPEC,)):
        return _map_fields(server or fixture_server(strings),fields,{'items':unresolved or []},
                           {'counts':{SPEC[3]:{'value':count}}},limits,checkpoint)['ItemName']


class LocaleMappingTests(unittest.TestCase):
    def test_valid_key_missing_fallback_and_exact_reference_scope(self):
        result=map_fixture([(1,'First'),(2,'Missing')],{'ItemName.First':'Original fixture'})
        self.assertEqual(2,result['provenStaticKeyMappings'])
        self.assertEqual('BASELINE_TEXT_REFERENCE',result['records'][0]['locales']['en-US'])
        self.assertEqual('KEY_TEXT_FALLBACK',result['records'][1]['locales']['en-US'])
        self.assertEqual('ItemName.Missing',result['records'][1]['key'])
        self.assertFalse(result['complete'])
        self.assertIn('binary locale-loader equivalence',result['pending'])

    def test_zero_negative_count_boundary_alias_and_unfilled_slots(self):
        result=map_fixture([(-1,'Negative'),(0,'None'),(1,'A'),(1,'Alias'),(4,'Last'),(5,'CountLike')],count=5)
        statuses={row['id']:row['status'] for row in result['records']}
        self.assertEqual('PENDING_NEGATIVE_ID_RULE',statuses[-1])
        self.assertEqual('PROVEN_EMPTY_NAME',statuses[0])
        self.assertEqual('PENDING_REFLECTION_ALIAS_ORDER',statuses[1])
        self.assertEqual('PROVEN_STATIC_KEY_MAPPING',statuses[4])
        self.assertEqual('OUTSIDE_CACHE_DOMAIN',statuses[5])
        self.assertEqual([2,3],result['unpopulatedCacheIds'])
        self.assertEqual('INITIAL_EMPTY_RETAINED',result['unpopulatedCacheOutcome'])
        self.assertEqual(['ItemName.A','ItemName.Alias'],next(row for row in result['records'] if row['id']==1)['keyCandidates'])

    def test_unknown_static_id_and_invalid_none_never_choose_a_winner(self):
        result=map_fixture([(1,'A')],unresolved=[{'name':'RuntimeId'}])
        self.assertEqual('PENDING_NONLITERAL_ID_COLLISION',result['records'][0]['status'])
        self.assertEqual('PENDING_NONLITERAL_FIELDS',result['unpopulatedCacheOutcome'])
        result=map_fixture([(-1,'None'),(1,'A')])
        self.assertTrue(all(row['status']=='PENDING_INVALID_NONE_FIELD' for row in result['records']))

    def test_locale_errors_dynamic_values_and_transitive_copy_gaps(self):
        server=fixture_server({'ItemName.A':'plain','ItemName.B':'plain','ItemName.C':'{Variable}', 'ItemName.D':'{$Unresolved.Reference}'})
        proof=server['localeEvidence']['copyPasses'][0]
        proof['missingReferences']=[{'key':'C.Source','reference':'No.Key'}]
        proof['copyEvidence']={'ItemName.B':{'references':['C.Source'],'stabilized':True}}
        result=map_fixture([(1,'A'),(2,'B'),(3,'C'),(4,'D')],server=server)
        codes=[row['locales']['en-US'] for row in result['records']]
        self.assertEqual(['BASELINE_TEXT_REFERENCE','PENDING_COPY_EVIDENCE','PENDING_DYNAMIC_TEMPLATE','PENDING_COPY_REFERENCE'],codes)
        server['localization']['en-US']['baseline']['loadErrors']=[{'code':'CULTURE_LOAD_STOPPED_AT_BAD_RESOURCE'}]
        result=map_fixture([(1,'A')],server=server)
        self.assertEqual('PENDING_LOCALE_LOAD',result['records'][0]['locales']['en-US'])

    def test_tooltip_base_does_not_claim_none_from_missing_key_or_rendered_lines(self):
        spec=('items','tooltipBaseStrings','ItemTooltip','Terraria.ID.ItemID',6,'_itemTooltipCache')
        with patch.object(mapping,'SPECS',(spec,)):
            result=_map_fields(fixture_server({'ItemTooltip.A':'original base'}),fixture_fields([(0,'None'),(1,'A'),(2,'Missing')]),
                {'items':[]},{'counts':{SPEC[3]:{'value':5}}},MappingLimits(),lambda:None)['ItemTooltip']
        self.assertEqual('PROVEN_TOOLTIP_NONE',result['records'][0]['status'])
        self.assertEqual('TOOLTIP_BASE_REFERENCE',result['records'][1]['locales']['en-US'])
        self.assertEqual('MISSING_TOOLTIP_BASE',result['records'][2]['locales']['en-US'])
        self.assertIn('HasValue/EnglishValue initialization history',result['pending'])

    def test_output_budget_reports_unattempted_ids_without_silent_truncation(self):
        result=map_fixture([(n,'x'*1024+str(n)) for n in range(1,101)],count=200,limits=replace(MappingLimits(),output_bytes=16*1024))
        self.assertEqual('MAPPING_OUTPUT_BUDGET',result['diagnostic'])
        self.assertGreater(result['unattemptedCount'],0)
        self.assertEqual(100,len(result['records'])+result['unattemptedCount'])
        self.assertEqual(result['unattemptedCount'],len(result['unattemptedIds']))

    def test_cancellation_propagates_from_value_and_size_preflight(self):
        def cancel():raise PipelineError('cancel original fixture')
        with self.assertRaisesRegex(PipelineError,'cancel original fixture'):map_fixture([(1,'A')],checkpoint=cancel)

    def test_unreviewed_input_ignores_uploaded_rule_and_completeness_claims(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source.exe';path.write_bytes(b'original inert fixture')
            evidence=fixture_server();evidence.update(input={'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},
                complete=True,publishable=True,ruleBinding={'status':'REVIEWED_FIXED_INPUT_STATIC_RULES'})
            result=extract_locale_mapping(path,evidence)
            self.assertEqual('PENDING_UNREVIEWED_INPUT',result['ruleBinding']['status'])
            self.assertEqual({},result['channels']);self.assertFalse(result['complete']);self.assertFalse(result['publishable'])
            evidence['input']['sha256']='0'*64
            self.assertEqual('MAPPING_SOURCE_BINDING_MISMATCH',extract_locale_mapping(path,evidence)['diagnostic'])

    def test_deadline_source_bounds_and_symlink_guards(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source.exe';path.write_bytes(b'original fixture')
            with patch.object(mapping.time,'monotonic',side_effect=[0,31]):
                result=extract_locale_mapping(path,{})
            self.assertEqual('MAPPING_TIME_LIMIT',result['diagnostic'])
            with self.assertRaisesRegex(PipelineError,'bounded regular file'):
                extract_locale_mapping(path,{},limits=replace(MappingLimits(),input_bytes=1))
            link=Path(folder)/'link.exe';link.symlink_to(path)
            with self.assertRaisesRegex(PipelineError,'symlinks'):extract_locale_mapping(link,{})
        for options in ({'output_bytes':1},{'fields':0},{'wall_seconds':121}):
            with self.assertRaises(ValueError):MappingLimits(**options)


class MetadataEligibilityTests(unittest.TestCase):
    def test_public_static_exact_short_literal_filter_and_nonliteral_caveat(self):
        class Meta:
            rows={11:4}
            field_rows=[(0x56,1,6),(0x51,2,6),(0x56,3,8),(0x46,4,6),(0x36,5,6),(0x16,6,6)]
            def row(self,table,rid):
                if table==4:return self.field_rows[rid-1],100+rid
                if table==11:return ((8 if rid==3 else 6),rid<<2,200+rid),500+rid
                raise AssertionError(table)
            def blob(self,index):
                if index in (6,8):return bytes((6,index)),300+index
                value=index-200
                return value.to_bytes(4 if value==3 else 2,'little'),400+index
            def string(self,index):return ['A','Private','WrongType','Instance','Count','RuntimeId'][index-1]
        class Image:
            meta=Meta();checkpoint=staticmethod(lambda:None)
            types={1:{'fullName':SPEC[3],'firstField':1,'lastField':7}}
        with patch.object(mapping,'SPECS',(SPEC,)):
            groups,unknown=_eligible_fields(Image(),MappingLimits())
        self.assertEqual([1],[row['id'] for row in groups['items']])
        self.assertEqual(['RuntimeId'],[row['name'] for row in unknown['items']])
        with patch.object(mapping,'SPECS',(SPEC,)),self.assertRaisesRegex(MappingBound,'MAPPING_FIELD_LIMIT'):
            _eligible_fields(Image(),replace(MappingLimits(),fields=1))


class StaticRuleBindingTests(unittest.TestCase):
    def image(self):
        class Image:
            element=6;wrong_hash=False;branch=False;early_return=False
            def method(self,name):
                proof={'methodToken':'0x06000001','ilSha256':'wrong' if self.wrong_hash else 'code','signatureSha256':'signature'}
                if name=='Rule':return [],proof
                if name=='Terraria.Lang::InitializeLegacyLocalization':
                    rows=[(0x72,3),(0x7e,2),(0x16,None),(0x28,0x2b000001)]
                elif name=='Terraria.Lang::.cctor':rows=[(0x7e,1),(0x8d,4),(0x80,2)]
                else:rows=([(0x2b,1)] if self.branch else [])+([(0x2a,None)] if self.early_return else [])+[(0x20,8),(0x80,1),(0x2a,None)]
                return [Instruction(n,op,arg,n+1) for n,(op,arg) in enumerate(rows)],proof
            def field_name(self,token):return SPEC[3]+'::Count' if token==1 else 'Terraria.Lang::_itemNameCache'
            def user_string(self,token):return 'ItemName'
            def generic_binding(self,token):return {'idClass':SPEC[3],'elementType':self.element}
        return Image()

    def test_body_binding_plus_generic_and_cache_checks_are_required(self):
        image=self.image()
        with patch.object(mapping,'SPECS',(SPEC,)),patch.object(mapping,'_METHOD_PROFILE',{'Rule':('code','signature')}):
            result=_inspect_rules(image)
            self.assertEqual(8,result['counts'][SPEC[3]]['value'])
            self.assertFalse(result['runtimeInitializationVerified'])
            image.element=8
            with self.assertRaisesRegex(MappingBound,'MAPPING_CATEGORY_BINDING_MISMATCH'):_inspect_rules(image)
            image.element=6;image.branch=True
            with self.assertRaisesRegex(MappingBound,'MAPPING_COUNT_INITIALIZER_CONTROL_FLOW'):_inspect_rules(image)
            image.branch=False;image.early_return=True
            with self.assertRaisesRegex(MappingBound,'MAPPING_COUNT_INITIALIZER_EARLY_RETURN'):_inspect_rules(image)
            image.early_return=False;image.wrong_hash=True
            with self.assertRaisesRegex(MappingBound,'REVIEWED_MAPPING_METHOD_MISMATCH'):_inspect_rules(image)
        with patch.object(mapping,'_METHOD_PROFILE',{}),self.assertRaisesRegex(MappingBound,'MAPPING_REVIEW_PROFILE_MISSING'):_inspect_rules(image)


class ProducerMappingTests(unittest.TestCase):
    def test_builtin_selects_requested_locales_but_injected_parser_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'server';source.mkdir();binary=source/'TerrariaServer.exe';binary.write_bytes(b'original inert fixture')
            evidence={'input':{'sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}}
            with patch('resource_pipeline.server_semantics.extract_server_semantics',return_value=evidence) as inspect:
                RawEvidenceProducer().produce({'server':source},{},root/'builtin')
            self.assertEqual(('en-US','zh-Hans'),inspect.call_args.kwargs['requested_locales'])
            calls=[]
            def injected(path,*,checkpoint):calls.append(path);return evidence
            RawEvidenceProducer(injected).produce({'server':source},{},root/'injected')
            self.assertEqual([binary],calls)

    def test_receipt_preserves_partial_capabilities_and_overall_blockers(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'server';source.mkdir();binary=source/'TerrariaServer.exe';binary.write_bytes(b'original inert fixture')
            digest=hashlib.sha256(binary.read_bytes()).hexdigest()
            inspector=lambda path,**kwargs:{'input':{'sha256':digest},'idFamilies':{'items':{'records':[{'id':1}]}},'gameVersionEvidence':{'trusted':False}}
            mapped={'status':'PARTIAL','complete':False,'publishable':False,'ruleBinding':{'status':'synthetic-fixture'},'channels':{
                'ItemName':{'family':'items','capability':'localizedNames','status':'PARTIAL','complete':False,'records':[{'id':1,'key':'ItemName.Fixture'}],
                            'unattemptedIds':[],'provenStaticKeyMappings':1,'pending':['runtime loader']}}}
            with patch('resource_pipeline.real_producer.extract_locale_mapping',return_value=mapped) as call:
                result=RawEvidenceProducer(inspector).produce({'server':source},{},root/'output')
            self.assertEqual(64,len(call.call_args.kwargs['server_evidence_sha256']))
            receipt=result['localeMappingEvidence'][0]
            self.assertNotIn('records',receipt['channels']['ItemName'])
            self.assertTrue((root/'output'/receipt['path']).is_file())
            self.assertEqual(1,result['familyCoverage']['items']['localizedSubcapabilities'][0]['channels']['ItemName']['provenStaticKeyMappings'])
            self.assertIn('localizedNames',result['familyCoverage']['items']['missingRules'])
            self.assertFalse(result['familyCoverage']['items']['complete'])
            self.assertFalse(result['publishable']);self.assertFalse(result['extractionComplete'])
            self.assertEqual(['GAME_VERSION_AND_FULL_SEMANTICS_NOT_VERIFIED'],result['blockers'])


if __name__=='__main__':unittest.main()
